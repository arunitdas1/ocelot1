import os
import gc
import ast
import time
import json
import traceback
from pathlib import Path

import discord
from discord.ext import commands

from db import admin_audit, active_events, citizens, conn, next_id, transactions
from utils import ensure_citizen, get_eco_state, set_eco_state, fmt


BACKUP_DIR = Path("backups")
BACKUP_DIR.mkdir(exist_ok=True)


class OwnerPanelView(discord.ui.View):
    def __init__(self, cog, owner_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This panel is restricted.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Economy Tools", style=discord.ButtonStyle.primary)
    async def econ_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="Owner Economy Tools", color=discord.Color.gold())
        embed.description = (
            "`!owaddcash @user <amount> [wallet|bank]`\n"
            "`!owsetbal @user <amount> [wallet|bank]`\n"
            "`!owresetecon @user CONFIRM`\n"
            "`!owresetall CONFIRM`\n"
            "`!owinject <total_amount> [wallet|bank]`\n"
            "`!owtotalmoney`"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Game Tools", style=discord.ButtonStyle.primary)
    async def game_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="Owner Game Tools", color=discord.Color.blue())
        embed.description = (
            "`!owresetcd @user`\n"
            "`!owresetcdall CONFIRM`\n"
            "`!owtrigger <market|event|cycle>`\n"
            "`!owsetmult <money|xp> <value>`\n"
            "`!owevents <on|off>`\n"
            "`!owrestartengine`"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Debug Tools", style=discord.ButtonStyle.secondary)
    async def debug_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="Owner Debug Tools", color=discord.Color.teal())
        embed.description = (
            "`!owstatus`\n"
            "`!owlogs [limit]`\n"
            "`!oweval <python_expr_or_code>`\n"
            "`!owsim <command_text>`"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Emergency", style=discord.ButtonStyle.danger)
    async def emergency_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="Owner Emergency Tools", color=discord.Color.red())
        embed.description = (
            "`!owmaintenance <on|off>`\n"
            "`!owfreezeecon <on|off>`\n"
            "`!owannounce <message>`\n"
            "`!owrollback <count> CONFIRM`"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class OwnerAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.owner_fallback_id = int(os.getenv("OWNER_ID", "0") or 0)

    async def _is_owner(self, user: discord.abc.User) -> bool:
        if self.owner_fallback_id and user.id == self.owner_fallback_id:
            return True
        try:
            return await self.bot.is_owner(user)
        except Exception:
            return False

    async def cog_check(self, ctx):
        if await self._is_owner(ctx.author):
            return True
        # Hide existence of owner commands from non-owner users.
        raise commands.CommandNotFound()

    def _audit(self, actor_id: int, action: str, details: str = ""):
        admin_audit.insert_one(
            {
                "audit_id": next_id("admin_audit"),
                "actor_id": actor_id,
                "action": action,
                "details": details[:1800],
                "created_at": int(time.time()),
            }
        )

    @commands.command(name="ownerpanel", hidden=True)
    async def ownerpanel(self, ctx):
        """Hidden owner control panel."""
        guilds = len(self.bot.guilds)
        users = sum((g.member_count or 0) for g in self.bot.guilds)
        latency_ms = round(self.bot.latency * 1000, 2)
        citizens_count = citizens.count_documents({})
        money = 0.0
        for row in citizens.find({}, {"_id": 0, "cash": 1, "bank": 1}):
            money += float(row.get("cash", 0.0) or 0.0) + float(row.get("bank", 0.0) or 0.0)

        embed = discord.Embed(title="Owner Control Panel", color=discord.Color.dark_gold())
        embed.description = "Private owner dashboard."
        embed.add_field(name="Latency", value=f"{latency_ms}ms", inline=True)
        embed.add_field(name="Servers", value=str(guilds), inline=True)
        embed.add_field(name="Users", value=str(users), inline=True)
        embed.add_field(name="Citizens", value=str(citizens_count), inline=True)
        embed.add_field(name="Money in circulation", value=fmt(money), inline=True)
        embed.add_field(name="Maintenance", value="ON" if get_eco_state("maintenance_mode") == "1" else "OFF", inline=True)
        embed.set_footer(text="Owner-only controls. All actions are audited.")

        dm = await ctx.author.create_dm()
        view = OwnerPanelView(self, ctx.author.id)
        await dm.send(embed=embed, view=view)
        await ctx.send("✅ Owner panel sent to your DM.")

    @commands.command(name="owhelp", hidden=True)
    async def owhelp(self, ctx):
        """Hidden owner cheat-sheet for all owner admin commands."""
        embed = discord.Embed(title="Owner Command Cheat-Sheet", color=discord.Color.dark_gold())
        embed.description = "Private owner-only command reference."
        embed.add_field(
            name="Economy Control",
            value=(
                "`!owaddcash @user <amount> [wallet|bank]`\n"
                "`!owsetbal @user <amount> [wallet|bank]`\n"
                "`!owresetecon @user CONFIRM`\n"
                "`!owresetall CONFIRM`\n"
                "`!owinject <total_amount> [wallet|bank]`\n"
                "`!owtotalmoney`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Database Control",
            value=(
                "`!owdbsave`\n"
                "`!owdbraw @user`\n"
                "`!owdbdelete <user_id> CONFIRM`\n"
                "`!owdbbackup`\n"
                "`!owdbrestore <filename> CONFIRM`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Game Control",
            value=(
                "`!owresetcd @user`\n"
                "`!owresetcdall CONFIRM`\n"
                "`!owtrigger <market|event|cycle>`\n"
                "`!owsetmult <money|xp> <value>`\n"
                "`!owevents <on|off>`\n"
                "`!owrestartengine`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Debug + Emergency",
            value=(
                "`!owstatus`\n"
                "`!owlogs [limit]`\n"
                "`!oweval <code>`\n"
                "`!owsim <command_text>`\n"
                "`!owmaintenance <on|off>`\n"
                "`!owfreezeecon <on|off>`\n"
                "`!owannounce <message>`\n"
                "`!owrollback <count> CONFIRM`"
            ),
            inline=False,
        )
        embed.set_footer(text="All owner actions are audited. Dangerous commands require CONFIRM.")
        await ctx.send(embed=embed)

    # Economy control
    @commands.command(name="owaddcash", hidden=True)
    async def owaddcash(self, ctx, member: discord.Member, amount: float, target: str = "wallet"):
        if amount == 0:
            await ctx.send("Amount must be non-zero.")
            return
        target = target.lower()
        if target not in {"wallet", "bank"}:
            await ctx.send("Target must be `wallet` or `bank`.")
            return
        ensure_citizen(member.id)
        col = "cash" if target == "wallet" else "bank"
        citizens.update_one({"user_id": member.id}, {"$inc": {col: round(amount, 2)}})
        self._audit(ctx.author.id, "owaddcash", f"user={member.id} amount={amount} target={target}")
        await ctx.send(f"✅ Updated {member.mention} {target} by {fmt(amount)}.")

    @commands.command(name="owsetbal", hidden=True)
    async def owsetbal(self, ctx, member: discord.Member, amount: float, target: str = "wallet"):
        target = target.lower()
        if target not in {"wallet", "bank"} or amount < 0:
            await ctx.send("Usage: `!owsetbal @user <amount>=0+ [wallet|bank]`")
            return
        ensure_citizen(member.id)
        col = "cash" if target == "wallet" else "bank"
        citizens.update_one({"user_id": member.id}, {"$set": {col: round(amount, 2)}})
        self._audit(ctx.author.id, "owsetbal", f"user={member.id} amount={amount} target={target}")
        await ctx.send(f"✅ Set {member.mention} {target} to {fmt(amount)}.")

    @commands.command(name="owresetecon", hidden=True)
    async def owresetecon(self, ctx, member: discord.Member, confirm: str = ""):
        if confirm != "CONFIRM":
            await ctx.send("Type `CONFIRM` to execute this reset.")
            return
        ensure_citizen(member.id)
        citizens.update_one(
            {"user_id": member.id},
            {
                "$set": {
                    "cash": 1000.0,
                    "bank": 0.0,
                    "debt": 0.0,
                    "credit_score": 650,
                    "skill_level": 1,
                    "education": "none",
                    "happiness": 75.0,
                    "job_id": None,
                    "job_xp": 0,
                    "last_work": 0,
                    "last_daily": 0,
                    "housing": "renting",
                    "last_expense": 0,
                }
            },
        )
        self._audit(ctx.author.id, "owresetecon", f"user={member.id}")
        await ctx.send(f"✅ Reset economy profile for {member.mention}.")

    @commands.command(name="owresetall", hidden=True)
    async def owresetall(self, ctx, confirm: str = ""):
        if confirm != "CONFIRM":
            await ctx.send("Type `CONFIRM` to execute global reset.")
            return
        citizens.update_many(
            {},
            {
                "$set": {
                    "cash": 1000.0,
                    "bank": 0.0,
                    "debt": 0.0,
                    "credit_score": 650,
                    "skill_level": 1,
                    "education": "none",
                    "happiness": 75.0,
                    "job_id": None,
                    "job_xp": 0,
                    "last_work": 0,
                    "last_daily": 0,
                    "housing": "renting",
                    "last_expense": 0,
                }
            },
        )
        self._audit(ctx.author.id, "owresetall", "global")
        await ctx.send("✅ Global economy reset completed.")

    @commands.command(name="owinject", hidden=True)
    async def owinject(self, ctx, total_amount: float, target: str = "wallet"):
        target = target.lower()
        if total_amount == 0 or target not in {"wallet", "bank"}:
            await ctx.send("Usage: `!owinject <total_amount> [wallet|bank]` (amount cannot be 0)")
            return
        users = [row.get("user_id") for row in citizens.find({}, {"_id": 0, "user_id": 1}) if row.get("user_id") is not None]
        if not users:
            await ctx.send("No citizens found.")
            return
        share = round(total_amount / len(users), 2)
        col = "cash" if target == "wallet" else "bank"
        for uid in users:
            citizens.update_one({"user_id": uid}, {"$inc": {col: share}})
        self._audit(ctx.author.id, "owinject", f"total={total_amount} target={target} users={len(users)}")
        await ctx.send(f"✅ Injected approx {fmt(total_amount)} across {len(users)} users ({fmt(share)} each).")

    @commands.command(name="owtotalmoney", hidden=True)
    async def owtotalmoney(self, ctx):
        money = 0.0
        for row in citizens.find({}, {"_id": 0, "cash": 1, "bank": 1}):
            money += float(row.get("cash", 0.0) or 0.0) + float(row.get("bank", 0.0) or 0.0)
        await ctx.send(f"✅ Total money in circulation: **{fmt(money)}**.")

    # Database control
    @commands.command(name="owdbsave", hidden=True)
    async def owdbsave(self, ctx):
        conn.commit()
        self._audit(ctx.author.id, "owdbsave", "")
        await ctx.send("✅ Database commit forced.")

    @commands.command(name="owdbraw", hidden=True)
    async def owdbraw(self, ctx, member: discord.Member):
        ensure_citizen(member.id)
        data = citizens.find_one({"user_id": member.id}, {"_id": 0}) or {}
        payload = json.dumps(data, indent=2, default=str)[:3900]
        embed = discord.Embed(title=f"Raw User Data: {member}", color=discord.Color.orange())
        embed.description = f"```json\n{payload}\n```"
        await ctx.send(embed=embed)

    @commands.command(name="owdbdelete", hidden=True)
    async def owdbdelete(self, ctx, user_id: int, confirm: str = ""):
        if confirm != "CONFIRM":
            await ctx.send("Type `CONFIRM` to delete the user record.")
            return
        citizens.delete_one({"user_id": user_id})
        self._audit(ctx.author.id, "owdbdelete", f"user_id={user_id}")
        await ctx.send("✅ User entry deleted.")

    @commands.command(name="owdbbackup", hidden=True)
    async def owdbbackup(self, ctx):
        self._audit(ctx.author.id, "owdbbackup", "mongo_noop")
        await ctx.send("✅ Mongo mode: SQLite file backup is unavailable here. Use `mongodump` for database backups.")

    @commands.command(name="owdbrestore", hidden=True)
    async def owdbrestore(self, ctx, filename: str, confirm: str = ""):
        if confirm != "CONFIRM":
            await ctx.send("Type `CONFIRM` to restore backup.")
            return
        self._audit(ctx.author.id, "owdbrestore", f"file={filename}")
        await ctx.send("✅ Mongo mode: SQLite file restore is unavailable. Restore via `mongorestore` from a Mongo backup.")

    # Game control
    @commands.command(name="owresetcd", hidden=True)
    async def owresetcd(self, ctx, member: discord.Member):
        ensure_citizen(member.id)
        citizens.update_one({"user_id": member.id}, {"$set": {"last_work": 0, "last_daily": 0}})
        self._audit(ctx.author.id, "owresetcd", f"user={member.id}")
        await ctx.send(f"✅ Cooldowns reset for {member.mention}.")

    @commands.command(name="owresetcdall", hidden=True)
    async def owresetcdall(self, ctx, confirm: str = ""):
        if confirm != "CONFIRM":
            await ctx.send("Type `CONFIRM` to reset all cooldowns.")
            return
        citizens.update_many({}, {"$set": {"last_work": 0, "last_daily": 0}})
        self._audit(ctx.author.id, "owresetcdall", "all")
        await ctx.send("✅ Cooldowns reset for all users.")

    @commands.command(name="owtrigger", hidden=True)
    async def owtrigger(self, ctx, target: str):
        target = target.lower()
        eco = self.bot.get_cog("EconomyEngine")
        if not eco:
            await ctx.send("Economy engine not loaded.")
            return
        if target == "market":
            await eco.simulate_market.coro(eco)
        elif target == "event":
            await eco.trigger_events.coro(eco)
        elif target == "cycle":
            await eco.process_economy.coro(eco)
        else:
            await ctx.send("Use `market`, `event`, or `cycle`.")
            return
        self._audit(ctx.author.id, "owtrigger", target)
        await ctx.send(f"✅ Triggered `{target}` cycle.")

    @commands.command(name="owsetmult", hidden=True)
    async def owsetmult(self, ctx, mult_type: str, value: float):
        mult_type = mult_type.lower()
        if value <= 0 or value > 10:
            await ctx.send("Multiplier must be > 0 and <= 10.")
            return
        if mult_type == "money":
            set_eco_state("global_money_multiplier", value)
        elif mult_type == "xp":
            set_eco_state("global_xp_multiplier", value)
        else:
            await ctx.send("Type must be `money` or `xp`.")
            return
        self._audit(ctx.author.id, "owsetmult", f"type={mult_type} value={value}")
        await ctx.send(f"✅ Set {mult_type} multiplier to `{value}`.")

    @commands.command(name="owevents", hidden=True)
    async def owevents(self, ctx, mode: str):
        mode = mode.lower()
        if mode not in {"on", "off"}:
            await ctx.send("Use `on` or `off`.")
            return
        set_eco_state("events_enabled", "1" if mode == "on" else "0")
        self._audit(ctx.author.id, "owevents", mode)
        await ctx.send(f"✅ Events system turned {mode.upper()}.")

    @commands.command(name="owrestartengine", hidden=True)
    async def owrestartengine(self, ctx):
        eco = self.bot.get_cog("EconomyEngine")
        if not eco:
            await ctx.send("Economy engine not loaded.")
            return
        eco.cog_unload()
        eco.simulate_market.start()
        eco.trigger_events.start()
        eco.process_economy.start()
        self._audit(ctx.author.id, "owrestartengine", "")
        await ctx.send("✅ Economy engine loops restarted.")

    # Debug tools
    @commands.command(name="owstatus", hidden=True)
    async def owstatus(self, ctx):
        uptime = time.time() - (self.bot.launch_time if hasattr(self.bot, "launch_time") else time.time())
        embed = discord.Embed(title="Owner Status", color=discord.Color.teal())
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000, 2)}ms", inline=True)
        embed.add_field(name="Guilds", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="GC objects", value=str(len(gc.get_objects())), inline=True)
        embed.add_field(name="Uptime (s)", value=f"{uptime:.0f}", inline=True)
        embed.add_field(name="Maintenance", value=get_eco_state("maintenance_mode") or "0", inline=True)
        embed.add_field(name="Economy frozen", value=get_eco_state("economy_frozen") or "0", inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="owlogs", hidden=True)
    async def owlogs(self, ctx, limit: int = 20):
        limit = max(1, min(50, limit))
        rows = list(
            admin_audit.find({}, {"_id": 0, "actor_id": 1, "action": 1, "details": 1, "created_at": 1})
            .sort("audit_id", -1)
            .limit(limit)
        )
        if not rows:
            await ctx.send("No audit logs yet.")
            return
        lines = []
        for row in rows:
            actor_id = row.get("actor_id")
            action = row.get("action")
            details = row.get("details", "")
            created_at = row.get("created_at", 0)
            lines.append(f"<t:{int(created_at)}:R> `{action}` by `{actor_id}` {details[:120]}")
        embed = discord.Embed(title="Admin Audit Logs", description="\n".join(lines), color=discord.Color.orange())
        await ctx.send(embed=embed)

    @commands.command(name="oweval", hidden=True)
    async def oweval(self, ctx, *, code: str):
        """
        Owner-only eval/exec.
        WARNING: still powerful; kept hidden and owner-restricted.
        """
        await ctx.send("Owner eval is permanently disabled in production-safe mode.")
        self._audit(ctx.author.id, "oweval_blocked", "disabled")

    @commands.command(name="owsim", hidden=True)
    async def owsim(self, ctx, *, command_text: str):
        """
        Dry-run simulation helper: parse and resolve a command without public execution.
        """
        if command_text.startswith(ctx.prefix):
            command_text = command_text[len(ctx.prefix):]
        name = command_text.split(" ")[0].lower()
        cmd = self.bot.get_command(name)
        if not cmd:
            await ctx.send("Command not found.")
            return
        embed = discord.Embed(title="Simulation Preview", color=discord.Color.blurple())
        embed.add_field(name="Resolved command", value=f"`{cmd.qualified_name}`", inline=False)
        embed.add_field(name="Input", value=f"`{command_text}`", inline=False)
        embed.add_field(name="Usage", value=f"`{ctx.prefix}{cmd.qualified_name} {cmd.signature}`", inline=False)
        await ctx.send(embed=embed)

    # Emergency controls
    @commands.command(name="owmaintenance", hidden=True)
    async def owmaintenance(self, ctx, mode: str):
        mode = mode.lower()
        if mode not in {"on", "off"}:
            await ctx.send("Use `on` or `off`.")
            return
        set_eco_state("maintenance_mode", "1" if mode == "on" else "0")
        self._audit(ctx.author.id, "owmaintenance", mode)
        await ctx.send(f"✅ Maintenance mode {mode.upper()}.")

    @commands.command(name="owfreezeecon", hidden=True)
    async def owfreezeecon(self, ctx, mode: str):
        mode = mode.lower()
        if mode not in {"on", "off"}:
            await ctx.send("Use `on` or `off`.")
            return
        set_eco_state("economy_frozen", "1" if mode == "on" else "0")
        self._audit(ctx.author.id, "owfreezeecon", mode)
        await ctx.send(f"✅ Economy freeze {mode.upper()}.")

    @commands.command(name="owannounce", hidden=True)
    async def owannounce(self, ctx, *, message: str):
        sent = 0
        for guild in self.bot.guilds:
            ch = guild.system_channel
            if ch and ch.permissions_for(guild.me).send_messages:
                try:
                    await ch.send(f"📢 **Owner Announcement:** {message}")
                    sent += 1
                except Exception:
                    pass
        self._audit(ctx.author.id, "owannounce", message[:200])
        await ctx.send(f"✅ Announcement sent to {sent} server channels.")

    @commands.command(name="oweventannounce", hidden=True)
    async def oweventannounce(self, ctx, event_id: int):
        """Broadcast a specific active event."""
        row = active_events.find_one({"event_id": event_id}, {"_id": 0, "name": 1, "description": 1, "ends_at": 1})
        if row is None:
            await ctx.send("Event not found.")
            return
        name = row.get("name")
        description = row.get("description", "")
        ends_at = row.get("ends_at", 0)
        sent = 0
        payload = (
            f"🎯 **Limited-Time Event:** {name}\n"
            f"{description}\n"
            f"Ends: <t:{int(ends_at)}:R>\n"
            f"Join with `!eventjoin {event_id}` and claim with `!eventrewards {event_id}`"
        )
        for guild in self.bot.guilds:
            ch = guild.system_channel
            if ch and ch.permissions_for(guild.me).send_messages:
                try:
                    await ch.send(payload)
                    sent += 1
                except Exception:
                    pass
        self._audit(ctx.author.id, "oweventannounce", f"event_id={event_id} sent={sent}")
        await ctx.send(f"✅ Event announcement sent to {sent} channels.")

    @commands.command(name="owrollback", hidden=True)
    async def owrollback(self, ctx, count: int, confirm: str = ""):
        if confirm != "CONFIRM":
            await ctx.send("Type `CONFIRM` to rollback transactions.")
            return
        count = max(1, min(100, count))
        rows = list(
            transactions.find({}, {"_id": 0, "tx_id": 1, "user_id": 1, "amount": 1})
            .sort("tx_id", -1)
            .limit(count)
        )
        if not rows:
            await ctx.send("No transactions to rollback.")
            return
        reversed_count = 0
        for row in rows:
            tx_id = row.get("tx_id")
            user_id = row.get("user_id")
            amount = row.get("amount", 0.0)
            ensure_citizen(user_id)
            # Best-effort rollback to wallet cash only (audit-safe, non-destructive to schema).
            citizens.update_one({"user_id": user_id}, {"$inc": {"cash": -float(amount)}})
            transactions.insert_one(
                {
                    "tx_id": next_id("transactions"),
                    "user_id": user_id,
                    "tx_type": "admin_rollback",
                    "amount": -float(amount),
                    "description": f"Rollback of tx_id {tx_id}",
                    "timestamp": int(time.time()),
                }
            )
            reversed_count += 1
        self._audit(ctx.author.id, "owrollback", f"count={reversed_count}")
        await ctx.send(f"✅ Rolled back {reversed_count} recent transactions.")


async def setup(bot):
    if not hasattr(bot, "launch_time"):
        bot.launch_time = time.time()
    await bot.add_cog(OwnerAdmin(bot))

