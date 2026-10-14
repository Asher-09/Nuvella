import discord
from discord import app_commands
import json
import math
import os
import sys
import time
import datetime
from pathlib import Path
import aiohttp
import asyncpg
from collections import defaultdict, deque
from dotenv import load_dotenv
from discord.ext import commands
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from utils.emoji import EMOJIS
from utils.components import ComponentsV2View
from utils.logging_setup import setup_logging
import logging

PROJECT_ROOT = Path(__file__).resolve().parent
# Windows Task Scheduler often starts with C:\Windows\System32 as the working
# directory unless "Start in" is set. Anchor configs/data/logs/.env to this project.
os.chdir(PROJECT_ROOT)
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")
LOG_DIR = setup_logging()
logger = logging.getLogger("nuvella")
command_logger = logging.getLogger("nuvella.commands")

console = Console()

DEFAULT_CONFIG = {
    "prefix": "$",
    "owner_id": 1327528116079296594,
    "guild_id": 1535960575341756527,
    "allowed_channel_id": 1555408897013977150,
    "staff_role_ids": [],
    "activity": "Watching over Nuvella",
    "activity_url": None,
    "sync_commands": True,
    "application_id": 1537342404099571762,
}


def load_bot_config():
    config = DEFAULT_CONFIG.copy()

    file_config = {}
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r") as f:
                file_config = json.load(f)
                config.update(file_config)
        except Exception:
            logger.exception("Failed to load config.json; using defaults and environment variables")

    if os.getenv("PREFIX"):
        config["prefix"] = os.getenv("PREFIX")
    if os.getenv("OWNER_ID"):
        config["owner_id"] = int(os.getenv("OWNER_ID"))
    if os.getenv("GUILD_ID"):
        config["guild_id"] = int(os.getenv("GUILD_ID"))
    if os.getenv("ALLOWED_CHANNEL_ID"):
        config["allowed_channel_id"] = int(os.getenv("ALLOWED_CHANNEL_ID"))
    if os.getenv("SYNC_COMMANDS"):
        config["sync_commands"] = os.getenv("SYNC_COMMANDS").lower() == "true"
    if os.getenv("APPLICATION_ID"):
        config["application_id"] = int(os.getenv("APPLICATION_ID"))
    if os.getenv("ACTIVITY"):
        config["activity"] = os.getenv("ACTIVITY")
    if os.getenv("ACTIVITY_URL"):
        config["activity_url"] = os.getenv("ACTIVITY_URL")

    staff_env = os.getenv("STAFF_ROLE_IDS")
    if staff_env:
        config["staff_role_ids"] = [int(x.strip()) for x in staff_env.split(",") if x.strip()]
    else:
        roles = file_config.get("roles", {})
        staff_keys = [
            "admin_role_id",
            "senior_moderator_role_id",
            "moderator_role_id",
            "trial_moderator_role_id",
            "developer_role_id",
            "event_manager_role_id",
            "trial_event_manager_role_id",
        ]
        staff_ids = []
        for key in staff_keys:
            if key in roles:
                try:
                    staff_ids.append(int(roles[key]))
                except (ValueError, TypeError) as e:
                    logger.warning("Invalid role ID for config key %s; skipping value", key)
        config["staff_role_ids"] = staff_ids

    return config


def get_effective_token():
    return os.getenv("TOKEN")


config = load_bot_config()

TOKEN = get_effective_token()
PREFIX = config.get("prefix", "$")
OWNER_ID = config.get("owner_id")
GUILD_ID = int(config.get("guild_id") or 0) or None

MAIN_COLOR = 0x6A0DAD
WIN_COLOR = 0x57F287
LOSS_COLOR = 0xED4245
BREAKER_LINE = EMOJIS["breaker"] * 10

ALLOWED_CHANNEL_ID = int(config.get("allowed_channel_id") or 0) or None
STAFF_ROLE_IDS = set(int(role_id) for role_id in config.get("staff_role_ids", []) if role_id)

