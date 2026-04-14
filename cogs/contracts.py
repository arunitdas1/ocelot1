import time
import json
import math
import discord
from discord.ext import commands
from db import contracts, contract_events, next_id, write_txn
from utils import ensure_citizen, fmt


def _now() -> int:
    return int(time.time())


class Contracts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="contract", invoke_without_command=True)
    async def contract(self, ctx):
        """Contract system. Use: !contract create | !contract sign | !contract list | !contract fulfill | !contract dispute"""
        await ctx.send("Use `!help contract` for subcommands.")

    @contract.command(name="create")
    async def create(self, ctx, member: discord.Member, contract_type: str, value: float, *, terms: str):
        """Create a draft contract with another user."""
        if member.bot or member.id == ctx.author.id:
            await ctx.send("Choose a valid human counterparty.")
            return
        if not math.isfinite(value) or value < 0:
            await ctx.send("Value must be a finite number (>= 0).")
            return
        if value > 1_000_000:
            await ctx.send("Contract value is too large.")
            return

        ensure_citizen(ctx.author.id)
        ensure_citizen(member.id)

        now = _now()
        contract_id = next_id("contracts")
        terms_json = json.dumps({"text": terms[:1500]})
        with write_txn():
            contracts.insert_one(
                {
                    "contract_id": contract_id,
                    "contract_type": contract_type.lower(),
                    "party_a_type": "citizen",
                    "party_a_id": ctx.author.id,
                    "party_b_type": "citizen",
                    "party_b_id": member.id,
                    "terms_json": terms_json,
                    "value": round(value, 2),
                    "status": "draft",
                    "signed_by_a": 0,
                    "signed_by_b": 0,
                    "created_at": now,
                    "last_event_at": now,
                }
            )
            contract_events.insert_one(
                {
                    "contract_id": contract_id,
                    "event_type": "created",
                    "payload_json": json.dumps({"by": ctx.author.id}),
                    "created_at": now,
                }
            )

        embed = discord.Embed(title="Contract created", color=discord.Color.green())
        embed.description = (
            f"Contract #{contract_id} created with {member.mention}.\n"
            f"Type: **{contract_type}** | Value: {fmt(value)}\n"
            f"Next: {member.mention} should run `!contract sign {contract_id}`."
        )
        await ctx.send(embed=embed)

    @contract.command(name="sign")
    async def sign(self, ctx, contract_id: int):
        """Sign a draft contract that involves you."""
        row = contracts.find_one(
            {"contract_id": contract_id},
            {"_id": 0, "contract_id": 1, "party_a_id": 1, "party_b_id": 1, "status": 1, "signed_by_a": 1, "signed_by_b": 1},
        )
        if row is None:
            await ctx.send("Contract not found.")
            return
        a_id = row.get("party_a_id")
        b_id = row.get("party_b_id")
        status = row.get("status")
        if ctx.author.id not in (a_id, b_id):
            await ctx.send("You are not a party to this contract.")
            return
        if status != "draft":
            await ctx.send("This contract is not in draft status.")
            return

        now = _now()
        signer_field = "signed_by_a" if ctx.author.id == a_id else "signed_by_b"
        with write_txn():
            sign_result = contracts.update_one(
                {"contract_id": contract_id, "status": "draft", signer_field: {"$ne": 1}},
                {"$set": {signer_field: 1, "last_event_at": now}},
            )
            if sign_result.modified_count == 0:
                await ctx.send("This contract is not in draft status.")
                return
            contract_events.insert_one(
                {
                    "contract_id": contract_id,
                    "event_type": "signed",
                    "payload_json": json.dumps({"by": ctx.author.id}),
                    "created_at": now,
                }
            )
            signed = contracts.find_one({"contract_id": contract_id}, {"_id": 0, "signed_by_a": 1, "signed_by_b": 1, "status": 1}) or {}
            if int(signed.get("signed_by_a") or 0) == 1 and int(signed.get("signed_by_b") or 0) == 1 and signed.get("status") == "draft":
                contracts.update_one(
                    {"contract_id": contract_id, "status": "draft"},
                    {
                        "$set": {
                            "status": "active",
                            "signed_at": now,
                            "start_at": now,
                            "end_at": now + 7 * 86400,
                            "last_event_at": now,
                        }
                    },
                )
                await ctx.send("✅ Contract signed and activated.")
                return

        await ctx.send("✅ Contract signed.")

    @contract.command(name="list")
    async def list_contracts(self, ctx):
        """List your contracts."""
        rows = list(
            contracts.find(
                {"$or": [{"party_a_id": ctx.author.id}, {"party_b_id": ctx.author.id}]},
                {"_id": 0, "contract_id": 1, "contract_type": 1, "party_a_id": 1, "party_b_id": 1, "value": 1, "status": 1, "end_at": 1},
            )
            .sort("contract_id", -1)
            .limit(15)
        )
        if not rows:
            await ctx.send("You have no contracts.")
            return

        embed = discord.Embed(title=f"{ctx.author.display_name}'s Contracts", color=discord.Color.blurple())
        for row in rows:
            cid = row.get("contract_id")
            ctype = row.get("contract_type")
            a_id = row.get("party_a_id")
            b_id = row.get("party_b_id")
            value = row.get("value", 0.0)
            status = row.get("status", "draft")
            end_at = int(row.get("end_at", 0) or 0)
            other = b_id if a_id == ctx.author.id else a_id
            embed.add_field(
                name=f"#{cid} — {ctype} ({status})",
                value=f"Counterparty: <@{other}> | Value: {fmt(value)} | Ends: <t:{int(end_at)}:R>",
                inline=False,
            )
        await ctx.send(embed=embed)

    @contract.command(name="fulfill")
    async def fulfill(self, ctx, contract_id: int):
        """Mark an active contract fulfilled (balanced: mutual honor system with audit trail)."""
        row = contracts.find_one(
            {"contract_id": contract_id},
            {"_id": 0, "contract_id": 1, "party_a_id": 1, "party_b_id": 1, "status": 1, "terms_json": 1},
        )
        if row is None:
            await ctx.send("Contract not found.")
            return
        a_id = row.get("party_a_id")
        b_id = row.get("party_b_id")
        status = row.get("status")
        if ctx.author.id not in (a_id, b_id):
            await ctx.send("You are not a party to this contract.")
            return
        if status != "active":
            await ctx.send("Contract is not active.")
            return

        now = _now()
        with write_txn():
            contracts.update_one(
                {"contract_id": contract_id},
                {"$set": {"status": "fulfilled", "last_event_at": now}},
            )
            contract_events.insert_one(
                {
                    "contract_id": contract_id,
                    "event_type": "fulfilled",
                    "payload_json": json.dumps({"by": ctx.author.id}),
                    "created_at": now,
                }
            )

        await ctx.send("✅ Contract marked fulfilled.")

    @contract.command(name="dispute")
    async def dispute(self, ctx, contract_id: int, *, reason: str):
        """Open a dispute on a contract (creates a record for admins/social trust impacts)."""
        row = contracts.find_one(
            {"contract_id": contract_id},
            {"_id": 0, "contract_id": 1, "party_a_id": 1, "party_b_id": 1, "status": 1},
        )
        if row is None:
            await ctx.send("Contract not found.")
            return
        a_id = row.get("party_a_id")
        b_id = row.get("party_b_id")
        if ctx.author.id not in (a_id, b_id):
            await ctx.send("You are not a party to this contract.")
            return

        now = _now()
        with write_txn():
            contracts.update_one(
                {"contract_id": contract_id},
                {"$set": {"status": "disputed", "last_event_at": now}},
            )
            contract_events.insert_one(
                {
                    "contract_id": contract_id,
                    "event_type": "disputed",
                    "payload_json": json.dumps({"by": ctx.author.id, "reason": reason[:500]}),
                    "created_at": now,
                }
            )

        await ctx.send("✅ Dispute recorded.")

    @contract.command(name="challenge")
    async def challenge(self, ctx):
        """Weekly social contract challenge."""
        embed = discord.Embed(title="Contract Challenge", color=discord.Color.blue())
        embed.description = (
            "Weekly challenge:\n"
            "- Create 2 contracts\n"
            "- Successfully fulfill at least 1\n"
            "- Avoid disputes\n\n"
            "Tip: use `!contract list` to monitor your progress."
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Contracts(bot))

