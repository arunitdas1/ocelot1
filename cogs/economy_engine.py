import time
import random
import json
import discord
from pymongo import UpdateOne
from discord.ext import commands, tasks
from db import (
    active_events,
    businesses,
    citizens,
    insurance_policies,
    loans,
    market_goods,
    quests_daily,
    quests_weekly,
    supply_chain_state,
    transactions,
    user_quests,
    next_id,
)
from utils import (
    get_eco_state, get_eco_states, set_eco_state, get_gov, set_gov,
    log_tx, fmt,
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


def _apply_supply_chain(category: str, base_supply: float, phase: str, state: dict | None = None) -> float:
    # Simple dependency choke model based on backlog/inventory of inputs.
    deps = SUPPLY_DEPENDENCIES.get(category, {})
    if not deps:
        return base_supply

    if state is None:
        state = {}
        for row in supply_chain_state.find({}, {"_id": 0, "category": 1, "backlog": 1, "inventory": 1}):
            category_key = row.get("category")
            state[category_key] = (
                safe_float(row.get("backlog", 0.0), 0.0),
                safe_float(row.get("inventory", 0.0), 0.0),
            )

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

        row = supply_chain_state.find_one({"category": cat}, {"_id": 0, "backlog": 1, "inventory": 1})
        backlog, inv = (
            safe_float(row.get("backlog", 0.0), 0.0),
            safe_float(row.get("inventory", 0.0), 0.0),
        ) if row else (0.0, 0.0)

        backlog = clamp(backlog + max(0.0, gap) * 0.2 - inv * 0.03, 0.0, 50000.0)
        inv = clamp(inv + max(0.0, -gap) * 0.15 - backlog * 0.02, 0.0, 50000.0)

        supply_chain_state.update_one(
            {"category": cat},
            {"$set": {"backlog": round(backlog, 2), "inventory": round(inv, 2), "updated_at": now}},
            upsert=True,
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
            eco = get_eco_states(["inflation_rate", "economic_phase", "seasonality_strength"])
            inflation = safe_float(eco.get("inflation_rate") or 0.02, 0.02)
            phase = eco.get("economic_phase") or "stable"
            consumer_conf, business_conf = _get_confidence()
            seasonality_strength = clamp(safe_float(eco.get("seasonality_strength") or 0.15, 0.15), 0.0, 0.5)
            month = _month_utc()
            seasonal = SEASONAL_DEMAND.get(month, {})
            sc_rows = list(supply_chain_state.find({}, {"_id": 0, "category": 1, "backlog": 1, "inventory": 1}))
            sc_state = {
                row.get("category"): (
                    safe_float(row.get("backlog", 0.0), 0.0),
                    safe_float(row.get("inventory", 0.0), 0.0),
                )
                for row in sc_rows
            }

            phase_demand_mult = {"boom": 1.15, "stable": 1.0, "recession": 0.85, "depression": 0.70}.get(phase, 1.0)

            goods = [
                (
                    row.get("good_id"),
                    row.get("name"),
                    row.get("category"),
                    safe_float(row.get("current_price", 0.0), 0.0),
                    safe_float(row.get("base_price", 0.0), 0.0),
                    int(row.get("supply", 0) or 0),
                    int(row.get("demand", 0) or 0),
                    safe_float(row.get("volatility", 0.0), 0.0),
                )
                for row in market_goods.find(
                    {},
                    {"_id": 0, "good_id": 1, "name": 1, "category": 1, "current_price": 1, "base_price": 1, "supply": 1, "demand": 1, "volatility": 1},
                )
            ]
            goods_by_cat = {}
            for g in goods:
                goods_by_cat.setdefault(g[2], []).append(g)

            goods_ops = []
            for good_id, name, category, curr_price, base_price, supply, demand, vol in goods:
                demand_adj = max(1, int(demand * phase_demand_mult))
                # Confidence and seasonality shape demand, supply chain shapes supply
                seasonal_mult = seasonal.get(category, 1.0)
                seasonal_mult = 1.0 + (seasonal_mult - 1.0) * seasonality_strength
                demand_adj = max(1, int(demand_adj * (0.85 + consumer_conf * 0.3) * seasonal_mult))

                base_supply = max(1.0, supply + random.randint(-50, 50))
                supply_adj = int(_apply_supply_chain(category, base_supply, phase, sc_state))
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

                goods_ops.append(
                    UpdateOne(
                        {"good_id": good_id},
                        {"$set": {"current_price": new_price, "supply": supply_adj, "demand": demand_new}},
                    )
                )
            if goods_ops:
                market_goods.bulk_write(goods_ops, ordered=False)

            _update_supply_chain_state(goods_by_cat)

            stocks = [
                (
                    row.get("biz_id"),
                    row.get("shares_issued"),
                    safe_float(row.get("share_price", 0.01), 0.01),
                    safe_float(row.get("reputation", 0.0), 0.0),
                )
                for row in businesses.find(
                    {"is_public": 1, "is_bankrupt": 0},
                    {"_id": 0, "biz_id": 1, "shares_issued": 1, "share_price": 1, "reputation": 1},
                )
            ]
            stock_ops = []
            for biz_id, shares, price, rep in stocks:
                # Confidence + reputation bias drift, bounded
                rep_mult = clamp(0.85 + (rep / 100) * 0.3, 0.85, 1.15)
                conf_mult = 0.9 + business_conf * 0.2
                phase_mult = (1.15 if phase == "boom" else 0.85 if phase == "recession" else 0.75 if phase == "depression" else 1.0)
                change = random.uniform(-0.025, 0.03) * rep_mult * conf_mult * phase_mult
                new_price = max(0.01, round(price * (1 + change), 4))
                stock_ops.append(UpdateOne({"biz_id": biz_id}, {"$set": {"share_price": new_price}}))
            if stock_ops:
                businesses.bulk_write(stock_ops, ordered=False)
        except Exception as e:
            print(f"[EconomyEngine] Market sim error: {e}")

    @tasks.loop(minutes=30)
    async def trigger_events(self):
        """Randomly trigger economic events."""
        try:
            if (get_eco_state("events_enabled") or "1") != "1":
                return
            now = int(time.time())
            active_count = active_events.count_documents({"ends_at": {"$gt": now}})

            if active_count >= 3:
                return

            # Mildly confidence-driven event likelihood, bounded
            consumer_conf, _ = _get_confidence()
            trigger_p = clamp(0.25 + (0.5 - consumer_conf) * 0.15, 0.15, 0.40)
            if random.random() > trigger_p:
                return

            # Basic anti-repeat: avoid same price_multiplier_cat in consecutive triggers
            last = active_events.find_one(
                {"ends_at": {"$gt": now}},
                {"_id": 0, "effects": 1},
                sort=[("started_at", -1)],
            )
            last_cat = None
            if last:
                last_eff = safe_json_loads(last.get("effects"), {})
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

            active_events.insert_one(
                {
                    "event_id": next_id("active_events"),
                    "name": event["name"],
                    "description": event["description"],
                    "effects": json.dumps(effects),
                    "started_at": now,
                    "ends_at": now + duration,
                    "tag": effects.get("price_multiplier_cat", "macro"),
                    "reward_pool": round(random.uniform(3000, 12000), 2),
                    "max_participants": 0,
                }
            )

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
                for row in market_goods.find({"category": cat}, {"_id": 0, "good_id": 1, "base_price": 1, "current_price": 1}):
                    base = safe_float(row.get("base_price", 0.0), 0.0)
                    curr = safe_float(row.get("current_price", base), base)
                    updated = min(base * 4.0, max(base * 0.3, curr * mult))
                    market_goods.update_one({"good_id": row.get("good_id")}, {"$set": {"current_price": updated}})
            elif mult and not cat:
                for row in market_goods.find({}, {"_id": 0, "good_id": 1, "base_price": 1, "current_price": 1}):
                    base = safe_float(row.get("base_price", 0.0), 0.0)
                    curr = safe_float(row.get("current_price", base), base)
                    updated = min(base * 4.0, max(base * 0.3, curr * mult))
                    market_goods.update_one({"good_id": row.get("good_id")}, {"$set": {"current_price": updated}})

            if "stock_multiplier" in effects:
                smult = effects["stock_multiplier"]
                for row in businesses.find({"is_public": 1}, {"_id": 0, "biz_id": 1, "share_price": 1}):
                    share_price = safe_float(row.get("share_price", 0.01), 0.01)
                    businesses.update_one({"biz_id": row.get("biz_id")}, {"$set": {"share_price": max(0.01, share_price * smult)}})

            self._update_phase()
            print(f"[EconomyEngine] Event triggered: {event['name']}")

        except Exception as e:
            print(f"[EconomyEngine] Event trigger error: {e}")

    @tasks.loop(hours=1)
    async def process_economy(self):
        """Hourly: expenses, welfare, loans, insurance, contracts, legal, business drift, telemetry."""
        try:
            now = int(time.time())
            inflation = safe_float(get_eco_state("inflation_rate") or 0.02, 0.02)
            all_citizens = list(
                citizens.find(
                    {},
                    {"_id": 0, "user_id": 1, "cash": 1, "bank": 1, "debt": 1, "job_id": 1, "housing": 1, "last_expense": 1, "lifestyle_tier": 1},
                )
            )
            defaults_last_7d = 0

            for row in all_citizens:
                uid = row.get("user_id")
                if uid is None:
                    continue
                cash = safe_float(row.get("cash", 0.0), 0.0)
                bank = safe_float(row.get("bank", 0.0), 0.0)
                debt = safe_float(row.get("debt", 0.0), 0.0)
                job_id = row.get("job_id")
                housing = row.get("housing")
                last_expense = int(row.get("last_expense", 0) or 0)
                lifestyle = row.get("lifestyle_tier")

                if now - (last_expense or 0) >= 86400:
                    base_food = 50.0 * (1 + clamp(inflation, -0.05, 0.5) * 0.5)
                    tier = lifestyle or "standard"
                    rent_cost, upkeep, comfort = housing_cost_for_tier(tier if housing != "homeless" else "homeless")
                    # Lifestyle scaling (balanced)
                    lifestyle_mult = {"budget": 0.85, "standard": 1.0, "premium": 1.25, "luxury": 1.55}.get(tier, 1.0)
                    food_cost = round(base_food * lifestyle_mult, 2)
                    total_exp = round(food_cost + rent_cost + upkeep, 2)

                    if cash >= total_exp:
                        paid = citizens.update_one(
                            {"user_id": uid, "cash": {"$gte": total_exp}, "last_expense": last_expense},
                            {"$inc": {"cash": -total_exp}, "$set": {"last_expense": now}},
                        )
                        if paid.modified_count > 0:
                            log_tx(uid, "living_expenses", -total_exp, f"Daily expenses ({tier})")
                    elif (cash + bank) >= total_exp:
                        from_bank = total_exp - cash
                        paid = citizens.update_one(
                            {
                                "user_id": uid,
                                "cash": cash,
                                "bank": {"$gte": from_bank},
                                "last_expense": last_expense,
                            },
                            {"$set": {"cash": 0.0, "last_expense": now}, "$inc": {"bank": -from_bank}},
                        )
                        if paid.modified_count > 0:
                            log_tx(uid, "living_expenses", -total_exp, f"Daily expenses ({tier}, bank used)")
                    else:
                        shortfall = total_exp - cash - bank
                        failed = citizens.update_one(
                            {"user_id": uid},
                            {"$set": {"cash": 0.0, "bank": 0.0, "last_expense": now}, "$inc": {"debt": shortfall}},
                        )
                        if failed.modified_count > 0:
                            if housing != "homeless":
                                citizens.update_one({"user_id": uid}, {"$set": {"housing": "homeless"}})
                            log_tx(uid, "expense_default", -shortfall, "Could not pay living expenses — fell into debt")

                if job_id is None:
                    welfare = 100.0
                    gov_reserves = get_gov("reserves")
                    if gov_reserves >= welfare:
                        citizens.update_one({"user_id": uid}, {"$inc": {"cash": welfare}})
                        deduct_gov_expense(welfare)
                        log_tx(uid, "welfare", welfare, "Unemployment welfare payment")

            # Auto-release users whose jail timers have expired.
            citizens.update_many(
                {"is_jailed": 1, "last_release_at": {"$gt": 0, "$lte": now}},
                {"$set": {"is_jailed": 0, "last_release_at": 0}},
            )

            # Insurance billing (daily)
            policies = list(
                insurance_policies.find(
                    {"status": "active"},
                    {"_id": 0, "policy_id": 1, "holder_id": 1, "premium": 1, "status": 1, "last_billed_at": 1},
                )
            )
            for policy in policies:
                policy_id = policy.get("policy_id")
                holder_id = policy.get("holder_id")
                premium = policy.get("premium", 0.0)
                last_billed_at = int(policy.get("last_billed_at", 0) or 0)
                if now - (last_billed_at or 0) < 86400:
                    continue
                prem = round(float(premium), 2)
                billed = insurance_policies.update_one(
                    {"policy_id": policy_id, "status": "active", "last_billed_at": last_billed_at},
                    {"$set": {"last_billed_at": now}},
                )
                if billed.modified_count == 0:
                    continue
                charged = citizens.update_one(
                    {"user_id": holder_id, "cash": {"$gte": prem}},
                    {"$inc": {"cash": -prem}},
                )
                if charged.modified_count == 0:
                    # Keep legacy behavior: unpaid premium lapses policy.
                    insurance_policies.update_one(
                        {"policy_id": policy_id, "last_billed_at": now},
                        {"$set": {"status": "lapsed"}},
                    )
                    continue
                log_tx(holder_id, "insurance_premium", -prem, f"Insurance premium (policy #{policy_id})")

            active_loans = list(
                loans.find(
                    {"status": "active"},
                    {"_id": 0, "loan_id": 1, "borrower_id": 1, "remaining": 1, "interest_rate": 1, "weekly_payment": 1, "last_payment": 1},
                )
            )

            for loan_doc in active_loans:
                loan_id = loan_doc.get("loan_id")
                borrower_id = loan_doc.get("borrower_id")
                remaining = safe_float(loan_doc.get("remaining", 0.0), 0.0)
                rate = safe_float(loan_doc.get("interest_rate", 0.0), 0.0)
                weekly_pay = safe_float(loan_doc.get("weekly_payment", 0.0), 0.0)
                last_pay = int(loan_doc.get("last_payment", 0) or 0)
                week_seconds = 604800
                if now - (last_pay or 0) >= week_seconds:
                    interest = round(remaining * (rate / 52), 2)
                    new_remaining = round(remaining + interest, 2)
                    row = citizens.find_one({"user_id": borrower_id}, {"_id": 0, "cash": 1})
                    if not row:
                        continue
                    citizen_cash = safe_float(row.get("cash", 0.0), 0.0)
                    pay = min(weekly_pay, citizen_cash)
                    after_pay = max(0.0, round(new_remaining - pay, 2))

                    if pay > 0:
                        charged = citizens.update_one({"user_id": borrower_id, "cash": {"$gte": pay}}, {"$inc": {"cash": -pay}})
                        if charged.modified_count == 0:
                            pay = 0.0
                            after_pay = new_remaining
                        else:
                            log_tx(borrower_id, "loan_interest_payment", -pay, "Weekly loan repayment + interest")
                    loan_guard = {"loan_id": loan_id, "status": "active", "last_payment": last_pay, "remaining": remaining}
                    if pay > 0 and after_pay <= 0:
                        updated = loans.update_one(loan_guard, {"$set": {"status": "paid", "remaining": 0, "last_payment": now}})
                    else:
                        updated = loans.update_one(loan_guard, {"$set": {"remaining": after_pay, "last_payment": now}})
                    if updated.modified_count == 0:
                        if pay > 0:
                            citizens.update_one({"user_id": borrower_id}, {"$inc": {"cash": pay}})
                        continue

                    if after_pay <= 0:
                        cdoc = citizens.find_one({"user_id": borrower_id}, {"_id": 0, "debt": 1, "credit_score": 1}) or {}
                        new_debt = max(0.0, safe_float(cdoc.get("debt", 0.0), 0.0) - remaining)
                        new_credit = min(850, int(cdoc.get("credit_score", 650) or 650) + 15)
                        citizens.update_one({"user_id": borrower_id}, {"$set": {"debt": new_debt, "credit_score": new_credit}})
                    elif citizen_cash < weekly_pay * 0.5:
                        cdoc = citizens.find_one({"user_id": borrower_id}, {"_id": 0, "credit_score": 1}) or {}
                        new_credit = max(300, int(cdoc.get("credit_score", 650) or 650) - 20)
                        citizens.update_one({"user_id": borrower_id}, {"$set": {"credit_score": new_credit, "debt": after_pay}})
                        log_tx(borrower_id, "loan_default", 0, "Missed loan payment — credit score penalised")
                        defaults_last_7d += 1
                    else:
                        cdoc = citizens.find_one({"user_id": borrower_id}, {"_id": 0, "credit_score": 1}) or {}
                        new_credit = min(850, int(cdoc.get("credit_score", 650) or 650) + 5)
                        citizens.update_one({"user_id": borrower_id}, {"$set": {"debt": after_pay, "credit_score": new_credit}})

            live_businesses = list(
                businesses.find(
                    {"is_bankrupt": 0},
                    {"_id": 0, "biz_id": 1, "cash": 1, "revenue": 1, "expenses": 1, "is_bankrupt": 1},
                )
            )
            for biz in live_businesses:
                biz_id = biz.get("biz_id")
                biz_cash = safe_float(biz.get("cash", 0.0), 0.0)
                rev = safe_float(biz.get("revenue", 0.0), 0.0)
                exp = safe_float(biz.get("expenses", 0.0), 0.0)
                if exp > 0:
                    net = round(rev - exp, 2)
                    new_cash = round(biz_cash + net * 0.1, 2)
                    if new_cash <= 0 and exp > rev * 1.5:
                        businesses.update_one({"biz_id": biz_id}, {"$set": {"is_bankrupt": 1}})
                    else:
                        businesses.update_one({"biz_id": biz_id}, {"$set": {"cash": max(0.0, new_cash)}})
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
        total = citizens.count_documents({})
        unemployed = citizens.count_documents({"job_id": None})
        unemp_rate = unemployed / max(total, 1)

        stressed_loans = 0
        for row in loans.find({"status": "active"}, {"_id": 0, "remaining": 1, "principal": 1}):
            remaining = safe_float(row.get("remaining", 0.0), 0.0)
            principal = safe_float(row.get("principal", 0.0), 0.0)
            if remaining > principal * 1.25:
                stressed_loans += 1
        bankrupt = businesses.count_documents({"is_bankrupt": 1})

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
        eco = get_eco_states(["inflation_rate", "base_interest_rate", "consumer_confidence"])
        inflation = safe_float(eco.get("inflation_rate") or 0.02, 0.02)
        base_interest = safe_float(eco.get("base_interest_rate") or 0.05, 0.05)
        total = citizens.count_documents({})
        unemployed = citizens.count_documents({"job_id": None})
        unemp_rate = unemployed / max(total, 1)

        score_sum = 0.0
        score_count = 0
        money_supply = 0.0
        for row in citizens.find({}, {"_id": 0, "credit_score": 1, "cash": 1, "bank": 1}):
            score_sum += safe_float(row.get("credit_score", 650), 650)
            score_count += 1
            money_supply += safe_float(row.get("cash", 0.0), 0.0) + safe_float(row.get("bank", 0.0), 0.0)
        avg_cs = safe_float((score_sum / score_count) if score_count else 650, 650)
        active_loans = loans.count_documents({"status": "active"})
        active_biz = businesses.count_documents({"is_bankrupt": 0})
        bankrupt_biz = businesses.count_documents({"is_bankrupt": 1})

        # proxies (simple, stable)
        gdp_proxy = money_supply * (0.02 + max(0.0, 0.02 - unemp_rate) * 0.05)
        velocity = clamp((0.8 + safe_float(eco.get("consumer_confidence") or 0.5, 0.5) * 0.6), 0.5, 1.6)
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
        users = [r.get("user_id") for r in citizens.find({}, {"_id": 0, "user_id": 1}) if r.get("user_id") is not None]
        daily = list(quests_daily.find({"is_active": 1}, {"_id": 0, "key": 1, "target_type": 1, "target_value": 1}))
        weekly = list(quests_weekly.find({"is_active": 1}, {"_id": 0, "key": 1, "target_type": 1, "target_value": 1}))
        for uid in users:
            for q in daily:
                key = q.get("key")
                target = float(q.get("target_value", 0.0) or 0.0)
                user_quests.update_one(
                    {"user_id": uid, "quest_type": "daily", "quest_key": key},
                    {"$setOnInsert": {"progress": 0.0, "target": target, "claimed": 0, "assigned_at": now, "resets_at": day_reset}},
                    upsert=True,
                )
            for q in weekly:
                key = q.get("key")
                target = float(q.get("target_value", 0.0) or 0.0)
                user_quests.update_one(
                    {"user_id": uid, "quest_type": "weekly", "quest_key": key},
                    {"$setOnInsert": {"progress": 0.0, "target": target, "claimed": 0, "assigned_at": now, "resets_at": week_reset}},
                    upsert=True,
                )
        user_quests.update_many(
            {"quest_type": "daily", "resets_at": {"$lte": now}},
            {"$set": {"progress": 0.0, "claimed": 0, "resets_at": day_reset}},
        )
        user_quests.update_many(
            {"quest_type": "weekly", "resets_at": {"$lte": now}},
            {"$set": {"progress": 0.0, "claimed": 0, "resets_at": week_reset}},
        )

    def _record_retention_metrics(self, now: int):
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now))
        dau = len(transactions.distinct("user_id", {"timestamp": {"$gte": now - 86400}}))
        quest_claims = user_quests.count_documents({"claimed": 1, "assigned_at": {"$gte": now - 86400}, "$expr": {"$gte": ["$progress", "$target"]}})
        active_streaks = citizens.count_documents({"daily_streak": {"$gt": 0}})
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
