import math
import discord
from discord.ext import commands
from utils import ensure_citizen, get_trust, update_trust, clamp


class Trust(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="trust")
    async def trust(self, ctx, member: discord.Member):
        """View your trust score toward a member."""
        if member.bot:
            await ctx.send("Bots do not have trust profiles.")
            return
        ensure_citizen(ctx.author.id)
        ensure_citizen(member.id)
        score = get_trust(ctx.author.id, member.id)
        embed = discord.Embed(title="Trust Score", color=discord.Color.blurple())
        embed.description = f"Your trust toward {member.mention}: **{score:+.2f}** (range -1.0 to +1.0)"
        await ctx.send(embed=embed)

    @commands.command(name="vouch")
    async def vouch(self, ctx, member: discord.Member, amount: float = 0.1):
        """Vouch for a member (small positive trust). Usage: !vouch @user [0.1]"""
        if member.bot or member.id == ctx.author.id:
            await ctx.send("Choose a valid member.")
            return
        if not math.isfinite(amount) or amount <= 0:
            await ctx.send("Amount must be positive.")
            return
        delta = clamp(round(amount, 2), 0.05, 0.25)
        update_trust(ctx.author.id, member.id, delta, reason="vouched")
        await ctx.send(f"✅ You vouched for {member.mention} (+{delta:.2f} trust).")

    @commands.command(name="report")
    async def report(self, ctx, member: discord.Member, amount: float = 0.1):
        """Report a member (small negative trust). Usage: !report @user [0.1]"""
        if member.bot or member.id == ctx.author.id:
            await ctx.send("Choose a valid member.")
            return
        if not math.isfinite(amount) or amount <= 0:
            await ctx.send("Amount must be positive.")
            return
        delta = -clamp(round(amount, 2), 0.05, 0.25)
        update_trust(ctx.author.id, member.id, delta, reason="reported")
        await ctx.send(f"✅ Report recorded for {member.mention} ({delta:.2f} trust).")


async def setup(bot):
    await bot.add_cog(Trust(bot))

