import time
import discord
from discord.ext import commands
from db import cursor, conn
from utils import ensure_citizen, get_citizen, log_tx, fmt, get_eco_state, add_gov_revenue, get_trust, clamp
from cogs.ui_components import PaginatorView, ConfirmView

CATEGORIES = ["food", "materials", "tech", "energy", "luxury"]


def get_good(good_id):
    cursor.execute("SELECT * FROM market_goods WHERE good_id = ?", (good_id,))
    row = cursor.fetchone()
    if row:
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))
    return None


def get_inventory(user_id, good_id):
    cursor.execute("SELECT quantity FROM inventories WHERE user_id = ? AND good_id = ?", (user_id, good_id))
    row = cursor.fetchone()
    return row[0] if row else 0


def update_inventory(user_id, good_id, delta):
    # Single upsert path reduces one read query per inventory update.
    cursor.execute(
        "INSERT INTO inventories(user_id, good_id, quantity) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, good_id) DO UPDATE SET quantity = quantity + excluded.quantity",
        (user_id, good_id, delta),
    )
    cursor.execute("DELETE FROM inventories WHERE user_id = ? AND good_id = ? AND quantity <= 0", (user_id, good_id))


class Market(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(aliases=["shop", "prices"])
    async def market(self, ctx, category: str = None):
        """View market prices. Filter by category: food, materials, tech, energy, luxury"""
        if category and category.lower() not in CATEGORIES:
            await ctx.send(f"Valid categories: {', '.join(f'`{c}`' for c in CATEGORIES)}")
            return

        query = "SELECT good_id, name, category, current_price, supply, demand FROM market_goods"
        params = ()
        if category:
            query += " WHERE category = ?"
            params = (category.lower(),)
        query += " ORDER BY category, name"

        cursor.execute(query, params)
        rows = cursor.fetchall()
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
        for good_id, name, cat, price, supply, demand in rows:
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
        c = get_citizen(ctx.author.id)
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

        if c["cash"] < total:
            await ctx.send(
                f"Not enough cash. Total cost: {fmt(total)} ({fmt(subtotal)} + {fmt(sales_tax)} tax). "
                f"You have {fmt(c['cash'])}."
            )
            return

        cursor.execute(
            "UPDATE citizens SET cash = cash - ? WHERE user_id = ? AND cash >= ?",
            (total, ctx.author.id, total)
        )
        if cursor.rowcount == 0:
            latest = get_citizen(ctx.author.id)
            await ctx.send(f"Not enough cash. Total: {fmt(total)}. Wallet: {fmt(latest['cash'])}.")
            return
        cursor.execute(
            "UPDATE market_goods SET supply = supply - ?, demand = demand + ? WHERE good_id = ?",
            (quantity, quantity // 2 + 1, good_id)
        )
        update_inventory(ctx.author.id, good_id, quantity)
        conn.commit()
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

        owned = get_inventory(ctx.author.id, good_id)
        if owned < quantity:
            await ctx.send(f"You only have **{owned}x {good['name']}** in your inventory.")
            return

        max_price = good["current_price"] * 3
        if price > max_price:
            await ctx.send(f"Price too high. Maximum listing price: {fmt(max_price)} (3x market rate).")
            return

        update_inventory(ctx.author.id, good_id, -quantity)
        cursor.execute(
            "INSERT INTO market_listings(seller_id, good_id, quantity, price_per_unit, listed_at) VALUES (?, ?, ?, ?, ?)",
            (ctx.author.id, good_id, quantity, price, int(time.time()))
        )
        conn.commit()
        await ctx.send(
            f"📋 Listed **{quantity}x {good['name']}** at {fmt(price)}/unit. "
            f"Use `!listings` to manage your listings."
        )

    @commands.command()
    async def buyp2p(self, ctx, listing_id: int):
        """Buy from a player listing. Usage: !buyp2p <listing_id>"""
        ensure_citizen(ctx.author.id)
        c = get_citizen(ctx.author.id)

        cursor.execute(
            "SELECT listing_id, seller_id, good_id, quantity, price_per_unit FROM market_listings WHERE listing_id = ?",
            (listing_id,)
        )
        row = cursor.fetchone()
        if not row:
            await ctx.send("Listing not found.")
            return

        lid, seller_id, good_id, qty, price_per = row
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

        cursor.execute(
            "UPDATE citizens SET cash = cash - ? WHERE user_id = ? AND cash >= ?",
            (grand, ctx.author.id, grand)
        )
        if cursor.rowcount == 0:
            latest = get_citizen(ctx.author.id)
            await ctx.send(f"Not enough cash. Total: {fmt(grand)}. You have {fmt(latest['cash'])}.")
            return
        cursor.execute("UPDATE citizens SET cash = cash + ? WHERE user_id = ?", (seller_gets, seller_id))
        cursor.execute("DELETE FROM market_listings WHERE listing_id = ?", (lid,))
        update_inventory(ctx.author.id, good_id, qty)
        conn.commit()
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
        query = (
            "SELECT ml.listing_id, ml.seller_id, mg.name, ml.quantity, ml.price_per_unit, ml.listed_at "
            "FROM market_listings ml JOIN market_goods mg ON ml.good_id = mg.good_id"
        )
        if category:
            query += " WHERE mg.category = ?"
            cursor.execute(query, (category.lower(),))
        else:
            cursor.execute(query)

        rows = cursor.fetchall()
        if not rows:
            await ctx.send("No active player listings.")
            return

        pages = []
        chunk_size = 8
        for idx in range(0, min(len(rows), 30), chunk_size):
            embed = discord.Embed(title="🏷️ Player Market Listings", color=discord.Color.teal())
            for lid, seller_id, name, qty, price, listed_at in rows[idx:idx + chunk_size]:
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
        cursor.execute(
            "SELECT i.good_id, mg.name, i.quantity, mg.current_price "
            "FROM inventories i JOIN market_goods mg ON i.good_id = mg.good_id "
            "WHERE i.user_id = ? ORDER BY mg.category, mg.name",
            (target.id,)
        )
        rows = cursor.fetchall()
        if not rows:
            await ctx.send(f"{'Your' if target == ctx.author else target.display_name + chr(39) + 's'} inventory is empty.")
            return

        total_value = sum(qty * price for _, _, qty, price in rows)
        pages = []
        chunk_size = 8
        for idx in range(0, len(rows), chunk_size):
            embed = discord.Embed(
                title=f"🎒 {target.display_name}'s Inventory",
                description=f"Estimated value: **{fmt(total_value)}**",
                color=discord.Color.green()
            )
            for good_id, name, qty, price in rows[idx:idx + chunk_size]:
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
        cursor.execute(
            "SELECT listing_id, good_id, quantity FROM market_listings WHERE listing_id = ? AND seller_id = ?",
            (listing_id, ctx.author.id)
        )
        row = cursor.fetchone()
        if not row:
            await ctx.send("Listing not found or you don't own it.")
            return

        confirm = ConfirmView(ctx.author.id)
        prompt = discord.Embed(
            title="Confirm Delist",
            description=f"Delist listing **#{listing_id}** and return items to your inventory?",
            color=discord.Color.orange(),
        )
        msg = await ctx.send(embed=prompt, view=confirm)
        await confirm.wait()
        if confirm.value is not True:
            await ctx.send("Delist cancelled.")
            return

        lid, good_id, qty = row
        update_inventory(ctx.author.id, good_id, qty)
        cursor.execute("DELETE FROM market_listings WHERE listing_id = ?", (lid,))
        conn.commit()
        good = get_good(good_id)
        await ctx.send(f"✅ Listing #{lid} removed. {qty}x **{good['name']}** returned to your inventory.")


async def setup(bot):
    await bot.add_cog(Market(bot))
