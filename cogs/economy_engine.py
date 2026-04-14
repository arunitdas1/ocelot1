import time
import random
import json
import discord
from discord.ext import commands, tasks
from db import cursor, conn
from utils import (
    get_eco_state, set_eco_state, get_gov, set_gov,
    get_all_citizens, log_tx, fmt,
    housing_expense, add_gov_revenue, deduct_gov_expense,
    clamp, safe_float, safe_json_loads, housing_cost_for_tier, snapshot_macro, record_retention_metric
)

SUPPLY_DEPENDENCIES = {
    "food": {"energy": 0.25},
    "materials": {"energy": 0.20},
    "tech": {"materials": 0.35, "energy": 0.25},
    "energy": {"materials": 0.10},
    "luxury": {"materials": 0.20, "tech": 0.15, "energy": 0.10},
}

SEASONAL_DEMAND = {
    # month -> category multipliers (low amplitude, realism flavor)
    1: {"energy": 1.15, "food": 1.05},
    2: {"energy": 1.12, "food": 1.03},
    6: {"energy": 0.92, "luxury": 1.08},
    7: {"energy": 0.90, "luxury": 1.10},
    11: {"food": 1.10, "luxury": 1.12},
    12: {"food": 1.15, "luxury": 1.20, "energy": 1.08},
}


def _month_utc() -> int:
    return time.gmtime().tm_mon


def _get_confidence() -> tuple[float, float]:
    consumer = safe_float(get_eco_state("consumer_confidence") or 0.5, 0.5)
    business = safe_float(get_eco_state("business_confidence") or 0.5, 0.5)
    return clamp(consumer, 0.0, 1.0), clamp(business, 0.0, 1.0)


def _apply_supply_chain(category: str, base_supply: float, phase: str) -> float:
    # Simple dependency choke model based on backlog/inventory of inputs.
    deps = SUPPLY_DEPENDENCIES.get(category, {})
    if not deps:
        return base_supply

    cursor.execute("SELECT category, backlog, inventory FROM supply_chain_state")
    state = {c: (safe_float(b, 0.0), safe_float(i, 0.0)) for c, b, i in cursor.fetchall()}

    choke = 1.0
    for dep_cat, weight in deps.items():
        backlog, inv = state.get(dep_cat, (0.0, 0.0))
        # Backlog hurts, inventory helps; damped.
        dep_factor = clamp(1.0 - (backlog / 5000.0) * weight + (inv / 5000.0) * (weight * 0.5), 0.5, 1.2)
        choke *= dep_factor

    phase_mult = {"boom": 1.05, "stable": 1.0, "recession": 0.92, "depression": 0.85}.get(phase, 1.0)
    return max(1.0, base_supply * choke * phase_mult)


def _update_supply_chain_state(goods_by_cat: dict[str, list[tuple]]):
    now = int(time.time())
    for cat, goods in goods_by_cat.items():
        # Backlog increases when demand materially exceeds supply; inventory grows when supply exceeds demand.
        total_supply = sum(max(1, int(g[4])) for g in goods)  # supply
        total_demand = sum(max(1, int(g[5])) for g in goods)  # demand
        gap = total_demand - total_supply

        cursor.execute("SELECT backlog, inventory FROM supply_chain_state WHERE category = ? LIMIT 1", (cat,))
        row = cursor.fetchone()
        backlog, inv = (safe_float(row[0], 0.0), safe_float(row[1], 0.0)) if row else (0.0, 0.0)

        backlog = clamp(backlog + max(0.0, gap) * 0.2 - inv * 0.03, 0.0, 50000.0)
        inv = clamp(inv + max(0.0, -gap) * 0.15 - backlog * 0.02, 0.0, 50000.0)

        cursor.execute(
            "INSERT OR REPLACE INTO supply_chain_state(category, backlog, inventory, updated_at) VALUES (?, ?, ?, ?)",
            (cat, round(backlog, 2), round(inv, 2), now)
        )

