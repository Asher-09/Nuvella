import discord
from discord.ext import commands
import json
from typing import Optional, List, Dict

from utils.emoji import EMOJIS
from utils.components import ComponentsV2View, prepare_v2_view, disable_all_components

# ─── Colours & style ──────────────────────────────────────────────
MAIN_COLOR   = 0x3498db
WIN_COLOR    = 0x57F287
LOSS_COLOR   = 0xED4245
BREAKER_LINE = EMOJIS["breaker"] * 10

# ─── Emoji Constants ──────────────────────────────────────────────
EMOJI_WARNING = f"{EMOJIS['warning']}"  # not yet used, but available

# ─── Commands hidden from the help menu ───────────────────────────
# Add / remove command names here (plain name or qualified name).
# Both `cmd.name` and `cmd.qualified_name` are checked.
HIDDEN_COMMANDS: set = {
        "role",
}


def _is_hidden(cmd: commands.Command) -> bool:
    """Return True if a command should not appear in help."""
    return (
        cmd.hidden
        or cmd.name in HIDDEN_COMMANDS
        or cmd.qualified_name in HIDDEN_COMMANDS
    )


with open("./config.json") as f:
    config = json.load(f)

PREFIX = config["prefix"]


# ─── Permission detection ──────────────────────────────────────────
def get_command_perm(command: commands.Command) -> str:
    """
    Return the permission group: 'Members', 'Moderators', 'Admins', 'Owner'.
    Relies on the perm_level attribute set by the require() decorator.
    """
    if hasattr(command, 'perm_level'):
        level = command.perm_level
        if level in ("owner", "bot_owner"):
            return "Owner"
        elif level == "admin":
            return "Admins"
        elif level == "mod":
            return "Moderators"
        return "Members"
    return "Members"


