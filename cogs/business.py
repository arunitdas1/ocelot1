import re
import time
import math
import discord
from discord.ext import commands
from db import cursor, conn
from utils import ensure_citizen, get_citizen, log_tx, fmt, add_gov_revenue
from utils import safe_float, clamp, add_reputation

BIZ_TYPES = {
    "retail":      {"name": "Retail Shop",          "cost": 5000,   "desc": "Buy and sell goods to customers"},
    "manufacturing":{"name":"Manufacturing Plant",   "cost": 15000,  "desc": "Produce raw goods and materials"},
    "tech":        {"name": "Tech Company",          "cost": 25000,  "desc": "Build software and hardware products"},
    "services":    {"name": "Services Firm",         "cost": 8000,   "desc": "Offer professional services"},
    "real_estate": {"name": "Real Estate Firm",      "cost": 30000,  "desc": "Buy, develop and sell properties"},
    "trading":     {"name": "Trading Firm",          "cost": 20000,  "desc": "Trade goods and financial instruments"},
}

EMPLOYEE_COST = 500
MAX_EMPLOYEES = 50
MAX_BIZ_TRANSFER = 1_000_000.0


def get_biz(biz_id=None, owner_id=None, name=None):
    if biz_id:
        cursor.execute("SELECT * FROM businesses WHERE biz_id = ?", (biz_id,))
    elif owner_id:
        cursor.execute("SELECT * FROM businesses WHERE owner_id = ? AND is_bankrupt = 0 LIMIT 1", (owner_id,))
    elif name:
        cursor.execute("SELECT * FROM businesses WHERE LOWER(name) = LOWER(?) AND is_bankrupt = 0", (name,))
    row = cursor.fetchone()
    if row:
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))
    return None


