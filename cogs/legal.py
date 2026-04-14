import time
import random
import math
import discord
from discord.ext import commands
from db import citizens, offenses, write_txn
from utils import ensure_citizen, get_citizen, fmt, record_offense


OFFENSE_TYPES = {
    "pickpocket": {"severity": 1, "fine": (25, 120), "jail": (0, 600), "base_detect": 0.15},
    "fraud": {"severity": 2, "fine": (200, 1200), "jail": (600, 3600), "base_detect": 0.30},
    "robbery": {"severity": 3, "fine": (500, 3000), "jail": (1800, 7200), "base_detect": 0.45},
}


class Legal(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="crime")
    async def crime(self, ctx, offense_type: str = "pickpocket"):
        """Commit a crime for risky upside (balanced EV). Usage: !crime [pickpocket|fraud|robbery]"""
        offense_type = offense_type.lower()
        if offense_type not in OFFENSE_TYPES:
            await ctx.send("Valid crimes: `pickpocket`, `fraud`, `robbery`.")
            return

        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)
        if c.get("is_jailed"):
            await ctx.send("You cannot commit crimes while jailed.")
            return

        info = OFFENSE_TYPES[offense_type]
        severity = info["severity"]
        fine = round(random.uniform(*info["fine"]), 2)
        jail_seconds = int(random.randint(*info["jail"]))

        # Detection probability scales with severity and wanted level
        wanted = int(c.get("wanted_level") or 0)
        detect = info["base_detect"] + wanted * 0.02 + (0.05 if severity >= 3 else 0.0)
        detect = max(0.05, min(0.85, detect))

        # Reward is capped and tempered by detection
        reward = round(random.uniform(50, 250) * severity * (1.0 - detect * 0.4), 2)

        caught = random.random() < detect
        if caught:
            record_offense(ctx.author.id, offense_type, severity, fine, jail_seconds, detect)
            with write_txn():
                citizens.update_one(
                    {"user_id": ctx.author.id},
                    [
                        {
                            "$set": {
                                "wanted_level": {"$min": [10, {"$add": [{"$ifNull": ["$wanted_level", 0]}, 1]}]},
                                "criminal_record_points": {"$add": [{"$ifNull": ["$criminal_record_points", 0]}, severity]},
                            }
                        }
                    ],
                )
                paid_result = citizens.update_one(
                    {"user_id": ctx.author.id, "cash": {"$gte": fine}},
                    {"$inc": {"cash": -fine}},
                )
                paid = paid_result.modified_count > 0
                if not paid:
                    citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"debt": fine}})
                if jail_seconds > 0:
                    jail_until = int(time.time()) + jail_seconds
                    citizens.update_one({"user_id": ctx.author.id}, {"$set": {"is_jailed": 1, "last_release_at": jail_until}})
            await ctx.send(
                f"❌ Caught committing **{offense_type}**.\n"
                f"Fine: {fmt(fine)} ({'paid' if paid else 'added to debt'}) | Jail: {jail_seconds//60} min | Detection: {detect*100:.0f}%"
            )
            return

        with write_txn():
            citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": reward}})
            citizens.update_one(
                {"user_id": ctx.author.id},
                [{"$set": {"wanted_level": {"$max": [0, {"$subtract": [{"$ifNull": ["$wanted_level", 0]}, 1]}]}}}],
            )
        await ctx.send(f"✅ Crime succeeded: **{offense_type}**. You gained **{fmt(reward)}**. (Detection risk was {detect*100:.0f}%)")

    @commands.command(name="record")
    async def record(self, ctx, member: discord.Member = None):
        """View a criminal record (yours by default)."""
        target = member or ctx.author
        ensure_citizen(target.id)
        c = get_citizen(target.id)
        rows = list(
            offenses.find(
                {"offender_id": target.id},
                {"offense_type": 1, "severity": 1, "fine_amount": 1, "jail_seconds": 1, "committed_at": 1, "_id": 0},
            ).sort("committed_at", -1).limit(10)
        )
        embed = discord.Embed(title=f"Legal Record: {target.display_name}", color=discord.Color.red())
        embed.add_field(name="Wanted Level", value=str(int(c.get("wanted_level") or 0)), inline=True)
        embed.add_field(name="Record Points", value=str(int(c.get("criminal_record_points") or 0)), inline=True)
        if not rows:
            embed.description = "No recorded offenses."
            await ctx.send(embed=embed)
            return
        lines = []
        for row in rows:
            lines.append(
                f"<t:{int(row.get('committed_at') or 0)}:R> — **{row.get('offense_type')}** "
                f"(sev {row.get('severity')}) fine {fmt(float(row.get('fine_amount') or 0))} "
                f"jail {int(row.get('jail_seconds') or 0)//60}m"
            )
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @commands.command(name="bail")
    async def bail(self, ctx):
        """Pay bail to exit jail (balanced; converts time into money sink)."""
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)
        if not c.get("is_jailed"):
            await ctx.send("You are not jailed.")
            return
        bail_cost = 500.0 + (float(c.get("wanted_level") or 0) * 150.0)
        bail_cost = round(bail_cost, 2)
        if bail_cost > 50000:
            bail_cost = 50000.0
        bail_result = citizens.update_one(
            {"user_id": ctx.author.id, "cash": {"$gte": bail_cost}},
            {"$inc": {"cash": -bail_cost}},
        )
        if bail_result.modified_count == 0:
            await ctx.send(f"You need {fmt(bail_cost)} cash for bail.")
            return
        with write_txn():
            citizens.update_one({"user_id": ctx.author.id}, {"$set": {"is_jailed": 0, "last_release_at": 0}})
        await ctx.send(f"✅ Bail paid: {fmt(bail_cost)}. You are free.")


async def setup(bot):
    await bot.add_cog(Legal(bot))

