import time
import discord
from discord.ext import commands
from db import cursor
from utils import ensure_citizen, get_reminder_pref, set_reminder_pref


class Reminders(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def reminders(self, ctx):
        ensure_citizen(ctx.author.id)
        prefs = get_reminder_pref(ctx.author.id)
        embed = discord.Embed(title="Reminder Preferences", color=discord.Color.teal())
        embed.add_field(name="DM alerts", value="On" if prefs["dm_enabled"] else "Off")
        embed.add_field(name="Daily ready", value="On" if prefs["daily_ready"] else "Off")
        embed.add_field(name="Work ready", value="On" if prefs["work_ready"] else "Off")
        embed.add_field(name="Quest ready", value="On" if prefs["quest_ready"] else "Off")
        embed.set_footer(text="Use !setreminder <dm|daily|work|quest> <on|off>")
        await ctx.send(embed=embed)

    @commands.command()
    async def setreminder(self, ctx, reminder_type: str, state: str):
        ensure_citizen(ctx.author.id)
        on = 1 if state.lower() in ("on", "true", "1", "yes") else 0
        rt = reminder_type.lower()
        if rt == "dm":
            set_reminder_pref(ctx.author.id, dm_enabled=on)
        elif rt == "daily":
            set_reminder_pref(ctx.author.id, daily_ready=on)
        elif rt == "work":
            set_reminder_pref(ctx.author.id, work_ready=on)
        elif rt == "quest":
            set_reminder_pref(ctx.author.id, quest_ready=on)
        else:
            await ctx.send("Invalid type. Use dm/daily/work/quest.")
            return
        await ctx.send(f"✅ `{rt}` reminders set to {'on' if on else 'off'}.")


async def setup(bot):
    await bot.add_cog(Reminders(bot))

