import time
import random
import discord
from discord.ext import commands
from db import cursor, conn
from utils import (
    ensure_citizen, get_citizen, log_tx, fmt,
    calculate_income_tax, get_job_level,
    EDUCATION_LEVELS, EDUCATION_COSTS, EDUCATION_SALARY_BONUS,
    add_gov_revenue, get_eco_state, safe_float, clamp, record_employment_event
)

JOBS = {
    "factory_worker":     {"name": "Factory Worker",     "cat": "Labor",        "skill": 1, "edu": "none",       "salary": (80, 150),    "cd": 3600},
    "delivery_driver":    {"name": "Delivery Driver",    "cat": "Labor",        "skill": 1, "edu": "none",       "salary": (60, 120),    "cd": 3600},
    "construction_worker":{"name": "Construction Worker","cat": "Labor",        "skill": 1, "edu": "none",       "salary": (100, 180),   "cd": 3600},
    "janitor":            {"name": "Janitor",            "cat": "Labor",        "skill": 1, "edu": "none",       "salary": (50, 100),    "cd": 3600},
    "electrician":        {"name": "Electrician",        "cat": "Skilled",      "skill": 2, "edu": "highschool", "salary": (150, 280),   "cd": 3600},
    "plumber":            {"name": "Plumber",            "cat": "Skilled",      "skill": 2, "edu": "highschool", "salary": (130, 250),   "cd": 3600},
    "mechanic":           {"name": "Mechanic",           "cat": "Skilled",      "skill": 2, "edu": "highschool", "salary": (140, 260),   "cd": 3600},
    "chef":               {"name": "Chef",               "cat": "Skilled",      "skill": 2, "edu": "highschool", "salary": (120, 220),   "cd": 3600},
    "software_engineer":  {"name": "Software Engineer",  "cat": "Professional", "skill": 3, "edu": "college",    "salary": (300, 600),   "cd": 3600},
    "accountant":         {"name": "Accountant",         "cat": "Professional", "skill": 3, "edu": "college",    "salary": (250, 450),   "cd": 3600},
    "nurse":              {"name": "Nurse",              "cat": "Professional", "skill": 3, "edu": "college",    "salary": (280, 500),   "cd": 3600},
    "lawyer":             {"name": "Lawyer",             "cat": "Professional", "skill": 4, "edu": "masters",    "salary": (450, 900),   "cd": 3600},
    "doctor":             {"name": "Doctor",             "cat": "Professional", "skill": 4, "edu": "masters",    "salary": (600, 1200),  "cd": 3600},
    "manager":            {"name": "Manager",            "cat": "Corporate",    "skill": 3, "edu": "college",    "salary": (400, 700),   "cd": 3600},
    "financial_analyst":  {"name": "Financial Analyst",  "cat": "Corporate",    "skill": 4, "edu": "college",    "salary": (450, 800),   "cd": 3600},
    "executive":          {"name": "Executive",          "cat": "Corporate",    "skill": 5, "edu": "masters",    "salary": (800, 1500),  "cd": 3600},
    "postal_worker":      {"name": "Postal Worker",      "cat": "Government",   "skill": 1, "edu": "none",       "salary": (200, 300),   "cd": 3600},
    "teacher":            {"name": "Teacher",            "cat": "Government",   "skill": 3, "edu": "college",    "salary": (220, 380),   "cd": 3600},
    "police_officer":     {"name": "Police Officer",     "cat": "Government",   "skill": 2, "edu": "highschool", "salary": (250, 400),   "cd": 3600},
    "freelance_designer": {"name": "Freelance Designer", "cat": "Freelance",    "skill": 2, "edu": "none",       "salary": (50, 500),    "cd": 2700},
    "content_creator":    {"name": "Content Creator",    "cat": "Freelance",    "skill": 1, "edu": "none",       "salary": (20, 800),    "cd": 2700},
    "consultant":         {"name": "Consultant",         "cat": "Freelance",    "skill": 4, "edu": "college",    "salary": (200, 1000),  "cd": 2700},
}

