import discord
from discord.ext import commands
from db import cursor, conn
from utils import ensure_citizen, get_citizen, log_tx, fmt
from cogs.business import get_biz
import math

MAX_IPO_SHARE_PRICE = 10000.0
MAX_SHARE_TRADE_QTY = 100000


def get_portfolio(user_id, biz_id):
    cursor.execute("SELECT shares, avg_buy_price FROM portfolios WHERE user_id = ? AND biz_id = ?", (user_id, biz_id))
    row = cursor.fetchone()
    return row if row else (0, 0.0)


class Stocks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def stocks(self, ctx):
        """View all publicly listed companies on the stock exchange."""
        cursor.execute(
            "SELECT biz_id, name, type, share_price, shares_issued, revenue, reputation "
            "FROM businesses WHERE is_public = 1 AND is_bankrupt = 0 ORDER BY share_price DESC"
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send("No companies are publicly listed yet. Business owners can use `!ipo` to go public.")
            return

        embed = discord.Embed(title="📈 Stock Exchange", color=discord.Color.green())
        embed.description = "Use `!invest <biz_name> <shares>` to buy stock."
        for biz_id, name, btype, price, shares, revenue, rep in rows:
            market_cap = price * shares
            embed.add_field(
                name=f"{name} ({btype.title()})",
                value=(
                    f"Price: **{fmt(price)}** | Market Cap: {fmt(market_cap)}\n"
                    f"Shares: {shares:,} | Revenue: {fmt(revenue)} | Rep: {rep}/100"
                ),
                inline=False
            )
        await ctx.send(embed=embed)

    @commands.command()
    async def ipo(self, ctx, shares: int, price: float):
        """List your business on the stock exchange. Usage: !ipo <shares> <price_per_share>"""
        if shares <= 0 or not math.isfinite(price) or price <= 0:
            await ctx.send("Shares and price must be positive.")
            return
        if shares > 1000000:
            await ctx.send("Maximum IPO shares: 1,000,000.")
            return
        if price > MAX_IPO_SHARE_PRICE:
            await ctx.send(f"IPO share price cannot exceed {fmt(MAX_IPO_SHARE_PRICE)}.")
            return

        ensure_citizen(ctx.author.id)
        biz = get_biz(owner_id=ctx.author.id)
        if not biz:
            await ctx.send("You don't own a business to take public.")
            return
        if biz["is_public"]:
            await ctx.send(f"**{biz['name']}** is already publicly listed.")
            return
        if biz["reputation"] < 30:
            await ctx.send("Your business reputation is too low for an IPO (minimum 30).")
            return

        ipo_cost = 2000
        if biz["cash"] < ipo_cost:
            await ctx.send(f"Your business needs {fmt(ipo_cost)} to cover IPO fees.")
            return

        # IPO should not mint money upfront; funds arrive only when shares are sold.
        cursor.execute(
            "UPDATE businesses SET is_public = 1, shares_issued = ?, share_price = ?, cash = cash - ? WHERE biz_id = ?",
            (shares, price, ipo_cost, biz["biz_id"])
        )
        cursor.execute(
            "INSERT OR IGNORE INTO portfolios(user_id, biz_id, shares, avg_buy_price) VALUES (?, ?, 0, 0)",
            (ctx.author.id, biz["biz_id"])
        )
        conn.commit()
        await ctx.send(
            f"🎉 **{biz['name']}** has gone public!\n"
            f"{shares:,} shares listed at {fmt(price)}/share.\n"
            f"Potential market cap: **{fmt(shares * price)}** | IPO fee: {fmt(ipo_cost)}"
        )

    @commands.command()
    async def invest(self, ctx, biz_name: str, shares: int):
        """Buy shares in a public company. Usage: !invest <biz_name> <shares>"""
        if shares <= 0:
            await ctx.send("Share quantity must be positive.")
            return
        if shares > MAX_SHARE_TRADE_QTY:
            await ctx.send(f"Max shares per trade is {MAX_SHARE_TRADE_QTY:,}.")
            return

        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)
        biz = get_biz(name=biz_name)

        if not biz:
            await ctx.send(f"Company `{biz_name}` not found.")
            return
        if not biz["is_public"]:
            await ctx.send(f"**{biz['name']}** is not publicly listed.")
            return

        cursor.execute("SELECT COALESCE(SUM(shares), 0) FROM portfolios WHERE biz_id = ?", (biz["biz_id"],))
        shares_held = cursor.fetchone()[0]
        available = biz["shares_issued"] - shares_held
        if shares > available:
            await ctx.send(f"Only {available:,} shares available for purchase (of {biz['shares_issued']:,} total issued).")
            return

        total_cost = round(shares * biz["share_price"], 2)
        if c["cash"] < total_cost:
            await ctx.send(f"Insufficient funds. Cost: {fmt(total_cost)}. Wallet: {fmt(c['cash'])}.")
            return

        current_shares, avg_price = get_portfolio(ctx.author.id, biz["biz_id"])
        new_shares = current_shares + shares
        new_avg = round(((current_shares * avg_price) + total_cost) / new_shares, 4)

        cursor.execute(
            "UPDATE citizens SET cash = cash - ? WHERE user_id = ? AND cash >= ?",
            (total_cost, ctx.author.id, total_cost)
        )
        if cursor.rowcount == 0:
            latest = get_citizen(ctx.author.id)
            await ctx.send(f"Insufficient funds. Cost: {fmt(total_cost)}. Wallet: {fmt(latest['cash'])}.")
            return
        # IPO share purchases fund the business treasury.
        cursor.execute("UPDATE businesses SET cash = cash + ? WHERE biz_id = ?", (total_cost, biz["biz_id"]))
        cursor.execute(
            "INSERT OR REPLACE INTO portfolios(user_id, biz_id, shares, avg_buy_price) VALUES (?, ?, ?, ?)",
            (ctx.author.id, biz["biz_id"], new_shares, new_avg)
        )
        cursor.execute(
            "UPDATE businesses SET share_price = share_price * 1.005 WHERE biz_id = ?",
            (biz["biz_id"],)
        )
        conn.commit()
        log_tx(ctx.author.id, "stock_buy", -total_cost, f"Bought {shares} shares of {biz['name']}")
        await ctx.send(
            f"📈 Bought **{shares:,} shares** of **{biz['name']}** at {fmt(biz['share_price'])}/share.\n"
            f"Total cost: {fmt(total_cost)} | Avg buy price: {fmt(new_avg)}"
        )

    @commands.command()
    async def divest(self, ctx, biz_name: str, shares: int):
        """Sell shares you own. Usage: !divest <biz_name> <shares>"""
        if shares <= 0:
            await ctx.send("Share quantity must be positive.")
            return
        if shares > MAX_SHARE_TRADE_QTY:
            await ctx.send(f"Max shares per trade is {MAX_SHARE_TRADE_QTY:,}.")
            return

        ensure_citizen(ctx.author.id)
        biz = get_biz(name=biz_name)
        if not biz:
            await ctx.send(f"Company `{biz_name}` not found.")
            return
        if not biz["is_public"]:
            await ctx.send(f"**{biz['name']}** is not listed.")
            return

        current_shares, avg_price = get_portfolio(ctx.author.id, biz["biz_id"])
        if current_shares < shares:
            await ctx.send(f"You only own {current_shares:,} shares of **{biz['name']}**.")
            return

        proceeds = round(shares * biz["share_price"], 2)
        cost_basis = round(shares * avg_price, 2)
        gain_loss = round(proceeds - cost_basis, 2)
        capital_gains_tax = round(max(0, gain_loss) * 0.15, 2)
        net_proceeds = round(proceeds - capital_gains_tax, 2)

        new_shares = current_shares - shares
        if new_shares == 0:
            cursor.execute("DELETE FROM portfolios WHERE user_id = ? AND biz_id = ?", (ctx.author.id, biz["biz_id"]))
        else:
            cursor.execute("UPDATE portfolios SET shares = ? WHERE user_id = ? AND biz_id = ?",
                           (new_shares, ctx.author.id, biz["biz_id"]))

        cursor.execute("UPDATE citizens SET cash = cash + ? WHERE user_id = ?", (net_proceeds, ctx.author.id))
        cursor.execute("UPDATE businesses SET share_price = share_price * 0.995 WHERE biz_id = ?", (biz["biz_id"],))
        conn.commit()
        log_tx(ctx.author.id, "stock_sell", net_proceeds, f"Sold {shares} shares of {biz['name']}")

        gl_str = f"+{fmt(gain_loss)}" if gain_loss >= 0 else fmt(gain_loss)
        await ctx.send(
            f"📉 Sold **{shares:,} shares** of **{biz['name']}** at {fmt(biz['share_price'])}/share.\n"
            f"Proceeds: {fmt(proceeds)} | P&L: {gl_str} | Capital Gains Tax: {fmt(capital_gains_tax)} | Net: {fmt(net_proceeds)}"
        )

    @commands.command()
    async def portfolio(self, ctx, member: discord.Member = None):
        """View your stock portfolio."""
        target = member or ctx.author
        ensure_citizen(target.id)

        cursor.execute(
            "SELECT p.biz_id, b.name, p.shares, p.avg_buy_price, b.share_price "
            "FROM portfolios p JOIN businesses b ON p.biz_id = b.biz_id "
            "WHERE p.user_id = ? AND p.shares > 0",
            (target.id,)
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send(f"{'Your' if target == ctx.author else target.display_name + chr(39) + 's'} portfolio is empty.")
            return

        total_value = 0.0
        total_cost = 0.0
        embed = discord.Embed(title=f"📊 {target.display_name}'s Portfolio", color=discord.Color.gold())
        for biz_id, name, shares, avg_buy, current_price in rows:
            value = shares * current_price
            cost = shares * avg_buy
            gl = value - cost
            gl_str = f"+{fmt(gl)}" if gl >= 0 else fmt(gl)
            total_value += value
            total_cost += cost
            embed.add_field(
                name=f"{name} — {shares:,} shares",
                value=f"Avg: {fmt(avg_buy)} | Current: {fmt(current_price)} | Value: {fmt(value)} | P&L: {gl_str}",
                inline=False
            )

        total_gl = total_value - total_cost
        gl_str = f"+{fmt(total_gl)}" if total_gl >= 0 else fmt(total_gl)
        embed.add_field(name="📈 Total Portfolio Value", value=fmt(total_value), inline=True)
        embed.add_field(name="Total P&L", value=gl_str, inline=True)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Stocks(bot))
