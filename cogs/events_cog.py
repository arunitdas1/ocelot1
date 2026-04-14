import time
import json
import discord
from discord.ext import commands
from db import cursor
from utils import fmt, get_eco_state


class EventsCog(commands.Cog, name="Events"):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="events")
    async def view_events(self, ctx):
        """View all active economic events affecting the world."""
        now = int(time.time())
        cursor.execute(
            "SELECT name, description, effects, started_at, ends_at FROM active_events WHERE ends_at > ?",
            (now,)
        )
        rows = cursor.fetchall()

        if not rows:
            await ctx.send("📰 No active economic events right now. The economy is stable.")
            return

        embed = discord.Embed(
            title="⚡ Active Economic Events",
            description="These events are currently affecting the economy.",
            color=discord.Color.dark_orange()
        )
        for name, desc, effects_json, started, ends in rows:
            try:
                effects = json.loads(effects_json)
                effect_lines = []
                for k, v in effects.items():
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
        cursor.execute(
            "SELECT name, description, started_at, ends_at FROM active_events WHERE ends_at <= ? ORDER BY ends_at DESC LIMIT 5",
            (now,)
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send("No past events recorded.")
            return

        import datetime
        embed = discord.Embed(title="📜 Recent Economic Events", color=discord.Color.greyple())
        for name, desc, started, ended in rows:
            start_str = datetime.datetime.fromtimestamp(started).strftime("%m/%d %H:%M")
            end_str = datetime.datetime.fromtimestamp(ended).strftime("%m/%d %H:%M")
            embed.add_field(
                name=name,
                value=f"{desc}\n{start_str} → {end_str}",
                inline=False
            )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(EventsCog(bot))