RANDOM_EVENTS = [
    {
        "name": "Tech Boom",
        "description": "A wave of technological innovation is boosting the tech sector!",
        "effects": {"price_multiplier_cat": "tech", "price_multiplier": 1.25, "salary_multiplier": 1.1},
        "duration": 7200,
    },
    {
        "name": "Supply Chain Crisis",
        "description": "Global supply chains are disrupted, causing shortages.",
        "effects": {"price_multiplier_cat": "materials", "price_multiplier": 1.40, "inflation_change": 0.02},
        "duration": 5400,
    },
    {
        "name": "Energy Crisis",
        "description": "Energy prices are spiking due to geopolitical tensions.",
        "effects": {"price_multiplier_cat": "energy", "price_multiplier": 1.60, "inflation_change": 0.03},
        "duration": 7200,
    },
    {
        "name": "Economic Recession",
        "description": "The economy has entered a downturn. Consumer confidence is low.",
        "effects": {"salary_multiplier": 0.85, "inflation_change": -0.01, "interest_change": 0.02},
        "duration": 10800,
    },
    {
        "name": "Market Boom",
        "description": "The stock market is surging! Investor confidence is at all-time highs.",
        "effects": {"stock_multiplier": 1.15, "salary_multiplier": 1.05},
        "duration": 5400,
    },
    {
        "name": "Pandemic",
        "description": "A global health crisis is reducing productivity and consumer spending.",
        "effects": {"salary_multiplier": 0.80, "price_multiplier_cat": "food", "price_multiplier": 1.20, "inflation_change": 0.04},
        "duration": 14400,
    },
    {
        "name": "Harvest Surplus",
        "description": "Exceptional harvests worldwide are driving food prices down.",
        "effects": {"price_multiplier_cat": "food", "price_multiplier": 0.75},
        "duration": 5400,
    },
    {
        "name": "Central Bank Stimulus",
        "description": "The central bank has cut interest rates to stimulate economic growth.",
        "effects": {"interest_change": -0.02, "salary_multiplier": 1.05},
        "duration": 7200,
    },
    {
        "name": "Regulatory Crackdown",
        "description": "New government regulations are increasing business costs.",
        "effects": {"salary_multiplier": 0.95, "interest_change": 0.01},
        "duration": 5400,
    },
    {
        "name": "Tech Crash",
        "description": "The tech bubble has burst. Tech company valuations are collapsing.",
        "effects": {"price_multiplier_cat": "tech", "price_multiplier": 0.65, "stock_multiplier": 0.80},
        "duration": 7200,
    },
    {
        "name": "Gold Rush",
        "description": "A commodities boom is sending luxury and material goods prices soaring.",
        "effects": {"price_multiplier_cat": "luxury", "price_multiplier": 1.30, "salary_multiplier": 1.05},
        "duration": 5400,
    },
    {
        "name": "Currency Devaluation",
        "description": "The currency is losing value. Inflation is accelerating.",
        "effects": {"inflation_change": 0.05, "price_multiplier": 1.10},
        "duration": 7200,
    },
]


