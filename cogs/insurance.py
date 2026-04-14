import math
import time
import discord
from discord.ext import commands
from pymongo.errors import DuplicateKeyError
from db import citizens, insurance_policies, insurance_claims, next_id, write_txn
from utils import ensure_citizen, get_citizen, fmt, log_tx, reserve_daily_cap, release_daily_cap


INSURANCE_PLANS = {
    "health_basic": {"name": "Health Basic", "premium": 25.0, "limit": 2000.0, "deductible": 50.0},
    "health_plus": {"name": "Health Plus", "premium": 55.0, "limit": 7000.0, "deductible": 100.0},
    "property_basic": {"name": "Property Basic", "premium": 30.0, "limit": 4000.0, "deductible": 75.0},
    "business_liability": {"name": "Business Liability", "premium": 80.0, "limit": 12000.0, "deductible": 250.0},
}
CLAIM_COOLDOWN_SECONDS = 12 * 3600
MIN_POLICY_AGE_SECONDS = 24 * 3600
MAX_DAILY_APPROVED_CLAIMS = 2


class Insurance(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="plans")
    async def plans(self, ctx):
        """View available insurance plans."""
        embed = discord.Embed(title="Insurance Plans", color=discord.Color.blurple())
        embed.description = "Use `!insurancebuy <plan_id>` to enroll."
        for pid, p in INSURANCE_PLANS.items():
            embed.add_field(
                name=f"`{pid}` — {p['name']}",
                value=f"Premium: {fmt(p['premium'])}/day | Coverage limit: {fmt(p['limit'])} | Deductible: {fmt(p['deductible'])}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="insurancebuy")
    async def insurancebuy(self, ctx, plan_id: str):
        """Buy an insurance policy. Usage: !insurancebuy <plan_id>"""
        plan_id = plan_id.lower()
        if plan_id not in INSURANCE_PLANS:
            await ctx.send("Unknown plan. Use `!plans`.")
            return

        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)
        p = INSURANCE_PLANS[plan_id]

        existing = insurance_policies.count_documents(
            {"holder_id": ctx.author.id, "policy_type": plan_id, "status": "active"}
        )
        if existing > 0:
            await ctx.send("You already have this plan active.")
            return

        now = int(time.time())
        policy_id = next_id("insurance_policies")
        with write_txn():
            try:
                insurance_policies.insert_one(
                    {
                        "policy_id": policy_id,
                        "holder_id": ctx.author.id,
                        "policy_type": plan_id,
                        "premium": p["premium"],
                        "coverage_limit": p["limit"],
                        "deductible": p["deductible"],
                        "risk_score": 1.0,
                        "approved_total": 0.0,
                        "status": "active",
                        "started_at": now,
                        "last_billed_at": now,
                    }
                )
            except DuplicateKeyError:
                await ctx.send("You already have this plan active.")
                return

        embed = discord.Embed(title="Policy activated", color=discord.Color.green())
        embed.description = f"You enrolled in **{p['name']}**.\nPremium: {fmt(p['premium'])}/day"
        await ctx.send(embed=embed)

    @commands.command(name="insurancestatus")
    async def insurancestatus(self, ctx):
        """View your active insurance policies."""
        ensure_citizen(ctx.author.id)
        rows = list(
            insurance_policies.find(
                {"holder_id": ctx.author.id},
                {"_id": 0, "policy_id": 1, "policy_type": 1, "premium": 1, "coverage_limit": 1, "deductible": 1, "status": 1},
            ).sort("policy_id", -1)
        )
        if not rows:
            await ctx.send("You have no insurance policies. Use `!plans`.")
            return

        embed = discord.Embed(title=f"{ctx.author.display_name}'s Insurance", color=discord.Color.blue())
        for row in rows[:10]:
            policy_id = row.get("policy_id")
            policy_type = row.get("policy_type")
            premium = row.get("premium", 0.0)
            limit_amt = row.get("coverage_limit", 0.0)
            deductible = row.get("deductible", 0.0)
            status = row.get("status", "active")
            plan = INSURANCE_PLANS.get(policy_type, {"name": policy_type})
            embed.add_field(
                name=f"Policy #{policy_id} — {plan['name']}",
                value=f"Status: **{status}** | Premium: {fmt(premium)}/day | Limit: {fmt(limit_amt)} | Deductible: {fmt(deductible)}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="insuranceclaim")
    async def insuranceclaim(self, ctx, policy_id: int, incident_type: str, amount: float):
        """File an insurance claim. Usage: !insuranceclaim <policy_id> <incident_type> <amount>"""
        if not math.isfinite(amount) or amount <= 0:
            await ctx.send("Claim amount must be a positive finite number.")
            return
        if amount > 1_000_000:
            await ctx.send("Claim amount is too large.")
            return

        ensure_citizen(ctx.author.id)
        now = int(time.time())
        row = insurance_policies.find_one(
            {"policy_id": policy_id, "holder_id": ctx.author.id},
            {"_id": 0, "policy_id": 1, "policy_type": 1, "coverage_limit": 1, "deductible": 1, "status": 1, "started_at": 1, "approved_total": 1},
        )
        if row is None:
            await ctx.send("Policy not found.")
            return

        policy_type = row.get("policy_type")
        limit_amt = row.get("coverage_limit", 0.0)
        deductible = row.get("deductible", 0.0)
        status = row.get("status")
        started_at = row.get("started_at", 0)
        if status != "active":
            await ctx.send("This policy is not active.")
            return
        if now - int(started_at or 0) < MIN_POLICY_AGE_SECONDS:
            await ctx.send("This policy is too new to claim yet. Please wait at least 24 hours from activation.")
            return
        last_doc = insurance_claims.find_one(
            {"policy_id": policy_id, "claimant_id": ctx.author.id},
            {"_id": 0, "filed_at": 1},
            sort=[("filed_at", -1)],
        )
        last_filed = int((last_doc or {}).get("filed_at", 0) or 0)
        if last_doc and now - last_filed < CLAIM_COOLDOWN_SECONDS:
            rem = CLAIM_COOLDOWN_SECONDS - (now - last_filed)
            await ctx.send(f"Claim cooldown active. Try again in {rem // 3600}h {(rem % 3600) // 60}m.")
            return
        approved_so_far = float(row.get("approved_total") or 0.0)
        remaining_coverage = max(0.0, float(limit_amt) - approved_so_far)
        if remaining_coverage <= 0:
            await ctx.send("This policy has exhausted its coverage limit.")
            return

        plan = INSURANCE_PLANS.get(policy_type, {"name": policy_type})
        claim_amount = round(amount, 2)
        approved = max(0.0, min(remaining_coverage, claim_amount - float(deductible)))
        approved = round(approved, 2)

        claim_id = next_id("insurance_claims")
        with write_txn():
            if not reserve_daily_cap(ctx.author.id, "insurance_claim_approved", MAX_DAILY_APPROVED_CLAIMS, now):
                await ctx.send("You reached your daily approved-claim cap. Try again tomorrow.")
                return
            reserve_result = insurance_policies.update_one(
                {
                    "policy_id": policy_id,
                    "holder_id": ctx.author.id,
                    "status": "active",
                    "$expr": {"$lte": [{"$add": [{"$ifNull": ["$approved_total", 0]}, approved]}, "$coverage_limit"]},
                },
                {"$inc": {"approved_total": approved}},
            )
            if reserve_result.modified_count == 0:
                release_daily_cap(ctx.author.id, "insurance_claim_approved", now)
                await ctx.send("This policy has exhausted its coverage limit.")
                return
            insurance_claims.insert_one(
                {
                    "claim_id": claim_id,
                    "policy_id": policy_id,
                    "claimant_id": ctx.author.id,
                    "incident_type": incident_type.lower(),
                    "claim_amount": claim_amount,
                    "approved_amount": approved,
                    "status": "approved",
                    "filed_at": now,
                    "resolved_at": now,
                }
            )

            if approved > 0:
                credited = citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": approved}})
                if credited.modified_count == 0:
                    insurance_claims.delete_one({"claim_id": claim_id, "claimant_id": ctx.author.id})
                    insurance_policies.update_one({"policy_id": policy_id}, {"$inc": {"approved_total": -approved}})
                    release_daily_cap(ctx.author.id, "insurance_claim_approved", now)
                    await ctx.send("Claim payout failed due to concurrent account state change. Please try again.")
                    return
        if approved > 0:
            log_tx(ctx.author.id, "insurance_claim", approved, f"Insurance claim #{policy_id}")

        embed = discord.Embed(title="Claim processed", color=discord.Color.green())
        embed.description = (
            f"Policy: **{plan['name']}**\n"
            f"Filed: {fmt(claim_amount)} | Deductible: {fmt(deductible)} | Approved: **{fmt(approved)}**"
        )
        await ctx.send(embed=embed)

    @commands.command(name="insurancecancel")
    async def insurancecancel(self, ctx, policy_id: int):
        """Cancel an insurance policy. Usage: !insurancecancel <policy_id>"""
        with write_txn():
            result = insurance_policies.update_one(
                {"policy_id": policy_id, "holder_id": ctx.author.id, "status": "active"},
                {"$set": {"status": "cancelled", "ends_at": int(time.time())}},
            )
            if result.modified_count == 0:
                await ctx.send("No active policy found with that ID.")
                return
        await ctx.send("✅ Policy cancelled.")


async def setup(bot):
    await bot.add_cog(Insurance(bot))

