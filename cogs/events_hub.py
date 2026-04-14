import time
import discord
from discord.ext import commands
from db import cursor, write_txn
from utils import ensure_citizen, fmt, log_tx


class EventsHub(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="events")
    async def events(self, ctx):
        now = int(time.time())
        cursor.execute(
            "SELECT event_id, name, description, tag, reward_pool, ends_at FROM active_events WHERE ends_at > ? ORDER BY ends_at ASC",
            (now,),
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send("No active events right now.")
            return
        embed = discord.Embed(title="Event Hub", color=discord.Color.gold())
        for event_id, name, description, tag, reward_pool, ends_at in rows[:10]:
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
        cursor.execute("SELECT event_id, effects, ends_at FROM active_events WHERE event_id = ? AND ends_at > ?", (event_id, now))
        row = cursor.fetchone()
        if not row:
            await ctx.send("Event not found or already ended.")
            return
        with write_txn():
            cursor.execute(
                "INSERT OR IGNORE INTO event_participants(event_id, user_id, points, joined_at, claimed) VALUES (?, ?, 0, ?, 0)",
                (event_id, ctx.author.id, now),
            )
        await ctx.send(f"✅ You joined event #{event_id}.")

    @commands.command()
    async def eventrewards(self, ctx, event_id: int):
        ensure_citizen(ctx.author.id)
        now = int(time.time())
        cursor.execute("SELECT ends_at, reward_pool FROM active_events WHERE event_id = ?", (event_id,))
        evt = cursor.fetchone()
        if not evt:
            await ctx.send("Event not found.")
            return
        ends_at, reward_pool = int(evt[0]), float(evt[1] or 0.0)
        if now < ends_at:
            await ctx.send("This event is still active.")
            return
        cursor.execute(
            "SELECT points, claimed FROM event_participants WHERE event_id = ? AND user_id = ?",
            (event_id, ctx.author.id),
        )
        me = cursor.fetchone()
        if not me:
            await ctx.send("You did not participate in this event.")
            return
        points, claimed = float(me[0] or 0), int(me[1] or 0)
        if claimed:
            await ctx.send("Rewards already claimed.")
            return
        cursor.execute("SELECT SUM(points) FROM event_participants WHERE event_id = ?", (event_id,))
        total_points = float(cursor.fetchone()[0] or 0.0)
        if total_points <= 0 or points <= 0:
            await ctx.send("No claimable points were recorded.")
            return
        payout = round((points / total_points) * reward_pool, 2)
        cursor.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id = ? AND tx_type = 'event_reward' AND timestamp >= ?",
            (ctx.author.id, now - 86400),
        )
        if int(cursor.fetchone()[0] or 0) >= 3:
            await ctx.send("Daily event reward cap reached.")
            return
        with write_txn():
            cursor.execute("UPDATE citizens SET cash = cash + ? WHERE user_id = ?", (payout, ctx.author.id))
            cursor.execute("UPDATE event_participants SET claimed = 1 WHERE event_id = ? AND user_id = ?", (event_id, ctx.author.id))
        log_tx(ctx.author.id, "event_reward", payout, f"Event reward claim #{event_id}")
        await ctx.send(f"🎉 You claimed {fmt(payout)} from event #{event_id}.")


async def setup(bot):
    await bot.add_cog(EventsHub(bot))

