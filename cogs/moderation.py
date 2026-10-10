# cogs/moderation.py
import discord
from discord.ext import commands
from discord import app_commands
import json
import time
import asyncio
import re
import random
import logging
import datetime
import unicodedata
import traceback
from typing import Optional, Union
from collections import defaultdict, deque
from urllib.parse import urlparse

from utils.emoji import EMOJIS
from utils.components import ComponentsV2View, prepare_v2_view, disable_all_components
logger = logging.getLogger(__name__)

# Timezone: prefer stdlib zoneinfo (Py3.9+); fall back to pytz
try:
    from zoneinfo import ZoneInfo, available_timezones
    TIMEZONE_AVAILABLE = True
    USE_ZONEINFO = True
except ImportError:
    ZoneInfo = None
    try:
        import pytz
        TIMEZONE_AVAILABLE = True
        USE_ZONEINFO = False
    except ImportError:
        TIMEZONE_AVAILABLE = False
        USE_ZONEINFO = False

try:
    import tldextract
    TLDEXTRACT_AVAILABLE = True
except ImportError:
    TLDEXTRACT_AVAILABLE = False


def _get_tz(name: str):
    if USE_ZONEINFO:
        return ZoneInfo(name)
    import pytz
    return pytz.timezone(name)

_TZ_LOOKUP = {name.lower(): name for name in available_timezones()} if TIMEZONE_AVAILABLE and USE_ZONEINFO else {}
_TZ_BROWSE_LIST = (
    sorted(name for name in available_timezones() if "/" in name and not name.startswith("Etc/"))
    if TIMEZONE_AVAILABLE and USE_ZONEINFO
    else []
)


def normalize_timezone(raw: str) -> Optional[str]:
    """Resolve a timezone case-insensitively to its canonical IANA name."""
    if not TIMEZONE_AVAILABLE:
        return None
    key = raw.strip().lower()
    if USE_ZONEINFO:
        return _TZ_LOOKUP.get(key)
    try:
        import pytz
        return {name.lower(): name for name in pytz.all_timezones}.get(key)
    except Exception:
        return None


# ---------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------
GREY_COLOR = 0x2B2D31

EMOJI_BREAKER  = EMOJIS["breaker"]
EMOJI_LOCK     = EMOJIS["lock"]
EMOJI_CROSS    = EMOJIS["cross"]
EMOJI_CLOCK    = EMOJIS["cooldown"]
EMOJI_TICK     = EMOJIS["tick"]
EMOJI_STAR     = EMOJIS["star"]
EMOJI_CALENDAR = EMOJIS["calendar"]
EMOJI_USER_ID  = EMOJIS["user_id"]
EMOJI_WARNING  = EMOJIS["warning"]
EMOJI_INFO     = EMOJIS["info"]
EMOJI_DELETE   = EMOJIS["delete"]
EMOJI_GLOBAL   = EMOJIS["global"]
EMOJI_DND      = EMOJIS["dnd"]


def _apply_footer(embed: discord.Embed, extra: Optional[str] = None) -> discord.Embed:
    """Apply the standard bot footer, optionally prefixed with extra info."""
    if extra:
        text = f"{extra} • Nuvella  •"
    else:
        text = f"Nuvella •"
    embed.set_footer(text=text[:2048])
    return embed


def _timezone_embed(*, title: str, description: str, color: int = GREY_COLOR, timestamp: bool = False) -> discord.Embed:
    breaker = EMOJI_BREAKER * 5
    embed = discord.Embed(title=f"{EMOJI_LOCK} {title}", description=f"{breaker}\n{description}\n{breaker}", color=GREY_COLOR)
    if timestamp:
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
    _apply_footer(embed)
    return embed


async def _safe_delete(message: Optional[discord.Message]) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass


async def _safe_ephemeral(interaction: discord.Interaction, content: str) -> None:
    try:
        await interaction.response.send_message(content, ephemeral=True)
    except discord.HTTPException:
        pass


async def _deny_silently(ctx: commands.Context) -> None:
    await _safe_delete(ctx.message)


