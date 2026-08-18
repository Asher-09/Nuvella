import discord
from discord.ext import commands
import json
from typing import Optional, List, Dict

# ─── Colours & style ──────────────────────────────────────────────
MAIN_COLOR   = 0x3498db
WIN_COLOR    = 0x57F287
LOSS_COLOR   = 0xED4245
BREAKER      = "<:breaker:1537913026643238932>"
BREAKER_LINE = BREAKER * 10

# ─── Emoji Constants ──────────────────────────────────────────────
# Custom animated
EMOJI_ARROW_LEFT   = "<a:White_Arrow_Left:1509817327682850866>"
EMOJI_ARROW_RIGHT  = "<a:White_Arrow_Right:1509817307638005830>"
EMOJI_OWNER        = "<a:owner:1537913450754473994>"

# Custom static
EMOJI_HELP_CENTER  = "<a:help:1537913645114069073>"
EMOJI_FILE         = "<:file:1537913812416729200>"
EMOJI_W_PINNED     = "<:pin_purple:1537913946357366824>"
EMOJI_SCROLL       = "<:scroll:1537914407663968306>"
EMOJI_USER_ID      = "<:userid:1537914514736025740>"
EMOJI_QUESTION_MARK = "<:question_mark:1537914997378912307>"

# Unicode emojis (used in messages and embeds)
EMOJI_ENVELOPE     = "<:message:1537915592240275506>"
EMOJI_CROSS        = "<:cross:1537915734087303199>"
EMOJI_CHECK        = "<a:tick:1537915747454554222>"
EMOJI_INFO         = "<a:info:1537916095351095376>"
EMOJI_WARNING      = "<:purple_warning:1537916196601733241>"   # not yet used, but available

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

    # ─── Consistent embed builder ────────────────────────────────
    def make_embed(self, ctx, title: str, desc: Optional[str] = None, color=MAIN_COLOR) -> discord.Embed:
        embed = discord.Embed(
            title=title,
            description=f"{desc}\n{BREAKER_LINE}" if desc else f"\n{BREAKER_LINE}",
            color=color,
            timestamp=discord.utils.utcnow()
        )
        if ctx.guild:
            embed.set_author(name=ctx.guild.name,
                             icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
            embed.set_footer(text=f"Requested by {ctx.author}",
                             icon_url=ctx.author.display_avatar.url)
        return embed

    # ─── Home page (page 0) ──────────────────────────────────────
    def home_embed(self, ctx: commands.Context) -> discord.Embed:
        guild = ctx.guild
        member_count = guild.member_count

        embed = self.make_embed(ctx, f"{EMOJI_HELP_CENTER} Help Center",
                                f"**Total Members:** {member_count}\n**Prefix:** `{PREFIX}`",
                                color=0xf1c40f)
        cog_names = [name for name, cog in self.bot.cogs.items() if cog.get_commands()]
        if cog_names:
            desc = ""
            for name in sorted(cog_names):
                desc += f"• **{name}**\n"
            embed.add_field(name=f"{EMOJI_FILE} Categories", value=desc.strip(), inline=False)
        else:
            embed.add_field(name=f"{EMOJI_FILE} Categories", value="No public commands.", inline=False)
        embed.add_field(
            name=f"{EMOJI_W_PINNED} Navigation",
            value=f"Use {EMOJI_ARROW_LEFT} {EMOJI_HELP_CENTER} {EMOJI_ARROW_RIGHT} buttons below to flip through pages.\n"
                  f"Or type `{PREFIX}help <category>` to jump directly.",
            inline=False
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        return embed

    # ─── Build a page for a single cog ───────────────────────────
    def cog_page_embed(self, ctx: commands.Context, cog_name: str) -> Optional[discord.Embed]:
        cog = self.bot.get_cog(cog_name)
        if not cog or not cog.get_commands():
            return None

        embed = self.make_embed(ctx, f"{EMOJI_FILE} {cog_name} Commands", color=MAIN_COLOR)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        groups: Dict[str, List[commands.Command]] = {
            "Members": [],
            "Moderators": [],
            "Admins": [],
            "Owner": []
        }
        for cmd in sorted(cog.get_commands(), key=lambda c: c.qualified_name):
            if cmd.hidden:
                continue
            perm = get_command_perm(cmd)
            groups[perm].append(cmd)

        non_empty = {k: v for k, v in groups.items() if v}
        if not non_empty:
            embed.description = "No public commands in this category."
            return embed

        # ─── Fixed truncation helper ────────────────────────────
        MAX_FIELD_LENGTH = 1024
        TRUNC_SUFFIX = "\n… (truncated)"

        def format_commands(cmds: List[commands.Command]) -> str:
            lines = []
            for cmd in cmds:
                signature = f"`{PREFIX}{cmd.qualified_name} {cmd.signature}`".rstrip()
                lines.append(f"• **{cmd.qualified_name}** – {signature}")
            text = "\n".join(lines)
            if len(text) <= MAX_FIELD_LENGTH:
                return text or "None"
            # Reserve space for suffix
            allowed = MAX_FIELD_LENGTH - len(TRUNC_SUFFIX)
            cut = text.rfind('\n', 0, allowed)
            if cut == -1:
                cut = allowed
            return text[:cut] + TRUNC_SUFFIX

        # Members field now shown as "Commands"
        if groups["Members"]:
            embed.add_field(name=f"{EMOJI_SCROLL} Commands", value=format_commands(groups["Members"]), inline=True)

        for group_name in ["Moderators", "Admins"]:
            if groups[group_name]:
                embed.add_field(name=f"{EMOJI_USER_ID} {group_name}", value=format_commands(groups[group_name]), inline=True)

        if groups["Owner"]:
            embed.add_field(name=f"{EMOJI_OWNER} Owner", value=format_commands(groups["Owner"]), inline=False)

        return embed

    # ─── Single command details ──────────────────────────────────
    def command_embed(self, ctx: commands.Context, command: commands.Command) -> discord.Embed:
        perm = get_command_perm(command)
        embed = self.make_embed(ctx, f"{EMOJI_QUESTION_MARK} {command.qualified_name}",
                                f"**Syntax:** `{PREFIX}{command.qualified_name} {command.signature}`\n"
                                f"**Permission:** {perm}",
                                color=0x2ecc71)
        if command.help:
            embed.add_field(name="Description", value=command.help, inline=False)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        return embed

    # ─── Paginator View ──────────────────────────────────────────
    class HelpPaginator(discord.ui.View):
        def __init__(self, cog_instance, ctx, pages: List[discord.Embed], start_page: int = 0):
            super().__init__(timeout=180)
            self.cog = cog_instance
            self.ctx = ctx
            self.pages = pages
            self.current = start_page

            self.prev_button = discord.ui.Button(style=discord.ButtonStyle.primary, emoji=EMOJI_ARROW_LEFT, row=0)
            self.home_button = discord.ui.Button(style=discord.ButtonStyle.success, emoji=EMOJI_HELP_CENTER, row=0)
            self.next_button = discord.ui.Button(style=discord.ButtonStyle.primary, emoji=EMOJI_ARROW_RIGHT, row=0)

            self.prev_button.callback = self.prev_callback
            self.home_button.callback = self.home_callback
            self.next_button.callback = self.next_callback

            self.add_item(self.prev_button)
            self.add_item(self.home_button)
            self.add_item(self.next_button)
            self.update_buttons()

        def update_buttons(self):
            self.prev_button.disabled = (self.current == 0)
            self.next_button.disabled = (self.current == len(self.pages) - 1)

        async def update_message(self, interaction: discord.Interaction):
            self.update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current], view=self)

        async def prev_callback(self, interaction: discord.Interaction):
            if self.current > 0:
                self.current -= 1
            await self.update_message(interaction)

        async def next_callback(self, interaction: discord.Interaction):
            if self.current < len(self.pages) - 1:
                self.current += 1
            await self.update_message(interaction)

        async def home_callback(self, interaction: discord.Interaction):
            self.current = 0
            await self.update_message(interaction)

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
                await ctx.send(embed=pages[page_idx], view=view)
                return

            cmd = self.bot.get_command(query_lower)
            if cmd and not cmd.hidden:
                embed = self.command_embed(ctx, cmd)
                await ctx.send(embed=embed)
                return

            embed = self.make_embed(ctx,
                                    f"{EMOJI_QUESTION_MARK} Unknown",
                                    f"`{query}` is not a valid category or command.\nUse `{PREFIX}help` to browse.",
                                    color=LOSS_COLOR)
            await ctx.send(embed=embed)
            return

        view = self.HelpPaginator(self, ctx, pages)
        await ctx.send(embed=pages[0], view=view)

    # ─── Extra help command (DM all commands) ────────────────────
    @commands.command(name="extrahelp", aliases=["commands"])
    async def extrahelp_command(self, ctx):
        """Send a DM with all bot commands organized by cog and permission level."""
        try:
            # Get all cogs with commands
            cog_names = sorted([name for name, cog in self.bot.cogs.items() if cog.get_commands()])

            # Build the comprehensive command list
            all_text = f"**{self.bot.user.name} - Complete Command List**\n\n"

            for cog_name in cog_names:
                cog = self.bot.get_cog(cog_name)
                if not cog:
                    continue

                commands_by_perm = {
                    "Owner": [],
                    "Admins": [],
                    "Moderators": [],
                    "Members": []
                }

                for cmd in cog.get_commands():
                    if cmd.hidden:
                        continue
                    perm = get_command_perm(cmd)
                    cmd_info = f"**{PREFIX}{cmd.name}** "
                    if cmd.aliases:
                        cmd_info += f"(aliases: {', '.join(cmd.aliases)}) "
                    if cmd.help:
                        cmd_info += f"- {cmd.help}"
                    else:
                        cmd_info += "- No description"
                    commands_by_perm[perm].append(cmd_info)

                # Add cog section if it has commands
                if any(commands_by_perm.values()):
                    all_text += f"\n**━━━ {cog_name.upper()} ━━━**\n\n"

                    for perm_level in ["Owner", "Admins", "Moderators", "Members"]:
                        if commands_by_perm[perm_level]:
                            all_text += f"*{perm_level}:*\n"
                            for cmd_info in commands_by_perm[perm_level]:
                                all_text += f"  • {cmd_info}\n"
                            all_text += "\n"

            # Split into chunks if too long (Discord DM limit is 2000 chars per message)
            chunks = []
            current_chunk = ""
            for line in all_text.split("\n"):
                if len(current_chunk) + len(line) + 1 > 1950:
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = line
                else:
                    current_chunk += line + "\n"
            if current_chunk:
                chunks.append(current_chunk)

            # Send DMs
            if chunks:
                await ctx.send(f"{EMOJI_ENVELOPE} Sending command list to your DMs...")
                for chunk in chunks:
                    try:
                        await ctx.author.send(chunk)
                    except discord.Forbidden:
                        await ctx.send(embed=self.make_embed(
                            ctx,
                            f"{EMOJI_CROSS} DM Failed",
                            "I couldn't send you a DM. Make sure DMs are enabled.",
                            LOSS_COLOR
                        ))
                        return

                await ctx.send(embed=self.make_embed(
                    ctx,
                    f"{EMOJI_CHECK} Command List Sent",
                    f"Check your DMs! ({len(chunks)} messages)",
                    WIN_COLOR
                ))
            else:
                await ctx.send(embed=self.make_embed(
                    ctx,
                    f"{EMOJI_INFO} No Commands",
                    "No commands found to display.",
                    MAIN_COLOR
                ))

        except Exception as e:
            await ctx.send(embed=self.make_embed(
                ctx,
                f"{EMOJI_WARNING} Error",
                f"Failed to generate command list: {str(e)[:100]}",
                LOSS_COLOR
            ))


async def setup(bot):
    await bot.add_cog(Help(bot))