STARTUP_ISSUES = []
if not OWNER_ID:
    STARTUP_ISSUES.append("owner_id is not set in config.json / OWNER_ID env")
if not GUILD_ID:
    STARTUP_ISSUES.append("guild_id is not set, slash command sync will be global if enabled")
if not ALLOWED_CHANNEL_ID:
    STARTUP_ISSUES.append("allowed_channel_id is not set, prefix commands are unrestricted")
if not STAFF_ROLE_IDS:
    STARTUP_ISSUES.append("no staff_role_ids configured")
if not config.get("sync_commands", False):
    STARTUP_ISSUES.append("sync_commands is disabled, slash commands will not be synced")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

_cooldowns = {}
COOLDOWN_TIME = 5
_channel_warned = set()

RATE_LIMIT_MAX_COMMANDS = 15
RATE_LIMIT_WINDOW = 60
_command_attempts = defaultdict(lambda: deque(maxlen=RATE_LIMIT_MAX_COMMANDS + 1))

DATABASE_URL = os.getenv("DATABASE_URL")


def is_staff(member: discord.Member) -> bool:
    if not member or not getattr(member, "guild", None):
        return False

    if member.id == member.guild.owner_id or member.id == OWNER_ID:
        return True

    if STAFF_ROLE_IDS and any(role.id in STAFF_ROLE_IDS for role in member.roles):
        return True

    staff_file = "data/staff.json"
    if not os.path.exists(staff_file):
        return False

    try:
        with open(staff_file) as handle:
            staff_data = json.load(handle)
    except Exception:
        logger.exception("Failed to read data/staff.json")
        return False

    uid = str(member.id)
    for values in staff_data.values():
        if isinstance(values, list) and uid in values:
            return True
    return False


def is_owner(user: discord.abc.User, guild: discord.Guild = None) -> bool:
    if user.id == OWNER_ID:
        return True
    if guild and user.id == guild.owner_id:
        return True
    return False


def load_np_users():
    """Load users who can use commands without the prefix."""
    path = os.path.join("data", "np_users.json")
    os.makedirs("data", exist_ok=True)

    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump({"users": []}, f, indent=2)
        return set()

    try:
        with open(path, "r") as f:
            data = json.load(f)
        return {int(uid) for uid in data.get("users", [])}
    except Exception:
        logger.exception("Failed to load data/np_users.json; returning an empty set")
        return set()


def save_np_users(users_set):
    """Save the set of no-prefix user IDs to file."""
    path = os.path.join("data", "np_users.json")
    os.makedirs("data", exist_ok=True)

    try:
        with open(path, "w") as f:
            json.dump({"users": sorted(list(users_set))}, f, indent=2)
        return True
    except Exception:
        logger.exception("Failed to save data/np_users.json")
        return False


