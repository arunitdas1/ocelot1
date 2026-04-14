import math
import time
import discord
from discord.ext import commands
from db import cursor, conn
from cogs.ui_components import PaginatorView
from utils import (
    ensure_citizen, get_citizen, log_tx, fmt,
    get_loan_interest_rate, credit_score_label,
    get_eco_state, add_gov_revenue
)


class Banking(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["dep"])
    async def deposit(self, ctx, amount: float):
        """Deposit cash from wallet into your bank. Usage: !deposit <amount>"""
        if not math.isfinite(amount) or amount <= 0:
            await ctx.send("Amount must be a positive finite number.")
            return
        amount = round(amount, 2)
        ensure_citizen(ctx.author.id)
        cursor.execute(
            "UPDATE citizens SET cash = cash - ? WHERE user_id = ? AND cash >= ?",
            (amount, ctx.author.id, amount)
        )
        if cursor.rowcount == 0:
            c = get_citizen(ctx.author.id)
            await ctx.send(f"Insufficient cash. Wallet: {fmt(c['cash'])}")
            return
        cursor.execute("UPDATE citizens SET bank = bank + ? WHERE user_id = ?", (amount, ctx.author.id))
        conn.commit()
        log_tx(ctx.author.id, "deposit", -amount, "Deposited to bank")
        c2 = get_citizen(ctx.author.id)
        await ctx.send(f"🏦 Deposited **{fmt(amount)}** to your bank.\nWallet: {fmt(c2['cash'])} | Bank: {fmt(c2['bank'])}")

    @commands.command(aliases=["wd"])
    async def withdraw(self, ctx, amount: float):
        """Withdraw cash from your bank to wallet. Usage: !withdraw <amount>"""
        if not math.isfinite(amount) or amount <= 0:
            await ctx.send("Amount must be a positive finite number.")
            return
        amount = round(amount, 2)
        ensure_citizen(ctx.author.id)
        cursor.execute(
            "UPDATE citizens SET bank = bank - ? WHERE user_id = ? AND bank >= ?",
            (amount, ctx.author.id, amount)
        )
        if cursor.rowcount == 0:
            c = get_citizen(ctx.author.id)
            await ctx.send(f"Insufficient bank funds. Bank: {fmt(c['bank'])}")
            return
        cursor.execute("UPDATE citizens SET cash = cash + ? WHERE user_id = ?", (amount, ctx.author.id))
        conn.commit()
        log_tx(ctx.author.id, "withdrawal", amount, "Withdrawn from bank")
        c2 = get_citizen(ctx.author.id)
        await ctx.send(f"💵 Withdrew **{fmt(amount)}** to your wallet.\nWallet: {fmt(c2['cash'])} | Bank: {fmt(c2['bank'])}")

    @commands.command(aliases=["borrow"])
    async def loan(self, ctx, amount: float):
        """Apply for a personal loan. Interest rate based on credit score. Usage: !loan <amount>"""
        if not math.isfinite(amount) or amount <= 0:
            await ctx.send("Loan amount must be a positive finite number.")
            return
        if amount > 50000:
            await ctx.send("Maximum personal loan is $50,000.")
            return

        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)

        if c["credit_score"] < 500:
            await ctx.send(f"Your credit score ({c['credit_score']}) is too low to qualify for a loan. Minimum: 500.")
            return

        cursor.execute("SELECT COUNT(*) FROM loans WHERE borrower_id = ? AND status = 'active'", (ctx.author.id,))
        active_loans = cursor.fetchone()[0]
        if active_loans >= 3:
            await ctx.send("You already have 3 active loans. Repay existing loans before applying for more.")
            return

        max_loan = c["credit_score"] * 50
        if amount > max_loan:
            await ctx.send(f"Based on your credit score, maximum loan is {fmt(max_loan)}.")
            return

        rate = get_loan_interest_rate(c["credit_score"])
        weekly_payment = round((amount * rate / 52) + (amount / 52), 2)
        total_repayable = round(weekly_payment * 52, 2)

        embed = discord.Embed(title="🏦 Loan Offer", color=discord.Color.green())
        embed.add_field(name="Principal", value=fmt(amount), inline=True)
        embed.add_field(name="Annual Interest Rate", value=f"{rate*100:.2f}%", inline=True)
        embed.add_field(name="Weekly Payment", value=fmt(weekly_payment), inline=True)
        embed.add_field(name="Total Repayable", value=fmt(total_repayable), inline=True)
        embed.add_field(name="Credit Score", value=f"{c['credit_score']} ({credit_score_label(c['credit_score'])})", inline=True)
        embed.set_footer(text="Loan is disbursed instantly — see details below.")
        await ctx.send(embed=embed)

        cursor.execute(
            "INSERT INTO loans(borrower_id, principal, remaining, interest_rate, weekly_payment, issued_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ctx.author.id, amount, amount, rate, weekly_payment, int(time.time()))
        )
        cursor.execute("UPDATE citizens SET cash = cash + ?, debt = debt + ? WHERE user_id = ?",
                       (amount, amount, ctx.author.id))
        cursor.execute("UPDATE citizens SET credit_score = MAX(300, credit_score - 10) WHERE user_id = ?",
                       (ctx.author.id,))
        conn.commit()
        log_tx(ctx.author.id, "loan_received", amount, f"Loan at {rate*100:.2f}% interest")
        await ctx.send(f"✅ Loan of **{fmt(amount)}** disbursed to your wallet. Repay with `!repay <amount>`.")

    @commands.command(aliases=["payloan"])
    async def repay(self, ctx, amount: float):
        """Repay an active loan from your wallet. Usage: !repay <amount>"""
        if not math.isfinite(amount) or amount <= 0:
            await ctx.send("Repayment amount must be a positive finite number.")
            return
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)
        if c["cash"] < amount:
            await ctx.send(f"Not enough cash. Wallet: {fmt(c['cash'])}")
            return

        cursor.execute(
            "SELECT loan_id, remaining FROM loans WHERE borrower_id = ? AND status = 'active' ORDER BY issued_at ASC LIMIT 1",
            (ctx.author.id,)
        )
        loan = cursor.fetchone()
        if not loan:
            await ctx.send("You have no active loans to repay.")
            return

        loan_id, remaining = loan
        actual = min(amount, remaining)
        new_remaining = round(remaining - actual, 2)

        if new_remaining <= 0:
            cursor.execute("UPDATE loans SET remaining = 0, status = 'paid' WHERE loan_id = ?", (loan_id,))
            cursor.execute("UPDATE citizens SET credit_score = MIN(850, credit_score + 25) WHERE user_id = ?", (ctx.author.id,))
            status_msg = f"🎉 Loan fully repaid! Credit score improved."
        else:
            cursor.execute("UPDATE loans SET remaining = ?, last_payment = ? WHERE loan_id = ?",
                           (new_remaining, int(time.time()), loan_id))
            cursor.execute("UPDATE citizens SET credit_score = MIN(850, credit_score + 5) WHERE user_id = ?", (ctx.author.id,))
            status_msg = f"Remaining balance: {fmt(new_remaining)}"

        cursor.execute("UPDATE citizens SET cash = cash - ?, debt = MAX(0, debt - ?) WHERE user_id = ?",
                       (actual, actual, ctx.author.id))
        conn.commit()
        log_tx(ctx.author.id, "loan_repayment", -actual, f"Loan repayment")
        await ctx.send(f"✅ Repaid **{fmt(actual)}**. {status_msg}")

    @commands.command(aliases=["loanlist"])
    async def loans(self, ctx):
        """View all your active loans."""
        ensure_citizen(ctx.author.id)
        cursor.execute(
            "SELECT loan_id, principal, remaining, interest_rate, weekly_payment, issued_at FROM loans "
            "WHERE borrower_id = ? AND status = 'active'",
            (ctx.author.id,)
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send("You have no active loans. 🎉")
            return

        import datetime
        pages = []
        chunk_size = 5
        for idx in range(0, len(rows), chunk_size):
            embed = discord.Embed(title="📋 Active Loans", color=discord.Color.red())
            for loan_id, principal, remaining, rate, weekly, issued in rows[idx:idx + chunk_size]:
                issued_str = datetime.datetime.fromtimestamp(issued).strftime("%Y-%m-%d")
                embed.add_field(
                    name=f"Loan #{loan_id} — {fmt(remaining)} remaining",
                    value=(
                        f"Original: {fmt(principal)} | Rate: {rate*100:.2f}%\n"
                        f"Weekly Payment: {fmt(weekly)} | Issued: {issued_str}"
                    ),
                    inline=False
                )
            pages.append(embed)
        if len(pages) == 1:
            await ctx.send(embed=pages[0])
            return
        view = PaginatorView(ctx.author.id, pages)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    @commands.command()
    async def credit(self, ctx):
        """View your credit score and borrowing power."""
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)
        rate = get_loan_interest_rate(c["credit_score"])
        max_loan = c["credit_score"] * 50

        embed = discord.Embed(title="💳 Credit Report", color=discord.Color.blue())
        embed.add_field(name="Credit Score", value=f"**{c['credit_score']}** — {credit_score_label(c['credit_score'])}", inline=False)
        embed.add_field(name="Available Loan Rate", value=f"{rate*100:.2f}% per year", inline=True)
        embed.add_field(name="Max Loan Eligible", value=fmt(max_loan), inline=True)
        embed.add_field(name="Current Debt", value=fmt(c["debt"]), inline=True)
        embed.set_footer(text="Repay loans on time to improve your credit score.")
        await ctx.send(embed=embed)

    @commands.command()
    async def bankinfo(self, ctx):
        """View current bank interest rates and monetary policy."""
        base_rate = float(get_eco_state("base_interest_rate") or 0.05)
        inflation = float(get_eco_state("inflation_rate") or 0.02)
        phase = get_eco_state("economic_phase") or "stable"

        embed = discord.Embed(title="🏛️ Central Bank Report", color=discord.Color.dark_gold())
        embed.add_field(name="Base Interest Rate", value=f"{base_rate*100:.2f}%", inline=True)
        embed.add_field(name="Inflation Rate", value=f"{inflation*100:.2f}%", inline=True)
        embed.add_field(name="Economic Phase", value=phase.capitalize(), inline=True)
        embed.add_field(name="Personal Loan Rates", value=(
            "500-579 credit: Very High\n"
            "580-669 credit: High\n"
            "670-739 credit: Moderate\n"
            "740-799 credit: Low\n"
            "800+ credit: Very Low"
        ), inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Banking(bot))
