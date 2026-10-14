import json
import os
import time
import logging
from urllib.parse import urlparse
import discord
from discord.ext import commands
import asyncpg
from dotenv import load_dotenv

from utils.emoji import EMOJIS
from utils.components import ComponentsV2View
from utils.database import run_transaction
load_dotenv()

HI_COOLDOWN = 600
TRIGGER_COOLDOWN = 10
AFK_MENTION_COOLDOWN = 30
log = logging.getLogger("nuvella.triggers")

# ── Light Purple Theme ──
MAIN_COLOR = 0xb19cd9
WIN_COLOR  = 0x8e44ad
LOSS_COLOR = 0xe74c3c

DEFAULT_WELCOME_CHANNEL_ID = 1535962095663980546

# ── Custom Discord Emote Constants ──



def embed_base(title, description, color=MAIN_COLOR, ctx=None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow()
    )
    if ctx:
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url
        )
        if ctx.guild:
            embed.set_author(
                name=ctx.guild.name,
                icon_url=ctx.guild.icon.url if ctx.guild.icon else None
            )
    return embed


def elevated_perms_check():
    """
    Allows users who are:
    - Bot owner (via discord.py's is_owner)
    - In config's owner_id
    - Have the configured Admin or Senior Moderator role.
    """
    async def predicate(ctx):
        if await ctx.bot.is_owner(ctx.author):
            return True

        config = ctx.cog.config_data
        owner_id = config.get("owner_id")
        roles = config.get("roles", {})

        # Check owner_id (could be int or list)
        if isinstance(owner_id, int):
            if ctx.author.id == owner_id:
                return True
        elif isinstance(owner_id, list):
            if ctx.author.id in owner_id:
                return True

        if ctx.guild:
            if getattr(getattr(ctx.author, "guild_permissions", None), "administrator", False):
                return True
            author_role_ids = [role.id for role in ctx.author.roles]

            # Collect elevated role IDs from config
            elevated_role_ids = []
            for key in ("admin_role_id", "senior_moderator_role_id"):
                rid = roles.get(key)
                if rid is not None:
                    try: elevated_role_ids.append(int(rid))
                    except (TypeError, ValueError): pass

            # Check if author has any of these roles
            if any(rid in author_role_ids for rid in elevated_role_ids):
                return True

        return False
    return commands.check(predicate)


class TriggerRecordModal(discord.ui.Modal):
    """Private create/edit form; secret settings never appear in the channel body."""
    def __init__(self, panel: "TriggerPanelView", record: dict | None = None):
        self.panel = panel
        self.record = record
        super().__init__(title="Edit Trigger" if record else "Create Trigger", timeout=180)
        self.name_input = discord.ui.TextInput(
            label="Trigger word / phrase", default=(record or {}).get("trigger", ""),
            placeholder="e.g. good morning", max_length=100, required=True,
        )
        self.response_input = discord.ui.TextInput(
            label="Response text", default=(record or {}).get("response", ""),
            placeholder="What should the bot respond with?", style=discord.TextStyle.paragraph,
            max_length=1800, required=False,
        )
        self.url_input = discord.ui.TextInput(
            label="Image URL (optional)", default=(record or {}).get("url") or "",
            placeholder="https://… or leave blank", max_length=500, required=False,
        )
        self.cooldown_input = discord.ui.TextInput(
            label="Cooldown seconds (0–60)", default=str((record or {}).get("cooldown", 0)),
            placeholder="0", max_length=2, required=True,
        )
        for item in (self.name_input, self.response_input, self.url_input, self.cooldown_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.panel.author_id:
            return await interaction.response.send_message("This form belongs to another moderator.", ephemeral=True)
        name = self.name_input.value.strip().lower()
        response = self.response_input.value.strip()
        raw_url = self.url_input.value.strip()
        url = raw_url or None
        try:
            cooldown = int(self.cooldown_input.value.strip())
        except ValueError:
            return await interaction.response.send_message("Cooldown must be a whole number from 0 to 60.", ephemeral=True)
        if not name:
            return await interaction.response.send_message("Trigger name cannot be empty.", ephemeral=True)
        if not response and not url:
            return await interaction.response.send_message("Provide response text, an image URL, or both.", ephemeral=True)
        if not 0 <= cooldown <= 60:
            return await interaction.response.send_message("Cooldown must be from 0 to 60 seconds.", ephemeral=True)
        if url:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                return await interaction.response.send_message("Image URL must be a valid HTTP(S) URL.", ephemeral=True)
        old_name = self.record.get("trigger") if self.record else None
        try:
            await self.panel.cog.save_trigger_record(
                self.panel.guild_id, old_name, name, response, url, cooldown, interaction.user.id
            )
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)
        except Exception:
            log.exception("Trigger form save failed | guild_id=%s | actor_id=%s", self.panel.guild_id, interaction.user.id)
            return await interaction.response.send_message("Could not save the trigger. Check `logs/errors.log`.", ephemeral=True)
        await interaction.response.send_message(f"{EMOJIS.get('tick', '✅')} Trigger `{name}` saved.", ephemeral=True)
        await self.panel.refresh_data()
        await self.panel.update_panel()