def error_embed(ctx, title, desc):
    author = ctx.user if isinstance(ctx, discord.Interaction) else ctx.author
    embed = discord.Embed(
        title=title,
        description=f"{desc}\n{BREAKER_LINE}",
        color=LOSS_COLOR,
        timestamp=discord.utils.utcnow()
    )
    if ctx.guild:
        embed.set_author(name=ctx.guild.name,
                         icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
    embed.set_footer(text=f"Requested by {author}", icon_url=author.display_avatar.url)
    return embed


def cog_has_custom_error_handler(cog) -> bool:
    """Whether this cog overrides discord.py's default no-op command error handler."""
    if cog is None:
        return False
    handler = getattr(type(cog), "cog_command_error", None)
    base_handler = getattr(commands.Cog, "cog_command_error", None)
    return handler is not None and handler is not base_handler


class NuvellaCommandTree(app_commands.CommandTree):
    """Application-command checks shared by all registered slash commands."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        command = getattr(interaction, "command", None)
        command_name = getattr(command, "qualified_name", None) or getattr(command, "name", "unknown")
        command_logger.info(
            "START | type=slash | user_id=%s | guild_id=%s | channel_id=%s | command=%s",
            getattr(interaction.user, "id", None),
            getattr(interaction.guild, "id", None),
            getattr(interaction, "channel_id", None),
            command_name,
        )

        async def deny(title: str, description: str) -> bool:
            embed = error_embed(interaction, title, description)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
            except discord.HTTPException:
                pass
            return False

        if not interaction.guild:
            return await deny("Server Only", "This command can only be used inside a server.")

        user = interaction.user
        member = user if isinstance(user, discord.Member) else interaction.guild.get_member(user.id)
        if member and is_staff(member):
            return True

        # Match prefix-command channel restrictions for non-staff users.
        if ALLOWED_CHANNEL_ID and interaction.channel_id != ALLOWED_CHANNEL_ID:
            return await deny("Wrong Channel", f"Commands can only be used in <#{ALLOWED_CHANNEL_ID}>.")

        now = time.time()
        attempts = _command_attempts[user.id]
        attempts.append(now)
        while attempts and now - attempts[0] > RATE_LIMIT_WINDOW:
            attempts.popleft()

        if len(attempts) > RATE_LIMIT_MAX_COMMANDS:
            try:
                async with self.client.pool.acquire() as conn:
                    await conn.execute(
                        "INSERT INTO blacklist (user_id, reason) VALUES ($1, $2) "
                        "ON CONFLICT (user_id) DO NOTHING",
                        user.id, "Auto-blacklisted for command spam",
                    )
                attempts.clear()
            except Exception:
                logger.exception("Failed to apply slash-command spam blacklist | user_id=%s", user.id)
                return await deny("Unavailable", "The bot could not verify your request. Please try again later.")
            return await deny("Blacklisted", "You have been blacklisted for spamming commands.")

        try:
            if await self.client.is_blacklisted(user.id):
                return await deny("Blacklisted", "You are blacklisted from using the bot.")
        except Exception:
            logger.exception("Failed to check slash-command blacklist | user_id=%s", user.id)
            return await deny("Unavailable", "The bot could not verify your request. Please try again later.")

        last_used = _cooldowns.get(user.id)
        if last_used is not None and now - last_used < COOLDOWN_TIME:
            remaining = max(1, int(COOLDOWN_TIME - (now - last_used)))
            return await deny("Cooldown", f"Please wait `{remaining}s` before using another command.")

        _cooldowns[user.id] = now
        return True

    async def on_error(
        self, interaction: discord.Interaction, command: app_commands.Command, error: Exception
    ) -> None:
        """Central traceback logging for unhandled slash-command failures."""
        root_error = error.original if isinstance(error, app_commands.AppCommandError) and getattr(error, "original", None) else error
        command_name = getattr(command, "qualified_name", None) or getattr(command, "name", "unknown")
        logger.error(
            "Slash command failed | user_id=%s | guild_id=%s | channel_id=%s | command=%s | error_type=%s | error=%s",
            getattr(interaction.user, "id", None),
            getattr(interaction.guild, "id", None),
            getattr(interaction, "channel_id", None),
            command_name, type(root_error).__name__, root_error,
            exc_info=(type(root_error), root_error, root_error.__traceback__),
        )
        try:
            message = "The command failed unexpectedly. The error has been logged; please try again later."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logger.debug("Could not send slash-command failure notice", exc_info=True)


def build_command_candidates(bot: commands.Bot):
    """Build a list of possible full command names (including aliases) for prefix commands."""
    candidates = set()

    for cmd in bot.walk_commands():
        if cmd.hidden:
            continue

        # Build full name chain with real names/aliases at each level
        parts = []
        parent = cmd
        while parent is not None:
            parts.append((parent.name.lower(), [a.lower() for a in parent.aliases]))
            parent = parent.parent
        parts.reverse()

        # Generate all possible command name combinations
        combos = [""]
        for name, aliases in parts:
            names = [name] + aliases
            combos = [
                f"{prefix} {n}".strip() if prefix else n
                for prefix in combos
                for n in names
            ]

        candidates.update(combos)

    return sorted(candidates, key=len, reverse=True)


class ComponentContext(commands.Context):
    """Render embed-only prefix replies through the shared Components V2 layout.

    Existing interactive messages that already have a View keep their original
    layout so buttons/selects and message-edit flows are not broken.
    """
    async def send(self, content=None, *, embed=None, embeds=None, view=None, **kwargs):
        target_embed = embed
        if target_embed is None and embeds and len(embeds) == 1:
            target_embed = embeds[0]
        if view is None and target_embed is not None:
            component_view = ComponentsV2View(timeout=180, embed=target_embed)
            if content:
                component_view.add_text(str(content))
            return await super().send(None, view=component_view, **kwargs)

        send_kwargs = dict(kwargs)
        if embed is not None:
            send_kwargs["embed"] = embed
        if embeds is not None:
            send_kwargs["embeds"] = embeds
        if view is not None:
            send_kwargs["view"] = view
        return await super().send(content, **send_kwargs)


class Nuvella(commands.Bot):
    context_class = ComponentContext

    def __init__(self):
        super().__init__(
            command_prefix=PREFIX,
            intents=intents,
            help_command=None,
            case_insensitive=True,
            application_id=config.get("application_id"),
            tree_cls=NuvellaCommandTree,
        )
        self.config = config
        self.start_time = datetime.datetime.now(datetime.timezone.utc)
        self.launch_time = time.perf_counter()
        self.http_session = None
        self.pool = None
        # Compatibility aliases used by older cogs; all point at the same pool.
        self.db_pool = None
        self.pg_pool = None
        self.startup_printed = False
        self.synced_commands = 0

    async def setup_hook(self):
        self.owner_id = OWNER_ID
        self.http_session = aiohttp.ClientSession()

        self.pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=1,
            max_size=20,
            command_timeout=60,
            max_inactive_connection_lifetime=300,
        )
        self.db_pool = self.pool
        self.pg_pool = self.pool
        async with self.pool.acquire() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS blacklist (user_id BIGINT PRIMARY KEY, reason TEXT)"
            )
            # This table is shared by the economy and clan cogs. Keep its shape
            # consistent regardless of which feature initializes first.
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id BIGINT PRIMARY KEY,
                    wallet BIGINT NOT NULL DEFAULT 0,
                    bank BIGINT NOT NULL DEFAULT 0,
                    xp BIGINT NOT NULL DEFAULT 0
                )
            """)
            # Add legacy-missing wallet/bank columns before touching them.
            # CREATE TABLE IF NOT EXISTS alone cannot upgrade an older users table.
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS wallet BIGINT DEFAULT 0")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bank BIGINT DEFAULT 0")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS xp BIGINT DEFAULT 0")
            await conn.execute("UPDATE users SET wallet = 0 WHERE wallet IS NULL")
            await conn.execute("UPDATE users SET bank = 0 WHERE bank IS NULL")
            await conn.execute("UPDATE users SET xp = 0 WHERE xp IS NULL")
            await conn.execute("ALTER TABLE users ALTER COLUMN wallet SET DEFAULT 0")
            await conn.execute("ALTER TABLE users ALTER COLUMN bank SET DEFAULT 0")
            await conn.execute("ALTER TABLE users ALTER COLUMN xp SET DEFAULT 0")
            await conn.execute("ALTER TABLE users ALTER COLUMN wallet SET NOT NULL")
            await conn.execute("ALTER TABLE users ALTER COLUMN bank SET NOT NULL")
            await conn.execute("ALTER TABLE users ALTER COLUMN xp SET NOT NULL")

        for file in sorted(os.listdir("./cogs")):
            if file.endswith(".py") and not file.startswith("_"):
                extension = f"cogs.{file[:-3]}"
                try:
                    await self.load_extension(extension)
                    logger.info("Cog loaded: %s", extension)
                except Exception:
                    logger.exception("Cog load failed: %s", extension)
                    raise

        if config.get("sync_commands", False):
            # Only giveaway.py should publish slash commands. Remove every other
            # local application command before syncing, and sync globally once
            # to delete stale commands left over from earlier deployments.
            for command in list(self.tree.get_commands()):
                if command.name != "giveaway":
                    self.tree.remove_command(command.name, type=command.type)

            if self.tree.get_command("giveaway") is None:
                raise RuntimeError(
                    "The /giveaway command group was not registered; refusing to sync an empty command tree."
                )

            if GUILD_ID:
                # Keep the command guild-scoped for immediate availability. Copy
                # the approved command before clearing the global tree, then sync
                # an empty global tree to delete stale app-wide registrations.
                guild = discord.Object(id=GUILD_ID)
                self.tree.clear_commands(guild=guild)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                self.tree.clear_commands(guild=None)
                await self.tree.sync()
            else:
                # With no guild configured, intentionally publish only giveaway
                # globally and remove any older global registrations.
                synced = await self.tree.sync()
            self.synced_commands = len(synced)

    async def on_ready(self):
        self.uptime = time.time()
        logger.info("Gateway ready as %s (%s)", self.user, self.user.id if self.user else "unknown")
        activity_name = config.get("activity", "Nuvella")
        activity_url = config.get("activity_url")
        if activity_url:
            activity = discord.Streaming(name=activity_name, url=activity_url)
        else:
            activity = discord.Activity(type=discord.ActivityType.playing, name=activity_name)
        await self.change_presence(activity=activity)

        if not self.startup_printed:
            self.startup_printed = True
            elapsed = time.perf_counter() - self.launch_time
            latency_ms = 0 if math.isnan(self.latency) else round(self.latency * 1000)

            table = Table.grid(padding=(0, 2))
            table.add_column(style="bold cyan", justify="right")
            table.add_column()
            table.add_row("Bot", f"{self.user} ({self.user.id})")
            table.add_row("Cogs Loaded", str(len(self.cogs)))
            table.add_row("Commands Synced", str(self.synced_commands))
            table.add_row("Startup Time", f"{elapsed:.2f}s")
            table.add_row("Latency", f"{latency_ms}ms")
            table.add_row("Issues", "\n".join(STARTUP_ISSUES) if STARTUP_ISSUES else "None")

            console.print(Panel(table, title="Nuvella Online", border_style="magenta", expand=False))
            logger.info(
                "Startup complete | cogs=%s | slash_synced=%s | startup_seconds=%.2f | latency_ms=%s | issues=%s",
                len(self.cogs), self.synced_commands, elapsed, latency_ms,
                "; ".join(STARTUP_ISSUES) if STARTUP_ISSUES else "none",
            )

    async def on_command(self, ctx):
        try:
            command_logger.info(
                "START | user_id=%s | guild_id=%s | channel_id=%s | command=%s | args=%s",
                getattr(ctx.author, "id", None), getattr(ctx.guild, "id", None),
                getattr(ctx.channel, "id", None),
                getattr(ctx.command, "qualified_name", "unknown"),
                (ctx.message.content or "")[:1000],
            )
        except Exception:
            logger.exception("Could not audit command start")

    async def on_command_completion(self, ctx):
        command_logger.info(
            "SUCCESS | user_id=%s | guild_id=%s | channel_id=%s | command=%s",
            getattr(ctx.author, "id", None), getattr(ctx.guild, "id", None),
            getattr(ctx.channel, "id", None), getattr(ctx.command, "qualified_name", "unknown"),
        )

    async def on_command_error(self, ctx, error):
        # User mistakes are logged as warnings, not stack traces. Runtime failures
        # retain full tracebacks in logs/errors.log for diagnosis.
        if isinstance(error, commands.CommandNotFound):
            command_logger.info(
                "UNKNOWN | user_id=%s | guild_id=%s | channel_id=%s | content=%s",
                getattr(ctx.author, "id", None), getattr(ctx.guild, "id", None),
                getattr(ctx.channel, "id", None), (ctx.message.content or "")[:500],
            )
            return

        root_error = error.original if isinstance(error, commands.CommandInvokeError) else error
        user_id = getattr(ctx.author, "id", None)
        command_name = getattr(ctx.command, "qualified_name", "unknown")

        # These are ordinary user-facing denials, not bot faults. Cog-level
        # handlers may already have replied; never produce a duplicate response.
        expected_denials = (
            commands.CheckFailure,
            commands.CommandOnCooldown,
            commands.DisabledCommand,
            commands.NoPrivateMessage,
            commands.NotOwner,
            commands.MissingPermissions,
            commands.BotMissingPermissions,
            commands.MaxConcurrencyReached,
        )
        if isinstance(root_error, expected_denials):
            command_logger.info(
                "DENIED | user_id=%s | guild_id=%s | channel_id=%s | command=%s | reason=%s",
                user_id, getattr(ctx.guild, "id", None),
                getattr(ctx.channel, "id", None), command_name,
                type(root_error).__name__,
            )
            # Global checks have no cog handler. Give a concise reply only in
            # that case; cogs with their own UX retain control of the response.
            if not cog_has_custom_error_handler(ctx.cog):
                try:
                    if isinstance(root_error, commands.CommandOnCooldown):
                        message = f"Please wait `{max(1, root_error.retry_after):.1f}s` before using another command."
                    else:
                        message = str(root_error) or "You cannot use that command here."
                    await ctx.send(embed=error_embed(ctx, "Not Allowed", message))
                except discord.HTTPException:
                    pass
            return

        if isinstance(root_error, commands.UserInputError):
            logger.warning(
                "Invalid command input | user_id=%s | command=%s | error=%s",
                user_id, command_name, root_error,
            )
            command_logger.info(
                "INPUT_ERROR | user_id=%s | guild_id=%s | channel_id=%s | command=%s | error=%s",
                user_id, getattr(ctx.guild, "id", None), getattr(ctx.channel, "id", None),
                command_name, type(root_error).__name__,
            )
            # Cogs with their own handler own the UX to avoid duplicate replies.
            if not cog_has_custom_error_handler(ctx.cog):
                try:
                    usage = f"Usage: `{ctx.clean_prefix}{command_name} {ctx.command.signature}`"
                    await ctx.send(embed=error_embed(ctx, "Incorrect Usage", f"{usage}\nUse `{ctx.clean_prefix}help {command_name}` for help."))
                except discord.HTTPException:
                    pass
            return

        logger.error(
            "Command failed | user_id=%s | guild_id=%s | channel_id=%s | command=%s | error_type=%s | error=%s",
            user_id, getattr(ctx.guild, "id", None), getattr(ctx.channel, "id", None),
            command_name, type(root_error).__name__, root_error,
            exc_info=(type(root_error), root_error, root_error.__traceback__),
        )
        command_logger.error(
            "FAILED | user_id=%s | guild_id=%s | channel_id=%s | command=%s | error_type=%s",
            user_id, getattr(ctx.guild, "id", None), getattr(ctx.channel, "id", None),
            command_name, type(root_error).__name__,
        )

    async def on_error(self, event_method, *args, **kwargs):
        logger.exception("Unhandled event exception in %s", event_method)

    async def close(self):
        # Let cogs cancel their background tasks and flush pending state while
        # the shared pool/session are still available.
        for cog_name in list(self.cogs):
            try:
                await self.remove_cog(cog_name)
            except Exception:
                logger.exception("Failed unloading cog %s", cog_name)
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        if self.pool and not self.pool.is_closing():
            await self.pool.close()
        logger.info("Bot resources closed")
        await super().close()

    async def is_blacklisted(self, uid: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchval("SELECT 1 FROM blacklist WHERE user_id=$1", uid)
            return row is not None

    async def on_message(self, message):
        if message.author.bot:
            return
        # Prefix commands in this project are server-oriented. Do not dispatch
        # them from DMs, where many commands legitimately require a guild/member.
        if not message.guild:
            return

        original = message.content
        np_users = load_np_users()

        # No-prefix handling: if user is in np_users and message starts with a command name,
        # add the prefix so it gets processed normally.
        if message.author.id in np_users:
            content_lower = original.lower().strip()
            candidates = build_command_candidates(self)

            for name in candidates:
                if content_lower == name or content_lower.startswith(name + " "):
                    # Only add prefix if not already present
                    if not original.lower().startswith(PREFIX.lower()):
                        message.content = PREFIX + original
                    break

        # If message still doesn't start with prefix, ignore it
        if not PREFIX or not message.content.lower().startswith(PREFIX.lower()):
            return

        # Staff bypass all restrictions
        if is_staff(message.author):
            await self.process_commands(message)
            return

        # Enforce allowed channel for non‑staff
        if ALLOWED_CHANNEL_ID and message.channel.id != ALLOWED_CHANNEL_ID:
            try:
                await message.delete()
            except Exception:
                logger.exception("Failed to delete out-of-channel command | user_id=%s | channel_id=%s",
                                 message.author.id, message.channel.id)
            if ALLOWED_CHANNEL_ID and message.author.id not in _channel_warned:
                _channel_warned.add(message.author.id)
                try:
                    embed = error_embed(message, "Wrong Channel",
                                        f"Commands can only be used in <#{ALLOWED_CHANNEL_ID}>.")
                    await message.channel.send(embed=embed, delete_after=10)
                except Exception:
                    logger.exception("Failed to send wrong-channel notice | user_id=%s | channel_id=%s",
                                     message.author.id, message.channel.id)
            return

        await self.process_commands(message)


bot = Nuvella()


@bot.check
async def global_prefix_check(ctx):
    if not ctx.guild:
        raise commands.NoPrivateMessage("This bot's prefix commands are only available inside a server.")

    if is_staff(ctx.author):
        return True

    now = time.time()
    dq = _command_attempts[ctx.author.id]
    dq.append(now)

    while dq and now - dq[0] > RATE_LIMIT_WINDOW:
        dq.popleft()

    if len(dq) > RATE_LIMIT_MAX_COMMANDS:
        async with bot.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO blacklist (user_id, reason) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
                ctx.author.id, "Auto-blacklisted for command spam"
            )
        dq.clear()
        raise commands.CheckFailure("You have been blacklisted for spamming commands.")

    if await bot.is_blacklisted(ctx.author.id):
        raise commands.CheckFailure("You are blacklisted from using the bot.")

    if ALLOWED_CHANNEL_ID and ctx.channel.id != ALLOWED_CHANNEL_ID:
        raise commands.CheckFailure(f"Commands can only be used in <#{ALLOWED_CHANNEL_ID}>.")

    if ctx.author.id in _cooldowns and now - _cooldowns[ctx.author.id] < COOLDOWN_TIME:
        remaining = COOLDOWN_TIME - (now - _cooldowns[ctx.author.id])
        raise commands.CommandOnCooldown(
            commands.Cooldown(1, COOLDOWN_TIME), remaining, commands.BucketType.user
        )
    _cooldowns[ctx.author.id] = now
    return True


