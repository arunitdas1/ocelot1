import time
import discord
from discord.ext import commands
from db import cursor
from utils import ensure_citizen, get_citizen, fmt


class Finance(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="budget")
    async def budget(self, ctx, tier: str = None):
        """Set or view your lifestyle tier. Usage: !budget [budget|standard|premium|luxury]"""
        ensure_citizen(ctx.author.id)
        if tier is None:
            c = get_citizen(ctx.author.id)
            current = (c.get("lifestyle_tier") or "standard").title()
            embed = discord.Embed(title="Lifestyle Budget", color=discord.Color.blurple())
            embed.description = f"Your current lifestyle tier is **{current}**.\nUse `!budget budget|standard|premium|luxury` to change."
            await ctx.send(embed=embed)
            return

        tier = tier.lower()
        valid = {"budget", "standard", "premium", "luxury"}
        if tier not in valid:
            await ctx.send("Valid tiers: `budget`, `standard`, `premium`, `luxury`.")
            return
        cursor.execute("UPDATE citizens SET lifestyle_tier = ? WHERE user_id = ?", (tier, ctx.author.id))
        cursor.connection.commit()
        await ctx.send(f"✅ Lifestyle tier set to **{tier.title()}**.")

    @commands.command(name="statement")
    async def statement(self, ctx, days: int = 30):
        """Show a spending/income statement summary. Usage: !statement [days]"""
        ensure_citizen(ctx.author.id)
        days = max(7, min(90, int(days)))
        since = int(time.time()) - days * 86400

        cursor.execute(
            "SELECT tx_type, SUM(amount) FROM transactions WHERE user_id = ? AND timestamp >= ? GROUP BY tx_type",
            (ctx.author.id, since),
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send("No transactions in that period.")
            return

        income = 0.0
        expense = 0.0
        for tx_type, total in rows:
            total = float(total or 0.0)
            if total >= 0:
                income += total
            else:
                expense += total

        embed = discord.Embed(title=f"Statement (last {days} days)", color=discord.Color.green())
        embed.add_field(name="Income", value=fmt(income), inline=True)
        embed.add_field(name="Spending", value=fmt(abs(expense)), inline=True)
        embed.add_field(name="Net", value=fmt(income + expense), inline=True)

        top = sorted(rows, key=lambda r: abs(float(r[1] or 0.0)), reverse=True)[:8]
        lines = []
        for tx_type, total in top:
            total = float(total or 0.0)
            sign = "+" if total >= 0 else "-"
            lines.append(f"`{tx_type}`: {sign}{fmt(abs(total))}")
        embed.add_field(name="Largest Categories", value="\n".join(lines), inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="planner")
    async def planner(self, ctx, goal: float, days: int = 30):
        """Debt/goal planner. Usage: !planner <goal_amount> [days]"""
        ensure_citizen(ctx.author.id)
        goal = float(goal)
        days = max(7, min(180, int(days)))
        per_day = goal / days
        embed = discord.Embed(title="Goal Planner", color=discord.Color.blurple())
        embed.description = f"Goal: **{fmt(goal)}** in **{days}** days.\nTarget: **{fmt(per_day)} / day**."
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Finance(bot))