class TriggerPanelView(discord.ui.View):
    """Single interactive trigger dashboard with owner-bound controls."""
    def __init__(self, cog: "Extra", guild_id: int, author_id: int, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.guild_id = guild_id
        self.author_id = author_id
        self.records: dict[str, dict] = {}
        self.selected_name: str | None = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This trigger panel belongs to another moderator.", ephemeral=True)
            return False
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message("This panel can only be used in its original server.", ephemeral=True)
            return False
        return True

    async def refresh_data(self) -> None:
        self.records = await self.cog.get_triggers(self.guild_id)
        if self.selected_name not in self.records:
            self.selected_name = next(iter(self.records), None)
        self._rebuild()

    def render_embed(self) -> discord.Embed:
        rows = []
        for idx, (name, record) in enumerate(sorted(self.records.items())[:20], 1):
            enabled = EMOJIS.get("green_circle", "🟢") if record.get("enabled", True) else EMOJIS.get("black_circle", "⚫")
            selected = f" {EMOJIS.get('arrow_right', '➡️')} selected" if name == self.selected_name else ""
            rows.append(
                f"`{idx:02}.` {enabled} **{discord.utils.escape_markdown(name)}** · "
                f"`{record.get('match_type', 'contains')}` · `{record.get('mode', 'text')}` · "
                f"`{int(record.get('cooldown') or 0)}s` · `{int(record.get('uses') or 0)} uses`{selected}"
            )
        description = "\n".join(rows) if rows else "No triggers yet. Click **New Trigger** to add one."
        if len(self.records) > 20:
            description += f"\n…and {len(self.records) - 20} more triggers. Use the dropdown to select any of the first 25."
        record = self.records.get(self.selected_name or "")
        if record:
            description += "\n\n**Selected response**\n" + ((record.get("response") or "*empty*")[:700])
            if record.get("url"):
                description += f"\n**Image URL:** {record['url'][:500]}"
            description += f"\n**Reply mention:** {'ON' if record.get('ping', True) else 'OFF'} · **Enabled:** {'yes' if record.get('enabled', True) else 'no'}"
        embed = discord.Embed(
            title=f"{EMOJIS.get('lightning', '⚡')} Trigger Dashboard",
            description=description,
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Select a trigger, then use the controls below. Only the panel creator can edit it.")
        return embed

    def _rebuild(self) -> None:
        self.clear_items()
        choices = sorted(self.records.items())[:25]
        if choices:
            select = discord.ui.Select(
                placeholder="Select a trigger to manage…", min_values=1, max_values=1, row=0,
                options=[discord.SelectOption(
                    label=name[:100], value=name,
                    description=f"{rec.get('match_type', 'contains')} · {rec.get('mode', 'text')} · {'enabled' if rec.get('enabled', True) else 'disabled'}"[:100],
                    default=name == self.selected_name,
                ) for name, rec in choices],
            )
            select.callback = self._select
            self.add_item(select)

        specs = [
            ("New Trigger", discord.ButtonStyle.success, self._new, 1),
            ("Edit", discord.ButtonStyle.secondary, self._edit, 1),
            ("Enabled", discord.ButtonStyle.secondary, self._toggle_enabled, 1),
            ("Match: exact/contains", discord.ButtonStyle.secondary, self._toggle_match, 2),
            ("Mode: text/embed", discord.ButtonStyle.secondary, self._toggle_mode, 2),
            ("Reply: on/off", discord.ButtonStyle.secondary, self._toggle_ping, 2),
            ("Test", discord.ButtonStyle.secondary, self._test, 3),
            ("Delete", discord.ButtonStyle.danger, self._delete, 3),
            ("Refresh", discord.ButtonStyle.secondary, self._refresh_panel, 3),
            ("Exit", discord.ButtonStyle.secondary, self._exit, 3),
        ]
        for label, style, callback, row in specs:
            button = discord.ui.Button(label=label, style=style, row=row)
            button.callback = callback
            self.add_item(button)

    async def update_panel(self) -> None:
        if self.message:
            try:
                await self.message.edit(embed=self.render_embed(), view=self)
            except discord.HTTPException:
                log.exception("Could not update trigger dashboard message | guild_id=%s", self.guild_id)

    async def _select(self, interaction: discord.Interaction) -> None:
        self.selected_name = interaction.data["values"][0]
        await self.refresh_data()
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    async def _new(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TriggerRecordModal(self))

    async def _edit(self, interaction: discord.Interaction) -> None:
        record = self.records.get(self.selected_name or "")
        if not record:
            return await interaction.response.send_message("Select a trigger first.", ephemeral=True)
        await interaction.response.send_modal(TriggerRecordModal(self, record))

    async def _toggle(self, interaction: discord.Interaction, field: str, new_value) -> None:
        record = self.records.get(self.selected_name or "")
        if not record:
            return await interaction.response.send_message("Select a trigger first.", ephemeral=True)
        async with self.cog.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE triggers SET {field}=$1, updated_by=$2, updated_at=now() WHERE guild_id=$3 AND trigger=$4",
                new_value, interaction.user.id, self.guild_id, self.selected_name,
            )
        self.cog.invalidate_trigger_cache(self.guild_id)
        log.info("Trigger setting changed | guild_id=%s | trigger=%s | field=%s | value=%s | actor_id=%s",
                 self.guild_id, self.selected_name, field, new_value, interaction.user.id)
        await self.refresh_data()
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    async def _toggle_enabled(self, interaction: discord.Interaction) -> None:
        record = self.records.get(self.selected_name or "")
        if not record:
            return await interaction.response.send_message("Select a trigger first.", ephemeral=True)
        await self._toggle(interaction, "enabled", not bool(record.get("enabled", True)))

    async def _toggle_match(self, interaction: discord.Interaction) -> None:
        record = self.records.get(self.selected_name or "")
        if not record:
            return await interaction.response.send_message("Select a trigger first.", ephemeral=True)
        await self._toggle(interaction, "match_type", "exact" if record.get("match_type") != "exact" else "contains")

    async def _toggle_mode(self, interaction: discord.Interaction) -> None:
        record = self.records.get(self.selected_name or "")
        if not record:
            return await interaction.response.send_message("Select a trigger first.", ephemeral=True)
        await self._toggle(interaction, "mode", "embed" if record.get("mode") != "embed" else "text")

    async def _toggle_ping(self, interaction: discord.Interaction) -> None:
        record = self.records.get(self.selected_name or "")
        if not record:
            return await interaction.response.send_message("Select a trigger first.", ephemeral=True)
        await self._toggle(interaction, "ping", not bool(record.get("ping", True)))

    async def _test(self, interaction: discord.Interaction) -> None:
        record = self.records.get(self.selected_name or "")
        if not record:
            return await interaction.response.send_message("Select a trigger first.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        response = (record.get("response") or "").strip()
        url = (record.get("url") or "").strip()
        try:
            if record.get("mode") == "embed" or url:
                embed = discord.Embed(description=response or None, color=MAIN_COLOR)
                if url:
                    embed.set_image(url=url)
                await interaction.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            else:
                await interaction.channel.send(response, allowed_mentions=discord.AllowedMentions.none())
            await interaction.followup.send("Test response sent in this channel.", ephemeral=True)
        except discord.HTTPException:
            log.exception("Trigger test failed | guild_id=%s | trigger=%s", self.guild_id, self.selected_name)
            await interaction.followup.send("Test failed; see logs/errors.log.", ephemeral=True)

    async def _delete(self, interaction: discord.Interaction) -> None:
        if not self.selected_name or self.selected_name not in self.records:
            return await interaction.response.send_message("Select a trigger first.", ephemeral=True)
        name = self.selected_name
        async with self.cog.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM triggers WHERE guild_id=$1 AND trigger=$2", self.guild_id, name)
        self.cog.invalidate_trigger_cache(self.guild_id)
        if result == "DELETE 0":
            await interaction.response.send_message("The trigger was already removed. Refresh the panel.", ephemeral=True)
        else:
            log.info("Trigger deleted | guild_id=%s | trigger=%s | actor_id=%s", self.guild_id, name, interaction.user.id)
            await self.refresh_data()
            await interaction.response.edit_message(embed=self.render_embed(), view=self)

    async def _refresh_panel(self, interaction: discord.Interaction) -> None:
        # NOTE: intentionally NOT named `_refresh`, because discord.ui.View
        # defines an internal synchronous `_refresh(components)` that is
        # invoked by the library whenever children change. Overriding it
        # with an async method produced:
        #   RuntimeWarning: coroutine 'TriggerPanelView._refresh' was never awaited
        await self.refresh_data()
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    async def _exit(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(content="Trigger dashboard closed.", embed=None, view=None)

    async def on_timeout(self) -> None:
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Extra(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pool = None
        self.config_data = {}
        self.hi_cooldowns = {}
        self.trigger_cooldowns = {}
        self.afk_mention_cooldowns = {}
        self._trigger_cache: dict[int, dict[str, dict]] = {}
        self._trigger_cache_at: dict[int, float] = {}

    async def cog_load(self):
        with open("config.json", "r", encoding="utf-8") as f:
            self.config_data = json.load(f)

        self.pool = getattr(self.bot, "pool", None)
        if self.pool is None:
            raise RuntimeError("The shared PostgreSQL pool was not initialized by main.py.")

        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS triggers (
                    guild_id BIGINT NOT NULL,
                    trigger TEXT NOT NULL,
                    type TEXT NOT NULL,
                    response TEXT NOT NULL,
                    PRIMARY KEY (guild_id, trigger)
                );
                CREATE TABLE IF NOT EXISTS afk (
                    user_id BIGINT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    timestamp BIGINT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS welcome_config (
                    guild_id BIGINT PRIMARY KEY,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    channel_id BIGINT
                );
                CREATE TABLE IF NOT EXISTS customgreet_config (
                    guild_id BIGINT PRIMARY KEY,
                    enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    message TEXT NOT NULL DEFAULT 'I greet thee, the Lord and Master of the Northern Frost {user}.',
                    cooldown INTEGER NOT NULL DEFAULT 1200,
                    role_ids BIGINT[] NOT NULL DEFAULT '{}',
                    user_ids BIGINT[] NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS customgreet_last_times (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    last_time DOUBLE PRECISION NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)
            # Additive, restart-safe migration: preserve every existing trigger.
            await conn.execute("ALTER TABLE triggers ADD COLUMN IF NOT EXISTS match_type TEXT NOT NULL DEFAULT 'contains'")
            await conn.execute("ALTER TABLE triggers ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'text'")
            await conn.execute("ALTER TABLE triggers ADD COLUMN IF NOT EXISTS ping BOOLEAN NOT NULL DEFAULT TRUE")
            await conn.execute("ALTER TABLE triggers ADD COLUMN IF NOT EXISTS cooldown INTEGER NOT NULL DEFAULT 0")
            await conn.execute("ALTER TABLE triggers ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE")
            await conn.execute("ALTER TABLE triggers ADD COLUMN IF NOT EXISTS url TEXT")
            await conn.execute("ALTER TABLE triggers ADD COLUMN IF NOT EXISTS uses BIGINT NOT NULL DEFAULT 0")
            await conn.execute("ALTER TABLE triggers ADD COLUMN IF NOT EXISTS created_by BIGINT")
            await conn.execute("ALTER TABLE triggers ADD COLUMN IF NOT EXISTS updated_by BIGINT")
            await conn.execute("ALTER TABLE triggers ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()")
            await conn.execute("UPDATE triggers SET url = response, response = '', mode = 'embed' WHERE type IN ('image', 'gif') AND url IS NULL")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_triggers_guild_enabled ON triggers (guild_id, enabled)")
        log.info("Extra cog schema ready; triggers use migrated interactive settings")

    async def cog_unload(self):
        # The PostgreSQL pool belongs to the bot and is shared by every cog.
        return

    # ─── Helper: Get logging channel ID from config ───
    def get_logging_channel_id(self) -> int | None:
        # First try top-level
        channel_id = self.config_data.get("logging_channel_id")
        if channel_id is not None:
            return int(channel_id)
        # Then try inside "roles"
        roles = self.config_data.get("roles", {})
        channel_id = roles.get("logging_channel_id")
        if channel_id is not None:
            return int(channel_id)
        return None

    # ─── Helper: Send command log to logging channel ──
    async def log_command_usage(self, ctx, success: bool, error: Exception = None):
        channel_id = self.get_logging_channel_id()
        if not channel_id:
            return  # No logging channel configured

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        # Build embed
        embed = discord.Embed(
            title=f"Command {'Succeeded' if success else 'Failed'}",
            color=WIN_COLOR if success else LOSS_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="User", value=f"{ctx.author} (ID: {ctx.author.id})", inline=False)
        embed.add_field(name="Command", value=f"`{ctx.message.content}`", inline=False)
        embed.add_field(name="Channel", value=ctx.channel.mention if ctx.guild else "DM", inline=True)
        embed.add_field(name="Guild", value=ctx.guild.name if ctx.guild else "N/A", inline=True)
        if not success and error:
            embed.add_field(name="Error", value=str(error), inline=False)

        try:
            await channel.send(view=ComponentsV2View(timeout=120, embed=embed))
        except discord.Forbidden:
            pass  # Bot lacks permission to send in logging channel
        except Exception:
            pass

    # ─── Listener: Successful command ────────────────
    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        await self.log_command_usage(ctx, success=True)

    # ─── Welcome Event ─────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member):
        if not member.guild:
            return
        enabled = await self.get_welcome_enabled(member.guild.id)
        if not enabled:
            return
        channel_id = await self.get_welcome_channel(member.guild.id) or DEFAULT_WELCOME_CHANNEL_ID
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return
        embed = discord.Embed(
            title=f"{EMOJIS['moon']} Welcome!",
            description=(
                f"Hey lunar traveller {member.mention}, enjoy your time here and if you want roles go to "
                f"<#1536190617359482990> and Check about the server in <#1535962074415505510>"
            ),
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(
            text=f"Joined {member.guild.name}",
            icon_url=member.guild.icon.url if member.guild.icon else None
        )
        try:
            await channel.send(view=ComponentsV2View(timeout=120, embed=embed))
        except Exception:
            pass

    # ─── Welcome DB helpers ───────────────────────────
    async def get_welcome_enabled(self, guild_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT enabled FROM welcome_config WHERE guild_id = $1", guild_id)
            if row is None:
                await conn.execute("INSERT INTO welcome_config (guild_id, enabled) VALUES ($1, TRUE)", guild_id)
                return True
            return row["enabled"]

    async def set_welcome_enabled(self, guild_id: int, enabled: bool):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO welcome_config (guild_id, enabled)
                VALUES ($1, $2)
                ON CONFLICT (guild_id) DO UPDATE SET enabled = $2
            """, guild_id, enabled)

    async def get_welcome_channel(self, guild_id: int) -> int | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT channel_id FROM welcome_config WHERE guild_id = $1", guild_id)
            return row["channel_id"] if row else None

    # ─── Welcome Toggle Commands (elevated perms) ──────
    @commands.group(name="welcome", invoke_without_command=True)
    @elevated_perms_check()
    async def welcome_group(self, ctx):
        state = "ENABLED" if await self.get_welcome_enabled(ctx.guild.id) else "DISABLED"
        embed = embed_base(
            f"{EMOJIS['moon']} Welcome System",
            f"The welcomer is currently **{state}**.\n"
            f"Use `{ctx.clean_prefix}welcome on` or `{ctx.clean_prefix}welcome off` to toggle.\n"
            f"*Requires the bot owner, Admin, or Senior Moderator role.*",
            MAIN_COLOR,
            ctx
        )
        await ctx.send(embed=embed)

    @welcome_group.command(name="on")
    @elevated_perms_check()
    async def welcome_on(self, ctx):
        await self.set_welcome_enabled(ctx.guild.id, True)
        embed = embed_base(f"{EMOJIS['tick']} Welcome Enabled", "New members will now receive a welcome message.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @welcome_group.command(name="off")
    @elevated_perms_check()
    async def welcome_off(self, ctx):
        await self.set_welcome_enabled(ctx.guild.id, False)
        embed = embed_base(f"{EMOJIS['cross']} Welcome Disabled", "The welcome message has been turned off.", LOSS_COLOR, ctx)
        await ctx.send(embed=embed)

    # ─── Custom Greet DB helpers ──────────────────────
    async def get_customgreet_config(self, guild_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM customgreet_config WHERE guild_id = $1", guild_id)
            if row is None:
                await conn.execute(
                    "INSERT INTO customgreet_config (guild_id) VALUES ($1) ON CONFLICT DO NOTHING",
                    guild_id
                )
                row = await conn.fetchrow("SELECT * FROM customgreet_config WHERE guild_id = $1", guild_id)
            return row

    async def update_customgreet_config(self, guild_id: int, **kwargs):
        columns = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(kwargs.keys()))
        values = list(kwargs.values())
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO customgreet_config (guild_id, {', '.join(kwargs.keys())}) "
                f"VALUES ($1, {', '.join(f'${i+2}' for i in range(len(kwargs)))}) "
                f"ON CONFLICT (guild_id) DO UPDATE SET {columns}",
                guild_id, *values
            )

    async def get_customgreet_last_time(self, guild_id: int, user_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_time FROM customgreet_last_times WHERE guild_id = $1 AND user_id = $2",
                guild_id, user_id
            )
            return row["last_time"] if row else 0.0

    async def set_customgreet_last_time(self, guild_id: int, user_id: int, last_time: float):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO customgreet_last_times (guild_id, user_id, last_time) "
                "VALUES ($1, $2, $3) ON CONFLICT (guild_id, user_id) DO UPDATE SET last_time = $3",
                guild_id, user_id, last_time
            )

    async def reset_customgreet_times(self, guild_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM customgreet_last_times WHERE guild_id = $1", guild_id)

    # ─── Custom Greet Commands (elevated perms) ─────────
    @commands.group(name="customgreet", invoke_without_command=True)
    @elevated_perms_check()
    async def customgreet_group(self, ctx):
        cfg = await self.get_customgreet_config(ctx.guild.id)
        status = f"{EMOJIS['tick']} Enabled" if cfg["enabled"] else f"{EMOJIS['cross']} Disabled"
        cooldown_min = cfg["cooldown"] // 60
        roles_mentions = ", ".join(f"<@&{rid}>" for rid in cfg["role_ids"]) or "None"
        users_mentions = ", ".join(f"<@{uid}>" for uid in cfg["user_ids"]) or "None"
        embed = embed_base(
            f"{EMOJIS['greet']} Custom Greet System",
            f"**Status:** {status}\n"
            f"**Message:** {cfg['message']}\n"
            f"**Inactivity cooldown:** {cooldown_min} minute(s)\n"
            f"**Greeted Roles:** {roles_mentions}\n"
            f"**Greeted Users:** {users_mentions}\n\n"
            f"Use subcommands to configure.\n"
            f"*Requires the bot owner, Admin, or Senior Moderator role.*",
            MAIN_COLOR,
            ctx
        )
        await ctx.send(embed=embed)

    @customgreet_group.command(name="on")
    @elevated_perms_check()
    async def customgreet_on(self, ctx):
        await self.update_customgreet_config(ctx.guild.id, enabled=True)
        embed = embed_base(f"{EMOJIS['tick']} Custom Greet Enabled", "The bot will now greet returning users.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_group.command(name="off")
    @elevated_perms_check()
    async def customgreet_off(self, ctx):
        await self.update_customgreet_config(ctx.guild.id, enabled=False)
        embed = embed_base(f"{EMOJIS['cross']} Custom Greet Disabled", "The bot will no longer greet returning users.", LOSS_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_group.command(name="message")
    @elevated_perms_check()
    async def customgreet_message(self, ctx, *, message: str):
        await self.update_customgreet_config(ctx.guild.id, message=message)
        embed = embed_base(f"{EMOJIS['notes']} Greeting Message Set", f"New message: {message}", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_group.command(name="cooldown")
    @elevated_perms_check()
    async def customgreet_cooldown(self, ctx, minutes: int):
        if minutes < 1:
            return await ctx.send(embed=embed_base("Invalid Cooldown", "Must be at least 1 minute.", LOSS_COLOR, ctx))
        await self.update_customgreet_config(ctx.guild.id, cooldown=minutes * 60)
        embed = embed_base(f"{EMOJIS['cooldown']} Cooldown Updated", f"Inactivity threshold set to **{minutes}** minute(s).", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_group.command(name="reset")
    @elevated_perms_check()
    async def customgreet_reset(self, ctx):
        await self.reset_customgreet_times(ctx.guild.id)
        embed = embed_base(f"{EMOJIS['refresh']} Times Reset", "All last‑message times have been cleared.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_group.group(name="role")
    @elevated_perms_check()
    async def customgreet_role(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `customgreet role add <role>` or `customgreet role remove <role>`")

    @customgreet_role.command(name="add")
    @elevated_perms_check()
    async def customgreet_role_add(self, ctx, role: discord.Role):
        cfg = await self.get_customgreet_config(ctx.guild.id)
        if role.id in cfg["role_ids"]:
            return await ctx.send(embed=embed_base("Already Added", f"{role.mention} is already in the greet list.", LOSS_COLOR, ctx))
        new_roles = list(cfg["role_ids"]) + [role.id]
        await self.update_customgreet_config(ctx.guild.id, role_ids=new_roles)
        embed = embed_base(f"{EMOJIS['tick']} Role Added", f"Members with {role.mention} will now be greeted upon return.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_role.command(name="remove")
    @elevated_perms_check()
    async def customgreet_role_remove(self, ctx, role: discord.Role):
        cfg = await self.get_customgreet_config(ctx.guild.id)
        if role.id not in cfg["role_ids"]:
            return await ctx.send(embed=embed_base("Not Found", f"{role.mention} is not in the greet list.", LOSS_COLOR, ctx))
        new_roles = [r for r in cfg["role_ids"] if r != role.id]
        await self.update_customgreet_config(ctx.guild.id, role_ids=new_roles)
        embed = embed_base(f"{EMOJIS['tick']} Role Removed", f"Members with {role.mention} will no longer be greeted.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_group.group(name="user")
    @elevated_perms_check()
    async def customgreet_user(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `customgreet user add <member>` or `customgreet user remove <member>`")

    @customgreet_user.command(name="add")
    @elevated_perms_check()
    async def customgreet_user_add(self, ctx, member: discord.Member):
        cfg = await self.get_customgreet_config(ctx.guild.id)
        if member.id in cfg["user_ids"]:
            return await ctx.send(embed=embed_base("Already Added", f"{member.mention} is already in the greet list.", LOSS_COLOR, ctx))
        new_users = list(cfg["user_ids"]) + [member.id]
        await self.update_customgreet_config(ctx.guild.id, user_ids=new_users)
        embed = embed_base(f"{EMOJIS['tick']} User Added", f"{member.mention} will now be greeted upon return.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_user.command(name="remove")
    @elevated_perms_check()
    async def customgreet_user_remove(self, ctx, member: discord.Member):
        cfg = await self.get_customgreet_config(ctx.guild.id)
        if member.id not in cfg["user_ids"]:
            return await ctx.send(embed=embed_base("Not Found", f"{member.mention} is not in the greet list.", LOSS_COLOR, ctx))
        new_users = [u for u in cfg["user_ids"] if u != member.id]
        await self.update_customgreet_config(ctx.guild.id, user_ids=new_users)
        embed = embed_base(f"{EMOJIS['tick']} User Removed", f"{member.mention} will no longer be greeted.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    # ─── Message Listener ─────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        content = message.content.strip().lower()
        now = time.time()
        guild_id = message.guild.id
        user_id = message.author.id

        # Custom greet
        cfg = await self.get_customgreet_config(guild_id)
        if cfg["enabled"]:
            should_greet = False
            if cfg["role_ids"] or cfg["user_ids"]:
                if any(role.id in cfg["role_ids"] for role in message.author.roles):
                    should_greet = True
                if message.author.id in cfg["user_ids"]:
                    should_greet = True
            if should_greet:
                last_time = await self.get_customgreet_last_time(guild_id, user_id)
                if now - last_time >= cfg["cooldown"]:
                    await self.set_customgreet_last_time(guild_id, user_id, now)
                    greeting = cfg["message"].replace("{user}", message.author.mention)
                    try:
                        await message.reply(greeting, mention_author=False)
                    except Exception:
                        pass

        # Hi/Hello auto-reply
        if content in ("hi", "hello"):
            key = (guild_id, user_id)
            last = self.hi_cooldowns.get(key, 0)
            if now - last >= HI_COOLDOWN:
                self.hi_cooldowns[key] = now
                await message.reply("hi : D", mention_author=False)

        # Trigger system: deterministic longest-match-first avoids overlapping
        # phrases firing the shorter trigger by accident. Each trigger has its
        # own per-user cooldown so unrelated triggers do not block one another.
        try:
            triggers = await self.get_triggers(guild_id)
            for trigger, data in sorted(triggers.items(), key=lambda item: len(item[0]), reverse=True):
                if not data.get("enabled", True):
                    continue
                matched = (content.strip() == trigger) if data.get("match_type") == "exact" else (trigger in content)
                if not matched:
                    continue
                tkey = (guild_id, user_id, trigger)
                last_trigger = self.trigger_cooldowns.get(tkey, 0)
                cooldown = max(0, min(int(data.get("cooldown") or 0), 3600))
                if now - last_trigger >= cooldown:
                    self.trigger_cooldowns[tkey] = now
                    await self.send_trigger_response(message, data)
                break
        except Exception:
            logging.getLogger("nuvella.database").exception(
                "Trigger processing failed | guild_id=%s | message_id=%s", guild_id, message.id
            )

        # AFK removal
        if await self.is_afk(user_id):
            prefixes = await self.bot.get_prefix(message)
            is_command = any(message.content.startswith(p) for p in prefixes)
            if not is_command:
                await self.remove_afk(user_id)
                embed = embed_base(
                    f"{EMOJIS['panda_wave']} Welcome Back!",
                    "Your AFK status has been removed.",
                    WIN_COLOR
                )
                embed.set_footer(text=f"Welcome back, {message.author.display_name}!",
                                 icon_url=message.author.display_avatar.url)
                try:
                    await message.reply(embed=embed, mention_author=False)
                except Exception:
                    pass

        # AFK mention check
        for mentioned in message.mentions:
            if mentioned.id != user_id and await self.is_afk(mentioned.id):
                last_ping = self.afk_mention_cooldowns.get(mentioned.id, 0)
                if now - last_ping < AFK_MENTION_COOLDOWN:
                    continue
                self.afk_mention_cooldowns[mentioned.id] = now
                afk_data = await self.get_afk_data(mentioned.id)
                reason = afk_data["reason"]
                since = afk_data["timestamp"]
                embed = embed_base(
                    f"{EMOJIS['purple_zzz']} User is AFK",
                    f"{mentioned.mention} is currently AFK.",
                    MAIN_COLOR
                )
                embed.add_field(name="Reason", value=reason, inline=False)
                if since:
                    embed.add_field(name="AFK Since", value=f"<t:{since}:R>", inline=True)
                embed.set_footer(text=f"Requested by {message.author}",
                                 icon_url=message.author.display_avatar.url)
                await message.reply(embed=embed, mention_author=False)
                break

    async def send_trigger_response(self, message, data):
        """Send one trigger response and count successful uses."""
        response = (data.get("response") or "").strip()
        image_url = (data.get("url") or "").strip()
        mode = data.get("mode") or ("embed" if data.get("type") in ("image", "gif") else "text")
        ping = bool(data.get("ping", True))
        try:
            if mode == "embed":
                embed = discord.Embed(
                    description=response[:4000] if response else None,
                    color=MAIN_COLOR,
                    timestamp=discord.utils.utcnow(),
                )
                if image_url:
                    embed.set_image(url=image_url)
                if not response and not image_url:
                    return
                await message.reply(embed=embed, mention_author=ping)
            elif response:
                await message.reply(response[:2000], mention_author=ping)
                if image_url:
                    image_embed = discord.Embed(color=MAIN_COLOR)
                    image_embed.set_image(url=image_url)
                    await message.channel.send(embed=image_embed, allowed_mentions=discord.AllowedMentions.none())
            elif image_url:
                embed = discord.Embed(color=MAIN_COLOR).set_image(url=image_url)
                await message.reply(embed=embed, mention_author=ping)
            else:
                return
        except discord.HTTPException:
            log.exception("Failed to send trigger response | guild_id=%s | trigger=%s", message.guild.id, data.get("trigger"))
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE triggers SET uses = uses + 1, updated_at = now() WHERE guild_id = $1 AND trigger = $2",
                message.guild.id, data.get("trigger", ""),
            )

    # ─── Trigger DB helpers ───────────────────────────
    def invalidate_trigger_cache(self, guild_id: int) -> None:
        self._trigger_cache.pop(guild_id, None)
        self._trigger_cache_at.pop(guild_id, None)

    async def get_triggers(self, guild_id: int, *, force: bool = False) -> dict:
        # A small per-guild cache keeps normal message traffic from querying
        # PostgreSQL for every message. Admin edits invalidate it immediately.
        cached_at = self._trigger_cache_at.get(guild_id, 0.0)
        if not force and guild_id in self._trigger_cache and time.monotonic() - cached_at < 10:
            return self._trigger_cache[guild_id]
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT trigger, type, response, match_type, mode, ping, cooldown, enabled, url, uses "
                "FROM triggers WHERE guild_id = $1 ORDER BY trigger", guild_id
            )
        records = {row["trigger"]: {**dict(row), "trigger": row["trigger"]} for row in rows}
        self._trigger_cache[guild_id] = records
        self._trigger_cache_at[guild_id] = time.monotonic()
        return records

    async def add_trigger_to_db(self, guild_id: int, trigger: str, response_type: str, response: str, *, actor_id: int | None = None):
        trigger = trigger.strip().lower()
        is_media = response_type in ("image", "gif")
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO triggers (guild_id, trigger, type, response, mode, url, created_by, updated_by)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $7)
                   ON CONFLICT (guild_id, trigger) DO UPDATE SET
                     type = EXCLUDED.type, response = EXCLUDED.response,
                     mode = EXCLUDED.mode, url = EXCLUDED.url,
                     updated_by = EXCLUDED.updated_by, updated_at = now()""",
                guild_id, trigger, response_type, "" if is_media else response,
                "embed" if is_media else "text", response if is_media else None, actor_id,
            )
        self.invalidate_trigger_cache(guild_id)

    async def remove_trigger_from_db(self, guild_id: int, trigger: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM triggers WHERE guild_id = $1 AND trigger = $2", guild_id, trigger
            )
        self.invalidate_trigger_cache(guild_id)
        return result != "DELETE 0"

    async def clear_triggers_db(self, guild_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM triggers WHERE guild_id = $1", guild_id)
        self.invalidate_trigger_cache(guild_id)

    async def save_trigger_record(
        self, guild_id: int, old_name: str | None, name: str, response: str,
        url: str | None, cooldown: int, actor_id: int,
    ) -> None:
        """Validate and save an interactive trigger edit atomically."""
        name = name.strip().lower()
        response = response.strip()

        async def operation(conn):
            # Serialize create/rename edits per guild so simultaneous panels
            # cannot both pass the duplicate-name check. The advisory lock is
            # transaction-scoped and will be reacquired on deadlock retries.
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", guild_id)
            clash = await conn.fetchval(
                "SELECT 1 FROM triggers WHERE guild_id=$1 AND trigger=$2 AND trigger<>$3",
                guild_id, name, old_name or "",
            )
            if clash:
                raise ValueError(f"A trigger named '{name}' already exists.")
            if old_name:
                result = await conn.execute(
                    """UPDATE triggers SET trigger=$1, response=$2, url=$3,
                       type=$4, cooldown=$5, updated_by=$6, updated_at=now()
                       WHERE guild_id=$7 AND trigger=$8""",
                    name, response, url, "image" if url else "text",
                    cooldown, actor_id, guild_id, old_name,
                )
                if result == "UPDATE 0":
                    raise ValueError("This trigger was removed by another moderator. Refresh the panel.")
            else:
                await conn.execute(
                    """INSERT INTO triggers (guild_id, trigger, type, response, mode, url, cooldown, created_by, updated_by)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$8)""",
                    guild_id, name, "image" if url else "text", response,
                    "embed" if url else "text", url, cooldown, actor_id,
                )

        await run_transaction(self.pool, operation, label="triggers.save_record")
        self.invalidate_trigger_cache(guild_id)

    # ─── AFK DB helpers ───────────────────────────────
    async def is_afk(self, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT 1 FROM afk WHERE user_id = $1", user_id)
            return row is not None

    async def get_afk_data(self, user_id: int) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT reason, timestamp FROM afk WHERE user_id = $1", user_id)
            return dict(row) if row else None

    async def set_afk(self, user_id: int, reason: str, timestamp: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO afk (user_id, reason, timestamp) VALUES ($1, $2, $3) "
                "ON CONFLICT (user_id) DO UPDATE SET reason = $2, timestamp = $3",
                user_id, reason, timestamp
            )

    async def remove_afk(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM afk WHERE user_id = $1", user_id)

    # ─── AFK Command ──────────────────────────────────
    @commands.command(name="afk")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def afk(self, ctx, *, reason: str = "No reason provided."):
        # AFK status is a purchased shop perk; require the configured role just
        # like the game perks in fun.py. Support legacy top-level config keys.
        roles_cfg = self.config_data.get("roles", {}) or {}
        raw_role_id = roles_cfg.get("afk_role_id") or self.config_data.get("afk_perm")
        try:
            afk_role_id = int(raw_role_id or 0)
        except (TypeError, ValueError):
            afk_role_id = 0
        if not ctx.guild or not isinstance(ctx.author, discord.Member) or not afk_role_id:
            return await ctx.reply(
                "The AFK Permission shop role is not configured or this perk is only available in the server. Ask an admin to check the economy role mapping.",
                delete_after=10,
                mention_author=False,
            )
        if not any(role.id == afk_role_id for role in ctx.author.roles):
            return await ctx.reply(
                f"You need the **AFK Permission** shop role to use this command. Purchase it with `{ctx.clean_prefix}shop`.",
                delete_after=10,
                mention_author=False,
            )
        now = int(time.time())
        await self.set_afk(ctx.author.id, reason, now)
        embed = embed_base(
            f"{EMOJIS['purple_zzz']} AFK Set",
            f"{ctx.author.mention} is now AFK.",
            WIN_COLOR
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Since", value=f"<t:{now}:R>", inline=True)
        await ctx.reply(embed=embed, mention_author=False)

    @afk.error
    async def afk_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            embed = embed_base(
                f"{EMOJIS['cooldown']} Slow Down",
                f"Try again in {error.retry_after:.0f}s.",
                LOSS_COLOR
            )
            await ctx.send(embed=embed, delete_after=5)

    # ─── Catfish Command ──────────────────────────────
    @commands.command(name="catfish")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def catfish(self, ctx, user: discord.Member = None):
        user = user or ctx.author
        avatar_url = user.display_avatar.replace(static_format="png", size=1024).url
        yandex_url = f"https://yandex.com/images/search?rpt=imageview&url={avatar_url}"
        google_url = f"https://images.google.com/searchbyimage?image_url={avatar_url}"
        tineye_url = f"https://tineye.com/search?url={avatar_url}"
        embed = discord.Embed(
            title=f"{EMOJIS['search']} Catfish Check",
            description=(
                f"Looking if user - **{user.display_name}**'s is a catfish or not.\n\n"
                f"[ Yandex]({yandex_url})\n"
                f"[ Google]({google_url})\n"
                f"[ TinEye]({tineye_url})"
            ),
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_image(url=avatar_url)
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url
        )
        await ctx.reply(embed=embed, mention_author=False)

    @catfish.error
    async def catfish_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            embed = embed_base(
                f"{EMOJIS['cooldown']} Slow Down",
                f"Try again in {error.retry_after:.0f}s.",
                LOSS_COLOR
            )
            await ctx.send(embed=embed, delete_after=5)
        elif isinstance(error, commands.BadArgument):
            embed = embed_base(f"{EMOJIS['cross']} Error", "Member not found.", LOSS_COLOR)
            await ctx.send(embed=embed)

    # ─── Trigger Management Commands (elevated perms) ──
    @commands.group(name="triggers", aliases=["trigger"], invoke_without_command=True)
    @elevated_perms_check()
    async def triggers_group(self, ctx):
        if not ctx.guild:
            return await ctx.reply("Trigger management is only available in a server.", mention_author=False)
        view = TriggerPanelView(self, ctx.guild.id, ctx.author.id)
        await view.refresh_data()
        view.message = await ctx.send(embed=view.render_embed(), view=view)

    @triggers_group.command(name="add")
    @elevated_perms_check()
    async def add_trigger(self, ctx, trigger: str, response_type: str, *, response: str):
        valid_types = ("text", "image", "gif")
        response_type = response_type.lower()
        if response_type not in valid_types:
            embed = embed_base(
                f"{EMOJIS['cross']} Invalid Type",
                f"Please use one of: {', '.join(valid_types)}",
                LOSS_COLOR, ctx
            )
            return await ctx.send(embed=embed)
        trigger = trigger.strip().lower()
        if not trigger or len(trigger) > 100:
            return await ctx.send(embed=embed_base("Invalid Trigger", "Trigger must be 1–100 characters.", LOSS_COLOR, ctx))
        if response_type in ("image", "gif"):
            parsed = urlparse(response.strip())
            if parsed.scheme not in ("https", "http") or not parsed.netloc:
                return await ctx.send(embed=embed_base("Invalid URL", "Image/GIF triggers need a valid HTTP(S) URL.", LOSS_COLOR, ctx))
        await self.add_trigger_to_db(ctx.guild.id, trigger, response_type, response, actor_id=ctx.author.id)
        embed = embed_base(
            f"{EMOJIS['tick']} Trigger Added",
            f"**{trigger}** → *{response_type}*\nResponse: {response}",
            WIN_COLOR, ctx
        )
        await ctx.send(embed=embed)

    @triggers_group.command(name="remove")
    @elevated_perms_check()
    async def remove_trigger(self, ctx, *, trigger: str):
        trigger = trigger.lower()
        success = await self.remove_trigger_from_db(ctx.guild.id, trigger)
        if success:
            embed = embed_base(f"{EMOJIS['delete']} Trigger Removed", f"`{trigger}` has been deleted.", WIN_COLOR, ctx)
        else:
            embed = embed_base(f"{EMOJIS['cross']} Not Found", f"No trigger named `{trigger}`.", LOSS_COLOR, ctx)
        await ctx.send(embed=embed)

    @triggers_group.command(name="list")
    @elevated_perms_check()
    async def list_triggers(self, ctx):
        if not ctx.guild:
            return await ctx.reply("Trigger management is only available in a server.", mention_author=False)
        view = TriggerPanelView(self, ctx.guild.id, ctx.author.id)
        await view.refresh_data()
        view.message = await ctx.send(embed=view.render_embed(), view=view)

    @triggers_group.command(name="clear")
    @elevated_perms_check()
    async def clear_triggers(self, ctx):
        await self.clear_triggers_db(ctx.guild.id)
        embed = embed_base(f"{EMOJIS['broom']} Triggers Cleared", "All triggers have been removed.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    # ─── Error handling ───────────────────────────────
    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            embed = embed_base(f"{EMOJIS['cross']} Invalid Argument", "The argument you provided is not valid. Please check the command usage.", LOSS_COLOR, ctx)
            await ctx.send(embed=embed)
        elif isinstance(error, commands.MissingRequiredArgument):
            embed = embed_base(f"{EMOJIS['warning']} Missing Argument", "You are missing a required argument. Check the command syntax.", LOSS_COLOR, ctx)
            await ctx.send(embed=embed)
        elif isinstance(error, commands.CheckFailure):
            embed = embed_base(f"{EMOJIS['cross']} Permission Denied", "You do not have permission to use this command.", LOSS_COLOR, ctx)
            await ctx.send(embed=embed)
        elif isinstance(error, commands.CommandOnCooldown):
            retry_after = error.retry_after
            embed = embed_base(f"{EMOJIS['cooldown']} Cooldown", f"Please wait {retry_after:.0f} seconds before using this command again.", LOSS_COLOR, ctx)
            await ctx.send(embed=embed, delete_after=5)
        else:
            log.error("Trigger cog command error | command=%s | user_id=%s | error=%s", getattr(ctx.command, "qualified_name", "unknown"), getattr(ctx.author, "id", None), error, exc_info=(type(error), error, error.__traceback__))

async def setup(bot):
    await bot.add_cog(Extra(bot))
