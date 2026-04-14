import time
import discord
from discord.ext import commands
from db import citizens, quests_daily, quests_weekly, transactions, user_quests, write_txn
from utils import ensure_citizen, fmt, update_season_stat, log_tx, reserve_daily_cap, release_daily_cap
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
            for quest in quests_daily.find({"is_active": 1}, {"key": 1, "target_value": 1, "_id": 0}):
                key = quest.get("key")
                target_value = quest.get("target_value")
                user_quests.update_one(
                    {"user_id": user_id, "quest_type": "daily", "quest_key": key},
                    {
                        "$setOnInsert": {
                            "user_id": user_id,
                            "quest_type": "daily",
                            "quest_key": key,
                            "progress": 0,
                            "target": float(target_value),
                            "claimed": 0,
                            "assigned_at": now,
                            "resets_at": _daily_reset_ts(now),
                        }
                    },
                    upsert=True,
                )
            for quest in quests_weekly.find({"is_active": 1}, {"key": 1, "target_value": 1, "_id": 0}):
                key = quest.get("key")
                target_value = quest.get("target_value")
                user_quests.update_one(
                    {"user_id": user_id, "quest_type": "weekly", "quest_key": key},
                    {
                        "$setOnInsert": {
                            "user_id": user_id,
                            "quest_type": "weekly",
                            "quest_key": key,
                            "progress": 0,
                            "target": float(target_value),
                            "claimed": 0,
                            "assigned_at": now,
                            "resets_at": _weekly_reset_ts(now),
                        }
                    },
                    upsert=True,
                )

    @commands.command(aliases=["missions"])
    async def quests(self, ctx):
        """View your active daily and weekly quests."""
        ensure_citizen(ctx.author.id)
        self.ensure_assignments(ctx.author.id)
        rows = list(
            user_quests.find(
                {"user_id": ctx.author.id},
                {"quest_type": 1, "quest_key": 1, "progress": 1, "target": 1, "claimed": 1, "resets_at": 1, "_id": 0},
            ).sort([("quest_type", 1), ("quest_key", 1)])
        )
        if not rows:
            await ctx.send("No quests assigned yet.")
            return

        pages = []
        chunk = 8
        for i in range(0, len(rows), chunk):
            embed = discord.Embed(title="Active Quests", color=discord.Color.blurple())
            for row in rows[i:i + chunk]:
                qtype = row.get("quest_type")
                key = row.get("quest_key")
                progress = float(row.get("progress") or 0)
                target = float(row.get("target") or 0)
                claimed = int(row.get("claimed") or 0)
                resets_at = int(row.get("resets_at") or 0)
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
        row = user_quests.find_one({"user_id": ctx.author.id, "quest_key": quest_key})
        if not row:
            await ctx.send("Quest not found.")
            return
        qtype = row.get("quest_type")
        key = row.get("quest_key")
        progress = float(row.get("progress") or 0)
        target = float(row.get("target") or 0)
        claimed = int(row.get("claimed") or 0)
        if qtype == "daily":
            quest_meta = quests_daily.find_one({"key": key}, {"reward_cash": 1, "reward_xp": 1, "_id": 0}) or {}
        else:
            quest_meta = quests_weekly.find_one({"key": key}, {"reward_cash": 1, "reward_xp": 1, "_id": 0}) or {}
        reward_cash = float(quest_meta.get("reward_cash") or 0)
        reward_xp = int(quest_meta.get("reward_xp") or 0)
        if claimed:
            await ctx.send("Quest reward already claimed.")
            return
        if progress < target:
            await ctx.send("Quest is not complete yet.")
            return
        with write_txn():
            if not reserve_daily_cap(ctx.author.id, "quest_claim_reward", 10, int(time.time())):
                await ctx.send("Daily quest reward cap reached. Try again after reset.")
                return
            claimed_now = user_quests.update_one(
                {"user_id": ctx.author.id, "quest_type": qtype, "quest_key": key, "claimed": 0},
                {"$set": {"claimed": 1}},
            )
            if claimed_now.modified_count == 0:
                release_daily_cap(ctx.author.id, "quest_claim_reward", int(time.time()))
                await ctx.send("Quest reward already claimed.")
                return
            credited = citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": reward_cash, "job_xp": int(reward_xp)}})
            if credited.modified_count == 0:
                user_quests.update_one(
                    {"user_id": ctx.author.id, "quest_type": qtype, "quest_key": key, "claimed": 1},
                    {"$set": {"claimed": 0}},
                )
                release_daily_cap(ctx.author.id, "quest_claim_reward", int(time.time()))
                await ctx.send("Quest payout failed due to concurrent account state change.")
                return
        log_tx(ctx.author.id, "quest_claim_reward", float(reward_cash), f"Quest claim {key}")
        update_season_stat(ctx.author.id, "quests_completed", 1)
        await ctx.send(f"✅ Claimed quest `{key}` rewards: {fmt(reward_cash)} + {int(reward_xp)} XP")

    @commands.command()
    async def streak(self, ctx):
        """View your daily streak and next milestone."""
        ensure_citizen(ctx.author.id)
        citizen = citizens.find_one(
            {"user_id": ctx.author.id},
            {"daily_streak": 1, "streak_protect_tokens": 1, "last_streak_claim": 1, "_id": 0},
        ) or {}
        streak = int(citizen.get("daily_streak") or 0)
        protect = int(citizen.get("streak_protect_tokens") or 0)
        last_claim = citizen.get("last_streak_claim")
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
        citizen = citizens.find_one({"user_id": ctx.author.id}, {"streak_protect_tokens": 1, "_id": 0}) or {}
        tok = int(citizen.get("streak_protect_tokens") or 0)
        if tok <= 0:
            await ctx.send("You have no streak protection tokens.")
            return
        with write_txn():
            citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"streak_protect_tokens": -1}})
        await ctx.send("✅ Streak protection consumed.")


async def setup(bot):
    await bot.add_cog(Quests(bot))