class ExitOnlyView(discord.ui.View):
    """Reusable Exit-only view used by timezone panels."""

    def __init__(self, author_id: int, timeout: float = 300) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id

    async def on_error(self, interaction, error, item) -> None:
        if isinstance(error, discord.HTTPException):
            return
        logger.exception("ExitOnlyView error", exc_info=error)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await _safe_ephemeral(interaction, f"{EMOJI_CROSS} This panel belongs to someone else.")
            return False
        return True

    @discord.ui.button(label="Exit", style=discord.ButtonStyle.secondary, emoji=EMOJI_CROSS)
    async def exit_button(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass
        self.stop()


MAIN_COLOR = GREY_COLOR
WIN_COLOR = GREY_COLOR
LOSS_COLOR = GREY_COLOR
WARN_COLOR = GREY_COLOR
INFO_COLOR = GREY_COLOR

EMBED_COLOR = GREY_COLOR
TZ_ERROR_COLOR = GREY_COLOR
TZ_SUCCESS_COLOR = GREY_COLOR
TZ_USERLIST_SHOW_LIMIT = 50
BREAKER_LINE = EMOJI_BREAKER * 5

EMOJI_LOCK_SPARK = EMOJIS["lock"]
EMOJI_SPARKLES = EMOJIS["sparkles"]

STAFF_ROLE_MAP = {
    "founder": "founders",
    "manager": "managers",
    "head_admin": "head_admins",
    "admin": "admins",
    "bot_manager": "bot_managers",
    "event_manager": "event_managers",
    "trial_event_manager": "trial_event_managers",
    "senior_moderator": "senior_moderators",
    "moderator": "moderators",
    "trial_moderator": "trial_moderators",
}

PERM_LEVELS = {
    "user": 0,
    "trial_moderator": 1,
    "trial_event_manager": 1,
    "moderator": 2,
    "senior_moderator": 3,
    "event_manager": 3,
    "bot_manager": 4,
    "admin": 5,
    "head_admin": 6,
    "manager": 7,
    "founder": 8,
    "bot_owner": 9,
}

# Event staff (event_manager / trial_event_manager) are ONLY allowed to run
# these commands. Everything else is denied — they do NOT inherit the normal
# moderation hierarchy.
EVENT_STAFF_ALLOWED_COMMANDS = {
    "nickname",
    "warns",
}

ROLE_TOGGLE_MAP = {
    "mod":      ("moderator",            "moderator_role_id"),
    "tmod":     ("trial_moderator",      "trial_moderator_role_id"),
    "smod":     ("senior_moderator",     "senior_moderator_role_id"),
    "admin":    ("admin",                "admin_role_id"),
    "emanager": ("event_manager",        "event_manager_role_id"),
    "etrial":   ("trial_event_manager",  "trial_event_manager_role_id"),
}

EXTRA_TOGGLE_ROLES = {
    "mod":      1535961415070785558,
    "smod":     1535961415070785558,
    "tmod":     1535961415070785558,
    "emanager": 1535961425426518029,
    "etrail":   1535961425426518029,
}

URL_REGEX = re.compile(
    r'(https?://\S+)'
    r'|(www\.\S+)'
    r'|(\b\S+\.(com|net|org|io|gg|tv|xyz|co|me|dev|app|site|online|store|info|biz)\b)',
    re.IGNORECASE,
)

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif", ".tiff", ".heic")


# ---------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------
def make_embed(ctx, title: str, description: str, color: int) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    user = ctx.author if isinstance(ctx, commands.Context) else ctx.user
    if user:
        embed.set_footer(text=f"Requested by {user}", icon_url=user.display_avatar.url)
    return embed


def error_embed(ctx, message: str) -> discord.Embed:
    return discord.Embed(title="Error", description=message, color=GREY_COLOR)


def success_embed(ctx, message: str) -> discord.Embed:
    return discord.Embed(title="Success", description=message, color=GREY_COLOR)


def parse_time(duration_str: str) -> int:
    if not duration_str:
        return 0
    match = re.match(r"(\d+)\s*([smhd])", duration_str.lower().strip())
    if not match:
        return 0
    amount = int(match.group(1))
    unit = match.group(2)
    return {"s": amount, "m": amount * 60, "h": amount * 3600, "d": amount * 86400}.get(unit, 0)


def fmt_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


class SilentBlacklistIgnore(commands.CheckFailure):
    """Raised internally to silently ignore commands from blacklisted users."""
    pass


# ---------------------------------------------------------------
#  Database
# ---------------------------------------------------------------
class Database:
    def __init__(self, bot):
        self.bot = bot
        self.pool = getattr(bot, "pool", None) or getattr(bot, "db", None)
        if self.pool is None:
            raise RuntimeError("Bot does not have an asyncpg pool (bot.pool or bot.db)")

    async def fetch(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)


# ---------------------------------------------------------------
#  require() decorator
# ---------------------------------------------------------------
def require(level: str):
    if level not in PERM_LEVELS:
        raise ValueError(f"Invalid permission level: {level}")

    async def predicate(ctx):
        cog = ctx.bot.get_cog("Moderation")
        if not cog:
            raise commands.CheckFailure("Moderation cog not loaded.")
        await cog.check_mod_permission(ctx, level)
        return True

    def decorator(func):
        decorated = commands.check(predicate)(func)
        decorated.perm_level = level
        return decorated
    return decorator


# ---------------------------------------------------------------
#  Timezone views / modals
# ---------------------------------------------------------------
class TimezoneButtonView(discord.ui.View):
    """'Set Timezone' button that opens a private modal, plus an Exit button."""

    def __init__(self, cog, *, guild_id, author_id, target_id, is_edit, timeout=120):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.guild_id = guild_id
        self.author_id = author_id
        self.target_id = target_id
        self.is_edit = is_edit
        self.message = None

    async def on_error(self, interaction, error, item):
        if isinstance(error, discord.HTTPException):
            return
        logger.exception("TimezoneButtonView error", exc_info=error)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await _safe_ephemeral(interaction, f"{EMOJI_CROSS} This button isn't for you.")
            return False
        return True

    @discord.ui.button(label="Set Timezone", style=discord.ButtonStyle.secondary, emoji=EMOJI_CLOCK)
    async def set_button(self, interaction, button):
        modal = TimezoneModal(
            self.cog,
            guild_id=self.guild_id,
            target_id=self.target_id,
            set_by_id=self.author_id,
            is_edit=self.is_edit,
            view=self,
        )
        try:
            await interaction.response.send_modal(modal)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Exit", style=discord.ButtonStyle.secondary, emoji=EMOJI_CROSS)
    async def exit_button(self, interaction, button):
        self.stop()
        try:
            await interaction.message.delete()
        except discord.HTTPException:
            pass

    async def on_timeout(self):
        if self.message is not None:
            await _safe_delete(self.message)


class TimezoneModal(discord.ui.Modal):
    """Collects a timezone/city privately — only the person who opened it ever sees it."""

    def __init__(self, cog, *, guild_id, target_id, set_by_id, is_edit, view):
        super().__init__(title="Edit Member's Timezone" if is_edit else "Set Your Timezone")
        self.cog = cog
        self.guild_id = guild_id
        self.target_id = target_id
        self.set_by_id = set_by_id
        self.is_edit = is_edit
        self.view_ref = view
        self.tz_input = discord.ui.TextInput(
            label="Timezone or city",
            placeholder="e.g. Asia/Kathmandu, America/New_York, Europe/London",
            required=True,
            max_length=100,
        )
        self.add_item(self.tz_input)

    async def on_submit(self, interaction):
        resolved = normalize_timezone(self.tz_input.value)
        if not resolved:
            error_embed_ = _timezone_embed(
                title="Invalid Timezone",
                description=(
                    f"{EMOJI_CROSS} That timezone could not be found.\n\n"
                    "Use an IANA timezone such as `Asia/Kathmandu`.\n"
                    "Use `$tz list` to browse valid options, then click the button again."
                ),
                color=TZ_ERROR_COLOR,
            )
            try:
                await interaction.response.send_message(embed=error_embed_, ephemeral=True)
            except discord.HTTPException:
                pass
            await self.cog._log_action(
                self.guild_id,
                f"{EMOJI_CLOCK} Timezone — Invalid Entry",
                actor=interaction.user,
                color=EMBED_COLOR,
                fields=[
                    ("Target", f"<@{self.target_id}> (`{self.target_id}`)"),
                    ("Reason", "Provided timezone could not be resolved"),
                ],
            )
            return

        async with self.cog.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO extra_tz_users (guild_id, user_id, timezone, set_by, updated_at)
                VALUES ($1, $2, $3, $4, now())
                ON CONFLICT (guild_id, user_id)
                DO UPDATE SET timezone = EXCLUDED.timezone, set_by = EXCLUDED.set_by, updated_at = now()
                """,
                self.guild_id, self.target_id, resolved, self.set_by_id,
            )

        if self.is_edit:
            success_embed_ = _timezone_embed(
                title="Timezone Updated",
                description=f"{EMOJI_TICK} The timezone for <@{self.target_id}> has been updated.",
                color=TZ_SUCCESS_COLOR,
            )
        else:
            success_embed_ = _timezone_embed(
                title="Timezone Saved",
                description=f"{EMOJI_TICK} Your timezone has been saved successfully.\n\nYour timezone identifier remains private.",
                color=TZ_SUCCESS_COLOR,
            )
        try:
            await interaction.response.send_message(embed=success_embed_, ephemeral=True)
        except discord.HTTPException:
            pass

        self.view_ref.stop()
        if self.view_ref.message is not None:
            await _safe_delete(self.view_ref.message)

        await self.cog._log_action(
            self.guild_id,
            f"{EMOJI_CLOCK} Timezone {'Edited (Staff)' if self.is_edit else 'Set'}",
            actor=interaction.user,
            color=EMBED_COLOR,
            fields=[
                ("Target", f"<@{self.target_id}> (`{self.target_id}`)"),
                ("Set By", f"<@{self.set_by_id}> (`{self.set_by_id}`)"),
                ("Value", "*hidden — privacy by design*"),
            ],
        )


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cfg = bot.config
        self.owner_id = getattr(bot, "owner_id", None)

        roles_cfg = cfg.get("roles", {})
        self.staff_role_ids = {
            "founder":           roles_cfg.get("founder_role_id"),
            "manager":           roles_cfg.get("manager_role_id"),
            "head_admin":        roles_cfg.get("head_admin_role_id"),
            "admin":             roles_cfg.get("admin_role_id"),
            "bot_manager":       roles_cfg.get("bot_manager_role_id"),
            "event_manager":     roles_cfg.get("event_manager_role_id"),
            "senior_moderator":  roles_cfg.get("senior_moderator_role_id"),
            "moderator":         roles_cfg.get("moderator_role_id"),
            "trial_event_manager": roles_cfg.get("trial_event_manager_role_id"),
            "trial_moderator":   roles_cfg.get("trial_moderator_role_id"),
        }

        self.allowed_link_role_ids = set(cfg.get("allowed_link_role_ids", []))
        self.log_channel_id = cfg.get("log_channel_id")
        self.clown_role_id = cfg.get("clown_role_id")
        self.jail_role_id = cfg.get("jail_role_id")
        self.jail_category_id = cfg.get("jail_category_id")
        self.quarantine_role_id = cfg.get("quarantine_role_id")
        self.quarantine_channel_id = cfg.get("quarantine_channel_id")
        self.warn_ban_threshold = cfg.get("warn_ban_threshold", cfg.get("warn_kick_threshold", 7))

        self.owners: set[int] = set()
        self.blacklisted_users: set[int] = set()
        self.staff_cache = defaultdict(lambda: defaultdict(list))
        self.locktext_channels: set[int] = set()
        self.clowns: dict = {}
        self.forcenicks: dict = {}
        self.phish_domains: list = []
        self.banned_words: list = []
        self.timezones: dict = {}
        self.modstats: dict = {}

        self.antiphish_enabled = True
        self.wordfilter_enabled = True

        self.snipe_data = defaultdict(lambda: deque(maxlen=5))
        self.edit_snipe_data = defaultdict(lambda: deque(maxlen=5))
        self.reaction_snipe_data = defaultdict(lambda: deque(maxlen=5))

        self.db = Database(bot)
        self.pool = self.db.pool

    # ---------------------------------------------------------------
    #  Reaction helpers
    # ---------------------------------------------------------------
    @staticmethod
    def _to_reaction(emoji):
        if isinstance(emoji, str) and emoji.startswith("<") and ":" in emoji:
            try:
                return discord.PartialEmoji.from_str(emoji)
            except Exception:
                return emoji
        return emoji

    async def _react(self, ctx, emoji):
        if not isinstance(ctx, commands.Context):
            return
        try:
            await ctx.message.add_reaction(self._to_reaction(emoji))
        except Exception:
            pass

    async def _react_ok(self, ctx):
        await self._react(ctx, EMOJIS.get("tick", "✅"))

    async def _react_fail(self, ctx):
        if getattr(ctx, "_mod_command_failed", False):
            return
        try:
            ctx._mod_command_failed = True
        except Exception:
            pass
        await self._react(ctx, EMOJIS.get("cross", EMOJIS.get("x", "❌")))

    async def _react_pending(self, ctx):
        await self._react(ctx, EMOJIS.get("cooldown", "⏳"))

    async def fail(self, ctx, message: Optional[str] = None):
        await self._react_fail(ctx)
        if message:
            await self._send_temp_error(ctx, error_embed(ctx, message))

    # ---------------------------------------------------------------
    #  Hooks
    # ---------------------------------------------------------------
    async def cog_before_invoke(self, ctx):
        try:
            ctx._mod_command_failed = False
        except Exception:
            pass

        # Silently swallow commands issued by blacklisted users:
        # no reply, no error message, no log entry — only a tick/cross reaction.
        author = ctx.author if isinstance(ctx, commands.Context) else getattr(ctx, "user", None)
        if author is not None and author.id in self.blacklisted_users:
            try:
                ctx._mod_command_failed = True
            except Exception:
                pass
            try:
                if random.random() < 0.5:
                    await self._react(ctx, EMOJIS.get("tick", "✅"))
                else:
                    await self._react(ctx, EMOJIS.get("cross", EMOJIS.get("x", "❌")))
            except Exception:
                pass
            raise SilentBlacklistIgnore()

    async def cog_after_invoke(self, ctx):
        if getattr(ctx, "_mod_command_failed", False):
            return
        await self._react_ok(ctx)

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            error = error.original

        if isinstance(error, SilentBlacklistIgnore):
            return

        if not getattr(ctx, "_mod_command_failed", False):
            await self._react_fail(ctx)

        if not isinstance(error, commands.CommandOnCooldown):
            await self._log_error(ctx, error)

        if isinstance(error, commands.UserInputError):
            return

        if isinstance(error, commands.CommandOnCooldown):
            await self._send_temp_error(
                ctx,
                error_embed(
                    ctx,
                    f"{EMOJIS.get('cooldown', '⏳')} **{ctx.command.qualified_name}** is on cooldown. "
                    f"Try again in **{error.retry_after:.1f}s**.",
                ),
            )
            return

        if isinstance(error, commands.CheckFailure):
            msg = str(error) or "You don't have permission to use this command."
            await self._send_temp_error(ctx, error_embed(ctx, msg))
            return

        logger.error(
            "Unhandled error in %s: %s: %s",
            getattr(ctx.command, "qualified_name", "?"),
            type(error).__name__,
            error,
        )

    async def _log_error(self, ctx, error):
        channel = self.get_log_channel()
        if not channel:
            return
        try:
            tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            cmd_name = getattr(ctx.command, "qualified_name", None) or "unknown"
            user = ctx.author if isinstance(ctx, commands.Context) else getattr(ctx, "user", None)

            embed = discord.Embed(
                title=f"{EMOJIS.get('warning', '⚠️')} Command Error — {cmd_name}",
                description=f"```py\n{tb[:1800]}\n```",
                color=GREY_COLOR,
                timestamp=discord.utils.utcnow(),
            )
            if user:
                embed.set_footer(text=f"Triggered by {user} ({user.id})", icon_url=user.display_avatar.url)
            if isinstance(ctx, commands.Context):
                embed.add_field(name="Channel", value=ctx.channel.mention, inline=True)
                if ctx.message and ctx.message.content:
                    embed.add_field(name="Message", value=f"```{ctx.message.content[:180]}```", inline=False)
            embed.add_field(name="Error Type", value=type(error).__name__, inline=True)
            embed.add_field(name="Detail", value=str(error)[:900] or "None", inline=False)

            await channel.send(embed=embed)
        except Exception as e:
            logger.error("Failed to log error to log channel: %s", e)

    async def _send_temp_error(self, ctx, embed: discord.Embed):
        try:
            if isinstance(ctx, commands.Context):
                msg = None
                try:
                    msg = await ctx.channel.send(embed=embed)
                except Exception:
                    pass
                try:
                    await ctx.message.delete()
                except Exception:
                    pass
                if msg is not None:
                    await asyncio.sleep(5)
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                return
            try:
                if ctx.response.is_done():
                    await ctx.followup.send(embed=embed, ephemeral=True)
                else:
                    await ctx.response.send_message(embed=embed, ephemeral=True)
            except Exception:
                pass
        except Exception as e:
            logger.error("Failed to send error: %s", e)

    # ---------------------------------------------------------------
    #  DB init
    # ---------------------------------------------------------------
    async def _create_tables_if_not_exists(self):
        tables = [
            "CREATE TABLE IF NOT EXISTS owners (user_id BIGINT PRIMARY KEY)",
            """CREATE TABLE IF NOT EXISTS staff (
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, role TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, role))""",
            "CREATE TABLE IF NOT EXISTS locktext (channel_id BIGINT PRIMARY KEY)",
            """CREATE TABLE IF NOT EXISTS clowns (
                guild_id BIGINT, user_id BIGINT, original_nick TEXT,
                PRIMARY KEY (guild_id, user_id))""",
            """CREATE TABLE IF NOT EXISTS forcenicks (
                guild_id BIGINT, user_id BIGINT, forced_nick TEXT, original_nick TEXT,
                PRIMARY KEY (guild_id, user_id))""",
            "CREATE TABLE IF NOT EXISTS phish_domains (domain TEXT PRIMARY KEY)",
            "CREATE TABLE IF NOT EXISTS banned_words (word TEXT PRIMARY KEY)",
            "CREATE TABLE IF NOT EXISTS user_timezones (user_id BIGINT PRIMARY KEY, timezone TEXT)",
            """CREATE TABLE IF NOT EXISTS extra_tz_users (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                timezone TEXT NOT NULL,
                set_by BIGINT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS extra_tz_settings (
                guild_id BIGINT PRIMARY KEY,
                enabled BOOLEAN NOT NULL DEFAULT FALSE
            )""",
            """CREATE TABLE IF NOT EXISTS extra_tz_blacklist (
                guild_id BIGINT NOT NULL,
                target_id BIGINT NOT NULL,
                target_type TEXT NOT NULL CHECK (target_type IN ('user', 'role')),
                added_by BIGINT,
                added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (guild_id, target_id, target_type)
            )""",
            """CREATE TABLE IF NOT EXISTS modstats (
                user_id BIGINT, command TEXT, count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, command))""",
            """CREATE TABLE IF NOT EXISTS warnings (
                id SERIAL PRIMARY KEY,
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
                moderator_id BIGINT NOT NULL, reason TEXT,
                timestamp BIGINT NOT NULL, countable BOOLEAN DEFAULT TRUE)""",
            "CREATE TABLE IF NOT EXISTS lockstate (channel_id BIGINT PRIMARY KEY, data JSONB)",
            """CREATE TABLE IF NOT EXISTS jail (
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
                role_ids BIGINT[], channel_id BIGINT, reason TEXT,
                moderator_id BIGINT, created_at BIGINT,
                PRIMARY KEY (guild_id, user_id))""",
            """CREATE TABLE IF NOT EXISTS quarantine (
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
                role_ids BIGINT[], reason TEXT,
                moderator_id BIGINT, created_at BIGINT,
                PRIMARY KEY (guild_id, user_id))""",
            "CREATE TABLE IF NOT EXISTS np_users (user_id BIGINT PRIMARY KEY)",
            """CREATE TABLE IF NOT EXISTS nick_history (
                id SERIAL PRIMARY KEY,
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
                old_nick TEXT, new_nick TEXT, timestamp BIGINT)""",
            "CREATE TABLE IF NOT EXISTS log_config (id SERIAL PRIMARY KEY, channel_id BIGINT)",
            "CREATE TABLE IF NOT EXISTS blacklist (user_id BIGINT PRIMARY KEY, reason TEXT)",
        ]
        for t in tables:
            await self.db.execute(t)

        for a in [
            "ALTER TABLE staff ADD COLUMN IF NOT EXISTS guild_id BIGINT",
            "ALTER TABLE clowns ADD COLUMN IF NOT EXISTS guild_id BIGINT",
            "ALTER TABLE forcenicks ADD COLUMN IF NOT EXISTS guild_id BIGINT",
            "ALTER TABLE warnings ADD COLUMN IF NOT EXISTS guild_id BIGINT",
            "ALTER TABLE warnings ADD COLUMN IF NOT EXISTS user_id BIGINT",
            "ALTER TABLE warnings ADD COLUMN IF NOT EXISTS moderator_id BIGINT",
            "ALTER TABLE warnings ADD COLUMN IF NOT EXISTS reason TEXT",
            "ALTER TABLE warnings ADD COLUMN IF NOT EXISTS timestamp BIGINT",
            "ALTER TABLE warnings ADD COLUMN IF NOT EXISTS countable BOOLEAN DEFAULT TRUE",
            "ALTER TABLE warnings ADD COLUMN IF NOT EXISTS id SERIAL PRIMARY KEY",
            "ALTER TABLE jail ADD COLUMN IF NOT EXISTS guild_id BIGINT",
            "ALTER TABLE jail ADD COLUMN IF NOT EXISTS user_id BIGINT",
            "ALTER TABLE jail ADD COLUMN IF NOT EXISTS role_ids BIGINT[]",
            "ALTER TABLE jail ADD COLUMN IF NOT EXISTS channel_id BIGINT",
            "ALTER TABLE jail ADD COLUMN IF NOT EXISTS reason TEXT",
            "ALTER TABLE jail ADD COLUMN IF NOT EXISTS moderator_id BIGINT",
            "ALTER TABLE jail ADD COLUMN IF NOT EXISTS created_at BIGINT",
            "ALTER TABLE nick_history ADD COLUMN IF NOT EXISTS guild_id BIGINT",
            "ALTER TABLE nick_history ADD COLUMN IF NOT EXISTS user_id BIGINT",
            "ALTER TABLE nick_history ADD COLUMN IF NOT EXISTS old_nick TEXT",
            "ALTER TABLE nick_history ADD COLUMN IF NOT EXISTS new_nick TEXT",
            "ALTER TABLE nick_history ADD COLUMN IF NOT EXISTS timestamp BIGINT",
        ]:
            await self.db.execute(a)

        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_warnings_guild_user_countable "
            "ON warnings (guild_id, user_id, countable)"
        )

        for table in ("jail", "quarantine"):
            try:
                await self.db.execute(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {table}_guild_user_key "
                    f"ON {table} (guild_id, user_id)"
                )
            except Exception as e:
                logger.error("Could not ensure unique index on %s: %s", table, e)

    async def _load_data_files(self):
        self.owners = {r["user_id"] for r in await self.db.fetch("SELECT user_id FROM owners")}
        self.blacklisted_users = {r["user_id"] for r in await self.db.fetch("SELECT user_id FROM blacklist")}

        for r in await self.db.fetch("SELECT guild_id, user_id, role FROM staff"):
            plural = STAFF_ROLE_MAP.get(r["role"])
            if plural:
                uid = str(r["user_id"])
                if uid not in self.staff_cache[r["guild_id"]][plural]:
                    self.staff_cache[r["guild_id"]][plural].append(uid)

        self.locktext_channels = {r["channel_id"] for r in await self.db.fetch("SELECT channel_id FROM locktext")}

        for r in await self.db.fetch("SELECT guild_id, user_id, original_nick FROM clowns"):
            self.clowns.setdefault(r["guild_id"], {})[r["user_id"]] = r["original_nick"]

        for r in await self.db.fetch("SELECT guild_id, user_id, forced_nick, original_nick FROM forcenicks"):
            self.forcenicks.setdefault(r["guild_id"], {})[r["user_id"]] = {
                "forced": r["forced_nick"],
                "original": r["original_nick"],
            }

        self.phish_domains = [r["domain"] for r in await self.db.fetch("SELECT domain FROM phish_domains")]
        self.banned_words = [r["word"] for r in await self.db.fetch("SELECT word FROM banned_words")]

        self.timezones = {
            str(r["user_id"]): r["timezone"]
            for r in await self.db.fetch("SELECT user_id, timezone FROM user_timezones")
        }

        self.modstats = {}
        for r in await self.db.fetch("SELECT user_id, command, count FROM modstats"):
            self.modstats.setdefault(str(r["user_id"]), {})[r["command"]] = r["count"]

    # ---------------------------------------------------------------
    #  Permissions  (hierarchy-aware)
    # ---------------------------------------------------------------
    def _get_actor(self, ctx) -> Optional[discord.Member]:
        if isinstance(ctx, discord.Interaction):
            user = ctx.user
            if isinstance(user, discord.Member):
                return user
            if ctx.guild:
                return ctx.guild.get_member(user.id)
            return None
        return ctx.author

    def _discord_staff_level(self, member: discord.Member) -> int:
        """
        Highest internal permission level this member holds based on their
        *Discord roles*. Used to verify cached levels still match reality.
        """
        highest = 0
        for lv, rid in self.staff_role_ids.items():
            if not rid:
                continue
            try:
                if member.get_role(int(rid)):
                    highest = max(highest, PERM_LEVELS.get(lv, 0))
            except (TypeError, ValueError):
                continue
        return highest

    def _current_command_name(self, ctx) -> Optional[str]:
        """Best-effort lookup of the invoked command's primary name."""
        cmd = getattr(ctx, "command", None)
        if cmd is None:
            return None
        return getattr(cmd, "qualified_name", None) or getattr(cmd, "name", None)

    async def check_mod_permission(self, ctx, required_level: str):
        actor = self._get_actor(ctx)
        if actor is None:
            raise commands.CheckFailure("This command can only be used by a guild member.")

        internal_level = self.get_level(actor.id, ctx.guild.id)

        # EVENT STAFF SPECIAL CASE
        if internal_level in ("event_manager", "trial_event_manager"):
            cmd_name = self._current_command_name(ctx) or ""
            if cmd_name not in EVENT_STAFF_ALLOWED_COMMANDS:
                raise commands.CheckFailure(
                    "Event staff can only use `nickname` and `warns`."
                )
            return

        required_num = PERM_LEVELS.get(required_level, 0)
        actor_num = PERM_LEVELS.get(internal_level, 0)

        if actor_num < required_num:
            raise commands.CheckFailure(
                f"Only **{required_level.replace('_', ' ').title()}**+ can use this command."
            )

        if internal_level == "bot_owner":
            return

        if self._discord_staff_level(actor) >= required_num:
            return

        if actor.guild_permissions.administrator:
            return

        raise commands.CheckFailure(
            "Your Discord staff role is missing, so this action is blocked."
        )

    def can_target(self, actor, target):
        """
        Strict hierarchy check. The actor must be strictly ABOVE the target.
        Used for every target-based command (nickname, forcenick, warn, mute,
        kick, ban, jail, clown, ...).
        """
        guild = actor.guild
        if actor == target:
            return False, "You cannot target yourself."
        if target.id == guild.owner_id:
            return False, "You cannot target the server owner."
        if target.id in self.owners or target.id == self.bot.owner_id:
            return False, "You cannot target a bot owner."

        actor_internal = PERM_LEVELS.get(self.get_level(actor.id, guild.id), 0)
        target_internal = PERM_LEVELS.get(self.get_level(target.id, guild.id), 0)

        # Actor must be strictly above target internally (no peer targeting).
        if target_internal >= actor_internal:
            return False, "Target has an equal or higher internal staff level than you."

        if actor.top_role <= target.top_role:
            return False, "Your top role must be above the target's top role."
        if guild.me.top_role <= target.top_role:
            return False, "My top role must be above the target's top role."
        return True, None

    def get_level(self, user_id, guild_id=None):
        if user_id == self.bot.owner_id or user_id in self.owners:
            return "bot_owner"
        order = [
            ("founder", "founders"),
            ("manager", "managers"),
            ("head_admin", "head_admins"),
            ("admin", "admins"),
            ("bot_manager", "bot_managers"),
            ("senior_moderator", "senior_moderators"),
            ("event_manager", "event_managers"),
            ("moderator", "moderators"),
            ("trial_moderator", "trial_moderators"),
            ("trial_event_manager", "trial_event_managers"),
        ]
        if guild_id is not None:
            staff = self.staff_cache.get(guild_id, {})
            for sing, plur in order:
                if str(user_id) in staff.get(plur, []):
                    return sing
        else:
            for _, staff in self.staff_cache.items():
                for sing, plur in order:
                    if str(user_id) in staff.get(plur, []):
                        return sing
        return "user"

    # ---------------------------------------------------------------
    #  Logging — detailed audit trail
    # ---------------------------------------------------------------
    async def log_action(
        self,
        ctx,
        action: str,
        target: Optional[discord.abc.User] = None,
        reason: Optional[str] = None,
        extra: Optional[str] = None,
        *,
        before: Optional[str] = None,
        after: Optional[str] = None,
        fields: Optional[list] = None,
        channel: Optional[discord.abc.GuildChannel] = None,
        duration: Optional[str] = None,
        moderator: Optional[discord.abc.User] = None,
        is_automod: bool = False,
    ) -> None:
        """
        Post a detailed audit-log embed.

        Always includes:
          - Case ID + UTC timestamp (in footer / field)
          - Moderator (mention, ID, top role)
          - Target (mention, ID, top role)
          - The invoking channel
          - Optional before/after change block
          - Optional reason, duration, and free-form extra
        """
        log_channel = self.get_log_channel()
        if not log_channel:
            return

        case_id = f"NUV-{int(time.time())}-{random.randint(100, 999)}"

        if moderator is not None:
            mod = moderator
        elif is_automod:
            mod = self.bot.user
        else:
            mod = ctx.user if isinstance(ctx, discord.Interaction) else getattr(ctx, "author", None)

        invoke_channel = channel
        if invoke_channel is None:
            try:
                if isinstance(ctx, commands.Context):
                    invoke_channel = ctx.channel
                else:
                    invoke_channel = getattr(ctx, "channel", None)
            except Exception:
                invoke_channel = None

        embed = discord.Embed(
            title=f"{EMOJIS['notes']} {action}",
            color=GREY_COLOR,
            timestamp=discord.utils.utcnow(),
        )

        embed.add_field(name="Case", value=f"`{case_id}`", inline=True)

        if mod is not None:
            mod_line = f"{getattr(mod, 'mention', f'`{mod.id}`')} (`{mod.id}`)"
            if isinstance(mod, discord.Member):
                mod_line += f"\nTop role: {mod.top_role.mention}"
            embed.add_field(name="Moderator", value=mod_line[:1024], inline=True)

        if target is not None:
            target_line = f"{getattr(target, 'mention', f'`{target.id}`')} (`{target.id}`)"
            if isinstance(target, discord.Member):
                target_line += f"\nTop role: {target.top_role.mention}"
            embed.add_field(name="Target", value=target_line[:1024], inline=True)
            try:
                embed.set_thumbnail(url=target.display_avatar.url)
            except Exception:
                pass

        if invoke_channel is not None:
            embed.add_field(
                name="Channel",
                value=f"{invoke_channel.mention} (`{invoke_channel.id}`)",
                inline=True,
            )

        if before is not None or after is not None:
            embed.add_field(
                name="Change",
                value=(
                    f"**Before:** {before if before is not None else '*none*'}\n"
                    f"**After:** {after if after is not None else '*none*'}"
                )[:1024],
                inline=False,
            )

        if reason:
            embed.add_field(name="Reason", value=str(reason)[:1024] or "—", inline=False)

        if duration:
            embed.add_field(name="Duration", value=str(duration)[:1024], inline=True)

        if extra:
            embed.add_field(name="Details", value=str(extra)[:1024], inline=False)

        for name, value in fields or []:
            embed.add_field(name=str(name)[:256], value=str(value)[:1024] or "—", inline=False)

        footer_bits = []
        if isinstance(log_channel, discord.TextChannel):
            footer_bits.append(f"Guild: {log_channel.guild.name} ({log_channel.guild.id})")
        footer_bits.append(datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
        embed.set_footer(text=" • ".join(footer_bits)[:2048])

        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning("Cannot send log: %s", e)

    def get_log_channel(self) -> Optional[discord.TextChannel]:
        return self.bot.get_channel(self.log_channel_id)

    def is_staff(self, member) -> bool:
        if not isinstance(member, discord.Member):
            return False
        if member.guild_permissions.administrator:
            return True
        return self.get_level(member.id, member.guild.id) != "user"

    @staticmethod
    def _help_embed(title, lines):
        embed = discord.Embed(title=title, description="\n".join(lines), color=GREY_COLOR)
        _apply_footer(embed)
        return embed

    # ---------------------------------------------------------------
    #  Timezone audit-log helper
    # ---------------------------------------------------------------
    async def _log_action(self, guild_id, title, *, actor=None, channel=None, color=None, fields=None, description=None):
        """Timezone-module compatible audit logger."""
        try:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            embed = discord.Embed(
                title=title,
                color=GREY_COLOR if color is None else color,
                timestamp=datetime.datetime.now(datetime.timezone.utc),
            )
            if description:
                embed.description = str(description)[:4000]
            if actor is not None:
                embed.add_field(name="Actor", value=f"{actor.mention} (`{actor.id}`)", inline=False)
            if channel is not None:
                embed.add_field(name="Channel", value=f"{channel.mention} (`{channel.id}`)", inline=False)
            for name, value in fields or []:
                embed.add_field(name=name, value=str(value)[:1024] or "—", inline=False)
            _apply_footer(embed)
            log_channel = self.get_log_channel()
            if log_channel is not None:
                await log_channel.send(embed=embed)
        except Exception:
            logger.exception("Failed to post timezone audit log: %s", title)

    async def send(self, ctx, emb, *, view=None):
        if view is not None:
            prepare_v2_view(view, emb)
        if isinstance(ctx, discord.Interaction):
            try:
                if ctx.response.is_done():
                    return await ctx.followup.send(view=view) if view is not None else await ctx.followup.send(embed=emb)
                return await ctx.response.send_message(view=view) if view is not None else await ctx.response.send_message(embed=emb)
            except discord.HTTPException:
                try:
                    return await ctx.followup.send(view=view) if view is not None else await ctx.followup.send(embed=emb)
                except Exception:
                    return None
        try:
            return await ctx.reply(view=view, mention_author=False) if view is not None else await ctx.reply(embed=emb, mention_author=False)
        except discord.HTTPException as e:
            if e.code == 50035 or "Unknown message" in str(e):
                try:
                    return await ctx.channel.send(view=view) if view is not None else await ctx.channel.send(embed=emb)
                except Exception:
                    return None
            raise
        except discord.NotFound:
            try:
                return await ctx.channel.send(view=view) if view is not None else await ctx.channel.send(embed=emb)
            except Exception:
                return None

    async def _try_dm(self, member, embed):
        try:
            await member.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _manual_dm(self, member, action, reason, duration=None):
        embed = discord.Embed(
            title=f"{EMOJIS['warning']} {action} in {member.guild.name}",
            color=GREY_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=False)
        await self._try_dm(member, embed)

    async def _record_modstat(self, user_id, command_name):
        await self.db.execute(
            "INSERT INTO modstats (user_id, command, count) VALUES ($1, $2, 1) "
            "ON CONFLICT (user_id, command) DO UPDATE SET count = modstats.count + 1",
            user_id, command_name
        )
        uid = str(user_id)
        self.modstats.setdefault(uid, {})
        self.modstats[uid][command_name] = self.modstats[uid].get(command_name, 0) + 1

    # ---------------------------------------------------------------
    #  Anti-phish helpers
    # ---------------------------------------------------------------
    def _normalize_domain(self, raw):
        raw = raw.strip().lower()
        if not raw:
            return None
        if not raw.startswith(("http://", "https://")):
            raw = "http://" + raw
        try:
            host = urlparse(raw).hostname
        except Exception:
            return None
        if not host:
            return None
        try:
            host = host.encode("idna").decode("ascii")
        except Exception:
            return None
        return host.rstrip(".")

    def _registered_domain(self, host):
        if TLDEXTRACT_AVAILABLE:
            ext = tldextract.extract(host)
            return f"{ext.domain}.{ext.suffix}"
        return host

    def _normalize_text(self, text):
        text = unicodedata.normalize("NFKC", text).lower()
        text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
        text = re.sub(r"(.)\1{2,}", r"\1\1", text)
        return text

    def contains_banned_word(self, text):
        normalized = self._normalize_text(text)
        for banned in self.banned_words:
            bn = self._normalize_text(banned)
            if not bn:
                continue
            if re.search(rf"(?<!\w){re.escape(bn)}(?!\w)", normalized):
                return True
        return False

    async def is_phishing(self, content):
        for word in re.findall(r'https?://[^\s]+|[^\s]+\.[^\s]+', content):
            word = word.rstrip(".,;:!?)]}>\"'")
            host = self._normalize_domain(word)
            if not host:
                continue
            host_reg = self._registered_domain(host)
            for blocked in self.phish_domains:
                bh = self._normalize_domain(blocked)
                if bh and (host_reg == self._registered_domain(bh) or host == bh):
                    return True
        return False

    def _has_allowed_locktext_content(self, message):
        for att in message.attachments:
            ct = (att.content_type or "").lower()
            if ct.startswith("image/") or ct.startswith("video/"):
                return True
            if att.filename.lower().endswith(IMAGE_EXTENSIONS):
                return True
        if message.embeds:
            return True
        if getattr(message, "stickers", None):
            return True
        if message.content and URL_REGEX.search(message.content):
            return True
        return False

    async def _ensure_role_isolation(self, guild, role, exempt_channel_ids):
        for channel in guild.channels:
            if isinstance(channel, discord.CategoryChannel):
                continue
            if channel.id in exempt_channel_ids:
                continue
            try:
                ow = channel.overwrites_for(role)
                if ow.view_channel is False:
                    continue
                ow.view_channel = False
                await channel.set_permissions(role, overwrite=ow, reason="Role isolation")
            except (discord.Forbidden, discord.HTTPException):
                continue

    # ---------------------------------------------------------------
    #  Listeners
    # ---------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return
        if await self._bypass_filters(message.author):
            return

        if message.channel.id in self.locktext_channels:
            if self._has_allowed_locktext_content(message):
                try:
                    await message.add_reaction(self._to_reaction(EMOJIS.get("tick", "✅")))
                except Exception:
                    pass
            else:
                try:
                    await message.delete()
                    await message.channel.send(
                        f"{message.author.mention} This channel only allows **images and links**.",
                        delete_after=5,
                    )
                    await self.log_action(
                        ctx,
                        "Auto-Mod · Locktext Violation",
                        message.author,
                        reason="Plain text message in image/link-only channel",
                        extra=f"Content: `{(message.content or '')[:180]}`",
                        is_automod=True,
                    )
                except (discord.Forbidden, discord.HTTPException) as e:
                    logger.warning("Cannot delete locktext message: %s", e)
            return

        if self.antiphish_enabled and self.phish_domains and await self.is_phishing(message.content):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention} Phishing links are not allowed.", delete_after=5)
                await self.log_action(
                    ctx,
                    "Auto-Mod · Anti-Phishing",
                    message.author,
                    reason="Phishing link detected",
                    extra=f"Content: `{(message.content or '')[:180]}`",
                    is_automod=True,
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.error("Anti-phish delete failed: %s", e)
            return

        if self.wordfilter_enabled and self.banned_words and self.contains_banned_word(message.content):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention} Your message contained a banned word.", delete_after=5)
                await self.log_action(
                    ctx,
                    "Auto-Mod · Word Filter",
                    message.author,
                    reason="Banned word detected",
                    extra=f"Content: `{(message.content or '')[:180]}`",
                    is_automod=True,
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.error("Wordfilter delete failed: %s", e)

    async def _bypass_filters(self, member):
        if self.get_level(member.id, member.guild.id) != "user":
            return True
        for role in member.roles:
            if role.id in self.allowed_link_role_ids:
                return True
        return False

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if not message.guild:
            return
        now = int(discord.utils.utcnow().timestamp())
        self.snipe_data[message.channel.id].appendleft({
            "author_id": message.author.id,
            "content": message.content,
            "sent_at": int(message.created_at.timestamp()),
            "deleted_at": now,
            "attachment_urls": [a.url for a in message.attachments],
        })

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if not before.guild or before.author.bot or before.content == after.content:
            return
        self.edit_snipe_data[before.channel.id].appendleft({
            "author_id": before.author.id,
            "old_content": before.content,
            "new_content": after.content,
            "sent_at": int(before.created_at.timestamp()),
            "edited_at": int(discord.utils.utcnow().timestamp()),
        })

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction, user):
        if not reaction.message.guild:
            return
        self.reaction_snipe_data[reaction.message.channel.id].appendleft({
            "message_id": reaction.message.id,
            "user_id": user.id,
            "emoji": str(reaction.emoji),
            "timestamp": int(discord.utils.utcnow().timestamp()),
            "message_content": reaction.message.content[:200] if reaction.message.content else None,
            "message_author_id": reaction.message.author.id if reaction.message.author else None,
        })

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.nick == after.nick:
            return
        await self.db.execute(
            "INSERT INTO nick_history (guild_id, user_id, old_nick, new_nick, timestamp) "
            "VALUES ($1, $2, $3, $4, $5)",
            after.guild.id, after.id, before.nick, after.nick, int(time.time())
        )
        gid = after.guild.id
        if gid in self.forcenicks and after.id in self.forcenicks[gid]:
            forced = self.forcenicks[gid][after.id].get("forced")
            if forced and after.nick != forced:
                try:
                    await after.edit(nick=forced, reason="Forcenick enforced")
                except (discord.Forbidden, discord.HTTPException) as e:
                    logger.warning("Could not enforce forcenick on %s: %s", after.id, e)

    # ===============================================================
    #  SNIPE
    # ===============================================================
    async def _snipe_logic(self, ctx, index=1, channel=None):
        chan = channel or ctx.channel
        entries = self.snipe_data.get(chan.id, deque())
        if not entries:
            return await self.fail(ctx, "Nothing to snipe in that channel.")
        if index < 1 or index > len(entries):
            return await self.fail(ctx, f"Index out of range (1-{len(entries)}).")
        data = entries[index - 1]
        author = self.bot.get_user(data["author_id"])
        embed = make_embed(ctx, f"{EMOJIS['snipe']} Sniped Message #{index}", "", GREY_COLOR)
        embed.add_field(name="Author", value=author.mention if author else f"<@{data['author_id']}>", inline=True)
        embed.add_field(name="Channel", value=chan.mention, inline=True)
        embed.add_field(name="Content", value=data["content"] or "*No text content*", inline=False)
        if data["attachment_urls"]:
            embed.add_field(name="Attachments", value="\n".join(data["attachment_urls"]), inline=False)
        _apply_footer(embed, extra=f"Sent <t:{data['sent_at']}:R> • Deleted <t:{data['deleted_at']}:R>")
        await self.send(ctx, embed)

    @commands.command(aliases=["sn", "s"])
    @require("trial_moderator")
    async def snipe(self, ctx, index: int = 1, channel: discord.TextChannel = None):
        await self._snipe_logic(ctx, index, channel)

    async def _editsnipe_logic(self, ctx, index=1, channel=None):
        chan = channel or ctx.channel
        entries = self.edit_snipe_data.get(chan.id, deque())
        if not entries:
            return await self.fail(ctx, "Nothing to editsnipe in that channel.")
        if index < 1 or index > len(entries):
            return await self.fail(ctx, f"Index out of range (1-{len(entries)}).")
        data = entries[index - 1]
        author = self.bot.get_user(data["author_id"])
        embed = make_embed(ctx, f"{EMOJIS['snipe']} Edit Sniped #{index}", "", GREY_COLOR)
        embed.add_field(name="Author", value=author.mention if author else f"<@{data['author_id']}>", inline=True)
        embed.add_field(name="Channel", value=chan.mention, inline=True)
        embed.add_field(name="Before", value=data["old_content"] or "*empty*", inline=False)
        embed.add_field(name="After", value=data["new_content"] or "*empty*", inline=False)
        _apply_footer(embed, extra=f"Sent <t:{data['sent_at']}:R> • Edited <t:{data['edited_at']}:R>")
        await self.send(ctx, embed)

    @commands.command(aliases=["es"])
    @require("trial_moderator")
    async def editsnipe(self, ctx, index: int = 1, channel: discord.TextChannel = None):
        await self._editsnipe_logic(ctx, index, channel)

    async def _snipereaction_logic(self, ctx, index=1, channel=None):
        chan = channel or ctx.channel
        entries = self.reaction_snipe_data.get(chan.id, deque())
        if not entries:
            return await self.fail(ctx, "Nothing to snipe in that channel.")
        if index < 1 or index > len(entries):
            return await self.fail(ctx, f"Index out of range (1-{len(entries)}).")
        data = entries[index - 1]
        user = self.bot.get_user(data["user_id"])
        embed = make_embed(ctx, f"{EMOJIS['snipe']} Reaction Sniped #{index}", "", GREY_COLOR)
        embed.add_field(name="User", value=user.mention if user else f"<@{data['user_id']}>", inline=True)
        embed.add_field(name="Emoji", value=data["emoji"], inline=True)
        embed.add_field(name="Channel", value=chan.mention, inline=True)
        if data["message_content"]:
            embed.add_field(name="Message", value=data["message_content"][:500], inline=False)
        if data["message_author_id"]:
            ma = self.bot.get_user(data["message_author_id"])
            if ma:
                embed.add_field(name="Message Author", value=ma.mention, inline=True)
        _apply_footer(embed, extra=f"Reaction removed <t:{data['timestamp']}:R>")
        await self.send(ctx, embed)

    @commands.command(name="snipereaction", aliases=["sr"])
    @require("trial_moderator")
    async def snipereaction(self, ctx, index: int = 1, channel: discord.TextChannel = None):
        await self._snipereaction_logic(ctx, index, channel)

    # ===============================================================
    #  SET LOG CHANNEL
    # ===============================================================
    @commands.command(name="setlogchannel")
    @require("admin")
    async def setlogchannel(self, ctx, channel: discord.TextChannel):
        old_id = self.log_channel_id
        self.log_channel_id = channel.id
        await self.db.execute(
            "INSERT INTO log_config (id, channel_id) VALUES (1, $1) "
            "ON CONFLICT (id) DO UPDATE SET channel_id = EXCLUDED.channel_id",
            channel.id
        )
        await self.log_action(
            ctx, "Log Channel Updated",
            before=f"<#{old_id}> (`{old_id}`)" if old_id else "*none*",
            after=f"{channel.mention} (`{channel.id}`)",
        )

    # ===============================================================
    #  ANTI-PHISHING
    # ===============================================================
    @commands.group(name="antiphish", invoke_without_command=True)
    @require("admin")
    async def antiphish_group(self, ctx):
        status = f"{EMOJIS['tick']} Enabled" if self.antiphish_enabled else f"{EMOJIS['cross']} Disabled"
        await self.send(ctx, make_embed(
            ctx, f"{EMOJIS['fishing_rod']} Anti-Phishing Protection",
            f"**Status:** {status}\n\n"
            f"`antiphish enable/disable`\n`antiphish add <domain>`\n"
            f"`antiphish remove <domain>`\n`antiphish list`",
            GREY_COLOR,
        ))

    @antiphish_group.command(name="enable")
    @require("admin")
    async def antiphish_enable(self, ctx):
        self.antiphish_enabled = True
        await self.log_action(ctx, "Anti-Phishing Enabled", before="Disabled", after="Enabled")

    @antiphish_group.command(name="disable")
    @require("admin")
    async def antiphish_disable(self, ctx):
        self.antiphish_enabled = False
        await self.log_action(ctx, "Anti-Phishing Disabled", before="Enabled", after="Disabled")

    @antiphish_group.command(name="add")
    @require("admin")
    async def antiphish_add(self, ctx, domain: str):
        domain = domain.lower().strip()
        if domain in self.phish_domains:
            return await self.fail(ctx, "Domain already in the list.")
        self.phish_domains.append(domain)
        await self.db.execute("INSERT INTO phish_domains (domain) VALUES ($1) ON CONFLICT DO NOTHING", domain)
        await self.log_action(ctx, "Phishing Domain Added", extra=f"Domain: `{domain}`")

    @antiphish_group.command(name="remove")
    @require("admin")
    async def antiphish_remove(self, ctx, domain: str):
        domain = domain.lower().strip()
        if domain not in self.phish_domains:
            return await self.fail(ctx, "Domain not found.")
        self.phish_domains.remove(domain)
        await self.db.execute("DELETE FROM phish_domains WHERE domain = $1", domain)
        await self.log_action(ctx, "Phishing Domain Removed", extra=f"Domain: `{domain}`")

    @antiphish_group.command(name="list")
    @require("moderator")
    async def antiphish_list(self, ctx):
        if not self.phish_domains:
            return await self.send(ctx, make_embed(ctx, "Phishing Domains", "No domains configured.", GREY_COLOR))
        desc = "\n".join(f"• `{d}`" for d in self.phish_domains)
        await self.send(ctx, make_embed(ctx, f"{EMOJIS['fishing_rod']} Phishing Domains", desc, GREY_COLOR))

    # ===============================================================
    #  WORD FILTER
    # ===============================================================
    @commands.group(name="wordfilter", invoke_without_command=True)
    @require("admin")
    async def wordfilter_group(self, ctx):
        status = f"{EMOJIS['tick']} Enabled" if self.wordfilter_enabled else f"{EMOJIS['cross']} Disabled"
        await self.send(ctx, make_embed(
            ctx, f"{EMOJIS['alphabet']} Word Filter",
            f"**Status:** {status}\n\n"
            f"`wordfilter enable/disable`\n`wordfilter add <word>`\n"
            f"`wordfilter remove <word>`\n`wordfilter list`",
            GREY_COLOR,
        ))

    @wordfilter_group.command(name="enable")
    @require("admin")
    async def wordfilter_enable(self, ctx):
        self.wordfilter_enabled = True
        await self.log_action(ctx, "Word Filter Enabled", before="Disabled", after="Enabled")

    @wordfilter_group.command(name="disable")
    @require("admin")
    async def wordfilter_disable(self, ctx):
        self.wordfilter_enabled = False
        await self.log_action(ctx, "Word Filter Disabled", before="Enabled", after="Disabled")

    @wordfilter_group.command(name="add")
    @require("admin")
    async def wordfilter_add(self, ctx, *, word: str):
        word = word.lower().strip()
        if word in self.banned_words:
            return await self.fail(ctx, "Word already in the filter.")
        self.banned_words.append(word)
        await self.db.execute("INSERT INTO banned_words (word) VALUES ($1) ON CONFLICT DO NOTHING", word)
        await self.log_action(ctx, "Filtered Word Added", extra=f"Word: `{word}`")

    @wordfilter_group.command(name="remove")
    @require("admin")
    async def wordfilter_remove(self, ctx, *, word: str):
        word = word.lower().strip()
        if word not in self.banned_words:
            return await self.fail(ctx, "Word not found in filter.")
        self.banned_words.remove(word)
        await self.db.execute("DELETE FROM banned_words WHERE word = $1", word)
        await self.log_action(ctx, "Filtered Word Removed", extra=f"Word: `{word}`")

    @wordfilter_group.command(name="list")
    @require("moderator")
    async def wordfilter_list(self, ctx):
        if not self.banned_words:
            return await self.send(ctx, make_embed(ctx, "Banned Words", "No words configured.", GREY_COLOR))
        desc = "\n".join(f"• `{w}`" for w in self.banned_words)
        await self.send(ctx, make_embed(ctx, f"{EMOJIS['alphabet']} Banned Words", desc, GREY_COLOR))

    # ===============================================================
    #  NICKNAME HISTORY
    # ===============================================================
    @commands.command(name="nickhistory", aliases=["nh"])
    @require("moderator")
    async def nickhistory(self, ctx, member: discord.Member):
        rows = await self.db.fetch(
            "SELECT old_nick, new_nick, timestamp FROM nick_history "
            "WHERE guild_id = $1 AND user_id = $2 ORDER BY timestamp DESC LIMIT 20",
            ctx.guild.id, member.id
        )
        if not rows:
            return await self.fail(ctx, "No nickname history found for that user.")
        desc = ""
        for e in reversed(rows):
            desc += f"<t:{e['timestamp']}:R> — **{e['old_nick']}** → **{e['new_nick']}**\n"
        embed = make_embed(ctx, f"{EMOJIS['notes']} Nickname History for {member.display_name}", desc, GREY_COLOR)
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.send(ctx, embed)

    # ===============================================================
    #  UNBAN ALL
    # ===============================================================
    @commands.command(name="unbanall")
    @require("bot_owner")
    async def unbanall(self, ctx):
        await self._react_pending(ctx)
        bans = [e async for e in ctx.guild.bans()]
        if not bans:
            return await self._react_fail(ctx)
        done = 0
        for be in bans:
            try:
                await ctx.guild.unban(be.user, reason="Mass unban")
                done += 1
            except (discord.Forbidden, discord.HTTPException):
                pass
            await asyncio.sleep(0.5)
        await self.log_action(
            ctx, "Unban All",
            extra=f"Unbanned `{done}` / `{len(bans)}` users.",
        )

    # ===============================================================
    #  OWNER
    # ===============================================================
    @commands.group(name="owner", invoke_without_command=True)
    @require("bot_owner")
    async def owner_group(self, ctx):
        await self.send(ctx, make_embed(
            ctx, f"{EMOJIS['owner']} Owner Management",
            "`owner add <user>` — Grant bot owner\n"
            "`owner remove <user>` — Revoke bot owner\n"
            "`owner list` — List all bot owners",
            GREY_COLOR,
        ))

    @owner_group.command(name="add")
    @require("bot_owner")
    async def owner_add(self, ctx, user: discord.Member):
        if ctx.author.id != self.bot.owner_id:
            return await self.fail(ctx, "Only the **primary** bot owner can add new owners.")
        if user.id in self.owners:
            return await self.fail(ctx, f"{user.mention} is already a bot owner.")
        self.owners.add(user.id)
        await self.db.execute("INSERT INTO owners (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user.id)
        await self.log_action(ctx, "Bot Owner Added", user)

    @owner_group.command(name="remove")
    @require("bot_owner")
    async def owner_remove(self, ctx, user: discord.Member):
        if ctx.author.id != self.bot.owner_id:
            return await self.fail(ctx, "Only the **primary** bot owner can remove owners.")
        if user.id not in self.owners:
            return await self.fail(ctx, f"{user.mention} is not a bot owner.")
        self.owners.discard(user.id)
        await self.db.execute("DELETE FROM owners WHERE user_id = $1", user.id)
        await self.log_action(ctx, "Bot Owner Removed", user)

    @owner_group.command(name="list")
    @require("bot_owner")
    async def owner_list(self, ctx):
        primary = self.bot.get_user(self.bot.owner_id)
        lines = [f"{EMOJIS['owner']} {primary.mention} *(primary)*" if primary else f"{EMOJIS['owner']} <@{self.bot.owner_id}> *(primary)*"]
        for uid in self.owners:
            m = ctx.guild.get_member(int(uid))
            lines.append(m.mention if m else f"<@{uid}> *(not in server)*")
        await self.send(ctx, make_embed(ctx, f"{EMOJIS['owner']} Bot Owners", "\n".join(lines) or "None", GREY_COLOR))

    # ===============================================================
    #  BLACKLIST  (Senior Mod and higher)
    # ===============================================================
    @commands.group(name="blacklist", aliases=["bl"])
    @require("senior_moderator")
    async def blacklist_group(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send("Usage: `blacklist add @user [reason]` | `blacklist remove @user` | `blacklist list`")

    @blacklist_group.command(name="add")
    @require("senior_moderator")
    async def blacklist_add(self, ctx, user: discord.Member, *, reason: str = "No reason provided"):
        await self.db.execute(
            "INSERT INTO blacklist (user_id, reason) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET reason = EXCLUDED.reason",
            user.id, reason
        )
        self.blacklisted_users.add(user.id)
        await self.log_action(ctx, "Blacklist Add", user, reason=reason,
                              before="Not blacklisted", after="Blacklisted")

    @blacklist_group.command(name="remove")
    @require("senior_moderator")
    async def blacklist_remove(self, ctx, user: discord.Member):
        await self.db.execute("DELETE FROM blacklist WHERE user_id = $1", user.id)
        self.blacklisted_users.discard(user.id)
        await self.log_action(ctx, "Blacklist Remove", user,
                              before="Blacklisted", after="Not blacklisted")

    @blacklist_group.command(name="list")
    @require("senior_moderator")
    async def blacklist_list(self, ctx):
        rows = await self.db.fetch("SELECT user_id, reason FROM blacklist")
        if not rows:
            return await self.send(ctx, make_embed(ctx, "Blacklist", "No blacklisted users.", GREY_COLOR))
        desc = "\n".join(f"• <@{r['user_id']}> — {r['reason']}" for r in rows)
        await self.send(ctx, make_embed(ctx, f"{EMOJIS['stop']} Blacklisted Users", desc, GREY_COLOR))

    # ===============================================================
    #  UTILITIES
    # ===============================================================
    @commands.command(aliases=["latency"])
    async def ping(self, ctx):
        await self.send(ctx, make_embed(
            ctx, f"{EMOJIS['tick']} Pong!",
            f"{EMOJIS['ping']} Latency: `{round(self.bot.latency * 1000)}ms`",
            GREY_COLOR,
        ))

    @commands.command(aliases=["up"])
    async def uptime(self, ctx):
        secs = int(time.time() - self.bot.uptime)
        h, r = divmod(secs, 3600)
        m, s = divmod(r, 60)
        await self.send(ctx, make_embed(
            ctx, f"{EMOJIS['tick']} Uptime",
            f"{EMOJIS['cooldown']} `{h}h {m}m {s}s`",
            GREY_COLOR,
        ))

    # ==================================================================
    #  TIMEZONES
    # ==================================================================
    TZ_HELP_LINES_STAFF = [
        "`$tz` / `$tz view [member]` — Show your (or someone else's) current local time.",
        "`$tz set` — Open the embedded setup panel and privately enter your timezone.",
        "`$tz remove [member]` — Remove your timezone, or (Staff) someone else's.",
        "`$tz edit <member>` — (Staff) Click the button to privately set someone else's timezone.",
        "`$tz list [page]` — Browse valid timezone names to use with `$tz set`.",
        "`$tz userlist` — (Staff) See how many members have a timezone set.",
        "`$tz blacklist [add|remove|list] <user/role>` — (Staff) Block specific users/roles from using timezone commands.",
        "`$tz enable` / `$tz disable` — (Staff) Turn the timezone module on or off.",
        "`$tz help` — Show this message.",
    ]
    TZ_HELP_LINES_LIMITED = [
        "`$tz` — Show your current local time.",
        "`$tz <user>` — Show another member's current local time.",
        "`$tz set` — Open the private setup panel and enter your timezone.",
        "`$tz remove` — Remove your own timezone.",
        "`$tz help` — Show this message.",
    ]

    async def _tz_is_enabled(self, guild_id):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT enabled FROM extra_tz_settings WHERE guild_id = $1", guild_id)
        return bool(row["enabled"]) if row else False

    async def _tz_set_enabled(self, guild_id, enabled):
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO extra_tz_settings (guild_id, enabled) VALUES ($1, $2)
                ON CONFLICT (guild_id) DO UPDATE SET enabled = EXCLUDED.enabled
                """,
                guild_id, enabled,
            )

    async def _tz_is_blacklisted(self, guild_id, member):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT target_id, target_type FROM extra_tz_blacklist WHERE guild_id = $1",
                guild_id,
            )
        if not rows:
            return False
        role_ids = {r.id for r in member.roles}
        for row in rows:
            if row["target_type"] == "user" and row["target_id"] == member.id:
                return True
            if row["target_type"] == "role" and row["target_id"] in role_ids:
                return True
        return False

    async def _tz_gate(self, ctx):
        author_is_staff = self.is_staff(ctx.author)
        enabled = await self._tz_is_enabled(ctx.guild.id)

        if not enabled:
            if author_is_staff:
                embed = discord.Embed(
                    description=f"{EMOJI_WARNING} The timezone module is disabled. Use `$tz enable` to start it.",
                    color=EMBED_COLOR,
                )
                _apply_footer(embed)
                await ctx.send(embed=embed)
            else:
                await _deny_silently(ctx)
            return False

        if not author_is_staff:
            if await self._tz_is_blacklisted(ctx.guild.id, ctx.author):
                await _deny_silently(ctx)
                await self._log_action(
                    ctx.guild.id,
                    f"{EMOJI_CLOCK} Timezone — Denied (Blacklisted)",
                    actor=ctx.author,
                    channel=ctx.channel,
                    color=EMBED_COLOR,
                )
                return False

        return True

    async def _tz_precheck(self, ctx, require_staff=False):
        if not await self._tz_gate(ctx):
            return False
        if require_staff and not self.is_staff(ctx.author):
            await _deny_silently(ctx)
            await self._log_action(
                ctx.guild.id,
                f"{EMOJI_CLOCK} Timezone — Denied (Staff Only)",
                actor=ctx.author,
                channel=ctx.channel,
                color=EMBED_COLOR,
            )
            return False
        return True

    async def _tz_render(self, ctx, target):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT timezone FROM extra_tz_users WHERE guild_id = $1 AND user_id = $2",
                ctx.guild.id, target.id,
            )
        if not row:
            if target.id == ctx.author.id:
                description = (
                    "You don't have a timezone set yet.\n\n"
                    "Use **`$tz set`** to open the private setup window and enter yours.\n"
                    "Your timezone is entered only inside the Discord popup and is **never shown in chat**."
                )
            else:
                description = (
                    f"{target.mention} doesn't have a timezone set yet.\n\n"
                    "They can use **`$tz set`** to configure their timezone privately."
                )
            embed = _timezone_embed(
                title="No Timezone Set",
                description=description,
                color=EMBED_COLOR,
                timestamp=True,
            )
            view = ExitOnlyView(ctx.author.id)
            await ctx.send(embed=embed, view=view)

            await self._log_action(
                ctx.guild.id,
                f"{EMOJI_CLOCK} Timezone — Viewed (No Value Set)",
                actor=ctx.author,
                channel=ctx.channel,
                color=EMBED_COLOR,
                fields=[
                    ("Target", f"{target.mention} (`{target.id}`)"),
                    ("Self View", "yes" if target.id == ctx.author.id else "no"),
                ],
            )
            return

        try:
            tzinfo = ZoneInfo(row["timezone"])
        except Exception:
            await _safe_delete(ctx.message)
            await self._log_action(
                ctx.guild.id,
                f"{EMOJI_CLOCK} Timezone — View Failed (Invalid Stored TZ)",
                actor=ctx.author,
                channel=ctx.channel,
                color=EMBED_COLOR,
                fields=[("Target", f"{target.mention} (`{target.id}`)")],
            )
            return

        now = datetime.datetime.now(tzinfo)
        embed = discord.Embed(
            title=f"{EMOJI_STAR} Local Time",
            color=EMBED_COLOR,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.description = "\n".join([
            BREAKER_LINE,
            f"{EMOJI_USER_ID} **User**\n{target.mention}",
            "",
            f"{EMOJI_CLOCK} **Local Time**\n`{now.strftime('%H:%M:%S')}`",
            f"{EMOJI_CALENDAR} **Date**\n`{now.strftime('%Y/%m/%d')}`",
            BREAKER_LINE,
        ])
        _apply_footer(embed)
        view = ExitOnlyView(ctx.author.id)
        await ctx.send(embed=embed, view=view)

        await self._log_action(
            ctx.guild.id,
            f"{EMOJI_CLOCK} Timezone — Viewed",
            actor=ctx.author,
            channel=ctx.channel,
            color=EMBED_COLOR,
            fields=[
                ("Target", f"{target.mention} (`{target.id}`)"),
                ("Self View", "yes" if target.id == ctx.author.id else "no"),
            ],
        )

    @commands.group(name="tz", invoke_without_command=True)
    @commands.guild_only()
    async def tz(self, ctx, member: Optional[discord.Member] = None):
        if not await self._tz_precheck(ctx):
            return
        await self._tz_render(ctx, member or ctx.author)

    @tz.command(name="help")
    @commands.guild_only()
    async def tz_help(self, ctx):
        if not await self._tz_precheck(ctx):
            return
        if self.is_staff(ctx.author):
            lines = self.TZ_HELP_LINES_STAFF
        else:
            lines = self.TZ_HELP_LINES_LIMITED
        embed = self._help_embed(f"{EMOJI_CLOCK} Timezone Commands", lines)
        view = ExitOnlyView(ctx.author.id)
        await ctx.send(embed=embed, view=view)
        await self._log_action(
            ctx.guild.id,
            f"{EMOJI_CLOCK} Timezone — Help Viewed",
            actor=ctx.author,
            channel=ctx.channel,
            color=EMBED_COLOR,
        )

    @tz.command(name="view")
    @commands.guild_only()
    async def tz_view(self, ctx, member: Optional[discord.Member] = None):
        if not await self._tz_precheck(ctx):
            return
        await self._tz_render(ctx, member or ctx.author)

    @tz.command(name="set")
    @commands.guild_only()
    async def tz_set(self, ctx):
        if not await self._tz_precheck(ctx):
            return

        await _safe_delete(ctx.message)

        view = TimezoneButtonView(
            self,
            guild_id=ctx.guild.id,
            author_id=ctx.author.id,
            target_id=ctx.author.id,
            is_edit=False,
        )
        embed = _timezone_embed(
            title="Set Your Timezone",
            description=(
                f"{ctx.author.mention}\n\n"
                "Use the button below to open a **private timezone setup window**.\n"
                "Your timezone is entered only inside the Discord popup and is **never shown in chat**.\n\n"
                f"**Quick setup**\n"
                "1. Click **Set Timezone**\n"
                "2. Enter your IANA timezone\n"
                "3. Submit and you're done\n\n"
                "Examples: `Asia/Kathmandu` • `America/New_York` • `Europe/London`"
            ),
            color=EMBED_COLOR,
            timestamp=True,
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

        await self._log_action(
            ctx.guild.id,
            f"{EMOJI_CLOCK} Timezone — Setup Prompt Opened",
            actor=ctx.author,
            channel=ctx.channel,
            color=EMBED_COLOR,
        )

    @tz.command(name="remove")
    @commands.guild_only()
    async def tz_remove(self, ctx, member: Optional[discord.Member] = None):
        if not await self._tz_precheck(ctx):
            return

        target = member or ctx.author
        if member is not None and member.id != ctx.author.id and not self.is_staff(ctx.author):
            await _deny_silently(ctx)
            return

        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM extra_tz_users WHERE guild_id = $1 AND user_id = $2",
                ctx.guild.id, target.id,
            )
        affected = int(result.split()[-1])
        if affected == 0:
            await ctx.send(f"{EMOJI_INFO} {target.mention} doesn't have a timezone set.", delete_after=6)
            return

        await ctx.send(f"{EMOJI_DELETE} Timezone removed for {target.mention}.", delete_after=6)

        await self._log_action(
            ctx.guild.id,
            f"{EMOJI_CLOCK} Timezone Removed",
            actor=ctx.author,
            channel=ctx.channel,
            color=EMBED_COLOR,
            fields=[("Target", f"{target.mention} (`{target.id}`)")],
        )

    @tz.command(name="edit")
    @commands.guild_only()
    async def tz_edit(self, ctx, member: discord.Member):
        if not await self._tz_precheck(ctx, require_staff=True):
            return

        await _safe_delete(ctx.message)

        view = TimezoneButtonView(
            self,
            guild_id=ctx.guild.id,
            author_id=ctx.author.id,
            target_id=member.id,
            is_edit=True,
        )
        embed = _timezone_embed(
            title="Edit Member Timezone",
            description=(
                f"{ctx.author.mention}\n\n"
                f"You are editing the timezone for {member.mention}.\n"
                "Click the button below to open the **private setup window**.\n"
                "The timezone entered in the popup is never displayed in this channel."
            ),
            color=EMBED_COLOR,
            timestamp=True,
        )
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

        await self._log_action(
            ctx.guild.id,
            f"{EMOJI_CLOCK} Timezone — Edit Prompt Opened",
            actor=ctx.author,
            channel=ctx.channel,
            color=EMBED_COLOR,
            fields=[("Target", f"{member.mention} (`{member.id}`)")],
        )

    @tz.command(name="list")
    @commands.guild_only()
    async def tz_list(self, ctx, page: int = 1):
        if not await self._tz_precheck(ctx):
            return

        per_page = 20
        total_pages = max(1, (len(_TZ_BROWSE_LIST) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start = (page - 1) * per_page
        chunk = _TZ_BROWSE_LIST[start:start + per_page]

        embed = discord.Embed(
            title=f"{EMOJI_GLOBAL} Available Timezones",
            description="\n".join(f"`{name}`" for name in chunk),
            color=EMBED_COLOR,
        )
        embed.add_field(
            name="Page",
            value=f"`{page}/{total_pages}` • Use `$tz set`, then enter the timezone privately",
            inline=False,
        )
        _apply_footer(embed)

        view = ExitOnlyView(ctx.author.id)
        await ctx.send(embed=embed, view=view)

        await self._log_action(
            ctx.guild.id,
            f"{EMOJI_CLOCK} Timezone — List Viewed",
            actor=ctx.author,
            channel=ctx.channel,
            color=EMBED_COLOR,
            fields=[("Page", f"`{page}/{total_pages}`")],
        )

    @tz.command(name="userlist")
    @commands.guild_only()
    async def tz_userlist(self, ctx):
        if not await self._tz_precheck(ctx, require_staff=True):
            return

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id FROM extra_tz_users WHERE guild_id = $1", ctx.guild.id
            )

        count = len(rows)
        embed = discord.Embed(
            title=f"{EMOJI_CLOCK} Timezone Users",
            description=f"**{count}** member(s) currently have a timezone set.",
            color=EMBED_COLOR,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        if rows:
            shown = rows[:TZ_USERLIST_SHOW_LIMIT]
            mentions = ", ".join(f"<@{r['user_id']}>" for r in shown)
            if count > len(shown):
                mentions += f", *and {count - len(shown)} more*"
            embed.add_field(name="Members", value=mentions[:1024] or "—", inline=False)
        _apply_footer(embed)
        view = ExitOnlyView(ctx.author.id)
        await ctx.send(embed=embed, view=view)

        await self._log_action(
            ctx.guild.id,
            f"{EMOJI_CLOCK} Timezone — User List Viewed",
            actor=ctx.author,
            channel=ctx.channel,
            color=EMBED_COLOR,
            fields=[("Count", f"`{count}`")],
        )

    @tz.command(name="enable")
    @commands.guild_only()
    async def tz_enable(self, ctx):
        if not self.is_staff(ctx.author):
            await _deny_silently(ctx)
            return
        await self._tz_set_enabled(ctx.guild.id, True)
        await ctx.send(f"{EMOJI_TICK} Timezone module enabled — everyone can use `$tz` unless blacklisted.")

        await self._log_action(
            ctx.guild.id,
            f"{EMOJI_CLOCK} Timezone Module Enabled",
            actor=ctx.author,
            channel=ctx.channel,
            color=EMBED_COLOR,
        )

    @tz.command(name="disable")
    @commands.guild_only()
    async def tz_disable(self, ctx):
        if not await self._tz_precheck(ctx, require_staff=True):
            return
        await self._tz_set_enabled(ctx.guild.id, False)
        await ctx.send(f"{EMOJI_DND} Timezone module disabled.")

        await self._log_action(
            ctx.guild.id,
            f"{EMOJI_CLOCK} Timezone Module Disabled",
            actor=ctx.author,
            channel=ctx.channel,
            color=EMBED_COLOR,
        )

    @tz.group(name="blacklist", aliases=["bl"], invoke_without_command=True)
    @commands.guild_only()
    async def tz_blacklist(self, ctx, target: Optional[Union[discord.Member, discord.Role]] = None):
        if not await self._tz_precheck(ctx, require_staff=True):
            return
        if target is None:
            await self._tz_blacklist_list(ctx)
        else:
            await self._tz_blacklist_add(ctx, target)

    @tz_blacklist.command(name="add")
    @commands.guild_only()
    async def tz_blacklist_add_cmd(self, ctx, target: Union[discord.Member, discord.Role]):
        if not await self._tz_precheck(ctx, require_staff=True):
            return
        await self._tz_blacklist_add(ctx, target)

    async def _tz_blacklist_add(self, ctx, target):
        target_type = "role" if isinstance(target, discord.Role) else "user"
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO extra_tz_blacklist (guild_id, target_id, target_type, added_by)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (guild_id, target_id, target_type) DO NOTHING
                """,
                ctx.guild.id, target.id, target_type, ctx.author.id,
            )
        await ctx.send(f"{EMOJI_TICK} {target.mention} is now **blocked** from timezone commands.")

        await self._log_action(
            ctx.guild.id,
            f"{EMOJI_CLOCK} Timezone Blacklist — Added",
            actor=ctx.author,
            channel=ctx.channel,
            color=EMBED_COLOR,
            fields=[
                ("Target", f"{target.mention} (`{target.id}`)"),
                ("Type", f"`{target_type}`"),
            ],
        )

    @tz_blacklist.command(name="remove")
    @commands.guild_only()
    async def tz_blacklist_remove(self, ctx, target: Union[discord.Member, discord.Role]):
        if not await self._tz_precheck(ctx, require_staff=True):
            return
        target_type = "role" if isinstance(target, discord.Role) else "user"
        async with self.pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM extra_tz_blacklist WHERE guild_id = $1 AND target_id = $2 AND target_type = $3",
                ctx.guild.id, target.id, target_type,
            )
        await ctx.send(f"{EMOJI_TICK} {target.mention} removed from the timezone blacklist.")

        await self._log_action(
            ctx.guild.id,
            f"{EMOJI_CLOCK} Timezone Blacklist — Removed",
            actor=ctx.author,
            channel=ctx.channel,
            color=EMBED_COLOR,
            fields=[
                ("Target", f"{target.mention} (`{target.id}`)"),
                ("Type", f"`{target_type}`"),
            ],
        )

    @tz_blacklist.command(name="list")
    @commands.guild_only()
    async def tz_blacklist_list_cmd(self, ctx):
        if not await self._tz_precheck(ctx, require_staff=True):
            return
        await self._tz_blacklist_list(ctx)

    async def _tz_blacklist_list(self, ctx):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT target_id, target_type FROM extra_tz_blacklist WHERE guild_id = $1", ctx.guild.id
            )
        if not rows:
            await ctx.send(
                f"{EMOJI_INFO} Timezone blacklist is empty — everyone can use timezone commands "
                "while the module is enabled."
            )
            await self._log_action(
                ctx.guild.id,
                f"{EMOJI_CLOCK} Timezone Blacklist — Viewed (Empty)",
                actor=ctx.author,
                channel=ctx.channel,
                color=EMBED_COLOR,
            )
            return
        lines = []
        for row in rows:
            mention = f"<@&{row['target_id']}>" if row["target_type"] == "role" else f"<@{row['target_id']}>"
            lines.append(f"• {mention} ({row['target_type']})")
        embed = discord.Embed(
            title=f"{EMOJI_CLOCK} Timezone Blacklist",
            description="\n".join(lines),
            color=EMBED_COLOR,
        )
        _apply_footer(embed)
        view = ExitOnlyView(ctx.author.id)
        await ctx.send(embed=embed, view=view)

        await self._log_action(
            ctx.guild.id,
            f"{EMOJI_CLOCK} Timezone Blacklist — Viewed",
            actor=ctx.author,
            channel=ctx.channel,
            color=EMBED_COLOR,
            fields=[("Entries", f"`{len(rows)}`")],
        )

    # ===============================================================
    #  PURGE
    # ===============================================================
    DEFAULT_PURGE_AMOUNT = 10

    async def _purge_logic(self, ctx, amount=10, mode="all", user=None, text_filter=None,
                           after_message=None, before_message=None):
        if amount > 500 or amount < 1:
            return await self.fail(ctx, "Amount must be between 1 and 500.")

        invoking_id = None
        if isinstance(ctx, commands.Context) and ctx.message is not None:
            invoking_id = ctx.message.id

        def check(msg):
            if invoking_id is not None and msg.id == invoking_id:
                return False
            if after_message and msg.created_at <= after_message.created_at:
                return False
            if before_message and msg.created_at >= before_message.created_at:
                return False
            if mode == "user":
                return bool(user) and msg.author == user
            if mode == "bots":
                return msg.author.bot
            if mode == "embeds":
                return bool(msg.embeds)
            if mode == "mentions":
                return bool(msg.mentions)
            if mode == "attachments":
                return bool(msg.attachments)
            if mode == "text":
                return not (msg.attachments or msg.embeds or msg.mentions)
            if mode == "startswith":
                return bool(text_filter) and msg.content.lower().startswith(text_filter.lower())
            if mode == "endswith":
                return bool(text_filter) and msg.content.lower().endswith(text_filter.lower())
            if mode == "contains":
                return bool(text_filter) and text_filter.lower() in msg.content.lower()
            return True

        try:
            deleted = await ctx.channel.purge(limit=amount + 1 if invoking_id else amount, check=check)
        except Exception as e:
            logger.error("Purge failed: %s", e)
            return await self.fail(ctx)

        actor = ctx.author if not isinstance(ctx, discord.Interaction) else ctx.user
        await self._record_modstat(actor.id, "purge")

        count = len(deleted) if deleted is not None else 0
        try:
            if isinstance(ctx, commands.Context):
                await ctx.send(f"{EMOJI_DELETE} Deleted {count} messages.", delete_after=5)
            else:
                await ctx.followup.send(f"{EMOJI_DELETE} Deleted {count} messages.", ephemeral=True)
        except Exception as e:
            logger.debug("Failed to send purge confirmation: %s", e)

        await self.log_action(
            ctx, "Purge",
            target=user if user is not None else None,
            channel=ctx.channel,
            extra=f"Deleted `{count}` messages (mode: `{mode}`, limit: `{amount}`"
                  + (f", text filter: `{text_filter}`" if text_filter else "") + ")",
        )

    @staticmethod
    def _split_trailing_amount(text):
        if not text:
            return text, None
        parts = text.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0].strip() or None, int(parts[1])
        return text, None

    @commands.command(aliases=["clear", "clean", "prune", "cb", "c"])
    @require("trial_moderator")
    async def purge(self, ctx, mode_or_amount: str = None, amount: int = None, *, text_filter: str = None):
        if mode_or_amount is None:
            return await self._purge_logic(ctx, self.DEFAULT_PURGE_AMOUNT, "all")
        if mode_or_amount.isdigit():
            return await self._purge_logic(ctx, int(mode_or_amount), "all")

        mode = mode_or_amount.lower()

        if mode in ("startswith", "endswith", "contains"):
            combined = " ".join(p for p in [str(amount) if amount is not None else None, text_filter] if p)
            remaining, trailing = self._split_trailing_amount(combined)
            parsed = trailing if trailing is not None else self.DEFAULT_PURGE_AMOUNT
            if not remaining:
                return await self.fail(ctx, f"You must provide text for `{mode}` mode.")
            return await self._purge_logic(ctx, parsed, mode, text_filter=remaining)

        if mode == "user":
            combined = " ".join(p for p in [str(amount) if amount is not None else None, text_filter] if p)
            remaining, trailing = self._split_trailing_amount(combined)
            parsed = trailing if trailing is not None else self.DEFAULT_PURGE_AMOUNT
            if not remaining:
                return await self.fail(ctx, "You must mention a user for `user` mode.")
            uid = re.sub(r'[<>@!]', '', remaining)
            try:
                user = await commands.MemberConverter().convert(ctx, uid)
            except Exception:
                return await self.fail(ctx, "Invalid user.")
            return await self._purge_logic(ctx, parsed, mode, user=user)

        if mode in ("bots", "embeds", "mentions", "attachments", "text", "all"):
            parsed = amount if amount is not None else self.DEFAULT_PURGE_AMOUNT
            return await self._purge_logic(ctx, parsed, mode)

        return await self._purge_logic(ctx, self.DEFAULT_PURGE_AMOUNT, "all")

    @commands.group(name="clearmsg", aliases=["clr"], invoke_without_command=True)
    @require("trial_moderator")
    async def clearmsg_group(self, ctx, amount: int = 10):
        await self._purge_logic(ctx, amount, "all")

    @clearmsg_group.command(name="bot", aliases=["b"])
    @require("trial_moderator")
    async def clearmsg_bot(self, ctx, amount: int = 10):
        await self._purge_logic(ctx, amount, "bots")

    @clearmsg_group.command(name="user", aliases=["u"])
    @require("trial_moderator")
    async def clearmsg_user(self, ctx, user: discord.Member, amount: int = 10):
        await self._purge_logic(ctx, amount, "user", user=user)

    @clearmsg_group.command(name="all", aliases=["a"])
    @require("trial_moderator")
    async def clearmsg_all(self, ctx, amount: int = 10):
        await self._purge_logic(ctx, amount, "all")

    # ===============================================================
    #  SLOWMODE  (Moderator and higher) — aliases: sm, slowmo, slow
    # ===============================================================
    @commands.command(aliases=["sm", "slowmo", "slow"])
    @require("moderator")
    async def slowmode(self, ctx, seconds: int):
        if seconds < 0:
            return await self.fail(ctx, "Seconds cannot be negative.")
        old_delay = getattr(ctx.channel, "slowmode_delay", 0)
        try:
            await ctx.channel.edit(slowmode_delay=seconds)
        except discord.Forbidden:
            return await self.fail(ctx, "I lack Manage Channels permission.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed to set slowmode: {e}")

        await self.log_action(
            ctx, "Slowmode Updated",
            channel=ctx.channel,
            before=f"`{old_delay}s`",
            after=f"`{seconds}s`",
        )

    # ===============================================================
    #  LOCK / UNLOCK  (Moderator and higher)
    # ===============================================================
    LOCK_SNAPSHOT_VERSION = 2

    def _get_lock_bypass_role_ids(self, guild):
        roles_cfg = self.bot.config.get("roles", {})
        bypass_ids = set()

        for role_name, level in PERM_LEVELS.items():
            if level < PERM_LEVELS["admin"]:
                continue

            role_id = roles_cfg.get(f"{role_name}_role_id") or self.bot.config.get(f"{role_name}_role_id")
            if role_id is None:
                continue

            try:
                bypass_ids.add(int(role_id))
            except (TypeError, ValueError):
                logger.warning("Invalid %s role ID: %r", role_name, role_id)

        return bypass_ids

    @staticmethod
    def _serialize_lock_overwrites(channel):
        snapshot = []
        for target, overwrite in channel.overwrites.items():
            if isinstance(target, discord.Role):
                tt = "role"
            elif isinstance(target, discord.Member):
                tt = "member"
            else:
                continue
            allow, deny = overwrite.pair()
            snapshot.append({
                "type": tt,
                "id": str(target.id),
                "allow": allow.value,
                "deny": deny.value,
            })
        return snapshot

    @staticmethod
    def _deserialize_lock_overwrites(guild, snapshot):
        overwrites = {}
        stale = []
        for entry in snapshot:
            try:
                tt = entry["type"]
                tid = int(entry["id"])
                allow = int(entry["allow"])
                deny = int(entry["deny"])
            except (KeyError, TypeError, ValueError):
                stale.append("malformed")
                continue
            if tt == "role":
                target = guild.get_role(tid)
            elif tt == "member":
                target = guild.get_member(tid)
            else:
                stale.append(f"unknown type:{tid}")
                continue
            if target is None:
                stale.append(f"{tt}:{tid}")
                continue
            try:
                ow = discord.PermissionOverwrite.from_pair(
                    allow=discord.Permissions(allow),
                    deny=discord.Permissions(deny),
                )
            except Exception:
                logger.exception("Failed to deserialize lock overwrite for %s:%s", tt, tid)
                stale.append(f"{tt}:{tid}")
                continue
            if not ow.is_empty():
                overwrites[target] = ow
        return overwrites, stale

    @staticmethod
    def _decode_lockstate(data):
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise ValueError("lockstate JSON must be an object")
        return data

    @staticmethod
    def _legacy_snapshot_to_v2(old_snapshot):
        converted = []
        for role_id, data in old_snapshot.items():
            try:
                converted.append({
                    "type": "role",
                    "id": str(int(role_id)),
                    "allow": int(data["allow"]),
                    "deny": int(data["deny"]),
                })
            except (KeyError, TypeError, ValueError):
                logger.warning("Ignoring malformed legacy lock entry: %r=%r", role_id, data)
        return converted

    async def _lock_channel(self, ctx, channel):
        guild = ctx.guild
        bot_member = guild.me

        if bot_member is None:
            return await self.fail(ctx, "I could not resolve my server member.")
        if not bot_member.guild_permissions.manage_channels:
            return await self.fail(ctx, "I lack **Manage Channels** permission.")
        if not channel.permissions_for(bot_member).view_channel:
            return await self.fail(ctx, "I cannot access that channel.")

        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                advisory_key = int(channel.id) & 0x7FFFFFFFFFFFFFFF
                await conn.execute("SELECT pg_advisory_xact_lock($1)", advisory_key)

                existing = await conn.fetchrow("SELECT data FROM lockstate WHERE channel_id = $1", channel.id)
                if existing is not None:
                    return await self.fail(ctx, f"{channel.mention} is already locked.")

                original = self._serialize_lock_overwrites(channel)
                snapshot = {
                    "version": self.LOCK_SNAPSHOT_VERSION,
                    "guild_id": str(guild.id),
                    "channel_id": str(channel.id),
                    "locked_by": str(ctx.author.id),
                    "locked_at": int(time.time()),
                    "overwrites": original,
                }

                new_overwrites = dict(channel.overwrites)
                bypass_role_ids = self._get_lock_bypass_role_ids(guild)

                roles_to_lock = {t for t in channel.overwrites if isinstance(t, discord.Role)}
                roles_to_lock.add(guild.default_role)

                for role in roles_to_lock:
                    if role.permissions.administrator or role.id in bypass_role_ids:
                        continue
                    old = channel.overwrites_for(role)
                    allow, deny = old.pair()
                    locked = discord.PermissionOverwrite.from_pair(allow=allow, deny=deny)
                    locked.send_messages = False
                    new_overwrites[role] = locked

                for target in list(channel.overwrites):
                    if not isinstance(target, discord.Member) or target.id == bot_member.id:
                        continue
                    old = channel.overwrites_for(target)
                    allow, deny = old.pair()
                    locked = discord.PermissionOverwrite.from_pair(allow=allow, deny=deny)
                    locked.send_messages = False
                    new_overwrites[target] = locked

                for role_id in bypass_role_ids:
                    role = guild.get_role(role_id)
                    if role is None or role.is_default():
                        continue
                    old = channel.overwrites_for(role)
                    allow, deny = old.pair()
                    bypass = discord.PermissionOverwrite.from_pair(allow=allow, deny=deny)
                    bypass.send_messages = True
                    new_overwrites[role] = bypass

                old_bot = channel.overwrites_for(bot_member)
                allow, deny = old_bot.pair()
                bot_ow = discord.PermissionOverwrite.from_pair(allow=allow, deny=deny)
                bot_ow.view_channel = True
                bot_ow.send_messages = True
                bot_ow.read_message_history = True
                new_overwrites[bot_member] = bot_ow

                try:
                    await conn.execute(
                        "INSERT INTO lockstate (channel_id, data) VALUES ($1, $2::jsonb)",
                        channel.id, json.dumps(snapshot),
                    )
                except Exception:
                    logger.exception("Failed to save lock snapshot for channel %s", channel.id)
                    return await self.fail(ctx, "Failed to save the channel lock state.")

                try:
                    await channel.edit(
                        overwrites=new_overwrites,
                        reason=f"Locked by {ctx.author} ({ctx.author.id})",
                    )
                except discord.Forbidden:
                    try:
                        await conn.execute("DELETE FROM lockstate WHERE channel_id = $1", channel.id)
                    except Exception:
                        logger.exception("Failed to remove failed lock snapshot for %s", channel.id)
                    return await self.fail(ctx, "Discord rejected the lock.")
                except discord.HTTPException as exc:
                    logger.exception("HTTP error locking channel %s: %s", channel.id, exc)
                    try:
                        await conn.execute("DELETE FROM lockstate WHERE channel_id = $1", channel.id)
                    except Exception:
                        logger.exception("Failed to remove failed lock snapshot for %s", channel.id)
                    return await self.fail(ctx, "Discord failed to apply the channel lock.")
                except Exception:
                    logger.exception("Unexpected lock failure for channel %s", channel.id)
                    try:
                        await conn.execute("DELETE FROM lockstate WHERE channel_id = $1", channel.id)
                    except Exception:
                        logger.exception("Failed to remove failed lock snapshot for %s", channel.id)
                    return await self.fail(ctx, "Unexpected error while locking channel.")

        await self.log_action(
            ctx, "Channel Locked",
            channel=channel,
            extra=f"Overwrites snapshot saved (`{len(original)}` entries).",
        )

    @commands.command()
    @require("moderator")
    async def lock(self, ctx, channel: discord.TextChannel = None):
        chan = channel or ctx.channel
        if not isinstance(chan, discord.TextChannel):
            return await self.fail(ctx, "That channel cannot be locked by this command.")
        await self._lock_channel(ctx, chan)

    async def _unlock_channel(self, ctx, channel):
        guild = ctx.guild
        bot_member = guild.me

        if bot_member is None:
            return await self.fail(ctx, "I could not resolve my server member.")
        if not bot_member.guild_permissions.manage_channels:
            return await self.fail(ctx, "I lack **Manage Channels** permission.")

        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                advisory_key = int(channel.id) & 0x7FFFFFFFFFFFFFFF
                await conn.execute("SELECT pg_advisory_xact_lock($1)", advisory_key)

                row = await conn.fetchrow("SELECT data FROM lockstate WHERE channel_id = $1", channel.id)
                if row is None:
                    return await self.fail(
                        ctx,
                        f"{channel.mention} has no saved lock state. "
                        "I will not guess which permissions belong to the lock.",
                    )

                try:
                    data = self._decode_lockstate(row["data"])
                except Exception:
                    logger.exception("Corrupt lockstate for channel %s", channel.id)
                    return await self.fail(ctx, "The saved lock state is corrupted.")

                version = data.get("version")
                if version is None:
                    logger.warning("Legacy role-only lock snapshot detected for channel %s", channel.id)
                    snapshot = self._legacy_snapshot_to_v2(data)
                elif version != self.LOCK_SNAPSHOT_VERSION:
                    return await self.fail(ctx, f"Unsupported lock snapshot version `{version}`.")
                else:
                    if str(data.get("guild_id")) != str(guild.id):
                        return await self.fail(ctx, "The saved lock belongs to another server.")
                    if str(data.get("channel_id")) != str(channel.id):
                        return await self.fail(ctx, "The saved lock belongs to another channel.")
                    snapshot = data.get("overwrites")
                    if not isinstance(snapshot, list):
                        return await self.fail(ctx, "The saved lock snapshot is malformed.")

                restored, stale = self._deserialize_lock_overwrites(guild, snapshot)

                if stale:
                    logger.warning(
                        "Unlock for channel %s has %d stale snapshot targets: %s",
                        channel.id, len(stale), ", ".join(stale[:20]),
                    )

                try:
                    await channel.edit(
                        overwrites=restored,
                        reason=f"Unlocked by {ctx.author} ({ctx.author.id})",
                    )
                except discord.Forbidden:
                    return await self.fail(ctx, "Discord rejected the unlock.")
                except discord.HTTPException as exc:
                    logger.exception("HTTP error unlocking channel %s: %s", channel.id, exc)
                    return await self.fail(ctx, "Discord failed to restore the channel permissions.")
                except Exception:
                    logger.exception("Unexpected unlock failure for channel %s", channel.id)
                    return await self.fail(ctx, "Unexpected error while unlocking channel.")

                try:
                    await conn.execute("DELETE FROM lockstate WHERE channel_id = $1", channel.id)
                except Exception:
                    logger.exception("Channel %s restored but lockstate cleanup failed", channel.id)
                    return await self.fail(
                        ctx,
                        "The channel was unlocked, but I could not remove the old "
                        "database snapshot. Please run unlock again.",
                    )

        await self.log_action(
            ctx, "Channel Unlocked",
            channel=channel,
            extra=f"Restored `{len(restored)}` overwrite entries"
                  + (f" (`{len(stale)}` stale targets skipped)." if stale else "."),
        )

    @commands.command()
    @require("moderator")
    async def unlock(self, ctx, channel: discord.TextChannel = None):
        chan = channel or ctx.channel
        if not isinstance(chan, discord.TextChannel):
            return await self.fail(ctx, "That channel cannot be unlocked by this command.")
        await self._unlock_channel(ctx, chan)

    # ===============================================================
    #  WARNS
    # ===============================================================
    async def _warn_logic(self, ctx, member, reason="No reason"):
        actor = self._get_actor(ctx)
        try:
            await self.check_mod_permission(ctx, "trial_moderator")
        except commands.CheckFailure as e:
            return await self.fail(ctx, str(e))
        ok, err = self.can_target(actor, member)
        if not ok:
            return await self.fail(ctx, err)

        should_punish = False
        new_count = 0
        previous = 0
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                lock_key = ((ctx.guild.id << 22) ^ member.id) & 0x7FFFFFFFFFFFFFFF
                await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)
                previous = await conn.fetchval(
                    "SELECT COUNT(*) FROM warnings WHERE guild_id = $1 AND user_id = $2 AND countable = TRUE",
                    ctx.guild.id, member.id
                )
                await conn.execute(
                    "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, timestamp, countable) "
                    "VALUES ($1, $2, $3, $4, $5, TRUE)",
                    ctx.guild.id, member.id, actor.id, reason, int(time.time())
                )
                new_count = previous + 1
                should_punish = previous < self.warn_ban_threshold <= new_count

        await self.send(ctx, make_embed(
            ctx, f"{EMOJIS['warning']} Warning Issued",
            f"**User:** {member.mention}\n**Reason:** {reason}\n**Total Warns:** {new_count}",
            GREY_COLOR,
        ))
        await self.log_action(
            ctx, "Warn", member,
            reason=reason,
            before=f"`{previous}` countable warn(s)",
            after=f"`{new_count}` countable warn(s)",
            extra=f"Auto-punish threshold: `{self.warn_ban_threshold}`"
                  + (" — **auto-ban triggered**" if should_punish else ""),
        )
        await self._manual_dm(member, "Warning", reason)
        await self._record_modstat(actor.id, "warn")

        if should_punish:
            try:
                await member.ban(reason=f"Automatic ban: reached {new_count} warnings")
                await self.log_action(ctx, "Automatic Ban", member, reason=reason,
                                      extra=f"Auto-ban after reaching `{new_count}` warnings.")
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.error("Auto-ban failed for %s: %s", member.id, e)

    @commands.command(aliases=["w"])
    @require("trial_moderator")
    async def warn(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        await self._warn_logic(ctx, member, reason)

    async def slash_warn(self, interaction, member: discord.Member, reason: str = "No reason"):
        await self._warn_logic(interaction, member, reason)

    async def _warns_logic(self, ctx, member):
        rows = await self.db.fetch(
            "SELECT moderator_id, reason, timestamp, countable FROM warnings "
            "WHERE guild_id = $1 AND user_id = $2 ORDER BY timestamp ASC",
            ctx.guild.id, member.id
        )
        if not rows:
            return await self.send(ctx, make_embed(
                ctx, f"{EMOJIS['alert']} No Warnings",
                f"{member.mention} has no warnings.",
                GREY_COLOR,
            ))
        desc = ""
        for i, w in enumerate(rows, 1):
            mod = self.bot.get_user(w["moderator_id"])
            tag = "" if w["countable"] else " *(not counted)*"
            desc += f"**{i}.** By {mod.mention if mod else 'Unknown'} — <t:{w['timestamp']}:R>{tag}\n> {w['reason']}\n"
        countable = sum(1 for w in rows if w["countable"])
        embed = make_embed(
            ctx,
            f"{EMOJIS['alert']} {member.display_name}'s Warnings ({len(rows)} total, {countable} countable)",
            desc, GREY_COLOR,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.send(ctx, embed)

    @commands.command(name="warns", aliases=["ws", "warnshow", "warnings"])
    @require("trial_moderator")
    async def warns(self, ctx, member: discord.Member):
        await self._warns_logic(ctx, member)

    async def _warnremove_logic(self, ctx, member, index=None):
        rows = await self.db.fetch(
            "SELECT id, reason FROM warnings WHERE guild_id = $1 AND user_id = $2 ORDER BY id ASC",
            ctx.guild.id, member.id
        )
        if not rows:
            return await self.send(ctx, make_embed(
                ctx, f"{EMOJIS['alert']} No Warnings", "Nothing to clear.", GREY_COLOR
            ))
        if index is None:
            await self.db.execute("DELETE FROM warnings WHERE guild_id = $1 AND user_id = $2",
                                  ctx.guild.id, member.id)
            await self.log_action(
                ctx, "Warnings Cleared", member,
                extra=f"Removed `{len(rows)}` warning(s) in bulk.",
            )
            return
        if index < 1 or index > len(rows):
            return await self.fail(ctx, f"Index out of range (1-{len(rows)}).")
        wid = rows[index - 1]["id"]
        reason = rows[index - 1]["reason"]
        await self.db.execute("DELETE FROM warnings WHERE id = $1", wid)
        await self.log_action(
            ctx, "Warning Removed", member,
            extra=f"Removed warn **#{index}** (id `{wid}`) — reason was: {reason}",
        )

    @commands.command(name="warnremove", aliases=["rw", "cw", "removewarn", "deletewarn", "clearwarns"])
    @require("trial_moderator")
    async def warnremove(self, ctx, member: discord.Member, index: Optional[int] = None):
        await self._warnremove_logic(ctx, member, index)

    # ===============================================================
    #  MODSTATS
    # ===============================================================
    @commands.group(name="modstats", invoke_without_command=True)
    @require("admin")
    async def modstats_group(self, ctx):
        if not self.modstats:
            return await self.send(ctx, make_embed(ctx, "Moderation Stats", "No stats recorded yet.", GREY_COLOR))
        sorted_stats = sorted(self.modstats.items(), key=lambda x: sum(x[1].values()), reverse=True)
        desc = ""
        for i, (uid, cmds) in enumerate(sorted_stats[:10], 1):
            member = ctx.guild.get_member(int(uid))
            name = member.mention if member else f"<@{uid}>"
            total = sum(cmds.values())
            cl = ", ".join(f"{c}: {n}" for c, n in sorted(cmds.items(), key=lambda x: x[1], reverse=True)[:3])
            desc += f"**{i}.** {name} — **{total}** total\n> {cl}\n"
        await self.send(ctx, make_embed(ctx, f"{EMOJIS['chart']} Moderation Stats — Top 10", desc, GREY_COLOR))

    @modstats_group.command(name="add")
    @require("admin")
    async def modstats_add(self, ctx, command_name: str, count: int, user: discord.Member = None):
        target = user or ctx.author
        uid = str(target.id)
        self.modstats.setdefault(uid, {})
        self.modstats[uid][command_name] = self.modstats[uid].get(command_name, 0) + count
        await self.db.execute(
            "INSERT INTO modstats (user_id, command, count) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id, command) DO UPDATE SET count = modstats.count + $3",
            target.id, command_name, count
        )

    @modstats_group.command(name="remove")
    @require("admin")
    async def modstats_remove(self, ctx, command_name: str, count: int, user: discord.Member = None):
        target = user or ctx.author
        uid = str(target.id)
        if uid not in self.modstats or command_name not in self.modstats[uid]:
            return await self.fail(ctx, "No stats found for that command/user.")
        self.modstats[uid][command_name] = max(0, self.modstats[uid].get(command_name, 0) - count)
        if self.modstats[uid][command_name] == 0:
            del self.modstats[uid][command_name]
        if not self.modstats[uid]:
            del self.modstats[uid]
        await self.db.execute(
            "UPDATE modstats SET count = GREATEST(count - $3, 0) WHERE user_id = $1 AND command = $2",
            target.id, command_name, count
        )

    @modstats_group.command(name="user")
    @require("admin")
    async def modstats_user(self, ctx, user: discord.Member):
        uid = str(user.id)
        if uid not in self.modstats:
            return await self.send(ctx, make_embed(ctx, f"{user.display_name}'s Stats", "No stats recorded.", GREY_COLOR))
        cmds = self.modstats[uid]
        total = sum(cmds.values())
        desc = "\n".join(f"• `{c}`: **{n}**" for c, n in sorted(cmds.items(), key=lambda x: x[1], reverse=True))
        embed = make_embed(ctx, f"{EMOJIS['chart']} {user.display_name}'s Stats", f"**Total:** {total}\n{desc}", GREY_COLOR)
        embed.set_thumbnail(url=user.display_avatar.url)
        await self.send(ctx, embed)

    # ===============================================================
    #  NICKNAME
    # ===============================================================
    @commands.command(aliases=["nick"])
    @require("trial_moderator")
    async def nickname(self, ctx, member: discord.Member, *, nick: str = None):
        actor = self._get_actor(ctx)
        if actor is None:
            return await self.fail(ctx, "Could not resolve your member.")
        if actor.id != member.id:
            ok, err = self.can_target(actor, member)
            if not ok:
                return await self.fail(ctx, err)

        if nick and len(nick) > 32:
            return await self.fail(ctx, "Nickname must be 32 characters or fewer.")

        old_nick = member.nick or member.name
        try:
            await member.edit(
                nick=nick,
                reason=f"Nickname {'cleared' if not nick else 'changed'} by {actor} ({actor.id})",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            return await self.fail(ctx, f"Failed: {e}")

        new_nick = nick if nick else member.name
        await self.log_action(
            ctx,
            "Nickname Cleared" if not nick else "Nickname Changed",
            member,
            before=f"`{old_nick}`",
            after=f"`{new_nick}`",
            moderator=actor,
        )

    # ===============================================================
    #  SAY
    # ===============================================================
    @commands.command(aliases=["echo"])
    @require("senior_moderator")
    async def say(self, ctx, channel: discord.TextChannel, *, text: str):
        try:
            await channel.send(text)
        except (discord.Forbidden, discord.HTTPException) as e:
            return await self.fail(ctx, f"Failed to send: {e}")

        await self.log_action(
            ctx, "Say",
            channel=channel,
            extra=f"Sent message:\n>>> {text[:800]}",
        )

    # ===============================================================
    #  MUTE / UNMUTE
    # ===============================================================
    async def _mute_logic(self, ctx, member, duration, reason="No reason"):
        actor = self._get_actor(ctx)
        try:
            await self.check_mod_permission(ctx, "trial_moderator")
        except commands.CheckFailure as e:
            return await self.fail(ctx, str(e))
        ok, err = self.can_target(actor, member)
        if not ok:
            return await self.fail(ctx, err)

        seconds = parse_time(duration)
        if seconds <= 0:
            return await self.fail(ctx, "Invalid duration. Use `10s`, `5m`, `2h`, `1d`.")
        if seconds > 28 * 86400:
            return await self.fail(ctx, "Maximum timeout is 28 days.")

        until = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
        try:
            await member.timeout(until, reason=reason)
        except discord.Forbidden:
            return await self.fail(ctx, "Missing Moderate Members permission.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed to timeout: {e}")

        await self.log_action(
            ctx, "Mute", member,
            reason=reason,
            before="Not timed out",
            after=f"Timed out until {discord.utils.format_dt(until, style='F')}",
            duration=fmt_duration(seconds),
        )
        await self._record_modstat(actor.id, "mute")

    @commands.command(aliases=["m", "stfu", "shut", "shh"])
    @require("trial_moderator")
    async def mute(self, ctx, member: discord.Member, duration: str, *, reason: str = "No reason"):
        await self._mute_logic(ctx, member, duration, reason)

    async def slash_mute(self, interaction, member: discord.Member, duration: str, reason: str = "No reason"):
        await self._mute_logic(interaction, member, duration, reason)

    async def _unmute_logic(self, ctx, member):
        if not member.is_timed_out():
            return await self.fail(ctx, f"{member.mention} is not timed out.")
        mod = ctx.author if not isinstance(ctx, discord.Interaction) else ctx.user
        try:
            await member.timeout(None, reason=f"Unmuted by {mod}")
        except (discord.Forbidden, discord.HTTPException) as e:
            return await self.fail(ctx, f"Failed: {e}")
        await self.log_action(ctx, "Unmute", member, before="Timed out", after="Timeout removed")
        await self._record_modstat(mod.id, "unmute")

    @commands.command(aliases=["um"])
    @require("trial_moderator")
    async def unmute(self, ctx, member: discord.Member):
        await self._unmute_logic(ctx, member)

    async def slash_unmute(self, interaction, member: discord.Member):
        await self._unmute_logic(interaction, member)

    # ===============================================================
    #  KICK
    # ===============================================================
    async def _kick_logic(self, ctx, member, reason="No reason"):
        actor = self._get_actor(ctx)
        try:
            await self.check_mod_permission(ctx, "moderator")
        except commands.CheckFailure as e:
            return await self.fail(ctx, str(e))
        ok, err = self.can_target(actor, member)
        if not ok:
            return await self.fail(ctx, err)

        target_info = f"{member} (`{member.id}`) — joined {discord.utils.format_dt(member.joined_at, style='R') if member.joined_at else 'unknown'}"
        try:
            await member.kick(reason=reason)
        except discord.Forbidden:
            return await self.fail(ctx, "I lack Kick Members permission.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Kick failed: {e}")

        await self.log_action(ctx, "Kick", member, reason=reason, extra=f"Target info: {target_info}")
        await self._manual_dm(member, "Kick", reason)
        await self._record_modstat(actor.id, "kick")

    @commands.command(aliases=["k", "boot", "gtfo"])
    @require("moderator")
    async def kick(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        await self._kick_logic(ctx, member, reason)

    async def slash_kick(self, interaction, member: discord.Member, reason: str = "No reason"):
        await self._kick_logic(interaction, member, reason)

    # ===============================================================
    #  BAN
    # ===============================================================
    async def _ban_logic(self, ctx, member, reason="No reason"):
        actor = self._get_actor(ctx)
        try:
            await self.check_mod_permission(ctx, "moderator")
        except commands.CheckFailure as e:
            return await self.fail(ctx, str(e))
        ok, err = self.can_target(actor, member)
        if not ok:
            return await self.fail(ctx, err)

        target_info = f"{member} (`{member.id}`)"
        try:
            await member.ban(reason=reason)
        except discord.Forbidden:
            return await self.fail(ctx, "I lack Ban Members permission.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Ban failed: {e}")

        await self.log_action(ctx, "Ban", member, reason=reason,
                              before="Not banned", after="Banned",
                              extra=f"Target: {target_info}")
        await self._manual_dm(member, "Ban", reason)
        await self._record_modstat(actor.id, "ban")

    @commands.command(aliases=["b", "hammer"])
    @require("moderator")
    async def ban(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        await self._ban_logic(ctx, member, reason)

    async def slash_ban(self, interaction, member: discord.Member, reason: str = "No reason"):
        await self._ban_logic(interaction, member, reason)

    async def _unban_logic(self, ctx, user, reason="No reason"):
        try:
            bans = [e async for e in ctx.guild.bans()]
            if not discord.utils.get(bans, user__id=user.id):
                return await self.fail(ctx, "That user is not banned.")
        except (discord.Forbidden, discord.HTTPException) as e:
            return await self.fail(ctx, f"Failed to fetch bans: {e}")
        try:
            await ctx.guild.unban(user, reason=reason)
        except discord.Forbidden:
            return await self.fail(ctx, "I lack Ban Members permission.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed to unban: {e}")
        await self.log_action(ctx, "Unban", user, reason=reason,
                              before="Banned", after="Not banned")

    @commands.command(aliases=["ub"])
    @require("moderator")
    async def unban(self, ctx, user: discord.User, *, reason: str = "No reason"):
        await self._unban_logic(ctx, user, reason)

    async def slash_unban(self, interaction, user_id: str, reason: str = "No reason"):
        try:
            user = await self.bot.fetch_user(int(user_id))
        except (ValueError, discord.NotFound):
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Invalid user ID or user not found."),
                ephemeral=True,
            )
        await self._unban_logic(interaction, user, reason)

    # ===============================================================
    #  SOFTBAN / HACKBAN
    # ===============================================================
    @commands.command(aliases=["sb"])
    @require("moderator")
    async def softban(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        try:
            await self.check_mod_permission(ctx, "moderator")
        except commands.CheckFailure as e:
            return await self.fail(ctx, str(e))
        ok, err = self.can_target(ctx.author, member)
        if not ok:
            return await self.fail(ctx, err)
        try:
            await member.ban(delete_message_days=7, reason=f"Softban: {reason}")
            await ctx.guild.unban(member, reason="Softban unban")
        except discord.Forbidden:
            return await self.fail(ctx, "I lack Ban Members permission.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Softban failed: {e}")

        await self.log_action(
            ctx, "Softban", member,
            reason=reason,
            before="Not banned",
            after="Unbanned (ban + immediate unban)",
            extra="Messages from the last 7 days were deleted.",
        )
        await self._record_modstat(ctx.author.id, "softban")

    @commands.command(aliases=["hb"])
    @require("moderator")
    async def hackban(self, ctx, user_id: int, *, reason: str = "No reason"):
        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            return await self.fail(ctx, "User not found.")
        try:
            await ctx.guild.ban(discord.Object(id=user_id), reason=reason)
        except (discord.Forbidden, discord.HTTPException) as e:
            return await self.fail(ctx, f"Failed: {e}")

        await self.log_action(
            ctx, "Hackban", user,
            reason=reason,
            before="Not banned",
            after="Banned",
            extra=f"Target ID: `{user_id}` (user was not a member of the server).",
        )

    # ===============================================================
    #  JAIL  (Moderator and higher only)
    # ===============================================================
    async def _jail(self, ctx, member, reason="No reason"):
        guild = ctx.guild
        actor = self._get_actor(ctx)
        if not self.jail_role_id:
            return await self.fail(ctx, "Jail role is not configured.")
        jail_role = guild.get_role(self.jail_role_id)
        if jail_role is None:
            return await self.fail(ctx, "Jail role was deleted.")
        if await self.db.fetchrow("SELECT 1 FROM quarantine WHERE guild_id = $1 AND user_id = $2",
                                  guild.id, member.id):
            return await self.fail(ctx, "User is quarantined. Release them first.")

        old_roles = [r for r in member.roles if r != guild.default_role and r != jail_role]
        jail_category = guild.get_channel(self.jail_category_id) if self.jail_category_id else None
        if self.jail_category_id and jail_category is None:
            return await self.fail(ctx, "The jail category was deleted.")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
            jail_role: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True,
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True,
                manage_permissions=True, manage_messages=True,
            ),
        }
        for sid in self.staff_role_ids.values():
            if sid:
                sr = guild.get_role(sid)
                if sr and sr != jail_role:
                    overwrites[sr] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True,
                    )

        safe_name = re.sub(r"[^a-z0-9\-]+", "-", member.name.lower()).strip("-") or "user"
        try:
            cell = await guild.create_text_channel(
                name=f"jail-{safe_name}"[:90],
                category=jail_category if isinstance(jail_category, discord.CategoryChannel) else None,
                overwrites=overwrites,
                reason=f"Jail cell for {member} ({member.id})",
            )
        except discord.Forbidden:
            return await self.fail(ctx, "I lack Manage Channels permission to create the jail cell.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed to create jail cell: {e}")

        await self._ensure_role_isolation(guild, jail_role, exempt_channel_ids={cell.id})

        try:
            await member.edit(roles=[jail_role], reason=f"Jailed by {actor}: {reason}")
        except discord.Forbidden:
            try:
                await cell.delete(reason="Jail failed")
            except Exception:
                pass
            return await self.fail(ctx, "I lack Manage Roles permission or my role is too low.")
        except discord.HTTPException as e:
            try:
                await cell.delete(reason="Jail failed")
            except Exception:
                pass
            return await self.fail(ctx, f"Failed to apply jail role: {e}")

        try:
            await self.db.execute(
                """INSERT INTO jail (guild_id, user_id, role_ids, channel_id, reason, moderator_id, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (guild_id, user_id) DO UPDATE SET
                       role_ids = EXCLUDED.role_ids, channel_id = EXCLUDED.channel_id,
                       reason = EXCLUDED.reason, moderator_id = EXCLUDED.moderator_id,
                       created_at = EXCLUDED.created_at""",
                guild.id, member.id, [r.id for r in old_roles], cell.id,
                reason, actor.id, int(time.time())
            )
        except Exception:
            logger.exception("Failed to persist jail state")
            try:
                await member.edit(roles=old_roles, reason="Jail save failed")
                await cell.delete(reason="Jail save failed")
            except Exception:
                pass
            return await self.fail(ctx, "Failed to save jail state. Reverted.")

        try:
            await cell.send(
                f"{member.mention} you have been jailed.\n**Reason:** {reason}\n"
                f"You will be released when a moderator uses `jail` on you again."
            )
        except Exception:
            pass

        role_list = ", ".join(r.mention for r in old_roles[:10]) or "*none*"
        if len(old_roles) > 10:
            role_list += f" (+{len(old_roles) - 10} more)"
        await self.log_action(
            ctx, "Jail", member,
            reason=reason,
            before=f"Roles: {role_list}",
            after=f"Roles: {jail_role.mention} (in {cell.mention})",
            extra=f"Cell: {cell.mention} (`{cell.id}`)",
        )
        await self._record_modstat(actor.id, "jail")

    async def _unjail(self, ctx, member):
        guild = ctx.guild
        row = await self.db.fetchrow(
            "SELECT role_ids, channel_id FROM jail WHERE guild_id = $1 AND user_id = $2",
            guild.id, member.id
        )
        if not row:
            return await self.fail(ctx, "User is not jailed.")
        roles = [guild.get_role(rid) for rid in row["role_ids"]]
        roles = [r for r in roles if r is not None]
        try:
            await member.edit(roles=roles, reason="Released from jail")
        except discord.Forbidden:
            return await self.fail(ctx, "I lack Manage Roles permission.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed to restore roles: {e}")
        cell_id = row["channel_id"]
        if cell_id:
            cell = guild.get_channel(cell_id)
            if cell:
                try:
                    await cell.delete(reason="Jail released")
                except (discord.Forbidden, discord.HTTPException):
                    pass
        await self.db.execute("DELETE FROM jail WHERE guild_id = $1 AND user_id = $2", guild.id, member.id)

        restored = ", ".join(r.mention for r in roles[:10]) or "*none*"
        if len(roles) > 10:
            restored += f" (+{len(roles) - 10} more)"
        await self.log_action(
            ctx, "Unjail", member,
            before="Jailed",
            after="Released",
            extra=f"Roles restored: {restored}"
                  + (f"\nCell <#{cell_id}> deleted." if cell_id else ""),
        )
        await self._record_modstat(ctx.author.id, "unjail")

    @commands.command(name="jail", aliases=["j"])
    @require("moderator")
    async def jail_toggle(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        if await self.db.fetchrow("SELECT 1 FROM jail WHERE guild_id = $1 AND user_id = $2", ctx.guild.id, member.id):
            return await self._unjail(ctx, member)
        ok, err = self.can_target(ctx.author, member)
        if not ok:
            return await self.fail(ctx, err)
        await self._jail(ctx, member, reason)

    # ===============================================================
    #  QUARANTINE
    # ===============================================================
    async def _quarantine(self, ctx, member, reason="No reason"):
        guild = ctx.guild
        actor = self._get_actor(ctx)
        if not self.quarantine_role_id:
            return await self.fail(ctx, "Quarantine role is not configured.")
        qt_role = guild.get_role(self.quarantine_role_id)
        if qt_role is None:
            return await self.fail(ctx, "Quarantine role was deleted.")
        if not self.quarantine_channel_id:
            return await self.fail(ctx, "Quarantine channel is not configured.")
        qt_channel = guild.get_channel(self.quarantine_channel_id)
        if qt_channel is None:
            return await self.fail(ctx, "Quarantine channel was deleted.")
        if await self.db.fetchrow("SELECT 1 FROM jail WHERE guild_id = $1 AND user_id = $2", guild.id, member.id):
            return await self.fail(ctx, "User is jailed. Release them first.")

        old_roles = [r for r in member.roles if r != guild.default_role and r != qt_role]
        await self._ensure_role_isolation(guild, qt_role, exempt_channel_ids={qt_channel.id})

        try:
            await member.edit(roles=[qt_role], reason=f"Quarantined by {actor}: {reason}")
        except discord.Forbidden:
            return await self.fail(ctx, "I lack Manage Roles permission.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed to apply quarantine role: {e}")

        try:
            await self.db.execute(
                """INSERT INTO quarantine (guild_id, user_id, role_ids, reason, moderator_id, created_at)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT (guild_id, user_id) DO UPDATE SET
                       role_ids = EXCLUDED.role_ids, reason = EXCLUDED.reason,
                       moderator_id = EXCLUDED.moderator_id, created_at = EXCLUDED.created_at""",
                guild.id, member.id, [r.id for r in old_roles],
                reason, actor.id, int(time.time())
            )
        except Exception:
            logger.exception("Failed to persist quarantine state")
            try:
                await member.edit(roles=old_roles, reason="Quarantine save failed")
            except Exception:
                pass
            return await self.fail(ctx, "Failed to save quarantine state. Reverted.")

        try:
            await qt_channel.send(
                f"{member.mention} has been quarantined.\n**Reason:** {reason}\n"
                f"You will be released when a moderator uses `quarantine` on you again."
            )
        except Exception:
            pass

        role_list = ", ".join(r.mention for r in old_roles[:10]) or "*none*"
        if len(old_roles) > 10:
            role_list += f" (+{len(old_roles) - 10} more)"
        await self.log_action(
            ctx, "Quarantine", member,
            reason=reason,
            before=f"Roles: {role_list}",
            after=f"Roles: {qt_role.mention} (in {qt_channel.mention})",
        )
        await self._record_modstat(actor.id, "quarantine")

    async def _unquarantine(self, ctx, member):
        guild = ctx.guild
        row = await self.db.fetchrow(
            "SELECT role_ids FROM quarantine WHERE guild_id = $1 AND user_id = $2",
            guild.id, member.id
        )
        if not row:
            return await self.fail(ctx, "User is not quarantined.")
        roles = [guild.get_role(rid) for rid in row["role_ids"]]
        roles = [r for r in roles if r is not None]
        try:
            await member.edit(roles=roles, reason="Released from quarantine")
        except discord.Forbidden:
            return await self.fail(ctx, "I lack Manage Roles permission.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed to restore roles: {e}")
        await self.db.execute("DELETE FROM quarantine WHERE guild_id = $1 AND user_id = $2", guild.id, member.id)

        restored = ", ".join(r.mention for r in roles[:10]) or "*none*"
        if len(roles) > 10:
            restored += f" (+{len(roles) - 10} more)"
        await self.log_action(
            ctx, "Unquarantine", member,
            before="Quarantined",
            after="Released",
            extra=f"Roles restored: {restored}",
        )
        await self._record_modstat(ctx.author.id, "unquarantine")

    @commands.command(name="quarantine", aliases=["qt"])
    @require("moderator")
    async def quarantine_toggle(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        if await self.db.fetchrow("SELECT 1 FROM quarantine WHERE guild_id = $1 AND user_id = $2",
                                  ctx.guild.id, member.id):
            return await self._unquarantine(ctx, member)
        ok, err = self.can_target(ctx.author, member)
        if not ok:
            return await self.fail(ctx, err)
        await self._quarantine(ctx, member, reason)

    # ===============================================================
    #  AUDIT LOG
    # ===============================================================
    @commands.command(name="audit", aliases=["auditlog"])
    @require("moderator")
    async def audit(self, ctx, count: int = 5):
        if not ctx.guild.me.guild_permissions.view_audit_log:
            return await self.fail(ctx, "I lack View Audit Log permission.")
        count = max(1, min(count, 25))
        try:
            entries = [e async for e in ctx.guild.audit_logs(limit=count)]
        except discord.Forbidden:
            return await self.fail(ctx, "I lack View Audit Log permission.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed to fetch audit log: {e}")
        if not entries:
            return await self.fail(ctx, "No audit log entries found.")
        embed = discord.Embed(
            title=f"{EMOJIS['scroll']} Audit Log — last {count}",
            color=GREY_COLOR, timestamp=discord.utils.utcnow(),
        )
        for i, e in enumerate(entries, 1):
            embed.add_field(
                name=f"{i}. {str(e.action).replace('_', ' ').title()}",
                value=(
                    f"**By:** {e.user or 'Unknown'}\n"
                    f"**Target:** {e.target or 'Unknown'}\n"
                    f"**Reason:** {e.reason or 'None'}\n"
                    f"**When:** {discord.utils.format_dt(e.created_at, style='R')}"
                ),
                inline=False,
            )
        await self.send(ctx, embed)

    # ===============================================================
    #  STAFF ROLE TOGGLES
    # ===============================================================
    async def _toggle_staff_role(self, ctx, member, key):
        internal_role, config_key = ROLE_TOGGLE_MAP[key]

        role_id = self.bot.config.get("roles", {}).get(config_key) or self.bot.config.get(config_key)
        if not role_id:
            return await self.fail(ctx, f"`{config_key}` is not configured in config.json.")
        discord_role = ctx.guild.get_role(role_id)
        if discord_role is None:
            return await self.fail(ctx, f"Role `{role_id}` not found in this guild.")

        ok, err = self.can_target(ctx.author, member)
        if not ok:
            return await self.fail(ctx, err)

        gid = ctx.guild.id
        plural = STAFF_ROLE_MAP.get(internal_role)
        has_role = discord_role in member.roles

        extra_role_id = EXTRA_TOGGLE_ROLES.get(key)
        extra_role = None
        if extra_role_id:
            extra_role = ctx.guild.get_role(extra_role_id)
            if extra_role is None:
                return await self.fail(ctx,
                    f"Extra role `{extra_role_id}` for `{key}` was not found in this guild.")
            if ctx.guild.me.top_role <= extra_role:
                return await self.fail(ctx,
                    f"I can't assign {extra_role.mention} — my top role is below it.")

        if has_role:
            try:
                await member.remove_roles(discord_role, reason=f"Removed by {ctx.author}")
            except discord.Forbidden:
                return await self.fail(ctx, "I lack Manage Roles permission or my role is too low.")
            except discord.HTTPException as e:
                return await self.fail(ctx, f"Failed to remove primary role: {e}")

            if plural and str(member.id) in self.staff_cache[gid][plural]:
                self.staff_cache[gid][plural].remove(str(member.id))
            await self.db.execute(
                "DELETE FROM staff WHERE guild_id = $1 AND user_id = $2 AND role = $3",
                gid, member.id, internal_role
            )

            removed_extra = False
            if extra_role is not None and extra_role in member.roles:
                try:
                    await member.remove_roles(extra_role, reason=f"Removed with {key} by {ctx.author}")
                    removed_extra = True
                except discord.Forbidden:
                    return await self.fail(ctx,
                        f"Primary role removed, but I couldn't remove {extra_role.mention}.")
                except discord.HTTPException as e:
                    return await self.fail(ctx,
                        f"Primary role removed, but removing {extra_role.mention} failed: {e}")

            extras = f", extra role {extra_role.mention}" if removed_extra else ""
            await self.log_action(
                ctx, f"Staff Role Removed — {key}",
                member,
                before=discord_role.mention,
                after="No role",
                extra=f"Removed `{discord_role.name}`{extras}.",
            )
        else:
            try:
                await member.add_roles(discord_role, reason=f"Added by {ctx.author}")
            except discord.Forbidden:
                return await self.fail(ctx, "I lack Manage Roles permission or my role is too low.")
            except discord.HTTPException as e:
                return await self.fail(ctx, f"Failed to add primary role: {e}")

            if plural and str(member.id) not in self.staff_cache[gid][plural]:
                self.staff_cache[gid][plural].append(str(member.id))
            await self.db.execute(
                "INSERT INTO staff (guild_id, user_id, role) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                gid, member.id, internal_role
            )

            added_extra = False
            if extra_role is not None and extra_role not in member.roles:
                try:
                    await member.add_roles(extra_role, reason=f"Added with {key} by {ctx.author}")
                    added_extra = True
                except discord.Forbidden:
                    return await self.fail(ctx,
                        f"Primary role added, but I couldn't add {extra_role.mention}.")
                except discord.HTTPException as e:
                    return await self.fail(ctx,
                        f"Primary role added, but adding {extra_role.mention} failed: {e}")

            extras = f", extra role {extra_role.mention}" if added_extra else ""
            await self.log_action(
                ctx, f"Staff Role Added — {key}",
                member,
                before="No role",
                after=discord_role.mention,
                extra=f"Added `{discord_role.name}`{extras}.",
            )

    @commands.command(name="mod")
    @require("admin")
    async def cmd_mod(self, ctx, member: discord.Member):
        await self._toggle_staff_role(ctx, member, "mod")

    @commands.command(name="tmod")
    @require("admin")
    async def cmd_tmod(self, ctx, member: discord.Member):
        await self._toggle_staff_role(ctx, member, "tmod")

    @commands.command(name="smod")
    @require("admin")
    async def cmd_smod(self, ctx, member: discord.Member):
        await self._toggle_staff_role(ctx, member, "smod")

    @commands.command(name="admin")
    @require("admin")
    async def cmd_admin(self, ctx, member: discord.Member):
        await self._toggle_staff_role(ctx, member, "admin")

    @commands.command(name="emanager")
    @require("admin")
    async def cmd_emanager(self, ctx, member: discord.Member):
        await self._toggle_staff_role(ctx, member, "emanager")

    @commands.command(name="etrial")
    @require("admin")
    async def cmd_etrial(self, ctx, member: discord.Member):
        await self._toggle_staff_role(ctx, member, "etrial")

    @commands.command(name="dev")
    @require("admin")
    async def cmd_dev(self, ctx, member: discord.Member):
        role_id = self.bot.config.get("roles", {}).get("developer_role_id") or self.bot.config.get("developer_role_id")
        if not role_id:
            return await self.fail(ctx, "`developer_role_id` is not configured in config.json.")
        dev_role = ctx.guild.get_role(role_id)
        if dev_role is None:
            return await self.fail(ctx, f"Role `{role_id}` not found in this guild.")

        ok, err = self.can_target(ctx.author, member)
        if not ok:
            return await self.fail(ctx, err)

        try:
            if dev_role in member.roles:
                await member.remove_roles(dev_role, reason=f"Dev role removed by {ctx.author}")
                await self.log_action(ctx, "Dev Role Removed", member,
                                      before=dev_role.mention, after="No role")
            else:
                await member.add_roles(dev_role, reason=f"Dev role added by {ctx.author}")
                await self.log_action(ctx, "Dev Role Added", member,
                                      before="No role", after=dev_role.mention)
        except discord.Forbidden:
            return await self.fail(ctx, "I lack Manage Roles permission or my role is too low.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed: {e}")

    # ===============================================================
    #  STAFF LIST
    # ===============================================================
    @commands.command(name="staff")
    @require("admin")
    async def staff(self, ctx):
        embed = make_embed(ctx, "Staff Members", "", GREY_COLOR)
        order = [
            ("Admin", self.staff_role_ids.get("admin")),
            ("Senior Moderator", self.staff_role_ids.get("senior_moderator")),
            ("Moderator", self.staff_role_ids.get("moderator")),
            ("Trial Moderator", self.staff_role_ids.get("trial_moderator")),
            ("Event Manager", self.staff_role_ids.get("event_manager")),
            ("Event Trial", self.staff_role_ids.get("trial_event_manager")),
        ]
        for label, role_id in order:
            if not role_id:
                continue
            role = ctx.guild.get_role(role_id)
            if not role:
                embed.add_field(name=label, value="Role not found", inline=True)
                continue
            members = role.members
            if not members:
                embed.add_field(name=label, value="None", inline=True)
                continue
            lines = [m.mention for m in sorted(members, key=lambda m: m.name.lower())]
            embed.add_field(name=label, value="\n".join(lines), inline=True)
        await self.send(ctx, embed)

    # ===============================================================
    #  MASS MOD
    # ===============================================================
    MAX_MASS_ACTION = 50

    class ConfirmView(ComponentsV2View):
        def __init__(self, ctx, action, targets):
            super().__init__(timeout=60)
            self.ctx = ctx
            self.action = action
            self.targets = targets
            self.confirmed = False
            self.controls = discord.ui.ActionRow()
            confirm_button = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.danger)
            cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
            confirm_button.callback = self.confirm
            cancel_button.callback = self.cancel
            self.controls.add_item(confirm_button)
            self.controls.add_item(cancel_button)
            self.add_item(self.controls)

        async def interaction_check(self, interaction):
            original = self.ctx.author if isinstance(self.ctx, commands.Context) else self.ctx.user
            if interaction.user.id != original.id:
                await interaction.response.send_message("This confirmation is not for you.", ephemeral=True)
                return False
            return True

        async def confirm(self, interaction):
            self.confirmed = True
            disable_all_components(self)
            await interaction.response.edit_message(view=self)
            self.stop()

        async def cancel(self, interaction):
            self.confirmed = False
            disable_all_components(self)
            await interaction.response.edit_message(view=self)
            self.stop()

    @commands.command(name="masskick")
    @require("admin")
    async def masskick(self, ctx, members: commands.Greedy[discord.Member], *, reason: str = "No reason"):
        await self._react_pending(ctx)
        if len(members) > self.MAX_MASS_ACTION:
            return await self.fail(ctx, f"Maximum {self.MAX_MASS_ACTION} targets allowed.")
        if len(members) > 10 and reason == "No reason":
            return await self.fail(ctx, "A reason is required for mass actions over 10 targets.")
        valid = [m for m in members if self.can_target(ctx.author, m)[0]]
        if not valid:
            return await self.fail(ctx, "No valid targets.")
        if len(valid) > 10:
            view = self.ConfirmView(ctx, "masskick", valid)
            await self.send(ctx, make_embed(ctx, "Confirm Mass Kick", f"Kick {len(valid)} members?", GREY_COLOR), view=view)
            await view.wait()
            if not view.confirmed:
                return await self.fail(ctx, "Mass kick cancelled.")
        done = 0
        for m in valid:
            try:
                await m.kick(reason=reason)
                done += 1
            except (discord.Forbidden, discord.HTTPException):
                pass
            await asyncio.sleep(0.5)
        await self.log_action(
            ctx, "Mass Kick",
            reason=reason,
            extra=f"Kicked `{done}` / `{len(valid)}` targets: "
                  + ", ".join(f"{m.mention} (`{m.id}`)" for m in valid[:10])
                  + (f" (+{len(valid)-10} more)" if len(valid) > 10 else ""),
        )

    @commands.command(name="massban")
    @require("admin")
    async def massban(self, ctx, members: commands.Greedy[discord.Member], *, reason: str = "No reason"):
        await self._react_pending(ctx)
        if len(members) > self.MAX_MASS_ACTION:
            return await self.fail(ctx, f"Maximum {self.MAX_MASS_ACTION} targets allowed.")
        if len(members) > 10 and reason == "No reason":
            return await self.fail(ctx, "A reason is required for mass actions over 10 targets.")
        valid = [m for m in members if self.can_target(ctx.author, m)[0]]
        if not valid:
            return await self.fail(ctx, "No valid targets.")
        if len(valid) > 10:
            view = self.ConfirmView(ctx, "massban", valid)
            await self.send(ctx, make_embed(ctx, "Confirm Mass Ban", f"Ban {len(valid)} members?", GREY_COLOR), view=view)
            await view.wait()
            if not view.confirmed:
                return await self.fail(ctx, "Mass ban cancelled.")
        done = 0
        for m in valid:
            try:
                await m.ban(reason=reason)
                done += 1
            except (discord.Forbidden, discord.HTTPException):
                pass
            await asyncio.sleep(0.5)
        await self.log_action(
            ctx, "Mass Ban",
            reason=reason,
            extra=f"Banned `{done}` / `{len(valid)}` targets: "
                  + ", ".join(f"{m.mention} (`{m.id}`)" for m in valid[:10])
                  + (f" (+{len(valid)-10} more)" if len(valid) > 10 else ""),
        )

    @commands.command(name="massmute")
    @require("admin")
    async def massmute(self, ctx, duration: str, members: commands.Greedy[discord.Member], *, reason: str = "No reason"):
        await self._react_pending(ctx)
        if len(members) > self.MAX_MASS_ACTION:
            return await self.fail(ctx, f"Maximum {self.MAX_MASS_ACTION} targets allowed.")
        seconds = parse_time(duration)
        if seconds <= 0:
            return await self.fail(ctx, "Invalid duration. Use `10m`, `1h`, `2d` etc.")
        valid = [m for m in members if self.can_target(ctx.author, m)[0]]
        if not valid:
            return await self.fail(ctx, "No valid targets.")
        if len(valid) > 10:
            view = self.ConfirmView(ctx, "massmute", valid)
            await self.send(ctx, make_embed(ctx, "Confirm Mass Mute", f"Mute {len(valid)} members?", GREY_COLOR), view=view)
            await view.wait()
            if not view.confirmed:
                return await self.fail(ctx, "Mass mute cancelled.")
        until = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
        done = 0
        for m in valid:
            try:
                await m.timeout(until, reason=reason)
                done += 1
            except (discord.Forbidden, discord.HTTPException):
                pass
            await asyncio.sleep(0.5)
        await self.log_action(
            ctx, "Mass Mute",
            reason=reason,
            duration=fmt_duration(seconds),
            extra=f"Muted `{done}` / `{len(valid)}` targets: "
                  + ", ".join(f"{m.mention} (`{m.id}`)" for m in valid[:10])
                  + (f" (+{len(valid)-10} more)" if len(valid) > 10 else ""),
        )

    @commands.command(name="massunban")
    @require("admin")
    async def massunban(self, ctx, *user_ids: int):
        await self._react_pending(ctx)
        if not user_ids:
            return await self.fail(ctx, "Provide at least one user ID.")
        if len(user_ids) > self.MAX_MASS_ACTION:
            return await self.fail(ctx, f"Maximum {self.MAX_MASS_ACTION} targets allowed.")
        done = 0
        for uid in user_ids:
            try:
                await ctx.guild.unban(discord.Object(id=uid), reason=f"Mass unban by {ctx.author}")
                done += 1
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            await asyncio.sleep(0.5)
        await self.log_action(
            ctx, "Mass Unban",
            extra=f"Unbanned `{done}` / `{len(user_ids)}` users. IDs: `{', '.join(str(u) for u in user_ids[:15])}`"
                  + (f" (+{len(user_ids)-15} more)" if len(user_ids) > 15 else ""),
        )

    @commands.command(name="massrole")
    @require("admin")
    async def massrole(self, ctx, action: str, role: discord.Role, members: commands.Greedy[discord.Member]):
        await self._react_pending(ctx)
        action = action.lower()
        if action not in ("add", "remove"):
            return await self.fail(ctx, "Action must be `add` or `remove`.")
        if not members:
            return await self.fail(ctx, "Mention at least one member.")
        if len(members) > self.MAX_MASS_ACTION:
            return await self.fail(ctx, f"Maximum {self.MAX_MASS_ACTION} targets allowed.")
        done = 0
        for m in members:
            try:
                if action == "add":
                    await m.add_roles(role)
                else:
                    await m.remove_roles(role)
                done += 1
            except (discord.Forbidden, discord.HTTPException):
                pass
            await asyncio.sleep(0.3)
        await self.log_action(
            ctx, f"Mass Role {'Add' if action == 'add' else 'Remove'}",
            extra=f"Role: {role.mention} (`{role.id}`) — affected `{done}` / `{len(members)}` targets.",
        )

    # ===============================================================
    #  TEMPROLE
    # ===============================================================
    @commands.command(name="temprole", aliases=["tr-temp"])
    @require("admin")
    async def temprole(self, ctx, member: discord.Member, role: discord.Role, duration: str):
        seconds = parse_time(duration)
        if seconds <= 0:
            return await self.fail(ctx, "Invalid duration. Use `10m`, `1h`, `2d` etc.")

        bot_member = ctx.guild.me
        if bot_member.top_role <= role:
            return await self.fail(ctx, f"My top role must be above {role.mention}.")
        if bot_member.top_role <= member.top_role:
            return await self.fail(ctx, f"My top role must be above {member.mention}'s top role.")

        try:
            await member.add_roles(role, reason=f"Temprole by {ctx.author} for {fmt_duration(seconds)}")
        except discord.Forbidden:
            return await self.fail(ctx, "I lack Manage Roles permission.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed to add role: {e}")

        await self.log_action(
            ctx, "Temprole Added", member,
            before="No role",
            after=role.mention,
            duration=fmt_duration(seconds),
            extra="Will be automatically removed after the duration.",
        )

        async def _remove():
            await asyncio.sleep(seconds)
            try:
                await member.remove_roles(role, reason="Temprole expired")
                await self.log_action(
                    ctx, "Temprole Expired", member,
                    before=role.mention,
                    after="No role",
                    is_automod=True,
                )
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning("Temprole remove failed for %s / %s: %s", member.id, role.id, e)

        self.bot.loop.create_task(_remove())

    # ===============================================================
    #  INFO COMMANDS
    # ===============================================================
    @commands.command(aliases=["ui", "whois"])
    async def userinfo(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        roles = [r.mention for r in reversed(member.roles) if r != ctx.guild.default_role]
        roles_ = (" ".join(roles[:10]) + (f" +{len(roles)-10} more" if len(roles) > 10 else "")) if roles else "None"
        status_map = {
            discord.Status.online: f"{EMOJIS['online']} Online",
            discord.Status.idle: f"{EMOJIS['idle']} Idle",
            discord.Status.dnd: f"{EMOJIS['dnd']} Do Not Disturb",
            discord.Status.offline: f"{EMOJIS['offline']} Offline",
        }
        status = status_map.get(member.status, "Unknown")
        activity = "None"
        if member.activity:
            if isinstance(member.activity, discord.Game):
                activity = f"Playing **{member.activity.name}**"
            elif isinstance(member.activity, discord.Streaming):
                activity = f"Streaming **{member.activity.name}**"
            elif isinstance(member.activity, discord.Spotify):
                activity = f"{EMOJIS['musical']} **{member.activity.title}** by {', '.join(member.activity.artists)}"
            else:
                activity = member.activity.name or "Unknown"
        flags = [f.name.replace("_", " ").title() for f, v in member.public_flags if v]
        badges = ", ".join(flags) if flags else "None"
        boosting = f"Yes {discord.utils.format_dt(member.premium_since, style='R')}" if member.premium_since else "No"

        embed = discord.Embed(color=GREY_COLOR, timestamp=discord.utils.utcnow())
        embed.set_author(name=f"{member} — {member.id}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name=f"{EMOJIS['calendar']} Joined Discord",
                        value=discord.utils.format_dt(member.created_at, style="R"), inline=True)
        embed.add_field(name=f"{EMOJIS['shield']} Joined Server",
                        value=discord.utils.format_dt(member.joined_at, style="R") if member.joined_at else "Unknown",
                        inline=True)
        embed.add_field(name=f"{EMOJIS['owner']} Top Role", value=member.top_role.mention, inline=True)
        embed.add_field(name=f"{EMOJIS['stats']} Status", value=status, inline=True)
        embed.add_field(name=f"{EMOJIS['game_controller']} Activity", value=activity, inline=True)
        embed.add_field(name=f"{EMOJIS['boost']} Boosting", value=boosting, inline=True)
        embed.add_field(name=f"{EMOJIS['gold_medal']} Badges", value=badges, inline=False)
        embed.add_field(name=f"{EMOJIS['roles']} Roles [{len(roles)}]", value=roles_, inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await self.send(ctx, embed)

    @commands.command(aliases=["si", "guildinfo"])
    async def serverinfo(self, ctx):
        guild = ctx.guild
        humans = sum(1 for m in guild.members if not m.bot)
        bots = guild.member_count - humans
        embed = discord.Embed(title=f"{guild.name} — {guild.id}", color=GREY_COLOR, timestamp=discord.utils.utcnow())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)
        embed.add_field(name=f"{EMOJIS['calendar']} Created",
                        value=discord.utils.format_dt(guild.created_at, style="R"), inline=True)
        embed.add_field(name=f"{EMOJIS['user_id']} Members",
                        value=f"{EMOJIS['user_id']} {humans} · {EMOJIS['robot']} {bots}", inline=True)
        embed.add_field(
            name=f"{EMOJIS['message']} Channels",
            value=(f"{EMOJIS['message']} {len(guild.text_channels)} text · "
                   f"{EMOJIS['announcement']} {len(guild.voice_channels)} voice · "
                   f"{EMOJIS['file']} {len(guild.categories)} categories"),
            inline=False,
        )
        embed.add_field(name=f"{EMOJIS['boost']} Boost",
                        value=f"Level {guild.premium_tier} · {guild.premium_subscription_count} boosts",
                        inline=True)
        embed.add_field(name=f"{EMOJI_LOCK_SPARK} Verification",
                        value=str(guild.verification_level).title(), inline=True)
        embed.add_field(name=f"{EMOJIS['fire']} Emojis",
                        value=f"{len(guild.emojis)}/{guild.emoji_limit}", inline=True)
        embed.add_field(name=f"{EMOJIS['roles']} Roles", value=str(len(guild.roles)), inline=True)
        if guild.features:
            embed.add_field(name=f"{EMOJI_SPARKLES} Features", value=", ".join(guild.features), inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await self.send(ctx, embed)

    @commands.command(aliases=["av", "pfp"])
    async def avatar(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(title=f"{member.display_name}'s Avatar", color=GREY_COLOR)
        embed.set_image(url=member.display_avatar.url)
        fmt = member.display_avatar
        links = (f"[PNG]({fmt.replace(format='png').url}) · "
                 f"[JPG]({fmt.replace(format='jpg').url}) · "
                 f"[WEBP]({fmt.replace(format='webp').url})")
        if fmt.is_animated():
            links += f" · [GIF]({fmt.replace(format='gif').url})"
        embed.add_field(name=f"{EMOJIS['download']} Download", value=links)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await self.send(ctx, embed)

    @commands.command(aliases=["userbanner"])
    async def banner(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        fetched = await self.bot.fetch_user(target.id)
        if not fetched.banner:
            return await self.fail(ctx, f"{target.mention} has no banner.")
        embed = discord.Embed(title=f"{target.display_name}'s Banner", color=GREY_COLOR)
        embed.set_image(url=fetched.banner.url)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await self.send(ctx, embed)

    @commands.command(aliases=["ri"])
    @require("moderator")
    async def roleinfo(self, ctx, role: discord.Role):
        perms = [p.replace("_", " ").title() for p, v in role.permissions if v]
        key_perms = {"Administrator", "Manage Guild", "Ban Members", "Kick Members",
                     "Manage Channels", "Manage Roles", "Manage Messages", "Mention Everyone"}
        danger = [p for p in perms if p in key_perms]
        embed = discord.Embed(title=f"Role: {role.name} — {role.id}", color=GREY_COLOR, timestamp=discord.utils.utcnow())
        embed.add_field(name=f"{EMOJIS['color']} Color", value=str(role.color), inline=True)
        embed.add_field(name=f"{EMOJIS['calendar']} Created",
                        value=discord.utils.format_dt(role.created_at, style="R"), inline=True)
        embed.add_field(name=f"{EMOJIS['user_id']} Members", value=str(len(role.members)), inline=True)
        embed.add_field(name=f"{EMOJIS['pin']} Hoisted", value="Yes" if role.hoist else "No", inline=True)
        embed.add_field(name=f"{EMOJIS['message']} Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        embed.add_field(name=f"{EMOJIS['number_1']} Position", value=str(role.position), inline=True)
        if danger:
            embed.add_field(name=f"{EMOJIS['warning']} Dangerous Perms", value=", ".join(danger), inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await self.send(ctx, embed)

    # ===============================================================
    #  POLL
    # ===============================================================
    @commands.command(name="poll")
    @require("moderator")
    async def poll(self, ctx, *, question: str):
        embed = discord.Embed(
            title=f"{EMOJIS['stats']} Poll",
            description=question,
            color=GREY_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Poll by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        try:
            msg = await ctx.send(embed=embed)
            await msg.add_reaction(self._to_reaction(EMOJIS["tick"]))
            await msg.add_reaction(self._to_reaction(EMOJIS["cross"]))
        except discord.Forbidden:
            return await self.fail(ctx, "I cannot send messages or add reactions here.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Poll failed: {e}")
        try:
            await ctx.message.delete()
        except Exception:
            pass
        await self.log_action(
            ctx, "Poll Created",
            channel=ctx.channel,
            extra=f"Question:\n>>> {question[:800]}\nMessage ID: `{msg.id}`",
        )

    # ===============================================================
    #  NUKE
    # ===============================================================
    @commands.command(name="nuke")
    @commands.is_owner()
    async def nuke(self, ctx, channel: discord.TextChannel = None):
        chan = channel or ctx.channel
        view = self.ConfirmView(ctx, "nuke", [chan])
        confirm = make_embed(
            ctx, f"{EMOJIS['warning']} Confirm Nuke",
            (f"Are you sure you want to nuke {chan.mention}?\n\n"
             f"This will **permanently delete** the channel, create a fresh clone, "
             f"and restore its position. This action cannot be undone."),
            GREY_COLOR,
        )
        await self.send(ctx, confirm, view=view)
        await view.wait()
        if not view.confirmed:
            return await self.fail(ctx, "Nuke cancelled.")
        pos = chan.position
        try:
            new = await chan.clone(reason=f"Nuke by {ctx.author}")
            await new.edit(position=pos)
            await chan.delete()
            await new.send(embed=make_embed(
                ctx, f"{EMOJIS['boom']} Channel Nuked",
                f"This channel was nuked by {ctx.author.mention}.", GREY_COLOR
            ))
            await self.log_action(
                ctx, "Channel Nuked",
                target=None,
                before=f"{chan.mention} (`{chan.id}`)",
                after=f"{new.mention} (`{new.id}`)",
            )
        except discord.Forbidden:
            return await self.fail(ctx, "I lack permissions to nuke this channel.")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Nuke failed: {e}")

    # ===============================================================
    #  EMOJI MANAGEMENT
    # ===============================================================
    def _build_emoji_list_embed(self, ctx, emojis, page_num, total_pages):
        desc = "\n".join(f"{e} `:{e.name}:`" for e in emojis)
        embed = make_embed(ctx, f"{EMOJIS['fire']} Server Emojis", desc, GREY_COLOR)
        embed.set_footer(text=f"Page {page_num}/{total_pages} • {len(emojis)} shown")
        return embed

    async def _emojis_logic(self, ctx, animated=None):
        emojis = list(ctx.guild.emojis)
        if animated:
            al = animated.lower()
            if al in ("yes", "true", "1", "animated"):
                emojis = [e for e in emojis if e.animated]
            elif al in ("no", "false", "0", "static"):
                emojis = [e for e in emojis if not e.animated]
        if not emojis:
            return await self.fail(ctx, "No custom emojis found.")
        pages = [emojis[i:i+20] for i in range(0, len(emojis), 20)]
        if len(pages) == 1:
            return await self.send(ctx, self._build_emoji_list_embed(ctx, pages[0], 1, 1))
        view = self.EmojiListView(ctx, pages)
        await self.send(ctx, self._build_emoji_list_embed(ctx, pages[0], 1, len(pages)), view=view)

    @commands.command(name="emojis", aliases=["listemojis"])
    @require("moderator")
    async def emojis(self, ctx, animated: Optional[str] = None):
        await self._emojis_logic(ctx, animated)

    async def slash_emojis(self, interaction, animated: Optional[str] = None):
        await self._emojis_logic(interaction, animated)

    async def _emojiinfo_logic(self, ctx, emoji):
        embed = make_embed(ctx, f"{emoji} Emoji Info", "", GREY_COLOR)
        embed.add_field(name="Name", value=emoji.name, inline=True)
        embed.add_field(name="ID", value=str(emoji.id), inline=True)
        embed.add_field(name="Animated", value="Yes" if emoji.animated else "No", inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(emoji.created_at, style="F"), inline=False)
        embed.add_field(name="URL", value=f"[Open]({emoji.url})", inline=False)
        if emoji.roles:
            embed.add_field(name="Allowed Roles", value=", ".join(r.mention for r in emoji.roles), inline=False)
        embed.set_thumbnail(url=emoji.url)
        await self.send(ctx, embed)

    @commands.command(name="emojiinfo", aliases=["ei"])
    @require("moderator")
    async def emojiinfo(self, ctx, emoji: discord.Emoji):
        await self._emojiinfo_logic(ctx, emoji)

    async def slash_emojiinfo(self, interaction, emoji: str):
        obj = await self._parse_emoji(interaction.guild, emoji)
        if not obj:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Invalid emoji."), ephemeral=True
            )
        await self._emojiinfo_logic(interaction, obj)

    async def _steal_logic(self, ctx, name=None, direct_url=None):
        guild = ctx.guild
        if not guild:
            return await self.fail(ctx, "Server only.")
        if not guild.me.guild_permissions.manage_emojis:
            return await self.fail(ctx, "I need Manage Emojis permission.")
        if not getattr(self.bot, "http_session", None):
            return await self.fail(ctx, "HTTP session not ready.")
        ref = None
        if isinstance(ctx, commands.Context) and ctx.message.reference:
            ref = ctx.message.reference.resolved
            if ref is None:
                try:
                    ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                except Exception:
                    pass
        if ref and not direct_url:
            matches = list(dict.fromkeys(re.findall(r"<(a?):(\w+):(\d+)>", ref.content)))
            if not matches:
                return await self.fail(ctx, "No custom emojis found in that message.")
            if len(matches) > 10:
                return await self.fail(ctx, "Too many emojis (max 10). Use `masssteal`.")
            added = []
            for anim, ename, eid in matches:
                ext = "gif" if anim else "png"
                use_name = name if (name and len(matches) == 1) else ename
                async with self.bot.http_session.get(f"https://cdn.discordapp.com/emojis/{eid}.{ext}") as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.read()
                try:
                    emoji = await guild.create_custom_emoji(name=use_name, image=data)
                    added.append(str(emoji))
                except discord.HTTPException as e:
                    await self._log_error(ctx, e)
            if added:
                await self.send(ctx, discord.Embed(description=f"Stolen: {' '.join(added)}", color=GREY_COLOR))
                await self.log_action(ctx, "Emoji Steal", extra=f"Added: {' '.join(added)}")
        else:
            if not name:
                return await self.fail(ctx, "Provide a name for the new emoji.")
            if not direct_url:
                if isinstance(ctx, commands.Context) and ctx.message.attachments:
                    direct_url = ctx.message.attachments[0].url
                else:
                    return await self.fail(ctx, "Reply to a message, attach an image, or provide a URL.")
            async with self.bot.http_session.get(direct_url) as resp:
                if resp.status != 200:
                    return await self.fail(ctx, "Could not download image.")
                data = await resp.read()
            try:
                emoji = await guild.create_custom_emoji(name=name, image=data)
                await self.send(ctx, discord.Embed(description=f"Emoji {emoji} added!", color=GREY_COLOR))
                await self.log_action(ctx, "Emoji Added", extra=f"Created `{emoji}` (`{emoji.id}`)")
            except discord.HTTPException as e:
                return await self.fail(ctx, f"Failed: {e}")

    @commands.command(name="esteal", aliases=["stealemoji", "se"])
    @require("moderator")
    async def steal(self, ctx, name: str = None):
        await self._steal_logic(ctx, name)

    async def slash_steal(self, interaction, name: str = None, image_url: str = None):
        await self._steal_logic(interaction, name, image_url)

    async def _masssteal_logic(self, ctx):
        guild = ctx.guild
        if not guild or not guild.me.guild_permissions.manage_emojis:
            return await self.fail(ctx, "I need Manage Emojis permission.")
        if not getattr(self.bot, "http_session", None):
            return await self.fail(ctx, "HTTP session not ready.")
        ref = None
        if isinstance(ctx, commands.Context) and ctx.message.reference:
            ref = ctx.message.reference.resolved
            if ref is None:
                try:
                    ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                except Exception:
                    pass
        if not ref:
            return await self.fail(ctx, "Reply to a message containing custom emojis.")
        matches = list(dict.fromkeys(re.findall(r"<(a?):(\w+):(\d+)>", ref.content)))
        if not matches or len(matches) > 20:
            return await self.fail(ctx, "No custom emojis or over 20.")
        if isinstance(ctx, discord.Interaction):
            await ctx.response.defer()
        added = []
        for anim, ename, eid in matches:
            ext = "gif" if anim else "png"
            async with self.bot.http_session.get(f"https://cdn.discordapp.com/emojis/{eid}.{ext}") as resp:
                if resp.status != 200:
                    continue
                data = await resp.read()
            try:
                emoji = await guild.create_custom_emoji(name=ename, image=data)
                added.append(str(emoji))
            except discord.HTTPException:
                pass
            await asyncio.sleep(0.3)
        msg = f"Stolen: {' '.join(added)}" if added else "No emojis added."
        if isinstance(ctx, discord.Interaction):
            try:
                await ctx.edit_original_response(embed=discord.Embed(description=msg, color=GREY_COLOR))
            except Exception:
                pass
        elif added:
            await self.send(ctx, discord.Embed(description=msg, color=GREY_COLOR))
        if added:
            await self.log_action(ctx, "Mass Emoji Steal",
                                  extra=f"Added `{len(added)}` emojis: {' '.join(added[:15])}"
                                        + (f" (+{len(added)-15} more)" if len(added) > 15 else ""))

    @commands.command(name="masssteal", aliases=["ms"])
    @require("admin")
    async def masssteal(self, ctx):
        await self._masssteal_logic(ctx)

    async def slash_masssteal(self, interaction):
        await self._masssteal_logic(interaction)

    async def _renameemoji_logic(self, ctx, emoji, new_name):
        if emoji.guild != ctx.guild:
            return await self.fail(ctx, "That emoji isn't from this server.")
        old_name = emoji.name
        try:
            await emoji.edit(name=new_name)
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed: {e}")
        await self.log_action(
            ctx, "Emoji Renamed",
            before=f"`:{old_name}:`",
            after=f"`:{new_name}:`",
            extra=f"Emoji: {emoji} (`{emoji.id}`)",
        )

    @commands.command(name="renameemoji", aliases=["re"])
    @require("admin")
    async def renameemoji(self, ctx, emoji: discord.Emoji, *, new_name: str):
        await self._renameemoji_logic(ctx, emoji, new_name)

    async def slash_renameemoji(self, interaction, emoji: str, new_name: str):
        obj = await self._parse_emoji(interaction.guild, emoji)
        if not obj:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Invalid emoji."), ephemeral=True
            )
        await self._renameemoji_logic(interaction, obj, new_name)

    async def _changeemoji_logic(self, ctx, emoji, image_url=None):
        if emoji.guild != ctx.guild:
            return await self.fail(ctx, "That emoji isn't from this server.")
        if not getattr(self.bot, "http_session", None):
            return await self.fail(ctx, "HTTP session not ready.")
        if not image_url and isinstance(ctx, commands.Context) and ctx.message.attachments:
            image_url = ctx.message.attachments[0].url
        if not image_url:
            return await self.fail(ctx, "Provide an image URL or attachment.")
        async with self.bot.http_session.get(image_url) as resp:
            if resp.status != 200:
                return await self.fail(ctx, "Could not download image.")
            data = await resp.read()
        try:
            await emoji.edit(image=data)
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed: {e}")
        await self.log_action(
            ctx, "Emoji Image Changed",
            extra=f"Emoji: {emoji} (`{emoji.id}`) — new image from `{image_url[:100]}`",
        )

    @commands.command(name="changeemoji", aliases=["ce"])
    @require("admin")
    async def changeemoji(self, ctx, emoji: discord.Emoji, image_url: str = None):
        await self._changeemoji_logic(ctx, emoji, image_url)

    async def slash_changeemoji(self, interaction, emoji: str, image_url: str):
        obj = await self._parse_emoji(interaction.guild, emoji)
        if not obj:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Invalid emoji."), ephemeral=True
            )
        await self._changeemoji_logic(interaction, obj, image_url)

    async def _deleteemoji_logic(self, ctx, emoji):
        if emoji.guild != ctx.guild:
            return await self.fail(ctx, "That emoji isn't from this server.")
        name, eid = emoji.name, emoji.id
        try:
            await emoji.delete()
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed: {e}")
        await self.log_action(
            ctx, "Emoji Deleted",
            before=f"`:{name}:` (`{eid}`)",
            after="*deleted*",
        )

    @commands.command(name="deleteemoji", aliases=["de"])
    @require("admin")
    async def deleteemoji(self, ctx, emoji: discord.Emoji):
        await self._deleteemoji_logic(ctx, emoji)

    async def slash_deleteemoji(self, interaction, emoji: str):
        obj = await self._parse_emoji(interaction.guild, emoji)
        if not obj:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Invalid emoji."), ephemeral=True
            )
        await self._deleteemoji_logic(interaction, obj)

    async def _parse_emoji(self, guild, emoji_str):
        m = re.search(r"(\d+)", emoji_str)
        if m:
            obj = discord.utils.get(guild.emojis, id=int(m.group(1)))
            if obj:
                return obj
        return discord.utils.get(guild.emojis, name=emoji_str)

    # ===============================================================
    #  LOCKTEXT
    # ===============================================================
    @commands.command(name="locktext")
    @require("senior_moderator")
    async def locktext(self, ctx, channel: discord.TextChannel = None):
        chan = channel or ctx.channel
        if chan.id in self.locktext_channels:
            self.locktext_channels.discard(chan.id)
            await self.db.execute("DELETE FROM locktext WHERE channel_id = $1", chan.id)
            await self.log_action(ctx, "Locktext Disabled", target=None, channel=chan,
                                  before="Enabled", after="Disabled")
            return
        self.locktext_channels.add(chan.id)
        await self.db.execute("INSERT INTO locktext (channel_id) VALUES ($1) ON CONFLICT DO NOTHING", chan.id)
        await self.log_action(ctx, "Locktext Enabled", target=None, channel=chan,
                              before="Disabled", after="Enabled",
                              extra="Plain text messages will now be auto-deleted; only images/links allowed.")

    # ===============================================================
    #  CLOWN (Mods and higher; toggles role + forces nick to "Clown")
    # ===============================================================
    @commands.command(name="clown")
    @require("moderator")
    async def clown_toggle(self, ctx, member: discord.Member):
        gid = ctx.guild.id

        if gid in self.clowns and member.id in self.clowns[gid]:
            original = self.clowns[gid].pop(member.id)
            if not self.clowns[gid]:
                del self.clowns[gid]
            await self.db.execute("DELETE FROM clowns WHERE guild_id = $1 AND user_id = $2", gid, member.id)

            if gid in self.forcenicks and member.id in self.forcenicks[gid]:
                self.forcenicks[gid].pop(member.id, None)
                if not self.forcenicks[gid]:
                    del self.forcenicks[gid]
                await self.db.execute(
                    "DELETE FROM forcenicks WHERE guild_id = $1 AND user_id = $2",
                    gid, member.id,
                )

            role = ctx.guild.get_role(self.clown_role_id) if self.clown_role_id else None
            if role:
                try:
                    await member.remove_roles(role, reason="Clown removed")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            try:
                await member.edit(nick=original, reason="Clown mode removed")
            except (discord.Forbidden, discord.HTTPException):
                pass

            await self.log_action(
                ctx, "Clown Disabled", member,
                before="`Clown` mode on",
                after="`Clown` mode off",
                extra="Clown role removed and nickname restored.",
            )
            return

        try:
            await self.check_mod_permission(ctx, "moderator")
        except commands.CheckFailure as e:
            return await self.fail(ctx, str(e))
        ok, err = self.can_target(ctx.author, member)
        if not ok:
            return await self.fail(ctx, err)
        if not self.clown_role_id:
            return await self.fail(ctx, "Clown role is not configured.")
        role = ctx.guild.get_role(self.clown_role_id)
        if not role:
            return await self.fail(ctx, "Clown role was deleted.")

        if gid not in self.clowns:
            self.clowns[gid] = {}
        self.clowns[gid][member.id] = member.nick
        await self.db.execute(
            "INSERT INTO clowns (guild_id, user_id, original_nick) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET original_nick = EXCLUDED.original_nick",
            gid, member.id, member.nick,
        )

        try:
            await member.add_roles(role, reason="Clown mode")
        except discord.Forbidden:
            return await self.fail(ctx, "I cannot add the clown role.")

        if gid not in self.forcenicks:
            self.forcenicks[gid] = {}
        self.forcenicks[gid][member.id] = {"forced": "Clown", "original": member.nick}
        await self.db.execute(
            "INSERT INTO forcenicks (guild_id, user_id, forced_nick, original_nick) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET "
            "forced_nick = EXCLUDED.forced_nick, original_nick = EXCLUDED.original_nick",
            gid, member.id, "Clown", member.nick,
        )

        try:
            await member.edit(nick="Clown", reason="Clown mode")
        except (discord.Forbidden, discord.HTTPException):
            pass

        await self.log_action(
            ctx, "Clown Enabled", member,
            before=f"`{member.nick or member.name}` (no clown role)",
            after=f"`Clown` (role {role.mention} added, nickname forced)",
        )

    # ===============================================================
    #  FORCENICK (Mods and higher)
    # ===============================================================
    @commands.group(name="forcenick", invoke_without_command=True)
    @require("moderator")
    async def forcenick_group(self, ctx):
        await self.send(ctx, make_embed(
            ctx, f"{EMOJIS['gear']} Force Nickname",
            "`forcenick add @user <nick>` — Lock a user to a nickname permanently\n"
            "`forcenick remove @user` — Release the forced nickname",
            GREY_COLOR,
        ))

    @forcenick_group.command(name="add")
    @require("moderator")
    async def forcenick_add(self, ctx, member: discord.Member, *, nick: str):
        if len(nick) > 32:
            return await self.fail(ctx, "Nickname must be 32 characters or fewer.")

        ok, err = self.can_target(ctx.author, member)
        if not ok:
            return await self.fail(ctx, err)

        gid = ctx.guild.id
        if gid in self.forcenicks and member.id in self.forcenicks[gid]:
            return await self.fail(ctx, f"{member.mention} already has a forced nickname.")

        original = member.nick or member.name
        if gid not in self.forcenicks:
            self.forcenicks[gid] = {}
        self.forcenicks[gid][member.id] = {"forced": nick, "original": member.nick}

        await self.db.execute(
            "INSERT INTO forcenicks (guild_id, user_id, forced_nick, original_nick) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET "
            "forced_nick = EXCLUDED.forced_nick, original_nick = EXCLUDED.original_nick",
            gid, member.id, nick, member.nick,
        )

        try:
            await member.edit(nick=nick, reason=f"Forcenick by {ctx.author}")
        except discord.Forbidden:
            return await self.fail(ctx, "I cannot change that member's nickname (role too low).")
        except discord.HTTPException as e:
            return await self.fail(ctx, f"Failed to set nickname: {e}")

        await self.log_action(
            ctx, "Forcenick Added", member,
            before=f"`{original}`",
            after=f"`{nick}`",
            extra="Nickname will be re-enforced automatically if changed.",
        )

    @forcenick_group.command(name="remove")
    @require("moderator")
    async def forcenick_remove(self, ctx, member: discord.Member):
        gid = ctx.guild.id
        if gid not in self.forcenicks or member.id not in self.forcenicks[gid]:
            return await self.fail(ctx, f"{member.mention} does not have a forced nickname.")
        data = self.forcenicks[gid][member.id]
        forced = data.get("forced")
        original = data.get("original")
        self.forcenicks[gid].pop(member.id)
        if not self.forcenicks[gid]:
            del self.forcenicks[gid]
        await self.db.execute("DELETE FROM forcenicks WHERE guild_id = $1 AND user_id = $2", gid, member.id)
        try:
            await member.edit(nick=original, reason="Forcenick removed")
        except (discord.Forbidden, discord.HTTPException):
            pass
        await self.log_action(
            ctx, "Forcenick Removed", member,
            before=f"`{forced}`",
            after=f"`{original or member.name}`",
        )

    # ===============================================================
    #  EMOJI LIST PAGINATION VIEW
    # ===============================================================
    class EmojiListView(ComponentsV2View):
        def __init__(self, ctx, pages):
            super().__init__(timeout=120)
            self.ctx = ctx
            self.pages = pages
            self.current_page = 0
            self.controls = discord.ui.ActionRow()
            self.previous = discord.ui.Button(label="Prev", style=discord.ButtonStyle.secondary)
            self.next = discord.ui.Button(label="Next", style=discord.ButtonStyle.secondary)
            self.previous.callback = self.previous_page
            self.next.callback = self.next_page
            self.controls.add_item(self.previous)
            self.controls.add_item(self.next)
            self.add_item(self.controls)
            self.previous.disabled = True
            self.next.disabled = len(pages) <= 1

        async def interaction_check(self, interaction):
            original = self.ctx.author if isinstance(self.ctx, commands.Context) else self.ctx.user
            if interaction.user.id != original.id:
                await interaction.response.send_message("This menu isn't for you!", ephemeral=True)
                return False
            return True

        async def _update(self, interaction):
            embed = self.ctx.cog._build_emoji_list_embed(
                self.ctx, self.pages[self.current_page],
                self.current_page + 1, len(self.pages),
            )
            self.previous.disabled = (self.current_page == 0)
            self.next.disabled = (self.current_page == len(self.pages) - 1)
            self.set_embed(embed)
            await interaction.response.edit_message(view=self)

        async def previous_page(self, interaction):
            if self.current_page > 0:
                self.current_page -= 1
            await self._update(interaction)

        async def next_page(self, interaction):
            if self.current_page < len(self.pages) - 1:
                self.current_page += 1
            await self._update(interaction)


# ---------------------------------------------------------------
#  Setup
# ---------------------------------------------------------------
async def setup(bot):
    cog = Moderation(bot)
    await cog._create_tables_if_not_exists()
    await cog._load_data_files()
    await bot.add_cog(cog)
    logger.info("Moderation cog loaded.")
