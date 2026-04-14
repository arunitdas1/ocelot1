import time
import math
import discord
from discord.ext import commands
from db import active_events
from utils import get_eco_state, safe_json_loads


class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="worldevents")
    async def view_events(self, ctx):
        """View all active economic events affecting the world."""
        now = int(time.time())
        rows = list(
            active_events.find(
                {"ends_at": {"$gt": now}},
                {"name": 1, "description": 1, "effects": 1, "started_at": 1, "ends_at": 1, "_id": 0},
            )
        )

        if not rows:
            await ctx.send("📰 No active economic events right now. The economy is stable.")
            return

        embed = discord.Embed(
            title="⚡ Active Economic Events",
            description="These events are currently affecting the economy.",
            color=discord.Color.dark_orange()
        )
        for row in rows:
            name = row.get("name")
            desc = row.get("description")
            effects_json = row.get("effects")
            ends = row.get("ends_at")
            try:
                if isinstance(effects_json, dict):
                    effects = effects_json
                else:
                    effects = safe_json_loads(effects_json, {})
                if not isinstance(effects, dict):
                    effects = {}
                effect_lines = []
                for k, v in effects.items():
                    if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                        continue
                    if k == "price_multiplier":
                        effect_lines.append(f"Market prices ×{v}")
                    elif k == "salary_multiplier":
                        effect_lines.append(f"Salaries ×{v}")
                    elif k == "inflation_change":
                        sign = "+" if v >= 0 else ""
                        effect_lines.append(f"Inflation {sign}{v*100:.1f}%")
                    elif k == "interest_change":
                        sign = "+" if v >= 0 else ""
                        effect_lines.append(f"Interest rates {sign}{v*100:.1f}%")
                effect_str = "\n".join(effect_lines) if effect_lines else "General disruption"
            except Exception:
                effect_str = "Economic impact"

            duration_left = ends - now
            h_left = duration_left // 3600
            m_left = (duration_left % 3600) // 60
            embed.add_field(
                name=f"⚡ {name}",
                value=f"{desc}\n**Effects:** {effect_str}\n**Ends in:** {h_left}h {m_left}m",
                inline=False
            )

        phase = get_eco_state("economic_phase") or "stable"
        embed.set_footer(text=f"Current phase: {phase.capitalize()} | Use !economy for full report.")
        await ctx.send(embed=embed)

    @commands.command(name="eventhistory")
    async def event_history(self, ctx):
        """View recent past economic events."""
        now = int(time.time())
        rows = list(
            active_events.find(
                {"ends_at": {"$lte": now}},
                {"name": 1, "description": 1, "started_at": 1, "ends_at": 1, "_id": 0},
            ).sort("ends_at", -1).limit(5)
        )
        if not rows:
            await ctx.send("No past events recorded.")
            return

        import datetime
        embed = discord.Embed(title="📜 Recent Economic Events", color=discord.Color.greyple())
        for row in rows:
            start_str = datetime.datetime.fromtimestamp(int(row.get("started_at") or 0)).strftime("%m/%d %H:%M")
            end_str = datetime.datetime.fromtimestamp(int(row.get("ends_at") or 0)).strftime("%m/%d %H:%M")
            embed.add_field(
                name=row.get("name"),
                value=f"{row.get('description')}\n{start_str} → {end_str}",
                inline=False
            )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(EventsCog(bot))