@bot.command(name="amiblacklisted")
async def amiblacklisted(ctx):
    blacklisted = await bot.is_blacklisted(ctx.author.id)
    if blacklisted:
        await ctx.send(f"{EMOJIS['red_circle']} You **ARE** blacklisted. (But if you're staff, you're exempt.)")
    else:
        await ctx.send(f"{EMOJIS['green_circle']} You are **NOT** blacklisted.")


@bot.group(name="np", invoke_without_command=True)
async def np_group(ctx):
    """Manage no-prefix users."""
    if not is_staff(ctx.author):
        embed = error_embed(ctx, "Denied", "Only staff members can manage no-prefix users.")
        await ctx.send(embed=embed)
        return

    users = load_np_users()
    if users:
        user_list = "\n".join([f"• <@{uid}> (`{uid}`)" for uid in sorted(users)])
    else:
        user_list = "No users have no-prefix access."

    embed = discord.Embed(
        title="No-Prefix Users",
        description=f"{user_list}\n{BREAKER_LINE}",
        color=MAIN_COLOR,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)


@np_group.command(name="add")
async def np_add(ctx, user: discord.User = None):
    """Add a user to the no-prefix list."""
    if not is_staff(ctx.author):
        embed = error_embed(ctx, "Denied", "Only staff members can manage no-prefix users.")
        await ctx.send(embed=embed)
        return

    if user is None:
        embed = error_embed(ctx, "Error", "Please specify a user to add. Usage: `$np add @user`")
        await ctx.send(embed=embed)
        return

    users = load_np_users()
    if user.id in users:
        embed = discord.Embed(
            title="Already Added",
            description=f"{user.mention} already has no-prefix access.\n{BREAKER_LINE}",
            color=WIN_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)
        return

    users.add(user.id)
    if save_np_users(users):
        embed = discord.Embed(
            title="No-Prefix Access Granted",
            description=f"{user.mention} can now use commands without the prefix.\n{BREAKER_LINE}",
            color=WIN_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)
    else:
        embed = error_embed(ctx, "Error", "Failed to save no-prefix users.")
        await ctx.send(embed=embed)


