import os
import time
import asyncio
import difflib
import discord
from discord.ext import commands
from dotenv import load_dotenv
from keep_alive import keep_alive
from utils import get_eco_state

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

keep_alive()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

EXEMPT_COMMANDS = {
    "help", "market", "listings", "stocks", "portfolio", "profile",
    "inventory", "govbudget", "taxrate", "credit", "bankinfo",
}
COMMAND_COOLDOWNS = {
    "default": 1.2,
    "buy": 2.0,
    "sell": 2.0,
    "buyp2p": 2.0,
    "invest": 2.0,
    "divest": 2.0,
    "deposit": 1.5,
    "withdraw": 1.5,
    "pay": 1.5,
    "loan": 2.5,
    "repay": 2.0,
    "startbiz": 2.5,
    "bizdeposit": 1.5,
    "bizwithdraw": 1.5,
}
_user_locks: dict[int, asyncio.Lock] = {}
_active_ctx_locks: dict[int, asyncio.Lock] = {}
_last_command_at: dict[tuple[int, str], float] = {}
OWNER_FALLBACK_ID = int(os.getenv("OWNER_ID", "0") or 0)
TRANSACTION_COMMANDS = {
    "pay", "daily", "deposit", "withdraw", "loan", "repay",
    "buy", "sell", "buyp2p", "delist", "invest", "divest",
    "startbiz", "bizdeposit", "bizwithdraw", "hire", "fire", "closebiz",
    "educate", "train", "work",
}


class OcelotContext(commands.Context):
    def _style_embed(self, embed: discord.Embed) -> discord.Embed:
        if embed.color is None or embed.color.value == 0:
            embed.color = discord.Color.blurple()

        if embed.timestamp is None:
            embed.timestamp = discord.utils.utcnow()

        if not embed.footer or not embed.footer.text:
            embed.set_footer(text=f"Ocelot Economy • Requested by {self.author.display_name}")

        return embed

    def _content_to_embed(self, content: str) -> discord.Embed:
        clean = content.strip()
        title = "Ocelot Economy"
        color = discord.Color.blurple()
        icon = "ℹ️"

        if clean.startswith("✅"):
            title, color, icon = "Success", discord.Color.green(), "✅"
        elif clean.startswith("⏳"):
            title, color, icon = "Cooldown", discord.Color.orange(), "⏳"
        elif clean.startswith("🎉"):
            title, color, icon = "Milestone", discord.Color.gold(), "🎉"
        elif clean.startswith("❌"):
            title, color, icon = "Error", discord.Color.red(), "❌"
        elif clean.startswith("🔴"):
            title, color, icon = "Warning", discord.Color.red(), "🔴"

        # Keep multiline messages easier to scan by preserving line breaks.
        description = clean
        if len(description) > 3500:
            description = description[:3497] + "..."

        embed = discord.Embed(
            title=f"{icon} {title}",
            description=description,
            color=color,
        )
        return self._style_embed(embed)

    async def send(self, content=None, **kwargs):
        if "embed" in kwargs and isinstance(kwargs["embed"], discord.Embed):
            kwargs["embed"] = self._style_embed(kwargs["embed"])

        if "embeds" in kwargs and isinstance(kwargs["embeds"], list):
            kwargs["embeds"] = [
                self._style_embed(e) if isinstance(e, discord.Embed) else e
                for e in kwargs["embeds"]
            ]

        if (
            isinstance(content, str)
            and content.strip()
            and "embed" not in kwargs
            and "embeds" not in kwargs
            and "file" not in kwargs
            and "files" not in kwargs
        ):
            kwargs["embed"] = self._content_to_embed(content)
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
    "cogs.insurance",
    "cogs.contracts",
    "cogs.trust",
    "cogs.legal",
    "cogs.finance",
    "cogs.onboarding",
    "cogs.owner_admin",
]


@bot.event
async def on_ready():
    print(f"{bot.user} is online and the economy is running!")


async def _release_ctx_lock(ctx):
    lock = _active_ctx_locks.pop(ctx.message.id, None)
    if lock and lock.locked():
        lock.release()


async def _is_owner_user(user: discord.abc.User) -> bool:
    if OWNER_FALLBACK_ID and user.id == OWNER_FALLBACK_ID:
        return True
    try:
        return await bot.is_owner(user)
    except Exception:
        return False


@bot.before_invoke
async def anti_abuse_guard(ctx):
    if not ctx.command:
        return

    cmd_name = ctx.command.qualified_name
    uid = ctx.author.id
    is_owner = await _is_owner_user(ctx.author)

    # Emergency toggles should not affect owner access.
    maintenance_mode = (get_eco_state("maintenance_mode") or "0") == "1"
    economy_frozen = (get_eco_state("economy_frozen") or "0") == "1"
    if not is_owner and maintenance_mode:
        # Generic unknown-style response to avoid exposing admin internals.
        raise commands.CommandNotFound()
    if not is_owner and economy_frozen and cmd_name in TRANSACTION_COMMANDS:
        raise commands.CommandNotFound()

    if cmd_name not in EXEMPT_COMMANDS:
        now = time.monotonic()
        cooldown = COMMAND_COOLDOWNS.get(cmd_name, COMMAND_COOLDOWNS["default"])
        key = (uid, cmd_name)
        last = _last_command_at.get(key, 0.0)
        if now - last < cooldown:
            remain = cooldown - (now - last)
            await ctx.send(f"⏳ Slow down. You can use `!{cmd_name}` again in {remain:.1f}s.")
            raise commands.CheckFailure("Command rate-limited.")
        _last_command_at[key] = now

    lock = _user_locks.get(uid)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[uid] = lock

    await lock.acquire()
    _active_ctx_locks[ctx.message.id] = lock


@bot.after_invoke
async def release_after_command(ctx):
    await _release_ctx_lock(ctx)


@bot.event
async def on_command_error(ctx, error):
    await _release_ctx_lock(ctx)

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
        content = ctx.message.content.strip()
        if content.startswith("!"):
            attempted = content[1:].split(" ")[0].lower()
            all_commands = [c.name for c in bot.commands if not c.hidden]
            suggestion = difflib.get_close_matches(attempted, all_commands, n=1, cutoff=0.55)
            if suggestion:
                await ctx.send(f"Unknown command `{attempted}`. Did you mean `!{suggestion[0]}`?\nUse `!help` to browse commands.")
            else:
                await ctx.send(f"Unknown command `{attempted}`. Use `!help` to browse available commands.")
    elif isinstance(error, commands.CheckFailure) and str(error) == "Command rate-limited.":
        pass
    elif isinstance(error, commands.CheckFailure):
        # Hide protected commands from unauthorized users.
        content = ctx.message.content.strip()
        if content.startswith("!"):
            attempted = content[1:].split(" ")[0].lower()
            await ctx.send(f"Unknown command `{attempted}`. Use `!help` to browse available commands.")
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
