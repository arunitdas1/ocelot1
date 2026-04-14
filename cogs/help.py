import discord
from discord.ext import commands


def _chunk(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


class HelpView(discord.ui.View):
    def __init__(self, author_id: int, pages: list[discord.Embed]):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.pages = pages
        self.page_index = 0
        self._sync_buttons()

    def _sync_buttons(self):
        total = len(self.pages)
        self.prev_btn.disabled = self.page_index <= 0
        self.next_btn.disabled = self.page_index >= total - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the user who opened this help menu can use these buttons.",
                ephemeral=True,
            )
            return False
        return True

    async def _update(self, interaction: discord.Interaction):
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.page_index], view=self)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary)
    async def home_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page_index = 0
        await self._update(interaction)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.primary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page_index = max(0, self.page_index - 1)
        await self._update(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page_index = min(len(self.pages) - 1, self.page_index + 1)
        await self._update(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _visible_commands(self):
        commands_list = []
        for cmd in self.bot.commands:
            if cmd.hidden or cmd.name == "help":
                continue
            commands_list.append(cmd)
        return sorted(commands_list, key=lambda c: c.name)

    def _build_overview_pages(self, prefix: str) -> list[discord.Embed]:
        grouped = {}
        for cmd in self._visible_commands():
            category = cmd.cog_name or "Other"
            grouped.setdefault(category, []).append(cmd)

        categories = sorted(grouped.keys())
        total_commands = sum(len(v) for v in grouped.values())
        pages = []

        intro = discord.Embed(
            title="Ocelot Economy Help",
            description=(
                f"Use `{prefix}help <command>` for details.\n"
                f"Use the buttons below to move through command pages."
            ),
            color=discord.Color.blurple(),
        )
        intro.add_field(name="Total Commands", value=str(total_commands), inline=True)
        intro.add_field(name="Categories", value=str(len(categories)), inline=True)
        intro.set_footer(text="Page 1")
        pages.append(intro)

        current_page = 2
        for category in categories:
            commands_in_cat = grouped[category]
            commands_in_cat.sort(key=lambda c: c.name)
            for group in _chunk(commands_in_cat, 8):
                embed = discord.Embed(
                    title=f"{category} Commands",
                    color=discord.Color.blue(),
                )
                for cmd in group:
                    short_doc = cmd.short_doc or "No description provided."
                    embed.add_field(
                        name=f"{prefix}{cmd.name}",
                        value=short_doc,
                        inline=False,
                    )
                embed.set_footer(text=f"Page {current_page}")
                pages.append(embed)
                current_page += 1

        return pages

    @commands.command(name="help")
    async def help_cmd(self, ctx, *, command_name: str = None):
        """Show help for all commands or one command."""
        prefix = ctx.prefix

        if command_name:
            cmd = self.bot.get_command(command_name.lower())
            if not cmd or cmd.hidden:
                embed = discord.Embed(
                    title="Command not found",
                    description=f"No command named `{command_name}` was found.",
                    color=discord.Color.red(),
                )
                await ctx.send(embed=embed)
                return

            usage = f"{prefix}{cmd.name}"
            if cmd.signature:
                usage += f" {cmd.signature}"

            embed = discord.Embed(
                title=f"Help: {prefix}{cmd.name}",
                description=cmd.help or "No description provided.",
                color=discord.Color.green(),
            )
            embed.add_field(name="Usage", value=f"`{usage}`", inline=False)
            embed.add_field(name="Category", value=cmd.cog_name or "Other", inline=True)
            aliases = ", ".join(f"`{a}`" for a in cmd.aliases) if cmd.aliases else "None"
            embed.add_field(name="Aliases", value=aliases, inline=True)
            await ctx.send(embed=embed)
            return

        pages = self._build_overview_pages(prefix)
        view = HelpView(ctx.author.id, pages)
        await ctx.send(embed=pages[0], view=view)


async def setup(bot):
    await bot.add_cog(Help(bot))
