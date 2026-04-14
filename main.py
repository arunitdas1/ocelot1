import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
from keep_alive import keep_alive

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

keep_alive()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True


class OcelotContext(commands.Context):
    async def send(self, content=None, **kwargs):
        if (
            isinstance(content, str)
            and content.strip()
            and "embed" not in kwargs
            and "embeds" not in kwargs
            and "file" not in kwargs
            and "files" not in kwargs
        ):
            clean = content.strip()
            title = None
            color = discord.Color.blurple()

            if clean.startswith("✅"):
                title = "Success"
                color = discord.Color.green()
            elif clean.startswith("⏳"):
                title = "Cooldown"
                color = discord.Color.orange()
            elif clean.startswith("🎉"):
                title = "Milestone"
                color = discord.Color.gold()
            elif clean.startswith("❌"):
                title = "Error"
                color = discord.Color.red()

            embed = discord.Embed(description=clean, color=color)
            if title:
                embed.title = title
            kwargs["embed"] = embed
            content = None

        return await super().send(content=content, **kwargs)


bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    context_class=OcelotContext,
)

COGS = [
    "cogs.profile",
    "cogs.jobs",
    "cogs.banking",
    "cogs.market",
    "cogs.business",
    "cogs.stocks",
    "cogs.government",
    "cogs.indicators",
    "cogs.events_cog",
    "cogs.economy_engine",
    "cogs.help",
]


@bot.event
async def on_ready():
    print(f"{bot.user} is online and the economy is running!")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            title="Missing argument",
            description=f"Please provide `{error.param.name}`.\nUse `!help {ctx.command}` for usage.",
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)
    elif isinstance(error, commands.BadArgument):
        embed = discord.Embed(
            title="Invalid argument",
            description="One or more arguments have the wrong type. Use `!help` for correct usage.",
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)
    elif isinstance(error, commands.CommandNotFound):
        pass
    elif isinstance(error, commands.MissingPermissions):
        embed = discord.Embed(
            title="Permission denied",
            description="You don't have permission to use this command.",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(
            title="Unexpected error",
            description=f"An error occurred: `{error}`",
            color=discord.Color.red(),
        )
        await ctx.send(embed=embed)


async def load_cogs():
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            print(f"Loaded {cog}")
        except Exception as e:
            print(f"Failed to load {cog}: {e}")


@bot.event
async def setup_hook():
    await load_cogs()


bot.run(DISCORD_TOKEN)
