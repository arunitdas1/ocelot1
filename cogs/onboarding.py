import discord
from discord.ext import commands


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
            "- `!contract create ...` (agreements)\n"
            "- `!crime` (high risk, high downside)\n"
            "- `!trust @user` (market fees and reliability)\n"
        )
        embed.add_field(name="Need help?", value="Use `!help` or `!help <command>`", inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Onboarding(bot))

