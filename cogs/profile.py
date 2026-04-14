import math
import time
import discord
from discord.ext import commands
from db import citizens, season_meta, season_stats, transactions, write_txn
from utils import (
    ensure_citizen, get_citizen, log_tx, fmt,
    EDUCATION_LEVELS, calculate_income_tax, housing_expense, get_eco_state
)
from cogs.ui_components import PaginatorView


class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def profile(self, ctx, member: discord.Member = None):
        """View your economic profile or another user's."""
        target = member or ctx.author
        ensure_citizen(target.id)
        c = get_citizen(target.id)

        net_worth = c["cash"] + c["bank"] - c["debt"]
        edu_display = c["education"].replace("none", "No formal education").replace("highschool", "High School").replace("college", "College").replace("masters", "Master's").replace("phd", "PhD")
        housing_display = c["housing"].capitalize()
        job_display = c["job_id"].replace("_", " ").title() if c["job_id"] else "Unemployed"

        phase = get_eco_state("economic_phase") or "stable"
        phase_emoji = {"boom": "📈", "stable": "📊", "recession": "📉", "depression": "💀"}.get(phase, "📊")

        embed = discord.Embed(
            title=f"{phase_emoji} {target.display_name}'s Economic Profile",
            color=discord.Color.gold()
        )
        embed.add_field(name="💵 Cash (Wallet)", value=fmt(c["cash"]), inline=True)
        embed.add_field(name="🏦 Bank Balance", value=fmt(c["bank"]), inline=True)
        embed.add_field(name="📊 Net Worth", value=fmt(net_worth), inline=True)
        embed.add_field(name="💳 Credit Score", value=f"{c['credit_score']}", inline=True)
        embed.add_field(name="🎯 Skill Level", value=f"Level {c['skill_level']} / 5", inline=True)
        embed.add_field(name="🎓 Education", value=edu_display, inline=True)
        embed.add_field(name="💼 Job", value=job_display, inline=True)
        embed.add_field(name="🏠 Housing", value=housing_display, inline=True)
        embed.add_field(name="😊 Happiness", value=f"{c['happiness']:.0f}%", inline=True)
        if c["debt"] > 0:
            embed.add_field(name="🔴 Total Debt", value=fmt(c["debt"]), inline=True)
        embed.set_footer(text="Use !help to see all economy commands")
        await ctx.send(embed=embed)

    @commands.command()
    async def balance(self, ctx, member: discord.Member = None):
        """Check your wallet and bank balance quickly."""
        target = member or ctx.author
        ensure_citizen(target.id)
        c = get_citizen(target.id)
        await ctx.send(
            f"**{target.display_name}'s Balance**\n"
            f"💵 Wallet: **{fmt(c['cash'])}**\n"
            f"🏦 Bank: **{fmt(c['bank'])}**\n"
            f"📊 Net Worth: **{fmt(c['cash'] + c['bank'] - c['debt'])}**"
        )

    @commands.command()
    async def pay(self, ctx, member: discord.Member, amount: float):
        """Send cash to another user. Usage: !pay @user <amount>"""
        if member == ctx.author:
            await ctx.send("You can't pay yourself!")
            return
        if member.bot:
            await ctx.send("You can't pay a bot!")
            return
        if not math.isfinite(amount) or amount <= 0:
            await ctx.send("Amount must be a positive finite number!")
            return
        amount = round(amount, 2)

        ensure_citizen(ctx.author.id)
        ensure_citizen(member.id)

        with write_txn():
            debit = citizens.find_one_and_update(
                {"user_id": ctx.author.id, "cash": {"$gte": amount}},
                {"$inc": {"cash": -amount}},
            )
            if not debit:
                sender = get_citizen(ctx.author.id)
                await ctx.send(f"Insufficient funds! You only have {fmt(sender['cash'])} in your wallet.")
                return
            credit = citizens.update_one({"user_id": member.id}, {"$inc": {"cash": amount}})
            if credit.modified_count == 0:
                citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": amount}})
                await ctx.send("Payment failed due to concurrent account state change. No funds were moved.")
                return
        log_tx(ctx.author.id, "payment_sent", -amount, f"Paid {member.display_name}")
        log_tx(member.id, "payment_received", amount, f"Received from {ctx.author.display_name}")

        await ctx.send(f"✅ You sent **{fmt(amount)}** to {member.mention}.")

    @commands.command()
    async def daily(self, ctx):
        """Claim your daily basic income."""
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)
        now = int(time.time())
        cooldown = 86400

        if now - c["last_daily"] < cooldown:
            remaining = cooldown - (now - c["last_daily"])
            h = remaining // 3600
            m = (remaining % 3600) // 60
            await ctx.send(f"⏳ Daily reward available in **{h}h {m}m**.")
            return

        import random
        base = 200.0
        bonus = random.uniform(0, 100)
        if c["housing"] == "homeless":
            amount = base * 0.5 + bonus
        elif c["education"] != "none":
            edu_idx = EDUCATION_LEVELS.index(c["education"])
            amount = base + bonus + (edu_idx * 20)
        else:
            amount = base + bonus

        amount = round(amount, 2)
        with write_txn():
            claimed = citizens.find_one_and_update(
                {"user_id": ctx.author.id, "last_daily": c["last_daily"]},
                {"$inc": {"cash": amount}, "$set": {"last_daily": now}},
            )
            if not claimed:
                latest = get_citizen(ctx.author.id)
                if now - latest["last_daily"] < cooldown:
                    remaining = cooldown - (now - latest["last_daily"])
                    h = remaining // 3600
                    m = (remaining % 3600) // 60
                    await ctx.send(f"⏳ Daily reward available in **{h}h {m}m**.")
                    return
                await ctx.send("Daily claim state changed. Please try again.")
                return
        log_tx(ctx.author.id, "daily_income", amount, "Daily basic income")
        await ctx.send(f"✅ You claimed your daily income of **{fmt(amount)}**! Come back in 24 hours.")

    @commands.command()
    async def expenses(self, ctx):
        """View your recurring monthly expenses."""
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)
        rent = housing_expense(c["housing"])
        food_cost = 50.0
        total = rent + food_cost
        embed = discord.Embed(title="📋 Monthly Expenses", color=discord.Color.orange())
        embed.add_field(name="🏠 Housing", value=fmt(rent), inline=True)
        embed.add_field(name="🍞 Food & Essentials", value=fmt(food_cost), inline=True)
        embed.add_field(name="📊 Total / Month", value=fmt(total), inline=False)
        embed.set_footer(text="Expenses are automatically deducted every 24 hours.")
        await ctx.send(embed=embed)

    @commands.command()
    async def leaderboard(self, ctx):
        """View the wealth leaderboard."""
        rows = list(
            citizens.aggregate(
                [
                    {"$project": {"_id": 0, "user_id": 1, "net": {"$add": ["$cash", "$bank"]}}},
                    {"$sort": {"net": -1}},
                    {"$limit": 10},
                ]
            )
        )
        if not rows:
            await ctx.send("No citizens registered yet.")
            return

        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
        lines = []
        for i, row in enumerate(rows):
            uid = row["user_id"]
            net = row["net"]
            # Prefer guild/member cache before API call for better latency.
            member = ctx.guild.get_member(uid) if ctx.guild else None
            if member:
                name = member.display_name
            else:
                user = self.bot.get_user(uid)
                if user:
                    name = user.display_name
                else:
                    try:
                        user = await self.bot.fetch_user(uid)
                        name = user.display_name
                    except Exception:
                        name = f"User {uid}"
            lines.append(f"{medals[i]} **{name}** — {fmt(net)}")

        pages = []
        chunk_size = 5
        for idx in range(0, len(lines), chunk_size):
            embed = discord.Embed(title="🏆 Wealth Leaderboard", color=discord.Color.gold())
            embed.description = "\n".join(lines[idx:idx + chunk_size])
            pages.append(embed)
        if len(pages) == 1:
            await ctx.send(embed=pages[0])
            return
        view = PaginatorView(ctx.author.id, pages)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    @commands.command(name="seasonboard")
    async def seasonboard(self, ctx):
        """View current season standings."""
        season = season_meta.find_one({"status": "active"}, {"_id": 0, "season_id": 1, "name": 1}, sort=[("season_id", -1)])
        if not season:
            await ctx.send("No active season.")
            return
        season_id = season["season_id"]
        season_name = season["name"]
        rows = list(
            season_stats.aggregate(
                [
                    {"$match": {"season_id": season_id}},
                    {
                        "$project": {
                            "_id": 0,
                            "user_id": 1,
                            "net_worth": 1,
                            "work_shifts": 1,
                            "quests_completed": 1,
                            "score": {"$add": ["$net_worth", {"$multiply": ["$work_shifts", 50]}, {"$multiply": ["$quests_completed", 100]}]},
                        }
                    },
                    {"$sort": {"score": -1}},
                    {"$limit": 10},
                ]
            )
        )
        if not rows:
            await ctx.send("No season activity yet.")
            return
        embed = discord.Embed(title=f"🏁 {season_name} Standings", color=discord.Color.gold())
        for i, row in enumerate(rows, start=1):
            uid = row["user_id"]
            net = row.get("net_worth", 0)
            shifts = row.get("work_shifts", 0)
            quests = row.get("quests_completed", 0)
            embed.add_field(
                name=f"#{i} <@{uid}>",
                value=f"Net: {fmt(net)} | Shifts: {int(shifts)} | Quests: {int(quests)}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command()
    async def history(self, ctx, limit: int = 10):
        """View your recent transaction history."""
        ensure_citizen(ctx.author.id)
        limit = min(limit, 25)
        rows = list(
            transactions.find(
                {"user_id": ctx.author.id},
                {"_id": 0, "tx_type": 1, "amount": 1, "description": 1, "timestamp": 1},
            ).sort("timestamp", -1).limit(limit)
        )
        if not rows:
            await ctx.send("No transactions found.")
            return

        import datetime
        lines = []
        for row in rows:
            amount = row.get("amount", 0.0)
            desc = row.get("description", "")
            ts = row.get("timestamp", 0)
            sign = "+" if amount >= 0 else ""
            dt = datetime.datetime.fromtimestamp(ts).strftime("%m/%d %H:%M")
            lines.append(f"`{dt}` {sign}{fmt(amount)} — {desc}")
        pages = []
        chunk_size = 8
        for idx in range(0, len(lines), chunk_size):
            embed = discord.Embed(title="📜 Recent Transactions", color=discord.Color.blue())
            embed.description = "\n".join(lines[idx:idx + chunk_size])
            embed.set_footer(text="Tip: use !statement 30 for category summary.")
            pages.append(embed)
        if len(pages) == 1:
            await ctx.send(embed=pages[0])
            return
        view = PaginatorView(ctx.author.id, pages)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg


async def setup(bot):
    await bot.add_cog(Profile(bot))
