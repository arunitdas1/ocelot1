import discord
from discord.ext import commands
import time
from db import achievements, citizens, collections, user_achievements, write_txn
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
        ua_rows = list(
            user_achievements.find(
                {"user_id": ctx.author.id},
                {"ach_key": 1, "progress": 1, "unlocked": 1, "claimed": 1, "_id": 0},
            ).sort([("unlocked", -1), ("progress", -1)])
        )
        ach_map = {
            row["ach_key"]: row
            for row in achievements.find({}, {"ach_key": 1, "title": 1, "target_value": 1, "_id": 0})
        }
        embed = discord.Embed(title="Achievements", color=discord.Color.purple())
        for row in ua_rows[:15]:
            ach_key = row.get("ach_key")
            meta = ach_map.get(ach_key, {})
            title = meta.get("title", ach_key)
            target = float(meta.get("target_value") or 0)
            progress = float(row.get("progress") or 0)
            unlocked = int(row.get("unlocked") or 0)
            claimed = int(row.get("claimed") or 0)
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
        total = user_achievements.count_documents({"user_id": ctx.author.id})
        unlocked = user_achievements.count_documents({"user_id": ctx.author.id, "unlocked": 1})
        claimed = user_achievements.count_documents({"user_id": ctx.author.id, "claimed": 1})
        await ctx.send(f"Achievements: {claimed}/{total} claimed, {unlocked}/{total} unlocked.")

    @commands.command()
    async def claimbadge(self, ctx, ach_key: str):
        ensure_citizen(ctx.author.id)
        ensure_user_achievements(ctx.author.id)
        ua = user_achievements.find_one({"user_id": ctx.author.id, "ach_key": ach_key}, {"unlocked": 1, "claimed": 1, "_id": 0})
        meta = achievements.find_one({"ach_key": ach_key}, {"reward_cash": 1, "reward_badge": 1, "_id": 0})
        if not ua or not meta:
            await ctx.send("Achievement not found.")
            return
        unlocked = int(ua.get("unlocked") or 0)
        claimed = int(ua.get("claimed") or 0)
        reward_cash = float(meta.get("reward_cash") or 0)
        reward_badge = meta.get("reward_badge")
        if not unlocked:
            await ctx.send("That achievement is not unlocked yet.")
            return
        if claimed:
            await ctx.send("Achievement reward already claimed.")
            return
        with write_txn():
            claimed_now = user_achievements.update_one(
                {"user_id": ctx.author.id, "ach_key": ach_key, "unlocked": 1, "claimed": 0},
                {"$set": {"claimed": 1}},
            )
            if claimed_now.modified_count == 0:
                await ctx.send("Achievement reward already claimed.")
                return
            citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": reward_cash}})
            if reward_badge:
                collections.update_one(
                    {"user_id": ctx.author.id, "collection_key": "badges", "item_key": str(reward_badge)},
                    {
                        "$setOnInsert": {
                            "user_id": ctx.author.id,
                            "collection_key": "badges",
                            "item_key": str(reward_badge),
                            "obtained_at": int(time.time()),
                        }
                    },
                    upsert=True,
                )
        log_tx(ctx.author.id, "achievement_claim", reward_cash, f"Achievement {ach_key}")
        await ctx.send(f"✅ Claimed `{ach_key}`: {fmt(reward_cash)} and badge `{reward_badge}`.")


async def setup(bot):
    await bot.add_cog(Achievements(bot))

