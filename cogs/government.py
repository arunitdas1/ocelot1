import discord
from discord.ext import commands
from db import cursor, conn
from utils import (
    ensure_citizen, get_citizen, log_tx, fmt,
    get_gov, set_gov, add_gov_revenue, deduct_gov_expense,
    get_eco_state, set_eco_state, get_all_citizens
)


class Government(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _is_admin(self, ctx):
        return ctx.author.guild_permissions.administrator if ctx.guild else False

    @commands.command()
    async def govbudget(self, ctx):
        """View the government's budget and reserves."""
        revenue = get_gov("revenue")
        expenses = get_gov("expenses")
        reserves = get_gov("reserves")
        min_wage = float(get_eco_state("min_wage") or 50)
        phase = get_eco_state("economic_phase") or "stable"
        base_rate = float(get_eco_state("base_interest_rate") or 0.05)
        inflation = float(get_eco_state("inflation_rate") or 0.02)

        cursor.execute("SELECT COUNT(*) FROM citizens")
        citizens = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM citizens WHERE job_id IS NULL")
        unemployed = cursor.fetchone()[0]

        embed = discord.Embed(title="🏛️ Government Budget Report", color=discord.Color.red())
        embed.add_field(name="💰 Total Revenue (all-time)", value=fmt(revenue), inline=True)
        embed.add_field(name="📤 Total Expenses (all-time)", value=fmt(expenses), inline=True)
        embed.add_field(name="🏦 Reserves", value=fmt(reserves), inline=True)
        embed.add_field(name="📊 Economic Phase", value=phase.capitalize(), inline=True)
        embed.add_field(name="📉 Inflation Rate", value=f"{inflation*100:.2f}%", inline=True)
        embed.add_field(name="🏦 Base Interest Rate", value=f"{base_rate*100:.2f}%", inline=True)
        embed.add_field(name="💵 Minimum Wage", value=fmt(min_wage), inline=True)
        embed.add_field(name="👥 Citizens", value=str(citizens), inline=True)
        embed.add_field(name="🔴 Unemployed", value=str(unemployed), inline=True)
        await ctx.send(embed=embed)

    @commands.command()
    async def stimulus(self, ctx, amount: float):
        """[Admin] Issue a stimulus payment to all citizens."""
        if not self._is_admin(ctx):
            await ctx.send("You need administrator permissions to use this command.")
            return
        if amount <= 0:
            await ctx.send("Amount must be positive.")
            return

        reserves = get_gov("reserves")
        all_citizens = get_all_citizens()
        total_cost = amount * len(all_citizens)

        if reserves < total_cost:
            await ctx.send(f"Insufficient government reserves ({fmt(reserves)}) to pay {fmt(total_cost)} total.")
            return

        for uid in all_citizens:
            cursor.execute("UPDATE citizens SET cash = cash + ? WHERE user_id = ?", (amount, uid))
            log_tx(uid, "stimulus", amount, "Government stimulus payment")

        conn.commit()
        deduct_gov_expense(total_cost)
        await ctx.send(
            f"✅ Stimulus of **{fmt(amount)}** sent to all **{len(all_citizens)}** citizens.\n"
            f"Total cost: {fmt(total_cost)} | Remaining reserves: {fmt(reserves - total_cost)}"
        )

    @commands.command()
    async def setminwage(self, ctx, amount: float):
        """[Admin] Set the minimum wage per shift."""
        if not self._is_admin(ctx):
            await ctx.send("Administrator permissions required.")
            return
        if amount < 0:
            await ctx.send("Minimum wage cannot be negative.")
            return
        set_eco_state("min_wage", amount)
        await ctx.send(f"✅ Minimum wage set to **{fmt(amount)}** per shift.")

    @commands.command()
    async def setrate(self, ctx, rate_type: str, value: float):
        """[Admin] Set economic rates. Types: interest, inflation. Usage: !setrate <type> <value_percent>"""
        if not self._is_admin(ctx):
            await ctx.send("Administrator permissions required.")
            return

        rate_type = rate_type.lower()
        actual = value / 100

        if rate_type == "interest":
            if not 0 <= actual <= 0.5:
                await ctx.send("Interest rate must be between 0% and 50%.")
                return
            set_eco_state("base_interest_rate", actual)
            await ctx.send(f"✅ Base interest rate set to **{value:.2f}%**.")
        elif rate_type == "inflation":
            if not -0.1 <= actual <= 0.5:
                await ctx.send("Inflation rate must be between -10% and 50%.")
                return
            set_eco_state("inflation_rate", actual)
            await ctx.send(f"✅ Inflation rate set to **{value:.2f}%**.")
        else:
            await ctx.send("Unknown rate type. Use `interest` or `inflation`.")

    @commands.command()
    async def setphase(self, ctx, phase: str):
        """[Admin] Manually set economic phase: boom, stable, recession, depression."""
        if not self._is_admin(ctx):
            await ctx.send("Administrator permissions required.")
            return

        phase = phase.lower()
        valid = ["boom", "stable", "recession", "depression"]
        if phase not in valid:
            await ctx.send(f"Valid phases: {', '.join(f'`{p}`' for p in valid)}")
            return

        set_eco_state("economic_phase", phase)

        phase_effects = {
            "boom":       (0.03, 0.04),
            "stable":     (0.02, 0.05),
            "recession":  (0.01, 0.08),
            "depression": (-0.01, 0.12),
        }
        inflation, interest = phase_effects[phase]
        set_eco_state("inflation_rate", inflation)
        set_eco_state("base_interest_rate", interest)

        await ctx.send(
            f"✅ Economic phase set to **{phase.capitalize()}**.\n"
            f"Inflation: {inflation*100:.1f}% | Interest: {interest*100:.1f}%"
        )

    @commands.command()
    async def taxrate(self, ctx):
        """View the current tax brackets."""
        embed = discord.Embed(title="📊 Tax Brackets", color=discord.Color.dark_red())
        embed.add_field(name="Income Tax (per shift)", value=(
            "**0 – $200** → 5%\n"
            "**$200 – $500** → 12%\n"
            "**$500 – $1,000** → 22%\n"
            "**$1,000+** → 30%"
        ), inline=False)
        embed.add_field(name="Sales Tax", value="8% on all market purchases", inline=True)
        embed.add_field(name="Corporate Tax", value="20% on business withdrawals", inline=True)
        embed.add_field(name="Capital Gains Tax", value="15% on stock profits", inline=True)
        embed.set_footer(text="Government revenue funds welfare and stimulus programs.")
        await ctx.send(embed=embed)

    @commands.command()
    async def welfare(self, ctx):
        """View welfare payment status for unemployed citizens."""
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)
        if c["job_id"] is not None:
            await ctx.send("You're currently employed and do not qualify for unemployment welfare.")
            return

        reserves = get_gov("reserves")
        daily_welfare = 100.0
        if reserves < daily_welfare:
            await ctx.send("The government is out of funds to pay welfare right now.")
            return

        await ctx.send(
            f"🏛️ As an unemployed citizen, you receive **{fmt(daily_welfare)}** daily from the government "
            f"(distributed automatically by the economy engine). Use `!jobs` to find employment."
        )

    @commands.command()
    async def printmoney(self, ctx, amount: float):
        """[Admin] Inject money into the government reserves (simulates quantitative easing)."""
        if not self._is_admin(ctx):
            await ctx.send("Administrator permissions required.")
            return
        if amount <= 0 or amount > 1000000:
            await ctx.send("Amount must be between $1 and $1,000,000.")
            return

        reserves = get_gov("reserves")
        set_gov("reserves", reserves + amount)
        current_inflation = float(get_eco_state("inflation_rate") or 0.02)
        new_inflation = min(0.5, current_inflation + (amount / 10000000))
        set_eco_state("inflation_rate", new_inflation)
        await ctx.send(
            f"🖨️ Printed **{fmt(amount)}**. New reserves: {fmt(reserves + amount)}.\n"
            f"⚠️ Inflation nudged up to {new_inflation*100:.2f}% (money supply increase)."
        )


async def setup(bot):
    await bot.add_cog(Government(bot))