WORK_LINES = {
    "factory_worker":     ["You assembled components on the line", "You hit your production quota", "You ran the night shift"],
    "delivery_driver":    ["You delivered 40 packages", "You navigated rush hour traffic", "You completed your route early"],
    "construction_worker":["You laid concrete foundations", "You framed a new building", "You survived the heat on-site"],
    "janitor":            ["You deep-cleaned the office floors", "You maintained the building overnight", "You sanitised the facility"],
    "electrician":        ["You rewired a commercial unit", "You fixed a dangerous fault", "You installed a solar system"],
    "plumber":            ["You fixed a burst pipe", "You installed a new bathroom", "You cleared a blocked sewer"],
    "mechanic":           ["You rebuilt an engine", "You serviced 8 vehicles", "You diagnosed a tricky fault"],
    "chef":               ["You led the dinner service", "You perfected a new dish", "You survived a fully booked Saturday"],
    "software_engineer":  ["You shipped a new feature", "You squashed 12 bugs", "You optimised the database layer"],
    "accountant":         ["You reconciled the quarterly books", "You filed tax returns", "You audited a client's finances"],
    "nurse":              ["You cared for 15 patients", "You assisted in emergency triage", "You completed a 12-hour shift"],
    "lawyer":             ["You won a court case", "You negotiated a settlement", "You drafted a complex contract"],
    "doctor":             ["You performed a surgery", "You saved a critical patient", "You ran a full clinic day"],
    "manager":            ["You led your team to a record quarter", "You resolved a workplace conflict", "You onboarded new hires"],
    "financial_analyst":  ["You modelled a market scenario", "You issued a buy recommendation", "You built a risk report"],
    "executive":          ["You closed a major acquisition", "You presented to the board", "You signed a strategic deal"],
    "postal_worker":      ["You delivered the morning mail", "You sorted the overnight haul", "You processed 500 packages"],
    "teacher":            ["You taught a full day of classes", "You marked 30 essays", "You mentored a struggling student"],
    "police_officer":     ["You responded to 5 incidents", "You completed your patrol", "You made an important arrest"],
    "freelance_designer":  ["You delivered a logo rebrand", "You designed a full website", "You finished a client pitch deck"],
    "content_creator":    ["You went semi-viral", "You posted to all platforms", "You finished a video essay"],
    "consultant":         ["You advised a Fortune 500 firm", "You delivered a strategy report", "You ran a workshop"],
}

CAT_COLORS = {
    "Labor": discord.Color.light_grey(),
    "Skilled": discord.Color.blue(),
    "Professional": discord.Color.purple(),
    "Corporate": discord.Color.dark_blue(),
    "Government": discord.Color.green(),
    "Freelance": discord.Color.orange(),
}


