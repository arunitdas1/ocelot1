import discord
from discord.ext import commands
from db import active_events, businesses, citizens, loans, market_goods
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

        total_citizens = citizens.count_documents({})
        unemployed_count = citizens.count_documents({"job_id": None})
        total_wealth = 0.0
        for c in citizens.find({}, {"cash": 1, "bank": 1, "_id": 0}):
            total_wealth += float(c.get("cash") or 0) + float(c.get("bank") or 0)
        biz_wealth = 0.0
        for b in businesses.find({"is_bankrupt": 0}, {"cash": 1, "_id": 0}):
            biz_wealth += float(b.get("cash") or 0)
        gdp = total_wealth + biz_wealth

        unemp_rate = (unemployed_count / max(total_citizens, 1)) * 100
        active_biz = businesses.count_documents({"is_bankrupt": 0})
        active_events_count = active_events.count_documents({"ends_at": {"$gt": int(discord.utils.utcnow().timestamp())}})

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
        embed.add_field(name="⚡ Active Events", value=str(active_events_count), inline=True)

        if inflation > 0.15:
            embed.add_field(name="⚠️ Warning", value="Hyperinflation risk — prices rising rapidly!", inline=False)
        elif inflation < -0.02:
            embed.add_field(name="⚠️ Warning", value="Deflation detected — economic contraction possible.", inline=False)

        await ctx.send(embed=embed)

    @commands.command()
    async def inflation(self, ctx):
        """View current inflation data and market price trends."""
        inflation = float(get_eco_state("inflation_rate") or 0.02)

        rows = list(
            market_goods.find({}, {"name": 1, "current_price": 1, "base_price": 1, "category": 1, "_id": 0}).sort(
                [("category", 1), ("name", 1)]
            )
        )

        embed = discord.Embed(title="📉 Inflation & Price Report", color=discord.Color.orange())
        embed.add_field(name="Current Inflation Rate", value=f"**{inflation*100:.2f}%**", inline=False)

        by_cat = {}
        for row in rows:
            name = row.get("name")
            curr = float(row.get("current_price") or 0)
            base = float(row.get("base_price") or 0)
            cat = row.get("category")
            change_pct = ((curr - base) / base) * 100 if abs(base) > 1e-9 else 0.0
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
        wealth_values = [float(c.get("cash") or 0) + float(c.get("bank") or 0) for c in citizens.find({}, {"cash": 1, "bank": 1, "_id": 0})]
        count = len(wealth_values)
        total_w = sum(wealth_values) if wealth_values else 0
        max_w = max(wealth_values) if wealth_values else 0
        min_w = min(wealth_values) if wealth_values else 0
        avg_w = (total_w / count) if count else 0

        biz_total = sum(float(b.get("cash") or 0) for b in businesses.find({"is_bankrupt": 0}, {"cash": 1, "_id": 0}))
        total_loans = sum(float(l.get("remaining") or 0) for l in loans.find({"status": "active"}, {"remaining": 1, "_id": 0}))
        mkt_cap = sum(
            float(b.get("shares") or 0) * float(b.get("share_price") or 0)
            for b in businesses.find({"is_public": 1, "is_bankrupt": 0}, {"shares": 1, "share_price": 1, "_id": 0})
        )

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
        total = citizens.count_documents({})
        employed = citizens.count_documents({"job_id": {"$ne": None}})
        unemployed = citizens.count_documents({"job_id": None})

        job_counts = {}
        for c in citizens.find({"job_id": {"$ne": None}}, {"job_id": 1, "_id": 0}):
            jid = c.get("job_id")
            job_counts[jid] = job_counts.get(jid, 0) + 1
        top_jobs = sorted(job_counts.items(), key=lambda item: item[1], reverse=True)[:5]

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
        rows = [
            (c.get("user_id"), float(c.get("cash") or 0) + float(c.get("bank") or 0))
            for c in citizens.find({}, {"user_id": 1, "cash": 1, "bank": 1, "_id": 0})
        ]
        rows.sort(key=lambda r: r[1], reverse=True)
        rows = rows[:10]
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
        rows = [
            (
                g.get("name"),
                float(g.get("current_price") or 0),
                float(g.get("base_price") or 0),
                g.get("supply"),
                g.get("demand"),
            )
            for g in market_goods.find({}, {"name": 1, "current_price": 1, "base_price": 1, "supply": 1, "demand": 1, "_id": 0})
        ]
        rows.sort(key=lambda r: abs(r[1] - r[2]), reverse=True)
        rows = rows[:10]
        embed = discord.Embed(title="📈 Market Trends (Top Movers)", color=discord.Color.purple())
        for name, curr, base, supply, demand in rows:
            change = ((curr - base) / base) * 100 if abs(base) > 1e-9 else 0.0
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
