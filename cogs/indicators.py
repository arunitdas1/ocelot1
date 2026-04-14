import discord
from discord.ext import commands
from db import cursor
from utils import fmt, get_eco_state, get_gov


class Indicators(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def economy(self, ctx):
        """View a full macroeconomic report."""
        phase = get_eco_state("economic_phase") or "stable"
        inflation = float(get_eco_state("inflation_rate") or 0.02)
        interest = float(get_eco_state("base_interest_rate") or 0.05)
        min_wage = float(get_eco_state("min_wage") or 50)
        reserves = get_gov("reserves")
        revenue = get_gov("revenue")

        cursor.execute("SELECT COUNT(*) FROM citizens")
        total_citizens = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM citizens WHERE job_id IS NULL")
        unemployed_count = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(cash + bank) FROM citizens")
        total_wealth_row = cursor.fetchone()
        total_wealth = total_wealth_row[0] or 0.0
        cursor.execute("SELECT SUM(cash) FROM businesses WHERE is_bankrupt = 0")
        biz_wealth_row = cursor.fetchone()
        biz_wealth = biz_wealth_row[0] or 0.0
        gdp = total_wealth + biz_wealth

        unemp_rate = (unemployed_count / max(total_citizens, 1)) * 100
        cursor.execute("SELECT COUNT(*) FROM businesses WHERE is_bankrupt = 0")
        active_biz = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM active_events WHERE ends_at > strftime('%s', 'now')")
        active_events = cursor.fetchone()[0]

        phase_emoji = {"boom": "📈", "stable": "📊", "recession": "📉", "depression": "💀"}.get(phase, "📊")
        phase_color = {"boom": discord.Color.green(), "stable": discord.Color.blue(),
                       "recession": discord.Color.orange(), "depression": discord.Color.dark_red()}.get(phase, discord.Color.blue())

        embed = discord.Embed(
            title=f"{phase_emoji} Economic Report — {phase.upper()} Phase",
            color=phase_color
        )
        embed.add_field(name="📊 GDP (Total Wealth)", value=fmt(gdp), inline=True)
        embed.add_field(name="📉 Inflation Rate", value=f"{inflation*100:.2f}%", inline=True)
        embed.add_field(name="🏦 Base Interest Rate", value=f"{interest*100:.2f}%", inline=True)
        embed.add_field(name="👥 Citizens", value=str(total_citizens), inline=True)
        embed.add_field(name="🔴 Unemployment Rate", value=f"{unemp_rate:.1f}%", inline=True)
        embed.add_field(name="🏢 Active Businesses", value=str(active_biz), inline=True)
        embed.add_field(name="💵 Minimum Wage", value=fmt(min_wage), inline=True)
        embed.add_field(name="🏛️ Gov Reserves", value=fmt(reserves), inline=True)
        embed.add_field(name="⚡ Active Events", value=str(active_events), inline=True)

        if inflation > 0.15:
            embed.add_field(name="⚠️ Warning", value="Hyperinflation risk — prices rising rapidly!", inline=False)
        elif inflation < -0.02:
            embed.add_field(name="⚠️ Warning", value="Deflation detected — economic contraction possible.", inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    async def inflation(self, ctx):
        """View current inflation data and market price trends."""
        inflation = float(get_eco_state("inflation_rate") or 0.02)

        cursor.execute(
            "SELECT name, current_price, base_price, category FROM market_goods ORDER BY category, name"
        )
        rows = cursor.fetchall()

        embed = discord.Embed(title="📉 Inflation & Price Report", color=discord.Color.orange())
        embed.add_field(name="Current Inflation Rate", value=f"**{inflation*100:.2f}%**", inline=False)

        by_cat = {}
        for name, curr, base, cat in rows:
            change_pct = ((curr - base) / base) * 100
            sign = "+" if change_pct >= 0 else ""
            by_cat.setdefault(cat, []).append(f"**{name}**: {fmt(curr)} ({sign}{change_pct:.1f}%)")

        cat_emoji = {"food": "🍞", "materials": "⚙️", "tech": "💻", "energy": "⚡", "luxury": "💎"}
        for cat, lines in by_cat.items():
            embed.add_field(name=f"{cat_emoji.get(cat, '📦')} {cat.title()}", value="\n".join(lines), inline=True)

        embed.set_footer(text="Prices fluctuate based on supply, demand, and economic events.")
        await ctx.send(embed=embed)

    @commands.command()
    async def gdp(self, ctx):
        """View the GDP and wealth distribution breakdown."""
        cursor.execute("SELECT SUM(cash + bank), MAX(cash + bank), MIN(cash + bank), AVG(cash + bank), COUNT(*) FROM citizens")
        row = cursor.fetchone()
        total_w, max_w, min_w, avg_w, count = row
        total_w = total_w or 0
        max_w = max_w or 0
        avg_w = avg_w or 0

        cursor.execute("SELECT SUM(cash), COUNT(*) FROM businesses WHERE is_bankrupt = 0")
        biz_row = cursor.fetchone()
        biz_total = biz_row[0] or 0

        total_loans = 0
        cursor.execute("SELECT SUM(remaining) FROM loans WHERE status = 'active'")
        loan_row = cursor.fetchone()
        total_loans = loan_row[0] or 0

        cursor.execute("SELECT SUM(shares * share_price) FROM businesses WHERE is_public = 1 AND is_bankrupt = 0")
        mktcap_row = cursor.fetchone()
        mkt_cap = mktcap_row[0] or 0

        embed = discord.Embed(title="📊 GDP & Wealth Distribution", color=discord.Color.blue())
        embed.add_field(name="🌍 Total GDP (Citizens + Business)", value=fmt(total_w + biz_total), inline=False)
        embed.add_field(name="👤 Citizen Wealth", value=fmt(total_w), inline=True)
        embed.add_field(name="🏢 Business Wealth", value=fmt(biz_total), inline=True)
        embed.add_field(name="📈 Stock Market Cap", value=fmt(mkt_cap), inline=True)
        embed.add_field(name="💳 Total Outstanding Debt", value=fmt(total_loans), inline=True)
        embed.add_field(name="📊 Avg Net Worth", value=fmt(avg_w), inline=True)
        embed.add_field(name="🏆 Wealthiest Citizen", value=fmt(max_w), inline=True)
        embed.add_field(name="👥 Registered Citizens", value=str(count), inline=True)
        await ctx.send(embed=embed)

    @commands.command()
    async def unemployment(self, ctx):
        """View the unemployment breakdown."""
        cursor.execute("SELECT COUNT(*) FROM citizens")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM citizens WHERE job_id IS NOT NULL")
        employed = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM citizens WHERE job_id IS NULL")
        unemployed = cursor.fetchone()[0]

        cursor.execute("SELECT job_id, COUNT(*) as cnt FROM citizens WHERE job_id IS NOT NULL GROUP BY job_id ORDER BY cnt DESC LIMIT 5")
        top_jobs = cursor.fetchall()

        unemp_rate = (unemployed / max(total, 1)) * 100
        emp_rate = (employed / max(total, 1)) * 100

        embed = discord.Embed(title="👷 Labor Market Report", color=discord.Color.green())
        embed.add_field(name="Employment Rate", value=f"**{emp_rate:.1f}%** ({employed} citizens)", inline=True)
        embed.add_field(name="Unemployment Rate", value=f"**{unemp_rate:.1f}%** ({unemployed} citizens)", inline=True)
        embed.add_field(name="Total Citizens", value=str(total), inline=True)

        if top_jobs:
            lines = [f"`{job_id.replace('_',' ').title()}` — {cnt} workers" for job_id, cnt in top_jobs]
            embed.add_field(name="🔝 Top Jobs", value="\n".join(lines), inline=False)

        embed.set_footer(text="Unemployed citizens receive welfare support from the government.")
        await ctx.send(embed=embed)

    @commands.command()
    async def richlist(self, ctx):
        """View the top 10 wealthiest citizens."""
        cursor.execute(
            "SELECT user_id, cash + bank AS net_worth FROM citizens ORDER BY net_worth DESC LIMIT 10"
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send("No citizens registered.")
            return

        embed = discord.Embed(title="🏆 Wealth Leaderboard", color=discord.Color.gold())
        medals = ["🥇", "🥈", "🥉"] + ["🔹"] * 7
        lines = []
        for i, (uid, net) in enumerate(rows):
            try:
                user = await self.bot.fetch_user(uid)
                name = user.display_name
            except Exception:
                name = f"User {uid}"
            lines.append(f"{medals[i]} **{name}** — {fmt(net)}")
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @commands.command()
    async def markettrends(self, ctx):
        """View market price trends (current vs base price)."""
        cursor.execute(
            "SELECT name, current_price, base_price, supply, demand FROM market_goods ORDER BY ABS(current_price - base_price) DESC LIMIT 10"
        )
        rows = cursor.fetchall()
        embed = discord.Embed(title="📈 Market Trends (Top Movers)", color=discord.Color.purple())
        for name, curr, base, supply, demand in rows:
            change = ((curr - base) / base) * 100
            sign = "+" if change >= 0 else ""
            trend = "📈" if change > 0 else "📉"
            embed.add_field(
                name=f"{trend} {name}",
                value=f"Price: {fmt(curr)} ({sign}{change:.1f}%) | S/D: {supply}/{demand}",
                inline=True
            )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Indicators(bot))
