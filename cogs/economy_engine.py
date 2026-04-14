import time
import random
import json
import discord
from discord.ext import commands, tasks
from db import cursor, conn
from utils import (
    get_eco_state, set_eco_state, get_gov, set_gov,
    get_all_citizens, log_tx, fmt,
    housing_expense, add_gov_revenue, deduct_gov_expense
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
            inflation = float(get_eco_state("inflation_rate") or 0.02)
            phase = get_eco_state("economic_phase") or "stable"

            phase_demand_mult = {"boom": 1.15, "stable": 1.0, "recession": 0.85, "depression": 0.70}.get(phase, 1.0)

            cursor.execute("SELECT good_id, current_price, base_price, supply, demand, volatility FROM market_goods")
            goods = cursor.fetchall()

            for good_id, curr_price, base_price, supply, demand, vol in goods:
                demand_adj = max(1, int(demand * phase_demand_mult))
                supply_adj = max(1, supply + random.randint(-50, 50))
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

            cursor.execute("SELECT biz_id, shares_issued, share_price FROM businesses WHERE is_public = 1 AND is_bankrupt = 0")
            stocks = cursor.fetchall()
            for biz_id, shares, price in stocks:
                change = random.uniform(-0.03, 0.035) * (1.2 if phase == "boom" else 0.8 if phase == "recession" else 1.0)
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
            now = int(time.time())
            cursor.execute("SELECT COUNT(*) FROM active_events WHERE ends_at > ?", (now,))
            active_count = cursor.fetchone()[0]

            if active_count >= 3:
                return

            if random.random() > 0.35:
                return

            event = random.choice(RANDOM_EVENTS)
            effects = event["effects"]
            duration = event["duration"]

            cursor.execute(
                "INSERT INTO active_events(name, description, effects, started_at, ends_at) VALUES (?, ?, ?, ?, ?)",
                (event["name"], event["description"], json.dumps(effects), now, now + duration)
            )
            conn.commit()

            if "inflation_change" in effects:
                current = float(get_eco_state("inflation_rate") or 0.02)
                new_inf = max(-0.05, min(0.5, current + effects["inflation_change"]))
                set_eco_state("inflation_rate", new_inf)

            if "interest_change" in effects:
                current = float(get_eco_state("base_interest_rate") or 0.05)
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
        """Hourly: accrue loan interest, deduct living expenses, pay welfare."""
        try:
            now = int(time.time())
            all_citizens = get_all_citizens()

            for uid in all_citizens:
                cursor.execute("SELECT cash, bank, debt, job_id, housing, last_expense FROM citizens WHERE user_id = ?", (uid,))
                row = cursor.fetchone()
                if not row:
                    continue
                cash, bank, debt, job_id, housing, last_expense = row

                if now - (last_expense or 0) >= 86400:
                    food_cost = 50.0
                    rent_cost = housing_expense(housing)
                    total_exp = food_cost + rent_cost

                    if cash >= total_exp:
                        cursor.execute("UPDATE citizens SET cash = cash - ?, last_expense = ? WHERE user_id = ?",
                                       (total_exp, now, uid))
                        log_tx(uid, "living_expenses", -total_exp, "Daily living expenses (food + housing)")
                    elif (cash + bank) >= total_exp:
                        from_bank = total_exp - cash
                        cursor.execute(
                            "UPDATE citizens SET cash = 0, bank = bank - ?, last_expense = ? WHERE user_id = ?",
                            (from_bank, now, uid)
                        )
                        log_tx(uid, "living_expenses", -total_exp, "Daily expenses (bank used)")
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
            print(f"[EconomyEngine] Hourly cycle complete.")

        except Exception as e:
            print(f"[EconomyEngine] Hourly process error: {e}")

    def _update_phase(self):
        """Auto-detect economic phase based on indicators."""
        inflation = float(get_eco_state("inflation_rate") or 0.02)
        cursor.execute("SELECT COUNT(*) FROM citizens")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM citizens WHERE job_id IS NULL")
        unemployed = cursor.fetchone()[0]
        unemp_rate = unemployed / max(total, 1)

        if inflation > 0.10 and unemp_rate < 0.15:
            phase = "boom"
        elif inflation < 0.0 or unemp_rate > 0.40:
            phase = "depression"
        elif unemp_rate > 0.25 or inflation < 0.005:
            phase = "recession"
        else:
            phase = "stable"

        set_eco_state("economic_phase", phase)

    @simulate_market.before_loop
    @trigger_events.before_loop
    @process_economy.before_loop
    async def before_tasks(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(EconomyEngine(bot))
