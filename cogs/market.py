import time
import discord
from discord.ext import commands
from db import citizens, inventories, market_goods, market_listings, next_id, write_txn
from utils import ensure_citizen, get_citizen, log_tx, fmt, get_eco_state, add_gov_revenue, get_trust, clamp
from cogs.ui_components import PaginatorView, ConfirmView

CATEGORIES = ["food", "materials", "tech", "energy", "luxury"]


def get_good(good_id):
    return market_goods.find_one({"good_id": good_id}, {"_id": 0})


def get_inventory(user_id, good_id):
    row = inventories.find_one({"user_id": user_id, "good_id": good_id}, {"_id": 0, "quantity": 1})
    return int(row["quantity"]) if row else 0


def update_inventory(user_id, good_id, delta):
    result = inventories.update_one(
        {"user_id": user_id, "good_id": good_id},
        {"$inc": {"quantity": delta}, "$setOnInsert": {"user_id": user_id, "good_id": good_id}},
        upsert=True,
    )
    if not result.acknowledged:
        return False
    inventories.delete_one({"user_id": user_id, "good_id": good_id, "quantity": {"$lte": 0}})
    return True


class Market(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["shop", "prices"])
    async def market(self, ctx, category: str = None):
        """View market prices. Filter by category: food, materials, tech, energy, luxury"""
        if category and category.lower() not in CATEGORIES:
            await ctx.send(f"Valid categories: {', '.join(f'`{c}`' for c in CATEGORIES)}")
            return

        q = {"category": category.lower()} if category else {}
        rows = list(
            market_goods.find(
                q,
                {"_id": 0, "good_id": 1, "name": 1, "category": 1, "current_price": 1, "supply": 1, "demand": 1},
            ).sort([("category", 1), ("name", 1)])
        )
        if not rows:
            await ctx.send("No goods found.")
            return

        inflation = float(get_eco_state("inflation_rate") or 0.02)
        embed = discord.Embed(
            title="🏪 Global Market",
            description=f"Current inflation: **{inflation*100:.1f}%** | Use `!buy <good_id> <qty>` to purchase",
            color=discord.Color.green()
        )

        by_cat = {}
        for row in rows:
            good_id = row["good_id"]
            name = row["name"]
            cat = row["category"]
            price = row["current_price"]
            supply = row["supply"]
            demand = row["demand"]
            by_cat.setdefault(cat, []).append((good_id, name, price, supply, demand))

        cat_emoji = {"food": "🍞", "materials": "⚙️", "tech": "💻", "energy": "⚡", "luxury": "💎"}
        for cat, items in by_cat.items():
            lines = []
            for good_id, name, price, supply, demand in items:
                ratio = demand / max(supply, 1)
                trend = "🔴" if ratio > 1.2 else "🟢" if ratio < 0.8 else "🟡"
                lines.append(f"{trend} `{good_id}` **{name}** — {fmt(price)} (Supply: {supply} | Demand: {demand})")
            embed.add_field(name=f"{cat_emoji.get(cat, '📦')} {cat.title()}", value="\n".join(lines), inline=False)

        embed.set_footer(text="🔴 High demand (prices rising) | 🟡 Balanced | 🟢 Surplus (prices falling)")
        await ctx.send(embed=embed)

    @commands.command()
    async def buy(self, ctx, good_id: str, quantity: int = 1):
        """Buy goods from the market. Usage: !buy <good_id> <quantity>"""
        good_id = good_id.lower()
        if quantity <= 0:
            await ctx.send("Quantity must be positive.")
            return
        if quantity > 100:
            await ctx.send("Maximum 100 units per transaction.")
            return

        ensure_citizen(ctx.author.id)
        good = get_good(good_id)
        if not good:
            await ctx.send(f"Unknown good `{good_id}`. Use `!market` to see available goods.")
            return
        if good["supply"] < quantity:
            await ctx.send(f"Only {good['supply']} units available in the market.")
            return

        subtotal = round(good["current_price"] * quantity, 2)
        sales_tax = round(subtotal * 0.08, 2)
        total = round(subtotal + sales_tax, 2)

        try:
            with write_txn():
                stock = market_goods.find_one_and_update(
                    {"good_id": good_id, "supply": {"$gte": quantity}},
                    {"$inc": {"supply": -quantity, "demand": quantity // 2 + 1}},
                )
                if not stock:
                    raise RuntimeError("SUPPLY_CHANGED")
                debit = citizens.find_one_and_update(
                    {"user_id": ctx.author.id, "cash": {"$gte": total}},
                    {"$inc": {"cash": -total}},
                )
                if not debit:
                    market_goods.update_one(
                        {"good_id": good_id},
                        {"$inc": {"supply": quantity, "demand": -(quantity // 2 + 1)}},
                    )
                    raise RuntimeError("INSUFFICIENT_CASH")
                inv_ok = update_inventory(ctx.author.id, good_id, quantity)
                if not inv_ok:
                    citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": total}})
                    market_goods.update_one(
                        {"good_id": good_id},
                        {"$inc": {"supply": quantity, "demand": -(quantity // 2 + 1)}},
                    )
                    raise RuntimeError("INVENTORY_UPDATE_FAILED")
        except RuntimeError as e:
            if str(e) == "INSUFFICIENT_CASH":
                latest = get_citizen(ctx.author.id)
                await ctx.send(f"Not enough cash. Total: {fmt(total)}. Wallet: {fmt(latest['cash'])}.")
            elif str(e) == "INVENTORY_UPDATE_FAILED":
                await ctx.send("Purchase failed due to inventory sync error. No funds were moved.")
            else:
                await ctx.send("Market stock changed while purchasing. Please try again.")
            return
        add_gov_revenue(sales_tax)
        log_tx(ctx.author.id, "market_buy", -total, f"Bought {quantity}x {good['name']}")

        await ctx.send(
            f"✅ Bought **{quantity}x {good['name']}** for {fmt(subtotal)} + {fmt(sales_tax)} sales tax = **{fmt(total)}** total."
        )

    @commands.command()
    async def sell(self, ctx, good_id: str, quantity: int, price: float):
        """List goods for sale on the market. Usage: !sell <good_id> <quantity> <price_per_unit>"""
        import math
        good_id = good_id.lower()
        if not math.isfinite(price) or quantity <= 0 or price <= 0:
            await ctx.send("Quantity and price must be positive finite numbers.")
            return

        ensure_citizen(ctx.author.id)
        good = get_good(good_id)
        if not good:
            await ctx.send(f"Unknown good `{good_id}`. Use `!inventory` to see what you own.")
            return

        max_price = good["current_price"] * 3
        if price > max_price:
            await ctx.send(f"Price too high. Maximum listing price: {fmt(max_price)} (3x market rate).")
            return

        with write_txn():
            reserved = inventories.update_one(
                {"user_id": ctx.author.id, "good_id": good_id, "quantity": {"$gte": int(quantity)}},
                {"$inc": {"quantity": -int(quantity)}},
            )
            if reserved.modified_count == 0:
                latest_owned = get_inventory(ctx.author.id, good_id)
                await ctx.send(f"You only have **{latest_owned}x {good['name']}** in your inventory.")
                return
            listing_id = next_id("market_listings")
            inserted = market_listings.insert_one(
                {
                    "listing_id": listing_id,
                    "seller_id": ctx.author.id,
                    "good_id": good_id,
                    "quantity": quantity,
                    "price_per_unit": price,
                    "listed_at": int(time.time()),
                }
            )
            if not inserted.acknowledged:
                inventories.update_one(
                    {"user_id": ctx.author.id, "good_id": good_id},
                    {"$inc": {"quantity": int(quantity)}, "$setOnInsert": {"user_id": ctx.author.id, "good_id": good_id}},
                    upsert=True,
                )
                await ctx.send("Listing failed due to a concurrent market change. Please try again.")
                return
            inventories.delete_one({"user_id": ctx.author.id, "good_id": good_id, "quantity": {"$lte": 0}})
        await ctx.send(
            f"📋 Listed **{quantity}x {good['name']}** at {fmt(price)}/unit. "
            f"Use `!listings` to manage your listings."
        )

    @commands.command()
    async def buyp2p(self, ctx, listing_id: int):
        """Buy from a player listing. Usage: !buyp2p <listing_id>"""
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)

        row = market_listings.find_one(
            {"listing_id": listing_id},
            {"_id": 0, "listing_id": 1, "seller_id": 1, "good_id": 1, "quantity": 1, "price_per_unit": 1},
        )
        if not row:
            await ctx.send("Listing not found.")
            return

        lid = row["listing_id"]
        seller_id = row["seller_id"]
        good_id = row["good_id"]
        qty = row["quantity"]
        price_per = row["price_per_unit"]
        if seller_id == ctx.author.id:
            await ctx.send("You can't buy your own listing!")
            return

        good = get_good(good_id)
        total = round(qty * price_per, 2)
        sales_tax = round(total * 0.08, 2)
        grand = round(total + sales_tax, 2)

        if c["cash"] < grand:
            await ctx.send(f"Not enough cash. Total: {fmt(grand)}. You have {fmt(c['cash'])}.")
            return

        # Trust-aware platform fee (balanced): low trust pays higher fees, high trust pays slightly less.
        trust = get_trust(ctx.author.id, seller_id)
        platform_fee = clamp(0.05 + (0.2 - trust) * 0.03, 0.03, 0.08)
        seller_gets = round(total * (1.0 - platform_fee), 2)

        try:
            with write_txn():
                removed = market_listings.find_one_and_delete(
                    {"listing_id": lid, "seller_id": seller_id, "good_id": good_id, "quantity": qty, "price_per_unit": price_per}
                )
                if not removed:
                    raise RuntimeError("LISTING_GONE")
                debit = citizens.find_one_and_update(
                    {"user_id": ctx.author.id, "cash": {"$gte": grand}},
                    {"$inc": {"cash": -grand}},
                )
                if not debit:
                    market_listings.insert_one(removed)
                    raise RuntimeError("INSUFFICIENT_CASH")
                seller_paid = citizens.update_one({"user_id": seller_id}, {"$inc": {"cash": seller_gets}})
                if seller_paid.modified_count == 0:
                    citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": grand}})
                    market_listings.insert_one(removed)
                    raise RuntimeError("SELLER_UNAVAILABLE")
                inv_ok = update_inventory(ctx.author.id, good_id, qty)
                if not inv_ok:
                    citizens.update_one({"user_id": ctx.author.id}, {"$inc": {"cash": grand}})
                    citizens.update_one({"user_id": seller_id}, {"$inc": {"cash": -seller_gets}})
                    market_listings.insert_one(removed)
                    raise RuntimeError("INVENTORY_UPDATE_FAILED")
        except RuntimeError as e:
            if str(e) == "INSUFFICIENT_CASH":
                latest = get_citizen(ctx.author.id)
                await ctx.send(f"Not enough cash. Total: {fmt(grand)}. You have {fmt(latest['cash'])}.")
            elif str(e) == "SELLER_UNAVAILABLE":
                await ctx.send("Seller account was unavailable. Transaction was safely rolled back.")
            elif str(e) == "INVENTORY_UPDATE_FAILED":
                await ctx.send("Transaction failed due to inventory sync error. No funds were moved.")
            else:
                await ctx.send("Listing is no longer available. Please try another listing.")
            return
        add_gov_revenue(sales_tax + (total - seller_gets))
        log_tx(ctx.author.id, "p2p_buy", -grand, f"Bought {qty}x {good['name']} from player listing")
        log_tx(seller_id, "p2p_sell", seller_gets, f"Sold {qty}x {good['name']} via listing")

        await ctx.send(
            f"✅ Purchased **{qty}x {good['name']}** for {fmt(grand)} "
            f"(includes 8% tax + {platform_fee*100:.1f}% platform fee)."
        )

    @commands.command()
    async def listings(self, ctx, category: str = None):
        """View player market listings. Usage: !listings [category]"""
        if category:
            goods_q = {"category": category.lower()}
            goods = {
                g["good_id"]: g["name"]
                for g in market_goods.find(goods_q, {"_id": 0, "good_id": 1, "name": 1})
            }
            if not goods:
                await ctx.send("No active player listings.")
                return
            rows = list(
                market_listings.find(
                    {"good_id": {"$in": list(goods.keys())}},
                    {"_id": 0, "listing_id": 1, "seller_id": 1, "good_id": 1, "quantity": 1, "price_per_unit": 1, "listed_at": 1},
                ).sort("listed_at", -1)
            )
        else:
            rows = list(
                market_listings.find(
                    {},
                    {"_id": 0, "listing_id": 1, "seller_id": 1, "good_id": 1, "quantity": 1, "price_per_unit": 1, "listed_at": 1},
                ).sort("listed_at", -1)
            )
            if not rows:
                await ctx.send("No active player listings.")
                return
            listing_good_ids = sorted({row["good_id"] for row in rows})
            goods = {
                g["good_id"]: g["name"]
                for g in market_goods.find({"good_id": {"$in": listing_good_ids}}, {"_id": 0, "good_id": 1, "name": 1})
            }
        if not rows:
            await ctx.send("No active player listings.")
            return

        pages = []
        chunk_size = 8
        for idx in range(0, min(len(rows), 30), chunk_size):
            embed = discord.Embed(title="🏷️ Player Market Listings", color=discord.Color.teal())
            for row in rows[idx:idx + chunk_size]:
                lid = row["listing_id"]
                seller_id = row["seller_id"]
                qty = row["quantity"]
                price = row["price_per_unit"]
                name = goods.get(row["good_id"], row["good_id"])
                member = ctx.guild.get_member(seller_id) if ctx.guild else None
                if member:
                    seller_name = member.display_name
                else:
                    user = self.bot.get_user(seller_id)
                    if user:
                        seller_name = user.display_name
                    else:
                        try:
                            seller = await self.bot.fetch_user(seller_id)
                            seller_name = seller.display_name
                        except Exception:
                            seller_name = f"User {seller_id}"
                embed.add_field(
                    name=f"#{lid} — {qty}x {name} @ {fmt(price)}/unit",
                    value=f"Seller: {seller_name} | Total: {fmt(qty * price)} | `!buyp2p {lid}`",
                    inline=False
                )
            pages.append(embed)
        if len(pages) == 1:
            await ctx.send(embed=pages[0])
            return
        view = PaginatorView(ctx.author.id, pages)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    @commands.command(aliases=["inv", "bag"])
    async def inventory(self, ctx, member: discord.Member = None):
        """View your inventory."""
        target = member or ctx.author
        ensure_citizen(target.id)
        inv_rows = list(inventories.find({"user_id": target.id}, {"_id": 0, "good_id": 1, "quantity": 1}))
        if not inv_rows:
            await ctx.send(f"{'Your' if target == ctx.author else target.display_name + chr(39) + 's'} inventory is empty.")
            return
        inv_good_ids = sorted({inv["good_id"] for inv in inv_rows})
        goods_map = {
            g["good_id"]: g
            for g in market_goods.find(
                {"good_id": {"$in": inv_good_ids}},
                {"_id": 0, "good_id": 1, "name": 1, "current_price": 1, "category": 1},
            )
        }
        rows = []
        for inv in inv_rows:
            g = goods_map.get(inv["good_id"])
            if not g:
                continue
            rows.append((inv["good_id"], g["name"], inv["quantity"], g["current_price"], g["category"]))
        rows.sort(key=lambda x: (x[4], x[1]))
        if not rows:
            await ctx.send(f"{'Your' if target == ctx.author else target.display_name + chr(39) + 's'} inventory is empty.")
            return

        total_value = sum(qty * price for _, _, qty, price, _ in rows)
        pages = []
        chunk_size = 8
        for idx in range(0, len(rows), chunk_size):
            embed = discord.Embed(
                title=f"🎒 {target.display_name}'s Inventory",
                description=f"Estimated value: **{fmt(total_value)}**",
                color=discord.Color.green()
            )
            for good_id, name, qty, price, _ in rows[idx:idx + chunk_size]:
                embed.add_field(
                    name=f"{name} (x{qty})",
                    value=f"Market value: {fmt(price * qty)} | `!sell {good_id} {qty} <price>`",
                    inline=True
                )
            pages.append(embed)
        if len(pages) == 1:
            await ctx.send(embed=pages[0])
            return
        view = PaginatorView(ctx.author.id, pages)
        msg = await ctx.send(embed=pages[0], view=view)
        view.message = msg

    @commands.command(aliases=["unlist"])
    async def delist(self, ctx, listing_id: int):
        """Remove one of your active market listings. Usage: !delist <listing_id>"""
        row = market_listings.find_one(
            {"listing_id": listing_id, "seller_id": ctx.author.id},
            {"_id": 0, "listing_id": 1, "good_id": 1, "quantity": 1},
        )
        if not row:
            await ctx.send("Listing not found or you don't own it.")
            return

        confirm = ConfirmView(ctx.author.id)
        prompt = discord.Embed(
            title="Confirm Delist",
            description=f"Delist listing **#{listing_id}** and return items to your inventory?",
            color=discord.Color.orange(),
        )
        await ctx.send(embed=prompt, view=confirm)
        await confirm.wait()
        if confirm.value is not True:
            await ctx.send("Delist cancelled.")
            return

        lid = row["listing_id"]
        good_id = row["good_id"]
        qty = row["quantity"]
        with write_txn():
            removed = market_listings.find_one_and_delete({"listing_id": lid, "seller_id": ctx.author.id})
            if not removed:
                await ctx.send("Listing is no longer active.")
                return
            update_inventory(ctx.author.id, good_id, qty)
        good = get_good(good_id)
        await ctx.send(f"✅ Listing #{lid} removed. {qty}x **{good['name']}** returned to your inventory.")

    @commands.command(name="marketchallenge")
    async def marketchallenge(self, ctx):
        """Daily social market challenge prompt."""
        embed = discord.Embed(title="Market Challenge", color=discord.Color.orange())
        embed.description = (
            "Today's challenge:\n"
            "1) Complete 3 market actions (`!buy`, `!sell`, or `!buyp2p`)\n"
            "2) Keep your net trade spend under $2,000\n"
            "3) Post your result with `!history 5`\n\n"
            "Reward path: quest + season progression."
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Market(bot))
