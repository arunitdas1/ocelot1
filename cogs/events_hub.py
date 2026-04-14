import time
import discord
from discord.ext import commands
from db import active_events, citizens, event_participants, transactions, write_txn
from utils import ensure_citizen, fmt, log_tx, reserve_daily_cap, release_daily_cap


class EventsHub(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="events")
    async def events(self, ctx):
        now = int(time.time())
        rows = list(
            active_events.find(
                {"ends_at": {"$gt": now}},
                {"event_id": 1, "name": 1, "description": 1, "tag": 1, "reward_pool": 1, "ends_at": 1, "_id": 0},
            ).sort("ends_at", 1)
        )
        if not rows:
            await ctx.send("No active events right now.")
            return
        embed = discord.Embed(title="Event Hub", color=discord.Color.gold())
        for row in rows[:10]:
            event_id = row.get("event_id")
            name = row.get("name")
            description = row.get("description")
            tag = row.get("tag")
            reward_pool = row.get("reward_pool")
            ends_at = row.get("ends_at")
            embed.add_field(
                name=f"#{event_id} {name} {f'[{tag}]' if tag else ''}",
                value=f"{description}\nPool: {fmt(float(reward_pool or 0))} | Ends <t:{int(ends_at)}:R>",
                inline=False,
            )
        embed.set_footer(text="Use !eventjoin <id> then !eventrewards <id> when complete.")
        await ctx.send(embed=embed)

    @commands.command()
    async def eventjoin(self, ctx, event_id: int):
        ensure_citizen(ctx.author.id)
        now = int(time.time())
        row = active_events.find_one({"event_id": event_id, "ends_at": {"$gt": now}}, {"event_id": 1, "effects": 1, "ends_at": 1})
        if not row:
            await ctx.send("Event not found or already ended.")
            return
        with write_txn():
            event_participants.update_one(
                {"event_id": event_id, "user_id": ctx.author.id},
                {"$setOnInsert": {"event_id": event_id, "user_id": ctx.author.id, "points": 0, "joined_at": now, "claimed": 0}},
                upsert=True,
            )
        await ctx.send(f"✅ You joined event #{event_id}.")

    @commands.command()
    async def eventrewards(self, ctx, event_id: int):
        ensure_citizen(ctx.author.id)
        now = int(time.time())
        evt = active_events.find_one({"event_id": event_id}, {"ends_at": 1, "reward_pool": 1, "_id": 0})
        if not evt:
            await ctx.send("Event not found.")
            return
        ends_at, reward_pool = int(evt.get("ends_at") or 0), float(evt.get("reward_pool") or 0.0)
        if now < ends_at:
            await ctx.send("This event is still active.")
            return
        me = event_participants.find_one({"event_id": event_id, "user_id": ctx.author.id}, {"points": 1, "claimed": 1, "_id": 0})
        if not me:
            await ctx.send("You did not participate in this event.")
            return
        points, claimed = float(me.get("points") or 0), int(me.get("claimed") or 0)
        if claimed:
            await ctx.send("Rewards already claimed.")
            return
        agg = list(
            event_participants.aggregate(
                [
                    {"$match": {"event_id": event_id}},
                    {"$group": {"_id": None, "total_points": {"$sum": {"$ifNull": ["$points", 0]}}}},
                ]
            )
        )
        total_points = float(agg[0]["total_points"]) if agg else 0.0
        if total_points <= 0 or points <= 0:
            await ctx.send("No claimable points were recorded.")
            return
        payout = round((points / total_points) * reward_pool, 2)
        with write_txn():
            if not reserve_daily_cap(ctx.author.id, "event_reward", 3, now):
                await ctx.send("Daily event reward cap reached.")
                return
            claimed_now = event_participants.update_one(
                {"event_id": event_id, "user_id": ctx.author.id, "claimed": 0},
                {"$set": {"claimed": 1, "claimed_at": now}},
            )
            if claimed_now.modified_count == 0:
                release_daily_cap(ctx.author.id, "event_reward", now)
                await ctx.send("Rewards already claimed.")
                return
            credited = citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": payout}})
            if credited.modified_count == 0:
                event_participants.update_one(
                    {"event_id": event_id, "user_id": ctx.author.id, "claimed": 1},
                    {"$set": {"claimed": 0}, "$unset": {"claimed_at": ""}},
                )
                release_daily_cap(ctx.author.id, "event_reward", now)
                await ctx.send("Reward payout failed due to concurrent account state change.")
                return
        log_tx(ctx.author.id, "event_reward", payout, f"Event reward claim #{event_id}")
        await ctx.send(f"🎉 You claimed {fmt(payout)} from event #{event_id}.")


async def setup(bot):
    await bot.add_cog(EventsHub(bot))

