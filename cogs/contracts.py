import time
import json
import math
import discord
from discord.ext import commands
from db import cursor, conn
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

        terms_json = json.dumps({"text": terms[:1500]})
        cursor.execute(
            "INSERT INTO contracts(contract_type, party_a_type, party_a_id, party_b_type, party_b_id, terms_json, value, status, created_at, last_event_at) "
            "VALUES (?, 'citizen', ?, 'citizen', ?, ?, ?, 'draft', ?, ?)",
            (contract_type.lower(), ctx.author.id, member.id, terms_json, round(value, 2), _now(), _now()),
        )
        contract_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO contract_events(contract_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (contract_id, "created", json.dumps({"by": ctx.author.id}), _now()),
        )
        conn.commit()

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
        cursor.execute(
            "SELECT contract_id, party_a_id, party_b_id, status FROM contracts WHERE contract_id = ?",
            (contract_id,),
        )
        row = cursor.fetchone()
        if not row:
            await ctx.send("Contract not found.")
            return
        _, a_id, b_id, status = row
        if ctx.author.id not in (a_id, b_id):
            await ctx.send("You are not a party to this contract.")
            return
        if status != "draft":
            await ctx.send("This contract is not in draft status.")
            return

        cursor.execute(
            "UPDATE contracts SET status = 'active', signed_at = ?, start_at = ?, end_at = ?, last_event_at = ? WHERE contract_id = ?",
            (_now(), _now(), _now() + 7 * 86400, _now(), contract_id),
        )
        cursor.execute(
            "INSERT INTO contract_events(contract_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (contract_id, "signed", json.dumps({"by": ctx.author.id}), _now()),
        )
        conn.commit()

        await ctx.send("✅ Contract signed and activated.")

    @contract.command(name="list")
    async def list_contracts(self, ctx):
        """List your contracts."""
        cursor.execute(
            "SELECT contract_id, contract_type, party_a_id, party_b_id, value, status, end_at "
            "FROM contracts WHERE (party_a_id = ? OR party_b_id = ?) ORDER BY contract_id DESC LIMIT 15",
            (ctx.author.id, ctx.author.id),
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send("You have no contracts.")
            return

        embed = discord.Embed(title=f"{ctx.author.display_name}'s Contracts", color=discord.Color.blurple())
        for cid, ctype, a_id, b_id, value, status, end_at in rows:
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
        cursor.execute(
            "SELECT contract_id, party_a_id, party_b_id, status, terms_json FROM contracts WHERE contract_id = ?",
            (contract_id,),
        )
        row = cursor.fetchone()
        if not row:
            await ctx.send("Contract not found.")
            return
        _, a_id, b_id, status, terms_json = row
        if ctx.author.id not in (a_id, b_id):
            await ctx.send("You are not a party to this contract.")
            return
        if status != "active":
            await ctx.send("Contract is not active.")
            return

        cursor.execute(
            "UPDATE contracts SET status = 'fulfilled', last_event_at = ? WHERE contract_id = ?",
            (_now(), contract_id),
        )
        cursor.execute(
            "INSERT INTO contract_events(contract_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (contract_id, "fulfilled", json.dumps({"by": ctx.author.id}), _now()),
        )
        conn.commit()

        await ctx.send("✅ Contract marked fulfilled.")

    @contract.command(name="dispute")
    async def dispute(self, ctx, contract_id: int, *, reason: str):
        """Open a dispute on a contract (creates a record for admins/social trust impacts)."""
        cursor.execute(
            "SELECT contract_id, party_a_id, party_b_id, status FROM contracts WHERE contract_id = ?",
            (contract_id,),
        )
        row = cursor.fetchone()
        if not row:
            await ctx.send("Contract not found.")
            return
        _, a_id, b_id, status = row
        if ctx.author.id not in (a_id, b_id):
            await ctx.send("You are not a party to this contract.")
            return

        cursor.execute(
            "UPDATE contracts SET status = 'disputed', last_event_at = ? WHERE contract_id = ?",
            (_now(), contract_id),
        )
        cursor.execute(
            "INSERT INTO contract_events(contract_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (contract_id, "disputed", json.dumps({"by": ctx.author.id, "reason": reason[:500]}), _now()),
        )
        conn.commit()

        await ctx.send("✅ Dispute recorded.")


async def setup(bot):
    await bot.add_cog(Contracts(bot))

