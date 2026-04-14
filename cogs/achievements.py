import discord
from discord.ext import commands
from db import cursor, write_txn
from utils import ensure_citizen, ensure_user_achievements, fmt, log_tx


def _bar(progress: float, target: float, width: int = 12) -> str:
    ratio = 0.0 if target <= 0 else min(1.0, progress / target)
    filled = int(round(ratio * width))
    return "█" * filled + "░" * (width - filled)


class Achievements(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def achievements(self, ctx):
        ensure_citizen(ctx.author.id)
        ensure_user_achievements(ctx.author.id)
        cursor.execute(
            "SELECT a.ach_key, a.title, a.target_value, ua.progress, ua.unlocked, ua.claimed "
            "FROM achievements a JOIN user_achievements ua ON a.ach_key = ua.ach_key "
            "WHERE ua.user_id = ? ORDER BY ua.unlocked DESC, ua.progress DESC",
            (ctx.author.id,),
        )
        rows = cursor.fetchall()
        embed = discord.Embed(title="Achievements", color=discord.Color.purple())
        for ach_key, title, target, progress, unlocked, claimed in rows[:15]:
            state = "🏆 Claimed" if claimed else ("✅ Unlocked" if unlocked else "🕒 In Progress")
            embed.add_field(
                name=f"{title} ({ach_key})",
                value=f"{state}\n`{_bar(float(progress), float(target))}` {progress:.0f}/{target:.0f}",
                inline=False,
            )
        embed.set_footer(text="Use !claimbadge <achievement_key> for unlocked rewards.")
        await ctx.send(embed=embed)

    @commands.command()
    async def achprogress(self, ctx):
        ensure_citizen(ctx.author.id)
        ensure_user_achievements(ctx.author.id)
        cursor.execute(
            "SELECT COUNT(*), SUM(CASE WHEN unlocked = 1 THEN 1 ELSE 0 END), SUM(CASE WHEN claimed = 1 THEN 1 ELSE 0 END) "
            "FROM user_achievements WHERE user_id = ?",
            (ctx.author.id,),
        )
        total, unlocked, claimed = cursor.fetchone()
        await ctx.send(f"Achievements: {claimed}/{total} claimed, {unlocked}/{total} unlocked.")

    @commands.command()
    async def claimbadge(self, ctx, ach_key: str):
        ensure_citizen(ctx.author.id)
        ensure_user_achievements(ctx.author.id)
        cursor.execute(
            "SELECT ua.unlocked, ua.claimed, a.reward_cash, a.reward_badge "
            "FROM user_achievements ua JOIN achievements a ON ua.ach_key = a.ach_key "
            "WHERE ua.user_id = ? AND ua.ach_key = ?",
            (ctx.author.id, ach_key),
        )
        row = cursor.fetchone()
        if not row:
            await ctx.send("Achievement not found.")
            return
        unlocked, claimed, reward_cash, reward_badge = row
        if not unlocked:
            await ctx.send("That achievement is not unlocked yet.")
            return
        if claimed:
            await ctx.send("Achievement reward already claimed.")
            return
        with write_txn():
            cursor.execute("UPDATE citizens SET cash = cash + ? WHERE user_id = ?", (float(reward_cash), ctx.author.id))
            cursor.execute("UPDATE user_achievements SET claimed = 1 WHERE user_id = ? AND ach_key = ?", (ctx.author.id, ach_key))
            if reward_badge:
                cursor.execute(
                    "INSERT OR IGNORE INTO collections(user_id, collection_key, item_key, obtained_at) VALUES (?, 'badges', ?, strftime('%s','now'))",
                    (ctx.author.id, str(reward_badge)),
                )
        log_tx(ctx.author.id, "achievement_claim", float(reward_cash), f"Achievement {ach_key}")
        await ctx.send(f"✅ Claimed `{ach_key}`: {fmt(float(reward_cash))} and badge `{reward_badge}`.")


async def setup(bot):
    await bot.add_cog(Achievements(bot))

