import discord
from discord.ext import commands
from db import businesses, citizens, portfolios, season_meta, season_stats, write_txn
from utils import ensure_citizen, get_citizen, log_tx, fmt, add_gov_revenue
from cogs.business import get_biz
import math
from cogs.ui_components import PaginatorView

MAX_IPO_SHARE_PRICE = 10000.0
MAX_SHARE_TRADE_QTY = 100000


def get_portfolio(user_id, biz_id):
    row = portfolios.find_one({"user_id": user_id, "biz_id": biz_id}, {"_id": 0, "shares": 1, "avg_buy_price": 1})
    if not row:
        return (0, 0.0)
    return (row.get("shares", 0), row.get("avg_buy_price", 0.0))


class Stocks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["exchange"])
    async def stocks(self, ctx):
        """View all publicly listed companies on the stock exchange."""
        rows = list(
            businesses.find(
                {"is_public": 1, "is_bankrupt": 0},
                {"_id": 0, "biz_id": 1, "name": 1, "type": 1, "share_price": 1, "shares_issued": 1, "revenue": 1, "reputation": 1},
            ).sort("share_price", -1)
        )
        if not rows:
            await ctx.send("No companies are publicly listed yet. Business owners can use `!ipo` to go public.")
            return

        pages = []
        chunk_size = 6
        for idx in range(0, len(rows), chunk_size):
            embed = discord.Embed(title="📈 Stock Exchange", color=discord.Color.green())
            embed.description = "Use `!invest <biz_name> <shares>` to buy stock."
            for row in rows[idx:idx + chunk_size]:
                name = row["name"]
                btype = row["type"]
                price = row["share_price"]
                shares = row["shares_issued"]
                revenue = row["revenue"]
                rep = row["reputation"]
                market_cap = price * shares
                embed.add_field(
                    name=f"{name} ({btype.title()})",
                    value=(
                        f"Price: **{fmt(price)}** | Market Cap: {fmt(market_cap)}\n"
                        f"Shares: {shares:,} | Revenue: {fmt(revenue)} | Rep: {rep}/100"
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
        with write_txn():
            businesses.update_one(
                {"biz_id": biz["biz_id"]},
                {"$set": {"is_public": 1, "shares_issued": shares, "share_price": price}, "$inc": {"cash": -ipo_cost}},
            )
            portfolios.update_one(
                {"user_id": ctx.author.id, "biz_id": biz["biz_id"]},
                {"$setOnInsert": {"user_id": ctx.author.id, "biz_id": biz["biz_id"], "shares": 0, "avg_buy_price": 0.0}},
                upsert=True,
            )
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
        biz = get_biz(name=biz_name)

        if not biz:
            await ctx.send(f"Company `{biz_name}` not found.")
            return
        if not biz["is_public"]:
            await ctx.send(f"**{biz['name']}** is not publicly listed.")
            return
        if biz["owner_id"] == ctx.author.id:
            await ctx.send("You cannot buy shares of your own company.")
            return

        total_cost = round(shares * biz["share_price"], 2)

        with write_txn():
            debit = citizens.find_one_and_update(
                {"user_id": ctx.author.id, "cash": {"$gte": total_cost}},
                {"$inc": {"cash": -total_cost}},
            )
            if not debit:
                latest = get_citizen(ctx.author.id)
                await ctx.send(f"Insufficient funds. Cost: {fmt(total_cost)}. Wallet: {fmt(latest['cash'])}.")
                return
            # Enforce issued-share cap atomically at business document level.
            reserved = businesses.find_one_and_update(
                {
                    "biz_id": biz["biz_id"],
                    "$expr": {
                        "$gte": [
                            {"$subtract": ["$shares_issued", {"$ifNull": ["$shares_sold", 0]}]},
                            int(shares),
                        ]
                    },
                },
                {"$inc": {"cash": total_cost, "shares_sold": int(shares)}},
            )
            if not reserved:
                citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": total_cost}})
                await ctx.send("Not enough shares available right now. Please try again.")
                return
            pf = portfolios.find_one(
                {"user_id": ctx.author.id, "biz_id": biz["biz_id"]},
                {"_id": 0, "shares": 1, "avg_buy_price": 1},
            )
            current_shares = int((pf or {}).get("shares", 0) or 0)
            avg_price = float((pf or {}).get("avg_buy_price", 0.0) or 0.0)
            new_shares = current_shares + shares
            new_avg = round(((current_shares * avg_price) + total_cost) / new_shares, 4)
            if pf:
                pf_updated = portfolios.update_one(
                    {
                        "user_id": ctx.author.id,
                        "biz_id": biz["biz_id"],
                        "shares": current_shares,
                        "avg_buy_price": avg_price,
                    },
                    {"$set": {"shares": new_shares, "avg_buy_price": new_avg}},
                )
                if pf_updated.modified_count == 0:
                    citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": total_cost}})
                    businesses.update_one({"biz_id": biz["biz_id"]}, {"$inc": {"cash": -total_cost, "shares_sold": -int(shares)}})
                    await ctx.send("Portfolio changed concurrently. Trade was rolled back; try again.")
                    return
            else:
                created = portfolios.update_one(
                    {"user_id": ctx.author.id, "biz_id": biz["biz_id"]},
                    {"$setOnInsert": {"user_id": ctx.author.id, "biz_id": biz["biz_id"], "shares": new_shares, "avg_buy_price": new_avg}},
                    upsert=True,
                )
                if created.upserted_id is None and created.modified_count == 0:
                    citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": total_cost}})
                    businesses.update_one({"biz_id": biz["biz_id"]}, {"$inc": {"cash": -total_cost, "shares_sold": -int(shares)}})
                    await ctx.send("Portfolio changed concurrently. Trade was rolled back; try again.")
                    return
            businesses.update_one({"biz_id": biz["biz_id"]}, {"$mul": {"share_price": 1.005}})
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
        if float(biz["cash"]) < proceeds:
            await ctx.send(
                f"Insufficient market liquidity for this sale right now. "
                f"{biz['name']} treasury has {fmt(biz['cash'])}, but sale needs {fmt(proceeds)}."
            )
            return

        new_shares = current_shares - shares
        with write_txn():
            paid = businesses.find_one_and_update(
                {"biz_id": biz["biz_id"], "cash": {"$gte": proceeds}},
                {"$inc": {"cash": -proceeds, "shares_sold": -int(shares)}},
            )
            if not paid:
                await ctx.send("Sale failed due to concurrent liquidity change. Try again.")
                return
            sold = portfolios.update_one(
                {"user_id": ctx.author.id, "biz_id": biz["biz_id"], "shares": {"$gte": int(shares)}},
                {"$inc": {"shares": -int(shares)}},
            )
            if sold.modified_count == 0:
                businesses.update_one({"biz_id": biz["biz_id"]}, {"$inc": {"cash": proceeds, "shares_sold": int(shares)}})
                await ctx.send("Sale failed due to concurrent portfolio change. Try again.")
                return
            if new_shares == 0:
                portfolios.delete_one({"user_id": ctx.author.id, "biz_id": biz["biz_id"], "shares": {"$lte": 0}})
            credited = citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": net_proceeds}})
            if credited.modified_count == 0:
                portfolios.update_one(
                    {"user_id": ctx.author.id, "biz_id": biz["biz_id"]},
                    {
                        "$inc": {"shares": int(shares)},
                        "$setOnInsert": {"user_id": ctx.author.id, "biz_id": biz["biz_id"], "avg_buy_price": avg_price},
                    },
                    upsert=True,
                )
                businesses.update_one({"biz_id": biz["biz_id"]}, {"$inc": {"cash": proceeds, "shares_sold": int(shares)}})
                await ctx.send("Sale failed due to concurrent account state change. Try again.")
                return
            businesses.update_one({"biz_id": biz["biz_id"]}, {"$mul": {"share_price": 0.995}})
        if capital_gains_tax > 0:
            add_gov_revenue(capital_gains_tax)
        log_tx(ctx.author.id, "stock_sell", net_proceeds, f"Sold {shares} shares of {biz['name']}")

        gl_str = f"+{fmt(gain_loss)}" if gain_loss >= 0 else fmt(gain_loss)
        await ctx.send(
            f"📉 Sold **{shares:,} shares** of **{biz['name']}** at {fmt(biz['share_price'])}/share.\n"
            f"Proceeds: {fmt(proceeds)} | P&L: {gl_str} | Capital Gains Tax: {fmt(capital_gains_tax)} | Net: {fmt(net_proceeds)}"
        )

    @commands.command(aliases=["pf"])
    async def portfolio(self, ctx, member: discord.Member = None):
        """View your stock portfolio."""
        target = member or ctx.author
        ensure_citizen(target.id)

        pf_rows = list(
            portfolios.find(
                {"user_id": target.id, "shares": {"$gt": 0}},
                {"_id": 0, "biz_id": 1, "shares": 1, "avg_buy_price": 1},
            )
        )
        if not pf_rows:
            await ctx.send(f"{'Your' if target == ctx.author else target.display_name + chr(39) + 's'} portfolio is empty.")
            return
        held_biz_ids = sorted({row["biz_id"] for row in pf_rows})
        biz_map = {
            b["biz_id"]: b
            for b in businesses.find(
                {"biz_id": {"$in": held_biz_ids}},
                {"_id": 0, "biz_id": 1, "name": 1, "share_price": 1},
            )
        }
        rows = []
        for pf in pf_rows:
            b = biz_map.get(pf["biz_id"])
            if not b:
                continue
            rows.append((pf["biz_id"], b["name"], pf["shares"], pf["avg_buy_price"], b["share_price"]))
        if not rows:
            await ctx.send(f"{'Your' if target == ctx.author else target.display_name + chr(39) + 's'} portfolio is empty.")
            return

        total_value = 0.0
        total_cost = 0.0
        position_lines = []
        for biz_id, name, shares, avg_buy, current_price in rows:
            value = shares * current_price
            cost = shares * avg_buy
            gl = value - cost
            gl_str = f"+{fmt(gl)}" if gl >= 0 else fmt(gl)
            total_value += value
            total_cost += cost
            position_lines.append((name, shares, avg_buy, current_price, value, gl_str))

        total_gl = total_value - total_cost
        total_gl_str = f"+{fmt(total_gl)}" if total_gl >= 0 else fmt(total_gl)
        pages = []
        chunk_size = 6
        for idx in range(0, len(position_lines), chunk_size):
            embed = discord.Embed(title=f"📊 {target.display_name}'s Portfolio", color=discord.Color.gold())
            for name, shares, avg_buy, current_price, value, gl_str in position_lines[idx:idx + chunk_size]:
                embed.add_field(
                    name=f"{name} — {shares:,} shares",
                    value=f"Avg: {fmt(avg_buy)} | Current: {fmt(current_price)} | Value: {fmt(value)} | P&L: {gl_str}",
                    inline=False
                )
            embed.add_field(name="📈 Total Portfolio Value", value=fmt(total_value), inline=True)
            embed.add_field(name="Total P&L", value=total_gl_str, inline=True)
            pages.append(embed)

        if len(pages) == 1:
            await ctx.send(embed=pages[0])
            return
        view = PaginatorView(ctx.author.id, pages)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    @commands.command(name="seasonstocks")
    async def seasonstocks(self, ctx):
        """Seasonal stock/trade leaderboard."""
        season = season_meta.find_one({"status": "active"}, {"_id": 0, "season_id": 1, "name": 1}, sort=[("season_id", -1)])
        if not season:
            await ctx.send("No active season.")
            return
        season_id = season["season_id"]
        season_name = season["name"]
        rows = list(
            season_stats.find(
                {"season_id": season_id},
                {"_id": 0, "user_id": 1, "trade_volume": 1},
            ).sort("trade_volume", -1).limit(10)
        )
        if not rows:
            await ctx.send("No seasonal trade activity yet.")
            return
        embed = discord.Embed(title=f"📈 {season_name} Trade Ladder", color=discord.Color.green())
        embed.description = "\n".join(
            [f"**#{i+1}** <@{row['user_id']}> — {fmt(row.get('trade_volume', 0))} volume" for i, row in enumerate(rows)]
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Stocks(bot))