class Jobs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def jobs(self, ctx, category: str = None):
        """List all available jobs. Filter by: labor, skilled, professional, corporate, government, freelance"""
        cats = {}
        for jid, j in JOBS.items():
            if category and j["cat"].lower() != category.lower():
                continue
            cats.setdefault(j["cat"], []).append((jid, j))

        if not cats:
            await ctx.send(f"No jobs found for category `{category}`.")
            return

        embed = discord.Embed(title="💼 Job Listings", color=discord.Color.blue())
        embed.description = "Use `!apply <job_id>` to apply for a job."
        for cat, job_list in cats.items():
            lines = []
            for jid, j in job_list:
                lines.append(
                    f"`{jid}` — **{j['name']}** | Salary: {fmt(j['salary'][0])}–{fmt(j['salary'][1])} "
                    f"| Skill Lv{j['skill']} | Edu: {j['edu'].title()}"
                )
            embed.add_field(name=f"📂 {cat}", value="\n".join(lines), inline=False)
        embed.set_footer(text="Higher skill/education unlocks better-paying roles. Use !train and !educate to qualify.")
        await ctx.send(embed=embed)

    @commands.command()
    async def apply(self, ctx, job_id: str):
        """Apply for a job. Usage: !apply <job_id>"""
        job_id = job_id.lower()
        if job_id not in JOBS:
            await ctx.send(f"Unknown job ID `{job_id}`. Use `!jobs` to see available positions.")
            return

        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)

        if c["job_id"] == job_id:
            await ctx.send("You already work this job! Use `!resign` first to switch.")
            return
        if c["job_id"]:
            await ctx.send(f"You already have a job as **{JOBS[c['job_id']]['name']}**. Use `!resign` to quit first.")
            return

        j = JOBS[job_id]
        if c["skill_level"] < j["skill"]:
            await ctx.send(f"This job requires Skill Level **{j['skill']}**. Yours is {c['skill_level']}. Use `!train` to improve.")
            return

        edu_idx = EDUCATION_LEVELS.index(c["education"])
        req_idx = EDUCATION_LEVELS.index(j["edu"])
        if edu_idx < req_idx:
            await ctx.send(f"This job requires **{j['edu'].title()}** education. Use `!educate` to qualify.")
            return

        cursor.execute(
            "UPDATE citizens SET job_id = ?, job_xp = 0, last_work = 0 WHERE user_id = ?",
            (job_id, ctx.author.id)
        )
        conn.commit()
        await ctx.send(f"✅ You've been hired as a **{j['name']}**! Use `!work` to clock in for your first shift.")

    @commands.command()
    async def resign(self, ctx):
        """Quit your current job."""
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)
        if not c["job_id"]:
            await ctx.send("You're not employed! Use `!jobs` to find a job.")
            return

        job_name = JOBS[c["job_id"]]["name"]
        cursor.execute("UPDATE citizens SET job_id = NULL, job_xp = 0, last_work = 0 WHERE user_id = ?",
                       (ctx.author.id,))
        conn.commit()
        await ctx.send(f"You've resigned from **{job_name}**. Your XP has been reset.")

    @commands.command()
    async def work(self, ctx):
        """Work your shift and earn your salary (deposited to bank after tax)."""
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)

        if not c["job_id"]:
            await ctx.send("You don't have a job! Use `!jobs` to browse and `!apply <job_id>` to get hired.")
            return

        j = JOBS[c["job_id"]]
        now = int(time.time())
        elapsed = now - c["last_work"]

        if elapsed < j["cd"]:
            remaining = j["cd"] - elapsed
            m, s = divmod(remaining, 60)
            await ctx.send(f"⏳ You're still on break. Next shift available in **{m}m {s}s**.")
            return

        lvl_num, lvl_title, multiplier, next_xp = get_job_level(c["job_xp"])
        edu_bonus = EDUCATION_SALARY_BONUS.get(c["education"], 1.0)
        inflation = safe_float(get_eco_state("inflation_rate") or 0.02, 0.02)
        consumer_conf = clamp(safe_float(get_eco_state("consumer_confidence") or 0.5, 0.5), 0.0, 1.0)
        phase = get_eco_state("economic_phase") or "stable"

        gross = random.uniform(j["salary"][0], j["salary"][1])
        # Balanced realism: inflation affects nominal wages modestly; confidence + phase affect hours/bonuses.
        nominal_adj = 1.0 + clamp(inflation, -0.05, 0.5) * 1.2
        phase_adj = {"boom": 1.08, "stable": 1.0, "recession": 0.92, "depression": 0.85}.get(phase, 1.0)
        conf_adj = 0.9 + consumer_conf * 0.2
        gross *= multiplier * edu_bonus * nominal_adj * phase_adj * conf_adj
        gross = round(gross, 2)

        tax = calculate_income_tax(gross)
        net = round(gross - tax, 2)
        new_xp = c["job_xp"] + 50

        cursor.execute(
            "UPDATE citizens SET bank = bank + ?, job_xp = ?, last_work = ? WHERE user_id = ?",
            (net, new_xp, now, ctx.author.id)
        )
        conn.commit()
        add_gov_revenue(tax)
        log_tx(ctx.author.id, "salary", net, f"{j['name']} shift pay (after tax)")
        record_employment_event(ctx.author.id, "worked", c["job_id"], f"net={net}")

        work_line = random.choice(WORK_LINES.get(c["job_id"], ["You completed your shift"]))

        lvl_num_new, lvl_title_new, _, _ = get_job_level(new_xp)
        promoted = lvl_title_new != lvl_title

        msg = (
            f"**{work_line}.**\n\n"
            f"💰 Gross: **{fmt(gross)}** | Tax: **{fmt(tax)}** | Net to Bank: **{fmt(net)}**\n"
            f"🎯 XP: **{new_xp}** | Title: **{lvl_title_new}**"
        )
        if next_xp:
            msg += f" | Next level at {next_xp} XP"
        if promoted:
            msg += f"\n\n🎉 **Promoted to {lvl_title_new}!** Your salary multiplier increased!"

        await ctx.send(msg)

    @commands.command()
    async def career(self, ctx):
        """View your career stats, job level, and salary info."""
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)

        if not c["job_id"]:
            await ctx.send("You're unemployed. Use `!jobs` and `!apply <job_id>` to find work.")
            return

        j = JOBS[c["job_id"]]
        lvl_num, lvl_title, multiplier, next_xp = get_job_level(c["job_xp"])
        edu_bonus = EDUCATION_SALARY_BONUS.get(c["education"], 1.0)

        eff_min = round(j["salary"][0] * multiplier * edu_bonus, 0)
        eff_max = round(j["salary"][1] * multiplier * edu_bonus, 0)

        embed = discord.Embed(title=f"💼 {ctx.author.display_name}'s Career", color=CAT_COLORS.get(j["cat"], discord.Color.blue()))
        embed.add_field(name="Job", value=j["name"], inline=True)
        embed.add_field(name="Category", value=j["cat"], inline=True)
        embed.add_field(name="Level", value=f"Lv{lvl_num} — {lvl_title}", inline=True)
        embed.add_field(name="Job XP", value=str(c["job_xp"]), inline=True)
        embed.add_field(name="Next Level", value=f"{next_xp} XP" if next_xp else "Max level!", inline=True)
        embed.add_field(name="Salary Range / Shift", value=f"{fmt(eff_min)} – {fmt(eff_max)}", inline=True)
        embed.add_field(name="Salary Multiplier", value=f"{multiplier:.2f}x (Lv{lvl_num})", inline=True)
        embed.add_field(name="Edu Bonus", value=f"{edu_bonus:.2f}x ({c['education'].title()})", inline=True)
        cd_min = j["cd"] // 60
        embed.add_field(name="Shift Cooldown", value=f"{cd_min} min", inline=True)
        await ctx.send(embed=embed)

    @commands.command()
    async def educate(self, ctx, level: str):
        """Upgrade your education. Levels: highschool, college, masters, phd"""
        level = level.lower()
        if level not in EDUCATION_COSTS:
            await ctx.send(f"Valid education levels: `highschool`, `college`, `masters`, `phd`")
            return

        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)
        current_idx = EDUCATION_LEVELS.index(c["education"])
        target_idx = EDUCATION_LEVELS.index(level)

        if target_idx <= current_idx:
            await ctx.send(f"You already have **{c['education'].title()}** or higher education.")
            return

        cost = EDUCATION_COSTS[level]
        if c["cash"] < cost:
            await ctx.send(f"You need **{fmt(cost)}** for {level} education. You have {fmt(c['cash'])}.")
            return

        cursor.execute("UPDATE citizens SET cash = cash - ?, education = ? WHERE user_id = ?",
                       (cost, level, ctx.author.id))
        conn.commit()
        log_tx(ctx.author.id, "education", -cost, f"Enrolled in {level} education")
        await ctx.send(
            f"🎓 You've completed **{level.title()}** education! Cost: {fmt(cost)}.\n"
            f"You now qualify for higher-paying jobs and earn a salary bonus."
        )

    @commands.command()
    async def train(self, ctx):
        """Improve your skill level (costs money). Max skill level: 5."""
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)

        if c["skill_level"] >= 5:
            await ctx.send("You've already reached the maximum skill level (5)!")
            return

        cost = c["skill_level"] * 1000
        if c["cash"] < cost:
            await ctx.send(f"Training costs **{fmt(cost)}**. You have {fmt(c['cash'])}.")
            return

        new_skill = c["skill_level"] + 1
        cursor.execute("UPDATE citizens SET cash = cash - ?, skill_level = ? WHERE user_id = ?",
                       (cost, new_skill, ctx.author.id))
        conn.commit()
        log_tx(ctx.author.id, "training", -cost, f"Skill training Lv{c['skill_level']} → Lv{new_skill}")
        await ctx.send(
            f"💪 Training complete! Skill Level: **{c['skill_level']} → {new_skill}**.\n"
            f"Cost: {fmt(cost)}. You now qualify for more advanced jobs!"
        )


async def setup(bot):
    await bot.add_cog(Jobs(bot))
