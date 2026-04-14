import math
import time
import discord
from discord.ext import commands
from db import cursor, conn
from utils import ensure_citizen, get_citizen, fmt


INSURANCE_PLANS = {
    "health_basic": {"name": "Health Basic", "premium": 25.0, "limit": 2000.0, "deductible": 50.0},
    "health_plus": {"name": "Health Plus", "premium": 55.0, "limit": 7000.0, "deductible": 100.0},
    "property_basic": {"name": "Property Basic", "premium": 30.0, "limit": 4000.0, "deductible": 75.0},
    "business_liability": {"name": "Business Liability", "premium": 80.0, "limit": 12000.0, "deductible": 250.0},
}


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

        cursor.execute(
            "SELECT COUNT(*) FROM insurance_policies WHERE holder_id = ? AND policy_type = ? AND status = 'active'",
            (ctx.author.id, plan_id),
        )
        if cursor.fetchone()[0] > 0:
            await ctx.send("You already have this plan active.")
            return

        cursor.execute(
            "INSERT INTO insurance_policies(holder_id, policy_type, premium, coverage_limit, deductible, risk_score, started_at, last_billed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ctx.author.id, plan_id, p["premium"], p["limit"], p["deductible"], 1.0, int(time.time()), int(time.time())),
        )
        conn.commit()

        embed = discord.Embed(title="Policy activated", color=discord.Color.green())
        embed.description = f"You enrolled in **{p['name']}**.\nPremium: {fmt(p['premium'])}/day"
        await ctx.send(embed=embed)

    @commands.command(name="insurancestatus")
    async def insurancestatus(self, ctx):
        """View your active insurance policies."""
        ensure_citizen(ctx.author.id)
        cursor.execute(
            "SELECT policy_id, policy_type, premium, coverage_limit, deductible, status FROM insurance_policies "
            "WHERE holder_id = ? ORDER BY policy_id DESC",
            (ctx.author.id,),
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send("You have no insurance policies. Use `!plans`.")
            return

        embed = discord.Embed(title=f"{ctx.author.display_name}'s Insurance", color=discord.Color.blue())
        for policy_id, policy_type, premium, limit_amt, deductible, status in rows[:10]:
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
        cursor.execute(
            "SELECT policy_id, policy_type, coverage_limit, deductible, status FROM insurance_policies WHERE policy_id = ? AND holder_id = ?",
            (policy_id, ctx.author.id),
        )
        row = cursor.fetchone()
        if not row:
            await ctx.send("Policy not found.")
            return

        _, policy_type, limit_amt, deductible, status = row
        if status != "active":
            await ctx.send("This policy is not active.")
            return

        plan = INSURANCE_PLANS.get(policy_type, {"name": policy_type})
        claim_amount = round(amount, 2)
        approved = max(0.0, min(float(limit_amt), claim_amount - float(deductible)))
        approved = round(approved, 2)

        cursor.execute(
            "INSERT INTO insurance_claims(policy_id, claimant_id, incident_type, claim_amount, approved_amount, status, filed_at, resolved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (policy_id, ctx.author.id, incident_type.lower(), claim_amount, approved, "approved", int(time.time()), int(time.time())),
        )

        if approved > 0:
            cursor.execute("UPDATE citizens SET cash = cash + ? WHERE user_id = ?", (approved, ctx.author.id))
        conn.commit()

        embed = discord.Embed(title="Claim processed", color=discord.Color.green())
        embed.description = (
            f"Policy: **{plan['name']}**\n"
            f"Filed: {fmt(claim_amount)} | Deductible: {fmt(deductible)} | Approved: **{fmt(approved)}**"
        )
        await ctx.send(embed=embed)

    @commands.command(name="insurancecancel")
    async def insurancecancel(self, ctx, policy_id: int):
        """Cancel an insurance policy. Usage: !insurancecancel <policy_id>"""
        cursor.execute(
            "UPDATE insurance_policies SET status = 'cancelled', ends_at = ? WHERE policy_id = ? AND holder_id = ? AND status = 'active'",
            (int(time.time()), policy_id, ctx.author.id),
        )
        if cursor.rowcount == 0:
            await ctx.send("No active policy found with that ID.")
            return
        conn.commit()
        await ctx.send("✅ Policy cancelled.")


async def setup(bot):
    await bot.add_cog(Insurance(bot))