@np_group.command(name="remove")
async def np_remove(ctx, user: discord.User = None):
    """Remove a user from the no-prefix list."""
    if not is_staff(ctx.author):
        embed = error_embed(ctx, "Denied", "Only staff members can manage no-prefix users.")
        await ctx.send(embed=embed)
        return

    if user is None:
        embed = error_embed(ctx, "Error", "Please specify a user to remove. Usage: `$np remove @user`")
        await ctx.send(embed=embed)
        return

    users = load_np_users()
    if user.id not in users:
        embed = discord.Embed(
            title="Not Found",
            description=f"{user.mention} does not have no-prefix access.\n{BREAKER_LINE}",
            color=LOSS_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)
        return

    users.remove(user.id)
    if save_np_users(users):
        embed = discord.Embed(
            title="No-Prefix Access Revoked",
            description=f"{user.mention} can no longer use commands without the prefix.\n{BREAKER_LINE}",
            color=WIN_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)
    else:
        embed = error_embed(ctx, "Error", "Failed to save no-prefix users.")
        await ctx.send(embed=embed)


@bot.command(name="shutdown", hidden=True)
async def shutdown_command(ctx: commands.Context):
    if not is_owner(ctx.author, ctx.guild):
        await ctx.send(embed=error_embed(ctx, "Denied", "Only the bot owner can use this command."))
        return

    embed = discord.Embed(
        title="Shutting Down",
        description=f"Nuvella is shutting down.\n{BREAKER_LINE}",
        color=MAIN_COLOR,
        timestamp=discord.utils.utcnow()
    )
    await ctx.send(embed=embed)
    console.print(Panel(f"Shutdown requested by {ctx.author} ({ctx.author.id})", border_style="red", expand=False))
    await bot.close()
    raise SystemExit(0)


@bot.command(name="restart", hidden=True)
async def restart_command(ctx: commands.Context):
    if not is_owner(ctx.author, ctx.guild):
        await ctx.send(embed=error_embed(ctx, "Denied", "Only the bot owner can use this command."))
        return

    embed = discord.Embed(
        title="Restarting",
        description=f"Nuvella is restarting.\n{BREAKER_LINE}",
        color=MAIN_COLOR,
        timestamp=discord.utils.utcnow()
    )
    await ctx.send(embed=embed)
    console.print(Panel(f"Restart requested by {ctx.author} ({ctx.author.id})", border_style="yellow", expand=False))
    await bot.close()
    os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("No bot token found. Set TOKEN in your environment or .env.")
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is not set in .env")
    bot.run(TOKEN)