class EconomyEngine(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.simulate_market.start()
        self.trigger_events.start()
        self.process_economy.start()

    def cog_unload(self):
        self.simulate_market.cancel()
        self.trigger_events.cancel()
        self.process_economy.cancel()

    @tasks.loop(minutes=5)
    async def simulate_market(self):
        """Update market prices based on supply/demand and inflation."""
        try:
            inflation = safe_float(get_eco_state("inflation_rate") or 0.02, 0.02)
            phase = get_eco_state("economic_phase") or "stable"
            consumer_conf, business_conf = _get_confidence()
            seasonality_strength = clamp(safe_float(get_eco_state("seasonality_strength") or 0.15, 0.15), 0.0, 0.5)
            month = _month_utc()
            seasonal = SEASONAL_DEMAND.get(month, {})

            phase_demand_mult = {"boom": 1.15, "stable": 1.0, "recession": 0.85, "depression": 0.70}.get(phase, 1.0)

            cursor.execute("SELECT good_id, name, category, current_price, base_price, supply, demand, volatility FROM market_goods")
            goods = cursor.fetchall()
            goods_by_cat = {}
            for g in goods:
                goods_by_cat.setdefault(g[2], []).append(g)

            for good_id, name, category, curr_price, base_price, supply, demand, vol in goods:
                demand_adj = max(1, int(demand * phase_demand_mult))
                # Confidence and seasonality shape demand, supply chain shapes supply
                seasonal_mult = seasonal.get(category, 1.0)
                seasonal_mult = 1.0 + (seasonal_mult - 1.0) * seasonality_strength
                demand_adj = max(1, int(demand_adj * (0.85 + consumer_conf * 0.3) * seasonal_mult))

                base_supply = max(1.0, supply + random.randint(-50, 50))
                supply_adj = int(_apply_supply_chain(category, base_supply, phase))
                supply_adj = max(1, supply_adj)

                demand_new = max(1, demand_adj + random.randint(-30, 30))

                ratio = demand_new / max(supply_adj, 1)
                price_change = (ratio - 1.0) * vol * random.uniform(0.5, 1.5)
                inflation_push = inflation * 0.01 * random.uniform(0.8, 1.2)
                new_price = curr_price * (1 + price_change + inflation_push)

                lower = base_price * 0.3
                upper = base_price * 4.0
                new_price = max(lower, min(upper, new_price))
                new_price = round(new_price, 2)

                cursor.execute(
                    "UPDATE market_goods SET current_price = ?, supply = ?, demand = ? WHERE good_id = ?",
                    (new_price, supply_adj, demand_new, good_id)
                )

            _update_supply_chain_state(goods_by_cat)

            cursor.execute("SELECT biz_id, shares_issued, share_price, reputation FROM businesses WHERE is_public = 1 AND is_bankrupt = 0")
            stocks = cursor.fetchall()
            for biz_id, shares, price, rep in stocks:
                # Confidence + reputation bias drift, bounded
                rep_mult = clamp(0.85 + (rep / 100) * 0.3, 0.85, 1.15)
                conf_mult = 0.9 + business_conf * 0.2
                phase_mult = (1.15 if phase == "boom" else 0.85 if phase == "recession" else 0.75 if phase == "depression" else 1.0)
                change = random.uniform(-0.025, 0.03) * rep_mult * conf_mult * phase_mult
                new_price = max(0.01, round(price * (1 + change), 4))
                cursor.execute(
                    "UPDATE businesses SET share_price = ? WHERE biz_id = ?",
                    (new_price, biz_id)
                )

            conn.commit()
        except Exception as e:
            print(f"[EconomyEngine] Market sim error: {e}")

    @tasks.loop(minutes=30)
    async def trigger_events(self):
        """Randomly trigger economic events."""
        try:
            if (get_eco_state("events_enabled") or "1") != "1":
                return
            now = int(time.time())
            cursor.execute("SELECT COUNT(*) FROM active_events WHERE ends_at > ?", (now,))
            active_count = cursor.fetchone()[0]

            if active_count >= 3:
                return

            # Mildly confidence-driven event likelihood, bounded
            consumer_conf, _ = _get_confidence()
            trigger_p = clamp(0.25 + (0.5 - consumer_conf) * 0.15, 0.15, 0.40)
            if random.random() > trigger_p:
                return

            # Basic anti-repeat: avoid same price_multiplier_cat in consecutive triggers
            cursor.execute("SELECT effects FROM active_events WHERE ends_at > ? ORDER BY started_at DESC LIMIT 1", (now,))
            last = cursor.fetchone()
            last_cat = None
            if last:
                last_eff = safe_json_loads(last[0], {})
                last_cat = last_eff.get("price_multiplier_cat")

            candidates = []
            for ev in RANDOM_EVENTS:
                cat = ev.get("effects", {}).get("price_multiplier_cat")
                if last_cat and cat and cat == last_cat and random.random() < 0.7:
                    continue
                candidates.append(ev)

            event = random.choice(candidates or RANDOM_EVENTS)
            effects = event["effects"]
            duration = event["duration"]

            cursor.execute(
                "INSERT INTO active_events(name, description, effects, started_at, ends_at, tag, reward_pool, max_participants) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event["name"],
                    event["description"],
                    json.dumps(effects),
                    now,
                    now + duration,
                    effects.get("price_multiplier_cat", "macro"),
                    round(random.uniform(3000, 12000), 2),
                    0,
                )
            )
            conn.commit()

            # Confidence nudges (balanced, bounded)
            consumer_conf, business_conf = _get_confidence()
            conf_hit = 0.0
            if event["name"] in ("Pandemic", "Supply Chain Crisis", "Energy Crisis", "Economic Recession", "Currency Devaluation"):
                conf_hit = -0.06
            elif event["name"] in ("Tech Boom", "Market Boom", "Harvest Surplus", "Gold Rush", "Central Bank Stimulus"):
                conf_hit = 0.04
            consumer_conf = clamp(consumer_conf + conf_hit, 0.0, 1.0)
            business_conf = clamp(business_conf + conf_hit * 0.8, 0.0, 1.0)
            set_eco_state("consumer_confidence", consumer_conf)
            set_eco_state("business_confidence", business_conf)

            if "inflation_change" in effects:
                current = safe_float(get_eco_state("inflation_rate") or 0.02, 0.02)
                new_inf = max(-0.05, min(0.5, current + effects["inflation_change"]))
                set_eco_state("inflation_rate", new_inf)

            if "interest_change" in effects:
                current = safe_float(get_eco_state("base_interest_rate") or 0.05, 0.05)
                new_rate = max(0.01, min(0.4, current + effects["interest_change"]))
                set_eco_state("base_interest_rate", new_rate)

            cat = effects.get("price_multiplier_cat")
            mult = effects.get("price_multiplier")
            if cat and mult:
                cursor.execute(
                    "UPDATE market_goods SET current_price = MIN(base_price * 4, MAX(base_price * 0.3, current_price * ?)) WHERE category = ?",
                    (mult, cat)
                )
            elif mult and not cat:
                cursor.execute(
                    "UPDATE market_goods SET current_price = MIN(base_price * 4, MAX(base_price * 0.3, current_price * ?))",
                    (mult,)
                )

            if "stock_multiplier" in effects:
                smult = effects["stock_multiplier"]
                cursor.execute(
                    "UPDATE businesses SET share_price = MAX(0.01, share_price * ?) WHERE is_public = 1",
                    (smult,)
                )

            conn.commit()

            self._update_phase()
            print(f"[EconomyEngine] Event triggered: {event['name']}")

        except Exception as e:
            print(f"[EconomyEngine] Event trigger error: {e}")

    @tasks.loop(hours=1)
    async def process_economy(self):
        """Hourly: expenses, welfare, loans, insurance, contracts, legal, business drift, telemetry."""
        try:
            now = int(time.time())
            all_citizens = get_all_citizens()
            defaults_last_7d = 0

            for uid in all_citizens:
                cursor.execute("SELECT cash, bank, debt, job_id, housing, last_expense, lifestyle_tier FROM citizens WHERE user_id = ?", (uid,))
                row = cursor.fetchone()
                if not row:
                    continue
                cash, bank, debt, job_id, housing, last_expense, lifestyle = row

                if now - (last_expense or 0) >= 86400:
                    inflation = safe_float(get_eco_state("inflation_rate") or 0.02, 0.02)
                    base_food = 50.0 * (1 + clamp(inflation, -0.05, 0.5) * 0.5)
                    tier = lifestyle or "standard"
                    rent_cost, upkeep, comfort = housing_cost_for_tier(tier if housing != "homeless" else "homeless")
                    # Lifestyle scaling (balanced)
                    lifestyle_mult = {"budget": 0.85, "standard": 1.0, "premium": 1.25, "luxury": 1.55}.get(tier, 1.0)
                    food_cost = round(base_food * lifestyle_mult, 2)
                    total_exp = round(food_cost + rent_cost + upkeep, 2)

                    if cash >= total_exp:
                        cursor.execute("UPDATE citizens SET cash = cash - ?, last_expense = ? WHERE user_id = ?",
                                       (total_exp, now, uid))
                        log_tx(uid, "living_expenses", -total_exp, f"Daily expenses ({tier})")
                    elif (cash + bank) >= total_exp:
                        from_bank = total_exp - cash
                        cursor.execute(
                            "UPDATE citizens SET cash = 0, bank = bank - ?, last_expense = ? WHERE user_id = ?",
                            (from_bank, now, uid)
                        )
                        log_tx(uid, "living_expenses", -total_exp, f"Daily expenses ({tier}, bank used)")
                    else:
                        shortfall = total_exp - cash - bank
                        cursor.execute(
                            "UPDATE citizens SET cash = 0, bank = 0, debt = debt + ?, last_expense = ? WHERE user_id = ?",
                            (shortfall, now, uid)
                        )
                        if housing != "homeless":
                            cursor.execute("UPDATE citizens SET housing = 'homeless' WHERE user_id = ?", (uid,))
                        log_tx(uid, "expense_default", -shortfall, "Could not pay living expenses — fell into debt")

                if job_id is None:
                    welfare = 100.0
                    gov_reserves = get_gov("reserves")
                    if gov_reserves >= welfare:
                        cursor.execute("UPDATE citizens SET cash = cash + ? WHERE user_id = ?", (welfare, uid))
                        deduct_gov_expense(welfare)
                        log_tx(uid, "welfare", welfare, "Unemployment welfare payment")

            # Auto-release users whose jail timers have expired.
            cursor.execute(
                "UPDATE citizens SET is_jailed = 0, last_release_at = 0 "
                "WHERE is_jailed = 1 AND last_release_at > 0 AND last_release_at <= ?",
                (now,),
            )

            # Insurance billing (daily)
            cursor.execute(
                "SELECT policy_id, holder_id, premium, status, last_billed_at FROM insurance_policies WHERE status = 'active'"
            )
            policies = cursor.fetchall()
            for policy_id, holder_id, premium, status, last_billed_at in policies:
                if now - (last_billed_at or 0) < 86400:
                    continue
                prem = round(float(premium), 2)
                cursor.execute(
                    "UPDATE citizens SET cash = cash - ? WHERE user_id = ? AND cash >= ?",
                    (prem, holder_id, prem)
                )
                if cursor.rowcount:
                    cursor.execute("UPDATE insurance_policies SET last_billed_at = ? WHERE policy_id = ?", (now, policy_id))
                    log_tx(holder_id, "insurance_premium", -prem, f"Insurance premium (policy #{policy_id})")
                else:
                    # Soft lapse if unpaid repeatedly
                    cursor.execute("UPDATE insurance_policies SET status = 'lapsed' WHERE policy_id = ?", (policy_id,))

            cursor.execute(
                "SELECT loan_id, borrower_id, remaining, interest_rate, weekly_payment, last_payment "
                "FROM loans WHERE status = 'active'"
            )
            loans = cursor.fetchall()

            for loan_id, borrower_id, remaining, rate, weekly_pay, last_pay in loans:
                week_seconds = 604800
                if now - (last_pay or 0) >= week_seconds:
                    interest = round(remaining * (rate / 52), 2)
                    new_remaining = round(remaining + interest, 2)
                    cursor.execute(
                        "SELECT cash FROM citizens WHERE user_id = ?", (borrower_id,)
                    )
                    row = cursor.fetchone()
                    if not row:
                        continue
                    citizen_cash = row[0]
                    pay = min(weekly_pay, citizen_cash)
                    after_pay = max(0.0, round(new_remaining - pay, 2))

                    if pay > 0:
                        cursor.execute("UPDATE citizens SET cash = cash - ? WHERE user_id = ?", (pay, borrower_id))
                        log_tx(borrower_id, "loan_interest_payment", -pay, "Weekly loan repayment + interest")

                    if after_pay <= 0:
                        cursor.execute("UPDATE loans SET status = 'paid', remaining = 0 WHERE loan_id = ?", (loan_id,))
                        cursor.execute("UPDATE citizens SET debt = MAX(0, debt - ?), credit_score = MIN(850, credit_score + 15) WHERE user_id = ?",
                                       (remaining, borrower_id))
                    elif citizen_cash < weekly_pay * 0.5:
                        cursor.execute("UPDATE loans SET remaining = ?, last_payment = ? WHERE loan_id = ?",
                                       (after_pay, now, loan_id))
                        cursor.execute("UPDATE citizens SET credit_score = MAX(300, credit_score - 20), debt = ? WHERE user_id = ?",
                                       (after_pay, borrower_id))
                        log_tx(borrower_id, "loan_default", 0, "Missed loan payment — credit score penalised")
                        defaults_last_7d += 1
                    else:
                        cursor.execute("UPDATE loans SET remaining = ?, last_payment = ? WHERE loan_id = ?",
                                       (after_pay, now, loan_id))
                        cursor.execute("UPDATE citizens SET debt = ?, credit_score = MIN(850, credit_score + 5) WHERE user_id = ?",
                                       (after_pay, borrower_id))

            cursor.execute(
                "SELECT biz_id, cash, revenue, expenses, is_bankrupt FROM businesses WHERE is_bankrupt = 0"
            )
            businesses = cursor.fetchall()
            for biz_id, biz_cash, rev, exp, is_bankrupt in businesses:
                if exp > 0:
                    net = round(rev - exp, 2)
                    new_cash = round(biz_cash + net * 0.1, 2)
                    if new_cash <= 0 and exp > rev * 1.5:
                        cursor.execute("UPDATE businesses SET is_bankrupt = 1 WHERE biz_id = ?", (biz_id,))
                    else:
                        cursor.execute("UPDATE businesses SET cash = MAX(0, ?) WHERE biz_id = ?", (new_cash, biz_id))

            conn.commit()
            self._update_phase()
            self._refresh_quests(now)
            self._record_retention_metrics(now)
            self._snapshot(now, defaults_last_7d)
            print(f"[EconomyEngine] Hourly cycle complete.")

        except Exception as e:
            print(f"[EconomyEngine] Hourly process error: {e}")

    def _update_phase(self):
        """Auto-detect economic phase based on macro conditions (balanced scoring)."""
        inflation = safe_float(get_eco_state("inflation_rate") or 0.02, 0.02)
        cursor.execute("SELECT COUNT(*) FROM citizens")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM citizens WHERE job_id IS NULL")
        unemployed = cursor.fetchone()[0]
        unemp_rate = unemployed / max(total, 1)

        cursor.execute("SELECT COUNT(*) FROM loans WHERE status = 'active' AND remaining > principal * 1.25")
        stressed_loans = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM businesses WHERE is_bankrupt = 1")
        bankrupt = cursor.fetchone()[0]

        consumer_conf, business_conf = _get_confidence()
        stress = (
            clamp(unemp_rate, 0.0, 1.0) * 0.45
            + clamp(stressed_loans / max(total, 1), 0.0, 1.0) * 0.25
            + clamp(bankrupt / max(1, total // 5), 0.0, 1.0) * 0.15
            + clamp(max(0.0, inflation - 0.05) / 0.20, 0.0, 1.0) * 0.15
        )
        optimism = (consumer_conf * 0.55 + business_conf * 0.45)
        score = optimism - stress

        if score > 0.35 and inflation < 0.12 and unemp_rate < 0.18:
            phase = "boom"
        elif score < -0.35 or unemp_rate > 0.42 or inflation < -0.01:
            phase = "depression"
        elif score < -0.10 or unemp_rate > 0.27 or inflation < 0.004:
            phase = "recession"
        else:
            phase = "stable"

        set_eco_state("economic_phase", phase)

    def _snapshot(self, now: int, defaults_last_7d: int):
        inflation = safe_float(get_eco_state("inflation_rate") or 0.02, 0.02)
        base_interest = safe_float(get_eco_state("base_interest_rate") or 0.05, 0.05)
        cursor.execute("SELECT COUNT(*) FROM citizens")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM citizens WHERE job_id IS NULL")
        unemployed = cursor.fetchone()[0]
        unemp_rate = unemployed / max(total, 1)

        cursor.execute("SELECT AVG(credit_score) FROM citizens")
        avg_cs = safe_float(cursor.fetchone()[0] or 650, 650)
        cursor.execute("SELECT COUNT(*) FROM loans WHERE status = 'active'")
        active_loans = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM businesses WHERE is_bankrupt = 0")
        active_biz = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM businesses WHERE is_bankrupt = 1")
        bankrupt_biz = cursor.fetchone()[0]

        # proxies (simple, stable)
        cursor.execute("SELECT SUM(cash + bank) FROM citizens")
        money_supply = safe_float(cursor.fetchone()[0] or 0.0, 0.0)
        gdp_proxy = money_supply * (0.02 + max(0.0, 0.02 - unemp_rate) * 0.05)
        velocity = clamp((0.8 + safe_float(get_eco_state('consumer_confidence') or 0.5, 0.5) * 0.6), 0.5, 1.6)
        gov_reserves = safe_float(get_gov("reserves"), 0.0)

        snapshot_macro(
            inflation=inflation,
            base_interest=base_interest,
            unemployment=unemp_rate,
            gdp_proxy=gdp_proxy,
            money_supply=money_supply,
            velocity_proxy=velocity,
            avg_credit_score=avg_cs,
            gov_reserves=gov_reserves,
            active_loans=int(active_loans),
            active_businesses=int(active_biz),
            bankrupt_businesses=int(bankrupt_biz),
            defaults_last_7d=int(defaults_last_7d),
        )

    def _refresh_quests(self, now: int):
        day_reset = now - (now % 86400) + 86400
        week_reset = now - (now % (86400 * 7)) + (86400 * 7)
        cursor.execute("SELECT user_id FROM citizens")
        users = [r[0] for r in cursor.fetchall()]
        cursor.execute("SELECT key, target_type, target_value FROM quests_daily WHERE is_active = 1")
        daily = cursor.fetchall()
        cursor.execute("SELECT key, target_type, target_value FROM quests_weekly WHERE is_active = 1")
        weekly = cursor.fetchall()
        for uid in users:
            for key, _, target in daily:
                cursor.execute(
                    "INSERT OR IGNORE INTO user_quests(user_id, quest_type, quest_key, progress, target, claimed, assigned_at, resets_at) "
                    "VALUES (?, 'daily', ?, 0, ?, 0, ?, ?)",
                    (uid, key, float(target), now, day_reset),
                )
                cursor.execute(
                    "UPDATE user_quests SET progress = CASE WHEN resets_at <= ? THEN 0 ELSE progress END, "
                    "claimed = CASE WHEN resets_at <= ? THEN 0 ELSE claimed END, "
                    "resets_at = CASE WHEN resets_at <= ? THEN ? ELSE resets_at END "
                    "WHERE user_id = ? AND quest_type = 'daily' AND quest_key = ?",
                    (now, now, now, day_reset, uid, key),
                )
            for key, _, target in weekly:
                cursor.execute(
                    "INSERT OR IGNORE INTO user_quests(user_id, quest_type, quest_key, progress, target, claimed, assigned_at, resets_at) "
                    "VALUES (?, 'weekly', ?, 0, ?, 0, ?, ?)",
                    (uid, key, float(target), now, week_reset),
                )
                cursor.execute(
                    "UPDATE user_quests SET progress = CASE WHEN resets_at <= ? THEN 0 ELSE progress END, "
                    "claimed = CASE WHEN resets_at <= ? THEN 0 ELSE claimed END, "
                    "resets_at = CASE WHEN resets_at <= ? THEN ? ELSE resets_at END "
                    "WHERE user_id = ? AND quest_type = 'weekly' AND quest_key = ?",
                    (now, now, now, week_reset, uid, key),
                )
        conn.commit()

    def _record_retention_metrics(self, now: int):
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now))
        cursor.execute("SELECT COUNT(DISTINCT user_id) FROM transactions WHERE timestamp >= ?", (now - 86400,))
        dau = int(cursor.fetchone()[0] or 0)
        cursor.execute("SELECT COUNT(*) FROM user_quests WHERE progress >= target AND claimed = 1 AND assigned_at >= ?", (now - 86400,))
        quest_claims = int(cursor.fetchone()[0] or 0)
        cursor.execute("SELECT COUNT(*) FROM citizens WHERE daily_streak > 0")
        active_streaks = int(cursor.fetchone()[0] or 0)
        record_retention_metric("dau", dau, day_key)
        record_retention_metric("quest_claims_24h", quest_claims, day_key)
        record_retention_metric("active_streaks", active_streaks, day_key)

    @simulate_market.before_loop
    @trigger_events.before_loop
    @process_economy.before_loop
    async def before_tasks(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(EconomyEngine(bot))
