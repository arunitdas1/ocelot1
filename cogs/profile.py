import math
import time
import discord
from discord.ext import commands
from db import cursor, conn
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

        cursor.execute(
            "UPDATE citizens SET cash = cash - ? WHERE user_id = ? AND cash >= ?",
            (amount, ctx.author.id, amount)
        )
        if cursor.rowcount == 0:
            sender = get_citizen(ctx.author.id)
            await ctx.send(f"Insufficient funds! You only have {fmt(sender['cash'])} in your wallet.")
            return

        cursor.execute("UPDATE citizens SET cash = cash + ? WHERE user_id = ?", (amount, member.id))
        conn.commit()
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
        cursor.execute("UPDATE citizens SET cash = cash + ?, last_daily = ? WHERE user_id = ?",
                       (amount, now, ctx.author.id))
        conn.commit()
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
        cursor.execute(
            "SELECT user_id, cash + bank AS net FROM citizens ORDER BY net DESC LIMIT 10"
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send("No citizens registered yet.")
            return

        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
        lines = []
        for i, (uid, net) in enumerate(rows):
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

    @commands.command()
    async def history(self, ctx, limit: int = 10):
        """View your recent transaction history."""
        ensure_citizen(ctx.author.id)
        limit = min(limit, 25)
        cursor.execute(
            "SELECT tx_type, amount, description, timestamp FROM transactions "
            "WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (ctx.author.id, limit)
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send("No transactions found.")
            return

        import datetime
        lines = []
        for tx_type, amount, desc, ts in rows:
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
