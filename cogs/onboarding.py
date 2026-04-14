import discord
from discord.ext import commands
from db import cursor
from utils import ensure_citizen


class Onboarding(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="start")
    async def start(self, ctx):
        """Guided start for new players."""
        embed = discord.Embed(title="Getting Started", color=discord.Color.blurple())
        embed.description = (
            "Welcome to the economy.\n\n"
            "Recommended path:\n"
            "1) `!profile` to see your starting stats\n"
            "2) `!jobs` then `!apply <job_id>`\n"
            "3) `!work` to earn income\n"
            "4) `!deposit <amount>` to use the bank\n"
            "5) `!market` then `!buy <good_id> <qty>` to trade\n\n"
            "Realism systems:\n"
            "- `!budget` (lifestyle affects daily costs)\n"
            "- `!statement 30` (track your cashflow)\n"
            "- `!plans` (insurance)\n"
            "- `!quests` / `!claimquest` (daily & weekly loop)\n"
            "- `!events` / `!eventjoin` / `!eventrewards` (limited-time rewards)\n"
            "- `!achievements` / `!claimbadge` (long-term progression)\n"
            "- `!contract create ...` (agreements)\n"
            "- `!crime` (high risk, high downside)\n"
            "- `!trust @user` (market fees and reliability)\n"
        )
        ensure_citizen(ctx.author.id)
        cursor.execute(
            "SELECT COUNT(*) FROM user_quests WHERE user_id = ? AND progress >= target AND claimed = 0",
            (ctx.author.id,),
        )
        ready_claim = int(cursor.fetchone()[0] or 0)
        if ready_claim > 0:
            embed.add_field(name="Ready to claim", value=f"{ready_claim} quest reward(s) ready via `!claimquest <key>`", inline=False)
        embed.add_field(name="Need help?", value="Use `!help` or `!help <command>`", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="nextaction", aliases=["next"])
    async def next_action(self, ctx):
        """Show what you can do right now."""
        ensure_citizen(ctx.author.id)
        cursor.execute("SELECT COUNT(*) FROM user_quests WHERE user_id = ? AND claimed = 0 AND progress >= target", (ctx.author.id,))
        claimable = int(cursor.fetchone()[0] or 0)
        cursor.execute("SELECT COUNT(*) FROM user_quests WHERE user_id = ? AND claimed = 0 AND progress < target", (ctx.author.id,))
        active = int(cursor.fetchone()[0] or 0)
        cursor.execute("SELECT last_work, last_daily FROM citizens WHERE user_id = ?", (ctx.author.id,))
        last_work, last_daily = cursor.fetchone()
        now = int(discord.utils.utcnow().timestamp())
        work_ready = max(0, 3600 - (now - int(last_work or 0)))
        daily_ready = max(0, 86400 - (now - int(last_daily or 0)))
        recommendation = "!claimquest" if claimable > 0 else ("!daily" if daily_ready == 0 else ("!work" if work_ready == 0 else "!market"))

        embed = discord.Embed(title="Next Action Dashboard", color=discord.Color.blurple())
        embed.add_field(name="Available claims", value=str(claimable), inline=True)
        embed.add_field(name="Active quests", value=str(active), inline=True)
        embed.add_field(name="Work cooldown", value=("Ready" if work_ready == 0 else f"{work_ready//60}m"), inline=True)
        embed.add_field(name="Daily cooldown", value=("Ready" if daily_ready == 0 else f"{daily_ready//3600}h"), inline=True)
        embed.add_field(name="Recommended command", value=f"`{recommendation}`", inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Onboarding(bot))

