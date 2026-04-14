import time
import discord
from discord.ext import commands
from db import cursor, write_txn
from utils import ensure_citizen, fmt, update_season_stat, log_tx
from cogs.ui_components import PaginatorView


def _daily_reset_ts(now: int) -> int:
    return now - (now % 86400) + 86400


def _weekly_reset_ts(now: int) -> int:
    return now - (now % (86400 * 7)) + (86400 * 7)


class Quests(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def ensure_assignments(self, user_id: int):
        now = int(time.time())
        with write_txn():
            cursor.execute("SELECT key, target_type, target_value FROM quests_daily WHERE is_active = 1")
            for key, target_type, target_value in cursor.fetchall():
                cursor.execute(
                    "INSERT OR IGNORE INTO user_quests(user_id, quest_type, quest_key, progress, target, claimed, assigned_at, resets_at) "
                    "VALUES (?, 'daily', ?, 0, ?, 0, ?, ?)",
                    (user_id, key, float(target_value), now, _daily_reset_ts(now)),
                )
            cursor.execute("SELECT key, target_type, target_value FROM quests_weekly WHERE is_active = 1")
            for key, target_type, target_value in cursor.fetchall():
                cursor.execute(
                    "INSERT OR IGNORE INTO user_quests(user_id, quest_type, quest_key, progress, target, claimed, assigned_at, resets_at) "
                    "VALUES (?, 'weekly', ?, 0, ?, 0, ?, ?)",
                    (user_id, key, float(target_value), now, _weekly_reset_ts(now)),
                )

    @commands.command(aliases=["missions"])
    async def quests(self, ctx):
        """View your active daily and weekly quests."""
        ensure_citizen(ctx.author.id)
        self.ensure_assignments(ctx.author.id)
        cursor.execute(
            "SELECT quest_type, quest_key, progress, target, claimed, resets_at FROM user_quests "
            "WHERE user_id = ? ORDER BY quest_type, quest_key",
            (ctx.author.id,),
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send("No quests assigned yet.")
            return

        pages = []
        chunk = 8
        for i in range(0, len(rows), chunk):
            embed = discord.Embed(title="Active Quests", color=discord.Color.blurple())
            for qtype, key, progress, target, claimed, resets_at in rows[i:i + chunk]:
                status = "✅ Claimed" if claimed else ("✅ Complete" if progress >= target else "🕒 In Progress")
                embed.add_field(
                    name=f"[{qtype.title()}] {key}",
                    value=f"{status} — {progress:.0f}/{target:.0f} | resets <t:{int(resets_at)}:R>",
                    inline=False,
                )
            embed.set_footer(text="Use !claimquest <quest_key> to claim completed quests.")
            pages.append(embed)

        if len(pages) == 1:
            await ctx.send(embed=pages[0])
            return
        view = PaginatorView(ctx.author.id, pages)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    @commands.command()
    async def claimquest(self, ctx, quest_key: str):
        """Claim a completed quest reward."""
        ensure_citizen(ctx.author.id)
        cursor.execute(
            "SELECT uq.quest_type, uq.quest_key, uq.progress, uq.target, uq.claimed, "
            "COALESCE(qd.reward_cash, qw.reward_cash, 0), COALESCE(qd.reward_xp, qw.reward_xp, 0) "
            "FROM user_quests uq "
            "LEFT JOIN quests_daily qd ON uq.quest_key = qd.key AND uq.quest_type = 'daily' "
            "LEFT JOIN quests_weekly qw ON uq.quest_key = qw.key AND uq.quest_type = 'weekly' "
            "WHERE uq.user_id = ? AND uq.quest_key = ?",
            (ctx.author.id, quest_key),
        )
        row = cursor.fetchone()
        if not row:
            await ctx.send("Quest not found.")
            return
        qtype, key, progress, target, claimed, reward_cash, reward_xp = row
        if claimed:
            await ctx.send("Quest reward already claimed.")
            return
        if progress < target:
            await ctx.send("Quest is not complete yet.")
            return
        cursor.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id = ? AND tx_type = 'quest_claim_reward' AND timestamp >= ?",
            (ctx.author.id, int(time.time()) - 86400),
        )
        if int(cursor.fetchone()[0] or 0) >= 10:
            await ctx.send("Daily quest reward cap reached. Try again after reset.")
            return

        with write_txn():
            cursor.execute("UPDATE citizens SET cash = cash + ?, job_xp = job_xp + ? WHERE user_id = ?", (reward_cash, int(reward_xp), ctx.author.id))
            cursor.execute("UPDATE user_quests SET claimed = 1 WHERE user_id = ? AND quest_key = ?", (ctx.author.id, key))
        log_tx(ctx.author.id, "quest_claim_reward", float(reward_cash), f"Quest claim {key}")
        update_season_stat(ctx.author.id, "quests_completed", 1)
        await ctx.send(f"✅ Claimed quest `{key}` rewards: {fmt(reward_cash)} + {int(reward_xp)} XP")

    @commands.command()
    async def streak(self, ctx):
        """View your daily streak and next milestone."""
        ensure_citizen(ctx.author.id)
        cursor.execute("SELECT daily_streak, streak_protect_tokens, last_streak_claim FROM citizens WHERE user_id = ?", (ctx.author.id,))
        streak, protect, last_claim = cursor.fetchone()
        milestones = [3, 7, 14, 30]
        next_ms = next((m for m in milestones if streak < m), "MAX")
        embed = discord.Embed(title="Daily Streak", color=discord.Color.green())
        embed.add_field(name="Current streak", value=str(streak), inline=True)
        embed.add_field(name="Protection tokens", value=str(protect), inline=True)
        embed.add_field(name="Next milestone", value=str(next_ms), inline=True)
        if last_claim:
            embed.add_field(name="Last streak update", value=f"<t:{int(last_claim)}:R>", inline=False)
        await ctx.send(embed=embed)

    @commands.command()
    async def streakprotect(self, ctx):
        """Use a streak protection token when you miss a day."""
        ensure_citizen(ctx.author.id)
        cursor.execute("SELECT streak_protect_tokens FROM citizens WHERE user_id = ?", (ctx.author.id,))
        tok = int(cursor.fetchone()[0] or 0)
        if tok <= 0:
            await ctx.send("You have no streak protection tokens.")
            return
        with write_txn():
            cursor.execute("UPDATE citizens SET streak_protect_tokens = streak_protect_tokens - 1 WHERE user_id = ?", (ctx.author.id,))
        await ctx.send("✅ Streak protection consumed.")


async def setup(bot):
    await bot.add_cog(Quests(bot))