class Business(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def biztypes(self, ctx):
        """List all available business types."""
        embed = discord.Embed(title="🏢 Business Types", color=discord.Color.blue())
        embed.description = "Use `!startbiz <name> <type>` to found a company."
        for btype, info in BIZ_TYPES.items():
            embed.add_field(
                name=f"`{btype}` — {info['name']} (Startup: {fmt(info['cost'])})",
                value=info["desc"],
                inline=False
            )
        await ctx.send(embed=embed)

    @commands.command()
    async def startbiz(self, ctx, name: str, btype: str):
        """Found a new business. Usage: !startbiz <name> <type>"""
        btype = btype.lower()
        if btype not in BIZ_TYPES:
            await ctx.send(f"Unknown type. Use `!biztypes` to see options.")
            return
        if re.search(r'@everyone|@here|<@[!&]?\d+>', name, re.IGNORECASE):
            await ctx.send("Business name cannot contain Discord mentions.")
            return
        if len(name) > 30:
            await ctx.send("Business name max 30 characters.")
            return

        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)

        existing = get_biz(owner_id=ctx.author.id)
        if existing:
            await ctx.send(f"You already own **{existing['name']}**. You can only own one business.")
            return

        cost = BIZ_TYPES[btype]["cost"]
        if c["cash"] < cost:
            await ctx.send(f"You need {fmt(cost)} to start a {BIZ_TYPES[btype]['name']}. You have {fmt(c['cash'])}.")
            return

        cursor.execute(
            "INSERT INTO businesses(owner_id, name, type, founded_at) VALUES (?, ?, ?, ?)",
            (ctx.author.id, name, btype, int(time.time()))
        )
        cursor.execute("UPDATE citizens SET cash = cash - ? WHERE user_id = ?", (cost, ctx.author.id))
        conn.commit()
        log_tx(ctx.author.id, "biz_startup", -cost, f"Founded {name}")
        await ctx.send(
            f"🏢 **{name}** ({BIZ_TYPES[btype]['name']}) has been founded!\n"
            f"Startup cost: {fmt(cost)}. Use `!mybiz` to manage it."
        )

    @commands.command()
    async def mybiz(self, ctx):
        """View your business stats."""
        ensure_citizen(ctx.author.id)
        biz = get_biz(owner_id=ctx.author.id)
        if not biz:
            await ctx.send("You don't own a business. Use `!startbiz <name> <type>` to found one.")
            return

        btype_info = BIZ_TYPES.get(biz["type"], {"name": biz["type"].title()})
        profit = biz["revenue"] - biz["expenses"]
        profit_str = f"+{fmt(profit)}" if profit >= 0 else fmt(profit)

        embed = discord.Embed(title=f"🏢 {biz['name']}", color=discord.Color.dark_gold())
        embed.add_field(name="Type", value=btype_info["name"], inline=True)
        embed.add_field(name="Cash Reserves", value=fmt(biz["cash"]), inline=True)
        embed.add_field(name="Reputation", value=f"{biz['reputation']}/100", inline=True)
        embed.add_field(name="Revenue", value=fmt(biz["revenue"]), inline=True)
        embed.add_field(name="Expenses", value=fmt(biz["expenses"]), inline=True)
        embed.add_field(name="Net Profit", value=profit_str, inline=True)
        embed.add_field(name="Employees", value=f"{biz['employees']}/{MAX_EMPLOYEES}", inline=True)
        embed.add_field(name="Public", value="Yes" if biz["is_public"] else "No", inline=True)
        # Lightweight operating realism indicators
        payroll_daily = biz["employees"] * (EMPLOYEE_COST * 0.25)
        compliance = max(0.0, (biz["employees"] / 10) * 25.0)
        embed.add_field(name="Estimated Payroll / Day", value=fmt(payroll_daily), inline=True)
        embed.add_field(name="Compliance / Day", value=fmt(compliance), inline=True)
        if biz["is_public"]:
            embed.add_field(name="Share Price", value=fmt(biz["share_price"]), inline=True)
            embed.add_field(name="Shares Issued", value=str(biz["shares_issued"]), inline=True)
        await ctx.send(embed=embed)

    @commands.command()
    async def bizops(self, ctx):
        """Run an operating cycle for your business (payroll, compliance, small reputation drift)."""
        ensure_citizen(ctx.author.id)
        biz = get_biz(owner_id=ctx.author.id)
        if not biz:
            await ctx.send("You don't own a business.")
            return

        employees = int(biz["employees"])
        if employees <= 0:
            await ctx.send("Your business has no employees. Hire first with `!hire`.")
            return

        payroll = round(employees * (EMPLOYEE_COST * 0.25), 2)
        compliance = round(max(0.0, (employees / 10) * 25.0), 2)
        total = round(payroll + compliance, 2)

        cursor.execute(
            "UPDATE businesses SET cash = cash - ?, expenses = expenses + ? WHERE biz_id = ? AND cash >= ?",
            (total, total, biz["biz_id"], total),
        )
        if cursor.rowcount == 0:
            await ctx.send(f"Insufficient business reserves for ops. Needed: {fmt(total)}.")
            return

        # Small rep drift: paying ops on time increases rep slightly
        rep_delta = clamp(0.5 + employees * 0.01, 0.5, 2.0)
        cursor.execute("UPDATE businesses SET reputation = MIN(100, reputation + ?) WHERE biz_id = ?", (rep_delta, biz["biz_id"]))
        conn.commit()
        add_reputation("business", biz["biz_id"], rep_delta, reason="operations_cycle", source_type="bizops", source_id=str(ctx.author.id))

        await ctx.send(f"✅ Operations cycle complete. Payroll: {fmt(payroll)} | Compliance: {fmt(compliance)} | Rep +{rep_delta:.2f}")

    @commands.command()
    async def bizdeposit(self, ctx, amount: float):
        """Inject your own cash into your business. Usage: !bizdeposit <amount>"""
        if not math.isfinite(amount) or amount <= 0:
            await ctx.send("Amount must be a positive finite number.")
            return
        amount = round(amount, 2)
        if amount > MAX_BIZ_TRANSFER:
            await ctx.send(f"Maximum business deposit per transaction is {fmt(MAX_BIZ_TRANSFER)}.")
            return
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)
        biz = get_biz(owner_id=ctx.author.id)
        if not biz:
            await ctx.send("You don't own a business.")
            return
        if c["cash"] < amount:
            await ctx.send(f"Insufficient cash. You have {fmt(c['cash'])}.")
            return
        cursor.execute("UPDATE citizens SET cash = cash - ? WHERE user_id = ?", (amount, ctx.author.id))
        cursor.execute("UPDATE businesses SET cash = cash + ? WHERE biz_id = ?", (amount, biz["biz_id"]))
        conn.commit()
        log_tx(ctx.author.id, "biz_deposit", -amount, f"Injected into {biz['name']}")
        await ctx.send(f"✅ Deposited {fmt(amount)} into **{biz['name']}**.")

    @commands.command()
    async def bizwithdraw(self, ctx, amount: float):
        """Withdraw profits from your business. Usage: !bizwithdraw <amount>"""
        if not math.isfinite(amount) or amount <= 0:
            await ctx.send("Amount must be a positive finite number.")
            return
        amount = round(amount, 2)
        if amount > MAX_BIZ_TRANSFER:
            await ctx.send(f"Maximum business withdrawal per transaction is {fmt(MAX_BIZ_TRANSFER)}.")
            return
        ensure_citizen(ctx.author.id)
        biz = get_biz(owner_id=ctx.author.id)
        if not biz:
            await ctx.send("You don't own a business.")
            return
        if biz["cash"] < amount:
            await ctx.send(f"Business only has {fmt(biz['cash'])} available.")
            return

        corp_tax = round(amount * 0.20, 2)
        net = round(amount - corp_tax, 2)
        cursor.execute("UPDATE businesses SET cash = cash - ? WHERE biz_id = ?", (amount, biz["biz_id"]))
        cursor.execute("UPDATE citizens SET cash = cash + ? WHERE user_id = ?", (net, ctx.author.id))
        conn.commit()
        add_gov_revenue(corp_tax)
        log_tx(ctx.author.id, "biz_withdraw", net, f"Withdrew from {biz['name']} (20% corp tax)")
        await ctx.send(f"💰 Withdrew {fmt(amount)} from **{biz['name']}** — {fmt(corp_tax)} corporate tax deducted. Net: {fmt(net)}.")

    @commands.command()
    async def hire(self, ctx):
        """Hire an employee for your business (costs $500/employee/cycle)."""
        ensure_citizen(ctx.author.id)
        biz = get_biz(owner_id=ctx.author.id)
        if not biz:
            await ctx.send("You don't own a business.")
            return
        if biz["employees"] >= MAX_EMPLOYEES:
            await ctx.send(f"You've reached the maximum of {MAX_EMPLOYEES} employees.")
            return
        if biz["cash"] < EMPLOYEE_COST * 2:
            await ctx.send(f"You need at least {fmt(EMPLOYEE_COST * 2)} in business reserves to hire.")
            return

        new_count = biz["employees"] + 1
        revenue_boost = round(biz["revenue"] + (EMPLOYEE_COST * 0.5), 2)
        new_expenses = round(biz["expenses"] + EMPLOYEE_COST, 2)
        cursor.execute(
            "UPDATE businesses SET employees = ?, revenue = ?, expenses = ? WHERE biz_id = ?",
            (new_count, revenue_boost, new_expenses, biz["biz_id"])
        )
        conn.commit()
        await ctx.send(
            f"✅ Hired employee #{new_count} for **{biz['name']}**.\n"
            f"Cost: {fmt(EMPLOYEE_COST)}/cycle | Estimated revenue boost: +{fmt(EMPLOYEE_COST * 0.5)}/cycle"
        )

    @commands.command()
    async def fire(self, ctx):
        """Lay off an employee from your business."""
        ensure_citizen(ctx.author.id)
        biz = get_biz(owner_id=ctx.author.id)
        if not biz:
            await ctx.send("You don't own a business.")
            return
        if biz["employees"] == 0:
            await ctx.send("You have no employees to fire.")
            return

        new_count = biz["employees"] - 1
        revenue_cut = round(biz["revenue"] - (EMPLOYEE_COST * 0.5), 2)
        new_expenses = round(biz["expenses"] - EMPLOYEE_COST, 2)
        cursor.execute(
            "UPDATE businesses SET employees = ?, revenue = ?, expenses = ? WHERE biz_id = ?",
            (new_count, max(0, revenue_cut), max(0, new_expenses), biz["biz_id"])
        )
        conn.commit()
        await ctx.send(f"Employee laid off. **{biz['name']}** now has {new_count} employees.")

    @commands.command()
    async def bizlist(self, ctx):
        """View all active businesses."""
        cursor.execute(
            "SELECT biz_id, name, type, employees, reputation, cash, is_public FROM businesses "
            "WHERE is_bankrupt = 0 ORDER BY cash DESC LIMIT 15"
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send("No businesses registered yet. Use `!startbiz` to found one!")
            return

        embed = discord.Embed(title="🏢 Business Directory", color=discord.Color.dark_teal())
        for biz_id, name, btype, emp, rep, cash, is_pub in rows:
            pub_tag = " 📈" if is_pub else ""
            embed.add_field(
                name=f"{name}{pub_tag}",
                value=f"Type: {btype.title()} | Employees: {emp} | Rep: {rep}/100 | Reserves: {fmt(cash)}",
                inline=False
            )
        await ctx.send(embed=embed)

    @commands.command()
    async def closebiz(self, ctx):
        """Permanently close and liquidate your business."""
        ensure_citizen(ctx.author.id)
        biz = get_biz(owner_id=ctx.author.id)
        if not biz:
            await ctx.send("You don't own a business.")
            return

        liquidation = round(biz["cash"] * 0.5, 2)
        cursor.execute("UPDATE businesses SET is_bankrupt = 1 WHERE biz_id = ?", (biz["biz_id"],))
        cursor.execute("UPDATE citizens SET cash = cash + ? WHERE user_id = ?", (liquidation, ctx.author.id))
        conn.commit()
        log_tx(ctx.author.id, "biz_close", liquidation, f"Liquidated {biz['name']}")
        await ctx.send(
            f"**{biz['name']}** has been closed. You received {fmt(liquidation)} in liquidation proceeds (50% of reserves)."
        )


async def setup(bot):
    await bot.add_cog(Business(bot))