class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ─── Guild icon helper ───────────────────────────────────────
    def _guild_icon(self, ctx: commands.Context) -> Optional[str]:
        """Return the guild's icon URL, or None if unavailable."""
        if ctx.guild and ctx.guild.icon:
            return ctx.guild.icon.url
        return None

    # ─── Consistent embed builder ────────────────────────────────
    def make_embed(self, ctx, title: str, desc: Optional[str] = None, color=MAIN_COLOR) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=f"{desc}\n{BREAKER_LINE}" if desc else f"\n{BREAKER_LINE}",
            color=color,
            timestamp=discord.utils.utcnow()
        )
        # NOTE: guild-name author intentionally removed.
        if ctx.guild:
            embed.set_footer(text=f"Requested by {ctx.author}",
                             icon_url=ctx.author.display_avatar.url)
        return embed

    # ─── Home page (page 0) ──────────────────────────────────────
    def home_embed(self, ctx: commands.Context) -> discord.Embed:
        guild = ctx.guild
        member_count = guild.member_count if guild else "Server-only"

        embed = self.make_embed(ctx, f"{EMOJIS['help']} Help Center",
                                f"**Total Members:** {member_count}\n**Prefix:** `{PREFIX}`",
                                color=0xf1c40f)
        cog_names = [name for name, cog in self.bot.cogs.items() if cog.get_commands()]
        if cog_names:
            desc = ""
            for name in sorted(cog_names):
                desc += f"• **{name}**\n"
            embed.add_field(name=f"{EMOJIS['file']} Categories", value=desc.strip(), inline=False)
        else:
            embed.add_field(name=f"{EMOJIS['file']} Categories", value="No public commands.", inline=False)
        embed.add_field(
            name=f"{EMOJIS['pin']} Navigation",
            value=f"Use {EMOJIS['arrow_left']} {EMOJIS['help']} {EMOJIS['arrow_right']} buttons below to flip through pages.\n"
                  f"Or type `{PREFIX}help <category>` to jump directly.",
            inline=False
        )
        icon = self._guild_icon(ctx)
        if icon:
            embed.set_thumbnail(url=icon)
        return embed

    # ─── Build a page for a single cog ───────────────────────────
    def cog_page_embed(self, ctx: commands.Context, cog_name: str) -> Optional[discord.Embed]:
        cog = self.bot.get_cog(cog_name)
        if not cog or not cog.get_commands():
            return None

        embed = self.make_embed(ctx, f"{EMOJIS['file']} {cog_name} Commands", color=MAIN_COLOR)
        icon = self._guild_icon(ctx)
        if icon:
            embed.set_thumbnail(url=icon)

        groups: Dict[str, List[commands.Command]] = {
            "Members": [],
            "Moderators": [],
            "Admins": [],
            "Owner": []
        }
        for cmd in sorted(cog.get_commands(), key=lambda c: c.qualified_name):
            if _is_hidden(cmd):
                continue
            perm = get_command_perm(cmd)
            groups[perm].append(cmd)

        non_empty = {k: v for k, v in groups.items() if v}
        if not non_empty:
            embed.description = "No public commands in this category."
            return embed

        # ─── Compact listing helper ─────────────────────────────
        MAX_FIELD_LENGTH = 1024
        TRUNC_SUFFIX = "… (truncated)"

        def format_commands(cmds: List[commands.Command]) -> str:
            if not cmds:
                return "None"
            names = [f"`{cmd.qualified_name}`" for cmd in cmds]
            text = ", ".join(names)
            if len(text) <= MAX_FIELD_LENGTH:
                return text
            allowed = MAX_FIELD_LENGTH - len(TRUNC_SUFFIX)
            cut = text.rfind(',', 0, allowed)
            if cut == -1:
                cut = allowed
            return text[:cut] + TRUNC_SUFFIX

        if groups["Members"]:
            embed.add_field(name=f"{EMOJIS['scroll']} Commands",
                            value=format_commands(groups["Members"]), inline=True)

        for group_name in ["Moderators", "Admins"]:
            if groups[group_name]:
                embed.add_field(name=f"{EMOJIS['user_id']} {group_name}",
                                value=format_commands(groups[group_name]), inline=True)

        if groups["Owner"]:
            embed.add_field(name=f"{EMOJIS['owner']} Owner",
                            value=format_commands(groups["Owner"]), inline=False)

        return embed

    # ─── Single command details ──────────────────────────────────
    def command_embed(self, ctx: commands.Context, command: commands.Command) -> discord.Embed:
        perm = get_command_perm(command)
        embed = self.make_embed(ctx, f"{EMOJIS['question']} {command.qualified_name}",
                                f"**Syntax:** `{PREFIX}{command.qualified_name} {command.signature}`\n"
                                f"**Permission:** {perm}",
                                color=0x2ecc71)
        if command.help:
            embed.add_field(name="Description", value=command.help, inline=False)
        icon = self._guild_icon(ctx)
        if icon:
            embed.set_thumbnail(url=icon)
        return embed

    # ─── Paginator View ──────────────────────────────────────────
    class HelpPaginator(ComponentsV2View):
        def __init__(self, cog_instance, ctx, pages: List[discord.Embed], start_page: int = 0):
            super().__init__(timeout=180)
            self.cog = cog_instance
            self.ctx = ctx
            self.pages = pages
            self.current = start_page

            self.prev_button = discord.ui.Button(style=discord.ButtonStyle.primary, emoji=EMOJIS["arrow_left"])
            self.home_button = discord.ui.Button(style=discord.ButtonStyle.success, emoji=EMOJIS["help"])
            self.next_button = discord.ui.Button(style=discord.ButtonStyle.primary, emoji=EMOJIS["arrow_right"])

            self.prev_button.callback = self.prev_callback
            self.home_button.callback = self.home_callback
            self.next_button.callback = self.next_callback

            controls = discord.ui.ActionRow()
            controls.add_item(self.prev_button)
            controls.add_item(self.home_button)
            controls.add_item(self.next_button)
            self.add_item(controls)

            prepare_v2_view(self, self.pages[self.current])
            self.update_buttons()

        def update_buttons(self):
            self.prev_button.disabled = (self.current == 0)
            self.next_button.disabled = (self.current == len(self.pages) - 1)

        async def _render(self, interaction: discord.Interaction):
            """Rebuild the V2 layout and push the update to Discord."""
            self.update_buttons()
            page = self.pages[self.current]

            try:
                prepare_v2_view(self, page)
            except Exception:
                if hasattr(self, "set_embed"):
                    self.set_embed(page)

            try:
                if interaction.response.is_done():
                    await interaction.edit_original_response(view=self)
                else:
                    await interaction.response.edit_message(view=self)
            except discord.InteractionResponded:
                await interaction.edit_original_response(view=self)
            except discord.HTTPException:
                try:
                    await interaction.followup.send(view=self, ephemeral=True)
                except Exception:
                    pass

        async def update_message(self, interaction: discord.Interaction):
            await self._render(interaction)

        async def prev_callback(self, interaction: discord.Interaction):
            if self.current > 0:
                self.current -= 1
            await self._render(interaction)

        async def next_callback(self, interaction: discord.Interaction):
            if self.current < len(self.pages) - 1:
                self.current += 1
            await self._render(interaction)

        async def home_callback(self, interaction: discord.Interaction):
            self.current = 0
            await self._render(interaction)

        async def on_timeout(self):
            try:
                disable_all_components(self)
            except Exception:
                pass

    # ─── Main help command (with alias "h") ──────────────────────
    @commands.command(name="help", aliases=["h"])
    async def help_command(self, ctx, *, query: Optional[str] = None):
        """Browse all commands or jump to a specific category/command."""

        cog_names = sorted([name for name, cog in self.bot.cogs.items() if cog.get_commands()])
        pages = [self.home_embed(ctx)]
        for name in cog_names:
            page = self.cog_page_embed(ctx, name)
            if page:
                pages.append(page)

        if query:
            query_lower = query.lower()
            if query_lower in [n.lower() for n in cog_names]:
                for name in cog_names:
                    if name.lower() == query_lower:
                        target_cog = name
                        break
                page_idx = cog_names.index(target_cog) + 1
                view = self.HelpPaginator(self, ctx, pages, start_page=page_idx)
                await ctx.send(view=view)
                return

            cmd = self.bot.get_command(query_lower)
            if cmd and not _is_hidden(cmd):
                embed = self.command_embed(ctx, cmd)
                await ctx.send(embed=embed)
                return

            embed = self.make_embed(ctx,
                                    f"{EMOJIS['question']} Unknown",
                                    f"`{query}` is not a valid category or command.\nUse `{PREFIX}help` to browse.",
                                    color=LOSS_COLOR)
            await ctx.send(embed=embed)
            return

        view = self.HelpPaginator(self, ctx, pages)
        await ctx.send(view=view)


async def setup(bot):
    await bot.add_cog(Help(bot))
