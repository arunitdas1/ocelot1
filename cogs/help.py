import discord
from discord.ext import commands


CATEGORY_ORDER = [
    ("economy", "💰 Economy"),
    ("banking", "🏦 Banking"),
    ("shop", "🛒 Shop / Trading"),
    ("inventory", "📊 Inventory"),
    ("games", "🎮 Games / Mini-games"),
    ("leaderboard", "🏆 Leaderboard"),
    ("utility", "⚙️ Utility"),
    ("admin", "🛡️ Admin"),
    ("misc", "📜 Misc / Info"),
]


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class CategoryView(discord.ui.View):
    def __init__(self, help_cog, author_id: int, prefix: str, category_key: str, pages: list[discord.Embed], commands_on_pages: list[list[commands.Command]]):
        super().__init__(timeout=180)
        self.help_cog = help_cog
        self.author_id = author_id
        self.prefix = prefix
        self.category_key = category_key
        self.pages = pages
        self.commands_on_pages = commands_on_pages
        self.index = 0
        self.message = None
        self._sync()
        self._rebuild_select()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the user who opened help can use this menu.", ephemeral=True)
            return False
        return True

    def _sync(self):
        self.prev_btn.disabled = self.index <= 0
        self.next_btn.disabled = self.index >= len(self.pages) - 1

    def _rebuild_select(self):
        for c in list(self.children):
            if isinstance(c, discord.ui.Select):
                self.remove_item(c)
        options = []
        for cmd in self.commands_on_pages[self.index]:
            options.append(discord.SelectOption(label=cmd.name, description=(cmd.short_doc or "No description")[:100], value=cmd.name))
        if options:
            sel = CommandSelect(options=options, help_cog=self.help_cog, prefix=self.prefix, category_key=self.category_key, category_index=self.index)
            self.add_item(sel)

    async def _update(self, interaction: discord.Interaction):
        self._sync()
        self._rebuild_select()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="🏠 Home", style=discord.ButtonStyle.secondary, row=0)
    async def home_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.help_cog.build_home_embed(self.prefix, interaction.user)
        view = HomeView(self.help_cog, self.author_id, self.prefix)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = interaction.message

    @discord.ui.button(label="⬅️ Back", style=discord.ButtonStyle.secondary, row=0)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = self.help_cog.build_home_embed(self.prefix, interaction.user)
        view = HomeView(self.help_cog, self.author_id, self.prefix)
        await interaction.response.edit_message(embed=embed, view=view)
        view.message = interaction.message

    @discord.ui.button(label="◀️ Prev", style=discord.ButtonStyle.primary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        await self._update(interaction)

    @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.primary, row=0)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        await self._update(interaction)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=0)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class CommandSelect(discord.ui.Select):
    def __init__(self, options, help_cog, prefix: str, category_key: str, category_index: int):
        super().__init__(placeholder="View command details...", options=options, min_values=1, max_values=1, row=1)
        self.help_cog = help_cog
        self.prefix = prefix
        self.category_key = category_key
        self.category_index = category_index

    async def callback(self, interaction: discord.Interaction):
        cmd_name = self.values[0]
        cmd = self.help_cog.bot.get_command(cmd_name)
        if not cmd:
            await interaction.response.send_message("Command no longer available.", ephemeral=True)
            return
        embed = self.help_cog.build_command_embed(cmd, self.prefix)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class HomeView(discord.ui.View):
    def __init__(self, help_cog, author_id: int, prefix: str):
        super().__init__(timeout=180)
        self.help_cog = help_cog
        self.author_id = author_id
        self.prefix = prefix
        self.message = None
        self._add_category_buttons()

    def _add_category_buttons(self):
        catalog = self.help_cog.get_catalog()
        row = 0
        for key, label in CATEGORY_ORDER:
            if not catalog.get(key):
                continue
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, row=row)
            async def _cb(interaction: discord.Interaction, category_key=key):
                pages, commands_on_pages = self.help_cog.build_category_pages(category_key, self.prefix)
                view = CategoryView(self.help_cog, self.author_id, self.prefix, category_key, pages, commands_on_pages)
                await interaction.response.edit_message(embed=pages[0], view=view)
                view.message = interaction.message
            btn.callback = _cb
            self.add_item(btn)
            if len([c for c in self.children if isinstance(c, discord.ui.Button) and c.row == row]) >= 3:
                row += 1
        self.add_item(discord.ui.Button(label="Close", style=discord.ButtonStyle.danger, row=min(4, row + 1)))
        self.children[-1].callback = self._close

    async def _close(self, interaction: discord.Interaction):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Only the user who opened help can use this menu.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._catalog_cache = None
        self._catalog_signature = None

    def classify_command(self, cmd: commands.Command) -> str:
        cog = (cmd.cog_name or "").lower()
        name = cmd.name.lower()
        if cog in {"banking"}:
            return "banking"
        if cog in {"market", "stocks", "business"}:
            return "shop"
        if name in {"inventory", "inv", "bag"}:
            return "inventory"
        if name in {"leaderboard", "portfolio", "bizlist", "stocks"}:
            return "leaderboard"
        if cog in {"government"}:
            return "admin"
        if cog in {"help", "onboarding", "reminders"}:
            return "utility"
        if cog in {"jobs", "profile", "finance", "insurance", "contracts", "trust", "legal", "quests", "eventshub", "achievements"}:
            return "economy"
        return "misc"

    def get_catalog(self):
        visible = [c for c in self.bot.commands if not c.hidden and c.name != "help"]
        signature = tuple(
            sorted(
                (
                    c.qualified_name,
                    tuple(sorted(c.aliases)),
                    c.cog_name or "",
                    bool(c.hidden),
                    bool(c.enabled),
                )
                for c in visible
            )
        )
        if self._catalog_cache is not None and self._catalog_signature == signature:
            return self._catalog_cache
        grouped = {key: [] for key, _ in CATEGORY_ORDER}
        for cmd in sorted(visible, key=lambda c: c.name):
            grouped[self.classify_command(cmd)].append(cmd)
        self._catalog_cache = grouped
        self._catalog_signature = signature
        return grouped

    def build_home_embed(self, prefix: str, user: discord.abc.User) -> discord.Embed:
        catalog = self.get_catalog()
        total_commands = sum(len(v) for v in catalog.values())
        categories = [(label, len(catalog[key])) for key, label in CATEGORY_ORDER if len(catalog[key]) > 0]
        embed = discord.Embed(
            title="Help Dashboard",
            description=(
                "Interactive command center for Ocelot Economy.\n"
                f"Use `{prefix}help <command>` for direct details or buttons below to browse categories."
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(name="Total Commands", value=str(total_commands), inline=True)
        embed.add_field(name="Visible Categories", value=str(len(categories)), inline=True)
        embed.add_field(name="Quick Start", value=f"`{prefix}start` → `{prefix}quests` → `{prefix}nextaction`", inline=False)
        lines = [f"{label} — **{count}**" for label, count in categories]
        embed.add_field(name="Categories", value="\n".join(lines) if lines else "No commands found.", inline=False)
        embed.set_footer(text="Use buttons below to navigate categories.")
        return embed

    def build_category_pages(self, category_key: str, prefix: str):
        label = dict(CATEGORY_ORDER)[category_key]
        commands_list = self.get_catalog().get(category_key, [])
        pages = []
        page_commands = []
        for group in chunked(commands_list, 8):
            embed = discord.Embed(
                title=f"{label} Commands",
                description=f"Plain-language command guide. Prefix: `{prefix}`",
                color=discord.Color.blue(),
            )
            for cmd in group:
                aliases = f" | aliases: {', '.join(cmd.aliases)}" if cmd.aliases else ""
                desc = cmd.short_doc or "No description provided yet."
                embed.add_field(name=f"`{prefix}{cmd.name}`", value=f"{desc}{aliases}", inline=False)
            pages.append(embed)
            page_commands.append(group)
        total = len(pages) or 1
        if not pages:
            empty = discord.Embed(title=f"{label} Commands", description="No commands in this category.", color=discord.Color.blue())
            empty.set_footer(text="Use Home to select another category.")
            return [empty], [[]]
        for i, p in enumerate(pages, start=1):
            p.set_footer(text=f"Page {i}/{total} • Use command selector for details")
        return pages, page_commands

    def build_command_embed(self, cmd: commands.Command, prefix: str) -> discord.Embed:
        usage = f"{prefix}{cmd.qualified_name}"
        if cmd.signature:
            usage += f" {cmd.signature}"
        aliases = ", ".join(f"`{a}`" for a in cmd.aliases) if cmd.aliases else "None"
        cooldown = "None"
        if cmd.cooldown:
            cooldown = f"{cmd.cooldown.rate} use(s) / {cmd.cooldown.per:.0f}s"
        example = f"`{usage}`"
        embed = discord.Embed(
            title=f"Command: {prefix}{cmd.name}",
            description=cmd.help or cmd.short_doc or "No description provided.",
            color=discord.Color.green(),
        )
        embed.add_field(name="Usage", value=example, inline=False)
        embed.add_field(name="Aliases", value=aliases, inline=True)
        embed.add_field(name="Cooldown", value=cooldown, inline=True)
        embed.add_field(name="Category", value=dict(CATEGORY_ORDER).get(self.classify_command(cmd), "📜 Misc / Info"), inline=True)
        embed.set_footer(text="Tip: Use buttons in help to explore related commands.")
        return embed

    @commands.command(name="help", aliases=["h"])
    async def help_cmd(self, ctx, *, query: str = None):
        """Open interactive help dashboard or inspect one command."""
        prefix = ctx.prefix
        if query:
            q = query.strip()
            if q.lower().startswith("search "):
                q = q[7:].strip()
            cmd = self.bot.get_command(q.lower())
            if not cmd or cmd.hidden:
                embed = discord.Embed(
                    title="Command Not Found",
                    description=f"No command named `{q}` was found.\nTry `{prefix}help` to browse categories.",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return
            await ctx.send(embed=self.build_command_embed(cmd, prefix))
            return

        embed = self.build_home_embed(prefix, ctx.author)
        view = HomeView(self, ctx.author.id, prefix)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg


async def setup(bot):
    await bot.add_cog(Help(bot))
