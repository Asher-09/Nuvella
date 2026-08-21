import discord
from discord.ext import commands
from discord import app_commands
import json
import time
import asyncio
import re
import logging
import datetime
import unicodedata
from typing import Optional, Union
from collections import defaultdict, deque
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Optional dependencies
try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False

try:
    import pytz
    TIMEZONE_AVAILABLE = True
except ImportError:
    TIMEZONE_AVAILABLE = False

try:
    import tldextract
    TLDEXTRACT_AVAILABLE = True
except ImportError:
    TLDEXTRACT_AVAILABLE = False

# ---------------------------------------------------------------
#  Constants (unchanged)
# ---------------------------------------------------------------
MAIN_COLOR = 0x2F3136
WIN_COLOR = 0x57F287
LOSS_COLOR = 0xED4245
WARN_COLOR = 0xFEE75C
INFO_COLOR = 0x5865F2

EMOJI_MUTE = "<a:mute:1537920441627185274>"
EMOJI_SUCCESS = "<a:tick:1537915747454554222>"
EMOJI_WRONG = "<:cross:1537915734087303199>"
EMOJI_SNIPE = "<:1b_snipe:1537919015715082420>"
EMOJI_UNLOCK = "<:unlock:1537916484532183040>"
EMOJI_DELETE = "<:Deleted:1537919221969846413>"
EMOJI_WARN = "<:purple_warning:1537916196601733241>"
EMOJI_LOCK = "<:lock:1537916486532730890>"
EMOJI_ALERT = "<a:alert:1537916605298778143>"
EMOJI_OWNER = "<a:owner:1537913450754473994>"
EMOJI_ADMIN = "<:admin:1537920215399006229>"
EMOJI_MOD = "<:mod:1537920117503955094>"
EMOJI_COOLDOWN = "<a:cooldown:1537918560616185956>"
EMOJI_RED_MOD_SHIELD = "<:shield_1:1537917735122505778>"
EMOJI_Q_NOTES = "<:q_notes:1537922139431370854>"
EMOJI_MESSAGE = "<:message:1537915592240275506>"
EMOJI_NEW_MAIL = "<a:mail:1537917503471095919>"
EMOJI_JOIN_SHIELD = "<:shield:1537916746441166999>"
EMOJI_FILE = "<:file:1537913812416729200>"
EMOJI_ROLE = "<:roles:1537917922536591450>"
EMOJI_GEAR = "<a:gear:1537918036688773171>"
EMOJI_BARRIER_BLOCK = "<:stop_just_stop:1538063961579061278>"
EMOJI_SCROLL = "<:scroll:1537914407663968306>"
EMOJI_QUESTION_MARK = "<:question_mark:1537914997378912307>"
EMOJI_STATS = "<a:stats:1537921547774205972>"
EMOJI_CALENDAR = "<a:calendar:1538057240836182036>"
EMOJI_USER_ID = "<:userid:1537914514736025740>"
EMOJI_BOOST = "<a:Logo_Boosts:1538070650407616673>"
EMOJI_PINNED = "<:pin_purple:1537913946357366824>"
EMOJI_COLORS = "<:color:1537919527856111686>"
EMOJI_ONLINE = "<a:green_circle:1538071756609429594>"
EMOJI_IDLE = "<a:status_idle_animated:1538071759297839144>"
EMOJI_DND = "<a:Red_Circle:1538073209562468363>"
EMOJI_OFFLINE = "<:3211_black_circle:1538073205955235871>"
EMOJI_GAME_CONTROLLER = "<a:game_controller:1538063050601664533>"
EMOJI_GOLD_MEDAL = "<a:gold_medal:1538056224271114331>"
EMOJI_CLOWN = "<a:clown_silly_gif:1538063462289252382>"
EMOJI_BOOM = "<a:boom:1538053991064141845>"
EMOJI_FIRE_ANIMATED = "<a:purple_fire:1537923927106527304>"
EMOJI_BREAKER = "<:breaker:1537913026643238932>"
EMOJI_INCOMING_ENVELOPE = "<a:mail:1537917503471095919>"
EMOJI_STOP = "<a:neon_stop:1538074655334600815>"
EMOJI_FISHING_POLE = "<:Fishing_Rod:1537918276326395984>"
EMOJI_INPUT_LATIN_LETTERS = "<:alphabet_a:1537918457117675530>"
EMOJI_LINK = "<:link:1537918123192361090>"
EMOJI_LAUGHING = "<a:laugh:1537918671639547994>"
EMOJI_PING = "<a:Ping:1538075284970930277>"
EMOJI_TRASH = "<:Deleted:1537919221969846413>"
EMOJI_CHART = "<:chart:1537919996984959046>"
EMOJI_SPARKLES = "<a:purple_sparkles:1538075641260277810>"
EMOJI_DOWN = "<:roxo_download:1538075952473579592>"
EMOJI_NOTE = "<:musical:1538077179546439680>"
EMOJI_ROBOT = "<:robot:1538077322069151824>"
EMOJI_SPEAKER = "<a:announcement_speaker:1538077387932045343>"
EMOJI_NUMBERS = "<:number_1:1538077486532005969>"

BREAKER = "───────────────"
BREAKER_LINE = "━━━━━━━━━━━━━━━━━━━━"

STAFF_ROLE_MAP = {
    "founder": "founders",
    "manager": "managers",
    "head_admin": "head_admins",
    "admin": "admins",
    "bot_manager": "bot_managers",
    "event_manager": "event_managers",
    "moderator": "moderators",
    "trial_moderator": "trial_moderators",
}

PERM_LEVELS = {
    "user": 0,
    "trial_moderator": 1,
    "moderator": 2,
    "event_manager": 3,
    "bot_manager": 4,
    "admin": 5,
    "head_admin": 6,
    "manager": 7,
    "founder": 8,
    "bot_owner": 9,
}

LANGUAGES = {
    "af": "Afrikaans", "sq": "Albanian", "am": "Amharic", "ar": "Arabic",
    "hy": "Armenian", "az": "Azerbaijani", "eu": "Basque", "be": "Belarusian",
    "bn": "Bengali", "bs": "Bosnian", "bg": "Bulgarian", "ca": "Catalan",
    "ceb": "Cebuano", "ny": "Chichewa", "zh-cn": "Chinese (Simplified)",
    "zh-tw": "Chinese (Traditional)", "co": "Corsican", "hr": "Croatian",
    "cs": "Czech", "da": "Danish", "nl": "Dutch", "en": "English",
    "eo": "Esperanto", "et": "Estonian", "tl": "Filipino", "fi": "Finnish",
    "fr": "French", "fy": "Frisian", "gl": "Galician", "ka": "Georgian",
    "de": "German", "el": "Greek", "gu": "Gujarati", "ht": "Haitian Creole",
    "ha": "Hausa", "haw": "Hawaiian", "iw": "Hebrew", "hi": "Hindi",
    "hmn": "Hmong", "hu": "Hungarian", "is": "Icelandic", "ig": "Igbo",
    "id": "Indonesian", "ga": "Irish", "it": "Italian", "ja": "Japanese",
    "jw": "Javanese", "kn": "Kannada", "kk": "Kazakh", "km": "Khmer",
    "ko": "Korean", "ku": "Kurdish (Kurmanji)", "ky": "Kyrgyz", "lo": "Lao",
    "la": "Latin", "lv": "Latvian", "lt": "Lithuanian", "lb": "Luxembourgish",
    "mk": "Macedonian", "mg": "Malagasy", "ms": "Malay", "ml": "Malayalam",
    "mt": "Maltese", "mi": "Maori", "mr": "Marathi", "mn": "Mongolian",
    "my": "Myanmar (Burmese)", "ne": "Nepali", "no": "Norwegian", "ps": "Pashto",
    "fa": "Persian", "pl": "Polish", "pt": "Portuguese", "pa": "Punjabi",
    "ro": "Romanian", "ru": "Russian", "sm": "Samoan", "gd": "Scots Gaelic",
    "sr": "Serbian", "st": "Sesotho", "sn": "Shona", "sd": "Sindhi",
    "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "so": "Somali",
    "es": "Spanish", "su": "Sundanese", "sw": "Swahili", "sv": "Swedish",
    "tg": "Tajik", "ta": "Tamil", "te": "Telugu", "th": "Thai", "tr": "Turkish",
    "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek", "vi": "Vietnamese",
    "cy": "Welsh", "xh": "Xhosa", "yi": "Yiddish", "yo": "Yoruba", "zu": "Zulu",
}

# ---------------------------------------------------------------
#  Helper functions (unchanged)
# ---------------------------------------------------------------
def make_embed(ctx, title: str, description: str, color: int) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    user = ctx.author if isinstance(ctx, commands.Context) else ctx.user
    if user:
        embed.set_footer(text=f"Requested by {user}", icon_url=user.display_avatar.url)
    return embed

def error_embed(ctx, message: str) -> discord.Embed:
    return discord.Embed(title="Error", description=message, color=0xED4245)

def success_embed(ctx, message: str) -> discord.Embed:
    return discord.Embed(title="Success", description=message, color=0x57F287)

def parse_time(duration_str: str) -> int:
    if not duration_str:
        return 0
    match = re.match(r"(\d+)([smhd])", duration_str.lower().strip())
    if not match:
        return 0
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "s":
        return amount
    elif unit == "m":
        return amount * 60
    elif unit == "h":
        return amount * 3600
    elif unit == "d":
        return amount * 86400
    return 0

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

# ---------------------------------------------------------------
#  Database wrapper (asyncpg based)
# ---------------------------------------------------------------
class Database:
    def __init__(self, bot):
        self.bot = bot
        self.pool = getattr(bot, "pool", None)
        if self.pool is None:
            self.pool = getattr(bot, "db", None)
        if self.pool is None:
            raise RuntimeError("Bot does not have an asyncpg pool (bot.pool or bot.db)")

    async def fetch(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args):
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

# ---------------------------------------------------------------
#  Permission decorator (calls centralized check)
# ---------------------------------------------------------------
def require(level: str):
    if level not in PERM_LEVELS:
        raise ValueError(f"Invalid permission level: {level}")

    async def predicate(ctx: Union[commands.Context, discord.Interaction]):
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
#  Moderation Cog
# ---------------------------------------------------------------
class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        cfg = bot.config
        self.owner_id = getattr(bot, "owner_id", None)

        roles_cfg = cfg.get("roles", {})
        self.staff_role_ids = {
            "admin": roles_cfg.get("admin_role_id"),
            "moderator": roles_cfg.get("moderator_role_id"),
            "trial_moderator": roles_cfg.get("trial_moderator_role_id"),
            "event_manager": roles_cfg.get("event_manager_role_id"),
        }

        self.allowed_link_role_ids = set(cfg.get("allowed_link_role_ids", []))
        self.log_channel_id = cfg.get("log_channel_id")
        self.clown_role_id = cfg.get("clown_role_id")
        self.jail_role_id = cfg.get("jail_role_id")
        self.jail_channel_id = cfg.get("jail_channel_id")
        # CHANGED: Renamed to warn_ban_threshold, default 7, fallback to old key
        self.warn_ban_threshold = cfg.get("warn_ban_threshold", cfg.get("warn_kick_threshold", 7))

        self.owners: set[int] = set()
        # staff_cache: guild_id -> plural_role -> list of user_id strings
        self.staff_cache: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        self.locktext_channels: set[int] = set()
        self.clowns: dict[int, dict[int, str]] = {}
        self.forcenicks: dict[int, dict[int, dict]] = {}
        self.phish_domains: list[str] = []
        self.banned_words: list[str] = []
        self.timezones: dict[str, str] = {}
        self.modstats: dict = {}

        self.antiphish_enabled = True
        self.wordfilter_enabled = True

        self.snipe_data: dict = defaultdict(lambda: deque(maxlen=5))
        self.edit_snipe_data: dict = defaultdict(lambda: deque(maxlen=5))
        self.reaction_snipe_data: dict = defaultdict(lambda: deque(maxlen=5))

        self.db = Database(bot)

        # No startup tasks here; setup() will call _load_data_files

    # ---------------------------------------------------------------
    #  Database initialization (with migration fix)
    # ---------------------------------------------------------------
    async def _create_tables_if_not_exists(self):
        # List of all CREATE TABLE statements (no indexes here)
        tables = [
            """
            CREATE TABLE IF NOT EXISTS owners (
                user_id BIGINT PRIMARY KEY
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS staff (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                role TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, role)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS locktext (
                channel_id BIGINT PRIMARY KEY
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS clowns (
                guild_id BIGINT,
                user_id BIGINT,
                original_nick TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS forcenicks (
                guild_id BIGINT,
                user_id BIGINT,
                forced_nick TEXT,
                original_nick TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS phish_domains (
                domain TEXT PRIMARY KEY
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS banned_words (
                word TEXT PRIMARY KEY
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_timezones (
                user_id BIGINT PRIMARY KEY,
                timezone TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS modstats (
                user_id BIGINT,
                command TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, command)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS warnings (
                id SERIAL PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                moderator_id BIGINT NOT NULL,
                reason TEXT,
                timestamp BIGINT NOT NULL,
                countable BOOLEAN DEFAULT TRUE
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS lockstate (
                channel_id BIGINT PRIMARY KEY,
                data JSONB
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS jail (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                role_ids BIGINT[],
                channel_id BIGINT,
                reason TEXT,
                moderator_id BIGINT,
                created_at BIGINT,
                PRIMARY KEY (guild_id, user_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS np_users (
                user_id BIGINT PRIMARY KEY
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS nick_history (
                id SERIAL PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                old_nick TEXT,
                new_nick TEXT,
                timestamp BIGINT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS log_config (
                id SERIAL PRIMARY KEY,
                channel_id BIGINT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS blacklist (
                user_id BIGINT PRIMARY KEY,
                reason TEXT
            )
            """
        ]

        # Create all tables (if not exist)
        for table_sql in tables:
            await self.db.execute(table_sql)

        # ---- Migration steps for existing tables (add missing columns) ----
        # staff: add guild_id if missing
        await self.db.execute("ALTER TABLE staff ADD COLUMN IF NOT EXISTS guild_id BIGINT")
        # clowns: add guild_id if missing
        await self.db.execute("ALTER TABLE clowns ADD COLUMN IF NOT EXISTS guild_id BIGINT")
        # forcenicks: add guild_id if missing
        await self.db.execute("ALTER TABLE forcenicks ADD COLUMN IF NOT EXISTS guild_id BIGINT")
        # warnings: add guild_id and other potentially missing columns
        await self.db.execute("ALTER TABLE warnings ADD COLUMN IF NOT EXISTS guild_id BIGINT")
        await self.db.execute("ALTER TABLE warnings ADD COLUMN IF NOT EXISTS user_id BIGINT")
        await self.db.execute("ALTER TABLE warnings ADD COLUMN IF NOT EXISTS moderator_id BIGINT")
        await self.db.execute("ALTER TABLE warnings ADD COLUMN IF NOT EXISTS reason TEXT")
        await self.db.execute("ALTER TABLE warnings ADD COLUMN IF NOT EXISTS timestamp BIGINT")
        await self.db.execute("ALTER TABLE warnings ADD COLUMN IF NOT EXISTS countable BOOLEAN DEFAULT TRUE")
        # FIX: Add id column if missing (for warning deletion by index)
        await self.db.execute("ALTER TABLE warnings ADD COLUMN IF NOT EXISTS id SERIAL PRIMARY KEY")
        # jail: add guild_id and other columns if missing
        await self.db.execute("ALTER TABLE jail ADD COLUMN IF NOT EXISTS guild_id BIGINT")
        await self.db.execute("ALTER TABLE jail ADD COLUMN IF NOT EXISTS user_id BIGINT")
        await self.db.execute("ALTER TABLE jail ADD COLUMN IF NOT EXISTS role_ids BIGINT[]")
        await self.db.execute("ALTER TABLE jail ADD COLUMN IF NOT EXISTS channel_id BIGINT")
        await self.db.execute("ALTER TABLE jail ADD COLUMN IF NOT EXISTS reason TEXT")
        await self.db.execute("ALTER TABLE jail ADD COLUMN IF NOT EXISTS moderator_id BIGINT")
        await self.db.execute("ALTER TABLE jail ADD COLUMN IF NOT EXISTS created_at BIGINT")
        # nick_history: add guild_id and other columns if missing
        await self.db.execute("ALTER TABLE nick_history ADD COLUMN IF NOT EXISTS guild_id BIGINT")
        await self.db.execute("ALTER TABLE nick_history ADD COLUMN IF NOT EXISTS user_id BIGINT")
        await self.db.execute("ALTER TABLE nick_history ADD COLUMN IF NOT EXISTS old_nick TEXT")
        await self.db.execute("ALTER TABLE nick_history ADD COLUMN IF NOT EXISTS new_nick TEXT")
        await self.db.execute("ALTER TABLE nick_history ADD COLUMN IF NOT EXISTS timestamp BIGINT")

        # ---- Create indexes after columns are guaranteed ----
        await self.db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_warnings_guild_user_countable
            ON warnings (guild_id, user_id, countable)
            """
        )

    async def _load_data_files(self):
        rows = await self.db.fetch("SELECT user_id FROM owners")
        self.owners = {r["user_id"] for r in rows}

        rows = await self.db.fetch("SELECT guild_id, user_id, role FROM staff")
        for r in rows:
            gid = r["guild_id"]
            role = r["role"]
            plural = STAFF_ROLE_MAP.get(role)
            if plural:
                uid = str(r["user_id"])
                if uid not in self.staff_cache[gid][plural]:
                    self.staff_cache[gid][plural].append(uid)

        rows = await self.db.fetch("SELECT channel_id FROM locktext")
        self.locktext_channels = {r["channel_id"] for r in rows}

        rows = await self.db.fetch("SELECT guild_id, user_id, original_nick FROM clowns")
        for r in rows:
            self.clowns.setdefault(r["guild_id"], {})[r["user_id"]] = r["original_nick"]

        rows = await self.db.fetch("SELECT guild_id, user_id, forced_nick, original_nick FROM forcenicks")
        for r in rows:
            self.forcenicks.setdefault(r["guild_id"], {})[r["user_id"]] = {
                "forced": r["forced_nick"],
                "original": r["original_nick"]
            }

        rows = await self.db.fetch("SELECT domain FROM phish_domains")
        self.phish_domains = [r["domain"] for r in rows]

        rows = await self.db.fetch("SELECT word FROM banned_words")
        self.banned_words = [r["word"] for r in rows]

        rows = await self.db.fetch("SELECT user_id, timezone FROM user_timezones")
        self.timezones = {str(r["user_id"]): r["timezone"] for r in rows}

        rows = await self.db.fetch("SELECT user_id, command, count FROM modstats")
        self.modstats = {}
        for r in rows:
            uid = str(r["user_id"])
            self.modstats.setdefault(uid, {})[r["command"]] = r["count"]

    # ---------------------------------------------------------------
    #  Centralized permission & target checks
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

    async def check_mod_permission(self, ctx, required_level: str):
        actor = self._get_actor(ctx)
        if actor is None:
            raise commands.CheckFailure("This command can only be used by a guild member.")

        internal_level = self.get_level(actor.id, ctx.guild.id)

        if PERM_LEVELS.get(internal_level, 0) < PERM_LEVELS.get(required_level, 0):
            raise commands.CheckFailure(
                f"Only **{required_level.replace('_', ' ').title()}**+ can use this command."
            )

        # If not a bot owner, ensure the user actually has the Discord role
        if internal_level != "bot_owner":
            role_id = self.staff_role_ids.get(required_level)
            if role_id is not None and not actor.get_role(role_id):
                raise commands.CheckFailure(
                    "Your Discord staff role is missing, so this action is blocked."
                )

    def can_target(self, actor: discord.Member, target: discord.Member) -> tuple[bool, Optional[str]]:
        guild = actor.guild
        if actor == target:
            return False, "You cannot target yourself."
        if target.id == guild.owner_id:
            return False, "You cannot target the server owner."
        if target.id in self.owners or target.id == self.bot.owner_id:
            return False, "You cannot target a bot owner."

        actor_internal = PERM_LEVELS.get(self.get_level(actor.id, guild.id), 0)
        target_internal = PERM_LEVELS.get(self.get_level(target.id, guild.id), 0)
        if target_internal > actor_internal:
            return False, "Target has a higher internal staff level than you."

        if actor.top_role <= target.top_role:
            return False, "Your top role must be above the target's top role."
        if guild.me.top_role <= target.top_role:
            return False, "My top role must be above the target's top role."

        return True, None

    # ---------------------------------------------------------------
    #  Permission level lookup (guild‑scoped)
    # ---------------------------------------------------------------
    def get_level(self, user_id: int, guild_id: Optional[int] = None) -> str:
        if user_id == self.bot.owner_id or user_id in self.owners:
            return "bot_owner"

        if guild_id is not None:
            staff_roles = self.staff_cache.get(guild_id, {})
            for singular, plural in [
                ("founder", "founders"),
                ("manager", "managers"),
                ("head_admin", "head_admins"),
                ("admin", "admins"),
                ("bot_manager", "bot_managers"),
                ("event_manager", "event_managers"),
                ("moderator", "moderators"),
                ("trial_moderator", "trial_moderators"),
            ]:
                if str(user_id) in staff_roles.get(plural, []):
                    return singular
        else:
            for gid, staff_roles in self.staff_cache.items():
                for singular, plural in [
                    ("founder", "founders"),
                    ("manager", "managers"),
                    ("head_admin", "head_admins"),
                    ("admin", "admins"),
                    ("bot_manager", "bot_managers"),
                    ("event_manager", "event_managers"),
                    ("moderator", "moderators"),
                    ("trial_moderator", "trial_moderators"),
                ]:
                    if str(user_id) in staff_roles.get(plural, []):
                        return singular
        return "user"

    # ---------------------------------------------------------------
    #  Utility methods
    # ---------------------------------------------------------------
    async def log_action(self, ctx, action: str, target: discord.User, reason: str = None, extra: str = None):
        channel = self.get_log_channel()
        if not channel:
            return
        mod = ctx.user if isinstance(ctx, discord.Interaction) else ctx.author
        embed = discord.Embed(
            title=f"{EMOJI_Q_NOTES} {action}",
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Moderator", value=f"{mod.mention} (`{mod.id}`)", inline=True)
        embed.add_field(name="Target", value=f"{target.mention} (`{target.id}`)", inline=True)
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        if extra:
            embed.add_field(name="Details", value=extra, inline=False)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text="Moderation Log")
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning("Cannot send log to channel %s", self.log_channel_id)
        except discord.HTTPException as e:
            logger.error("Failed to send log: %s", e)

    def get_log_channel(self) -> Optional[discord.TextChannel]:
        return self.bot.get_channel(self.log_channel_id)

    async def send(self, ctx, emb: discord.Embed, *, view: Optional[discord.ui.View] = None):
        if isinstance(ctx, discord.Interaction):
            try:
                if ctx.response.is_done():
                    return await ctx.followup.send(embed=emb, view=view)
                else:
                    return await ctx.response.send_message(embed=emb, view=view)
            except discord.HTTPException:
                try:
                    return await ctx.followup.send(embed=emb, view=view)
                except Exception:
                    return None
        else:
            try:
                return await ctx.reply(embed=emb, view=view, mention_author=False)
            except discord.HTTPException as e:
                if e.code == 50035 or "Unknown message" in str(e):
                    try:
                        return await ctx.channel.send(embed=emb, view=view)
                    except Exception:
                        return None
                raise
            except discord.NotFound:
                try:
                    return await ctx.channel.send(embed=emb, view=view)
                except Exception:
                    return None

    async def _try_dm(self, member: discord.Member, embed: discord.Embed):
        try:
            await member.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _manual_dm(self, member: discord.Member, action: str, reason: str, duration: Optional[str] = None):
        embed = discord.Embed(
            title=f"{EMOJI_WARN} {action} in {member.guild.name}",
            color=WARN_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=False)
        await self._try_dm(member, embed)

    async def _record_modstat(self, user_id: int, command_name: str):
        await self.db.execute(
            "INSERT INTO modstats (user_id, command, count) VALUES ($1, $2, 1) "
            "ON CONFLICT (user_id, command) DO UPDATE SET count = modstats.count + 1",
            user_id, command_name
        )
        uid = str(user_id)
        if uid not in self.modstats:
            self.modstats[uid] = {}
        self.modstats[uid][command_name] = self.modstats[uid].get(command_name, 0) + 1

    # ---------------------------------------------------------------
    #  Anti‑phishing / word filter helper
    # ---------------------------------------------------------------
    def _normalize_domain(self, raw: str) -> Optional[str]:
        raw = raw.strip().lower()
        if not raw:
            return None
        if not raw.startswith(("http://", "https://")):
            raw = "http://" + raw
        try:
            parsed = urlparse(raw)
            host = parsed.hostname
        except Exception:
            return None
        if not host:
            return None
        try:
            host = host.encode("idna").decode("ascii")
        except Exception:
            return None
        return host.rstrip(".")

    def _registered_domain(self, host: str) -> str:
        if TLDEXTRACT_AVAILABLE:
            ext = tldextract.extract(host)
            return f"{ext.domain}.{ext.suffix}"
        return host

    def _normalize_text(self, text: str) -> str:
        text = unicodedata.normalize("NFKC", text).lower()
        text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
        text = re.sub(r"(.)\1{2,}", r"\1\1", text)
        return text

    def contains_banned_word(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        for banned in self.banned_words:
            banned_norm = self._normalize_text(banned)
            if not banned_norm:
                continue
            pattern = rf"(?<!\w){re.escape(banned_norm)}(?!\w)"
            if re.search(pattern, normalized):
                return True
        return False

    async def is_phishing(self, content: str) -> bool:
        words = re.findall(r'https?://[^\s]+|[^\s]+\.[^\s]+', content)
        for word in words:
            word = word.rstrip(".,;:!?)]}>\"'")
            host = self._normalize_domain(word)
            if not host:
                continue
            host_reg = self._registered_domain(host)
            for blocked in self.phish_domains:
                blocked_host = self._normalize_domain(blocked)
                if blocked_host:
                    blocked_reg = self._registered_domain(blocked_host)
                    if host_reg == blocked_reg or host == blocked_host:
                        return True
        return False

    # ---------------------------------------------------------------
    #  Event listeners for moderation
    # ---------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        ctx = await self.bot.get_context(message)
        if ctx.valid:
            return  # Let commands handle themselves

        # Skip staff / allowed roles
        actor = message.author
        if await self._bypass_filters(actor):
            return

        # Anti‑phishing
        if self.antiphish_enabled and self.phish_domains and await self.is_phishing(message.content):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention} Phishing links are not allowed.", delete_after=5)
                await self.log_action(ctx, "Anti‑Phishing", message.author, "Deleted phishing message")
            except discord.Forbidden:
                logger.warning("Cannot delete phishing message in %s", message.channel.id)
            except discord.HTTPException as e:
                logger.error("Failed to delete phishing message: %s", e)
            return

        # Word filter
        if self.wordfilter_enabled and self.banned_words and self.contains_banned_word(message.content):
            try:
                await message.delete()
                await message.channel.send(f"{message.author.mention} Your message contained a banned word.", delete_after=5)
                await self.log_action(ctx, "Word Filter", message.author, "Deleted filtered message")
            except discord.Forbidden:
                logger.warning("Cannot delete filtered message in %s", message.channel.id)
            except discord.HTTPException as e:
                logger.error("Failed to delete filtered message: %s", e)

    async def _bypass_filters(self, member: discord.Member) -> bool:
        # Staff bypass
        level = self.get_level(member.id, member.guild.id)
        if level != "user":
            return True
        # Allowed roles bypass
        for role in member.roles:
            if role.id in self.allowed_link_role_ids:
                return True
        return False

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        if not message.guild:
            return
        self.snipe_data[message.channel.id].appendleft({
            "author_id": message.author.id,
            "content": message.content,
            "timestamp": int(message.created_at.timestamp()),
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
            "timestamp": int(before.created_at.timestamp()),
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
        if before.nick != after.nick:
            await self.db.execute(
                "INSERT INTO nick_history (guild_id, user_id, old_nick, new_nick, timestamp) "
                "VALUES ($1, $2, $3, $4, $5)",
                after.guild.id, after.id, before.nick, after.nick, int(time.time())
            )

    # ---------------------------------------------------------------
    #  SNIPE COMMANDS (ID‑based)
    # ---------------------------------------------------------------
    async def _snipe_logic(self, ctx, index: int = 1, channel: discord.TextChannel = None):
        chan = channel or ctx.channel
        entries = self.snipe_data.get(chan.id, deque())
        if not entries:
            return await self.send(ctx, error_embed(ctx, "Nothing to snipe in that channel."))
        if index < 1 or index > len(entries):
            return await self.send(ctx, error_embed(ctx, f"Index out of range (1-{len(entries)})."))
        data = entries[index - 1]
        author = self.bot.get_user(data["author_id"])
        author_text = author.mention if author else f"<@{data['author_id']}>"
        embed = make_embed(ctx, f"{EMOJI_SNIPE} Sniped Message #{index}", "", MAIN_COLOR)
        embed.add_field(name="Author", value=author_text, inline=True)
        embed.add_field(name="Channel", value=chan.mention, inline=True)
        embed.add_field(name="Content", value=data["content"] or "*No text content*", inline=False)
        if data["attachment_urls"]:
            embed.add_field(name="Attachments", value="\n".join(data["attachment_urls"]), inline=False)
        embed.set_footer(text=f"Deleted • <t:{data['timestamp']}:f>")
        await self.send(ctx, embed)

    @commands.command(aliases=["sn", "s"])
    @require("trial_moderator")
    async def snipe(self, ctx, index: int = 1, channel: discord.TextChannel = None):
        await self._snipe_logic(ctx, index, channel)

    async def _editsnipe_logic(self, ctx, index: int = 1, channel: discord.TextChannel = None):
        chan = channel or ctx.channel
        entries = self.edit_snipe_data.get(chan.id, deque())
        if not entries:
            return await self.send(ctx, error_embed(ctx, "Nothing to editsnipe in that channel."))
        if index < 1 or index > len(entries):
            return await self.send(ctx, error_embed(ctx, f"Index out of range (1-{len(entries)})."))
        data = entries[index - 1]
        author = self.bot.get_user(data["author_id"])
        author_text = author.mention if author else f"<@{data['author_id']}>"
        embed = make_embed(ctx, f"{EMOJI_SNIPE} Edit Sniped #{index}", "", 0x2ECC71)
        embed.add_field(name="Author", value=author_text, inline=True)
        embed.add_field(name="Channel", value=chan.mention, inline=True)
        embed.add_field(name="Before", value=data["old_content"] or "*empty*", inline=False)
        embed.add_field(name="After", value=data["new_content"] or "*empty*", inline=False)
        embed.set_footer(text=f"Edited • <t:{data['timestamp']}:f>")
        await self.send(ctx, embed)

    @commands.command(aliases=["es"])
    @require("trial_moderator")
    async def editsnipe(self, ctx, index: int = 1, channel: discord.TextChannel = None):
        await self._editsnipe_logic(ctx, index, channel)

    async def _snipereaction_logic(self, ctx, index: int = 1, channel: discord.TextChannel = None):
        chan = channel or ctx.channel
        entries = self.reaction_snipe_data.get(chan.id, deque())
        if not entries:
            return await self.send(ctx, error_embed(ctx, "Nothing to snipe in that channel."))
        if index < 1 or index > len(entries):
            return await self.send(ctx, error_embed(ctx, f"Index out of range (1-{len(entries)})."))
        data = entries[index - 1]
        user = self.bot.get_user(data["user_id"])
        user_text = user.mention if user else f"<@{data['user_id']}>"
        embed = make_embed(ctx, f"{EMOJI_SNIPE} Reaction Sniped #{index}", "", 0xE91E63)
        embed.add_field(name="User", value=user_text, inline=True)
        embed.add_field(name="Emoji", value=data["emoji"], inline=True)
        embed.add_field(name="Channel", value=chan.mention, inline=True)
        if data["message_content"]:
            embed.add_field(name="Message", value=data["message_content"][:500], inline=False)
        if data["message_author_id"]:
            msg_author = self.bot.get_user(data["message_author_id"])
            if msg_author:
                embed.add_field(name="Message Author", value=msg_author.mention, inline=True)
        embed.set_footer(text=f"Reaction removed • <t:{data['timestamp']}:f>")
        await self.send(ctx, embed)

    @commands.command(name="snipereaction", aliases=["sr"])
    @require("trial_moderator")
    async def snipereaction(self, ctx, index: int = 1, channel: discord.TextChannel = None):
        await self._snipereaction_logic(ctx, index, channel)

    # ---------------------------------------------------------------
    #  SET LOG CHANNEL
    # ---------------------------------------------------------------
    @commands.command(name="setlogchannel")
    @require("admin")
    async def setlogchannel(self, ctx, channel: discord.TextChannel):
        self.log_channel_id = channel.id
        await self.db.execute(
            "INSERT INTO log_config (id, channel_id) VALUES (1, $1) "
            "ON CONFLICT (id) DO UPDATE SET channel_id = EXCLUDED.channel_id",
            channel.id
        )
        await self.send(ctx, success_embed(ctx, f"Log channel set to {channel.mention}."))

    # ---------------------------------------------------------------
    #  ANTI‑PHISHING COMMANDS
    # ---------------------------------------------------------------
    @commands.group(name="antiphish", invoke_without_command=True)
    @require("admin")
    async def antiphish_group(self, ctx):
        status = f"{EMOJI_SUCCESS} Enabled" if self.antiphish_enabled else f"{EMOJI_WRONG} Disabled"
        embed = make_embed(ctx, f"{EMOJI_FISHING_POLE} Anti‑Phishing Protection",
            f"**Status:** {status}\n\n`antiphish enable/disable`\n`antiphish add <domain>`\n`antiphish remove <domain>`\n`antiphish list`", MAIN_COLOR)
        await self.send(ctx, embed)

    @antiphish_group.command(name="enable")
    @require("admin")
    async def antiphish_enable(self, ctx):
        self.antiphish_enabled = True
        await self.send(ctx, success_embed(ctx, "Anti‑Phishing protection **enabled**."))

    @antiphish_group.command(name="disable")
    @require("admin")
    async def antiphish_disable(self, ctx):
        self.antiphish_enabled = False
        await self.send(ctx, success_embed(ctx, "Anti‑Phishing protection **disabled**."))

    @antiphish_group.command(name="add")
    @require("admin")
    async def antiphish_add(self, ctx, domain: str):
        domain = domain.lower().strip()
        if domain in self.phish_domains:
            return await self.send(ctx, error_embed(ctx, "Domain already in the list."))
        self.phish_domains.append(domain)
        await self.db.execute("INSERT INTO phish_domains (domain) VALUES ($1) ON CONFLICT DO NOTHING", domain)
        await self.send(ctx, success_embed(ctx, f"Domain `{domain}` added to phishing list."))

    @antiphish_group.command(name="remove")
    @require("admin")
    async def antiphish_remove(self, ctx, domain: str):
        domain = domain.lower().strip()
        if domain not in self.phish_domains:
            return await self.send(ctx, error_embed(ctx, "Domain not found."))
        self.phish_domains.remove(domain)
        await self.db.execute("DELETE FROM phish_domains WHERE domain = $1", domain)
        await self.send(ctx, success_embed(ctx, f"Domain `{domain}` removed from phishing list."))

    @antiphish_group.command(name="list")
    @require("moderator")
    async def antiphish_list(self, ctx):
        if not self.phish_domains:
            return await self.send(ctx, make_embed(ctx, "Phishing Domains", "No domains configured.", MAIN_COLOR))
        desc = "\n".join(f"• `{d}`" for d in self.phish_domains)
        await self.send(ctx, make_embed(ctx, f"{EMOJI_FISHING_POLE} Phishing Domains", desc, MAIN_COLOR))

    # ---------------------------------------------------------------
    #  WORD FILTER COMMANDS
    # ---------------------------------------------------------------
    @commands.group(name="wordfilter", invoke_without_command=True)
    @require("admin")
    async def wordfilter_group(self, ctx):
        status = f"{EMOJI_SUCCESS} Enabled" if self.wordfilter_enabled else f"{EMOJI_WRONG} Disabled"
        embed = make_embed(ctx, f"{EMOJI_INPUT_LATIN_LETTERS} Word Filter",
            f"**Status:** {status}\n\n`wordfilter enable/disable`\n`wordfilter add <word>`\n`wordfilter remove <word>`\n`wordfilter list`", MAIN_COLOR)
        await self.send(ctx, embed)

    @wordfilter_group.command(name="enable")
    @require("admin")
    async def wordfilter_enable(self, ctx):
        self.wordfilter_enabled = True
        await self.send(ctx, success_embed(ctx, "Word filter **enabled**."))

    @wordfilter_group.command(name="disable")
    @require("admin")
    async def wordfilter_disable(self, ctx):
        self.wordfilter_enabled = False
        await self.send(ctx, success_embed(ctx, "Word filter **disabled**."))

    @wordfilter_group.command(name="add")
    @require("admin")
    async def wordfilter_add(self, ctx, *, word: str):
        word = word.lower().strip()
        if word in self.banned_words:
            return await self.send(ctx, error_embed(ctx, "Word already in the filter."))
        self.banned_words.append(word)
        await self.db.execute("INSERT INTO banned_words (word) VALUES ($1) ON CONFLICT DO NOTHING", word)
        await self.send(ctx, success_embed(ctx, f"Word `{word}` added to filter."))

    @wordfilter_group.command(name="remove")
    @require("admin")
    async def wordfilter_remove(self, ctx, *, word: str):
        word = word.lower().strip()
        if word not in self.banned_words:
            return await self.send(ctx, error_embed(ctx, "Word not found in filter."))
        self.banned_words.remove(word)
        await self.db.execute("DELETE FROM banned_words WHERE word = $1", word)
        await self.send(ctx, success_embed(ctx, f"Word `{word}` removed from filter."))

    @wordfilter_group.command(name="list")
    @require("moderator")
    async def wordfilter_list(self, ctx):
        if not self.banned_words:
            return await self.send(ctx, make_embed(ctx, "Banned Words", "No words configured.", MAIN_COLOR))
        desc = "\n".join(f"• `{w}`" for w in self.banned_words)
        await self.send(ctx, make_embed(ctx, f"{EMOJI_INPUT_LATIN_LETTERS} Banned Words", desc, MAIN_COLOR))

    # ---------------------------------------------------------------
    #  NICKNAME HISTORY
    # ---------------------------------------------------------------
    @commands.command(name="nickhistory", aliases=["nh"])
    @require("moderator")
    async def nickhistory(self, ctx, member: discord.Member):
        rows = await self.db.fetch(
            "SELECT old_nick, new_nick, timestamp FROM nick_history "
            "WHERE guild_id = $1 AND user_id = $2 ORDER BY timestamp DESC LIMIT 20",
            ctx.guild.id, member.id
        )
        if not rows:
            return await self.send(ctx, error_embed(ctx, "No nickname history found for that user."))
        desc = ""
        for e in reversed(rows):
            desc += f"<t:{e['timestamp']}:f> — **{e['old_nick']}** → **{e['new_nick']}**\n"
        embed = make_embed(ctx, f"{EMOJI_Q_NOTES} Nickname History for {member.display_name}", desc, MAIN_COLOR)
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.send(ctx, embed)

    # ---------------------------------------------------------------
    #  UNBAN ALL
    # ---------------------------------------------------------------
    @commands.command(name="unbanall")
    @require("bot_owner")
    async def unbanall(self, ctx):
        bans = [e async for e in ctx.guild.bans()]
        if not bans:
            return await self.send(ctx, error_embed(ctx, "No banned users."))
        count = 0
        for ban_entry in bans:
            try:
                await ctx.guild.unban(ban_entry.user, reason="Mass unban")
                count += 1
            except discord.Forbidden:
                logger.warning("Cannot unban %s: missing permissions", ban_entry.user.id)
            except discord.HTTPException as e:
                logger.error("Failed to unban %s: %s", ban_entry.user.id, e)
            await asyncio.sleep(0.5)
        await self.send(ctx, success_embed(ctx, f"Unbanned **{count}** users."))

    # ---------------------------------------------------------------
    #  OWNER MANAGEMENT
    # ---------------------------------------------------------------
    @commands.group(name="owner", invoke_without_command=True)
    @require("bot_owner")
    async def owner_group(self, ctx):
        embed = make_embed(
            ctx,
            f"{EMOJI_OWNER} Owner Management",
            (
                "`owner add <user>` — Grant bot owner\n"
                "`owner remove <user>` — Revoke bot owner\n"
                "`owner list` — List all bot owners"
            ),
            MAIN_COLOR,
        )
        await self.send(ctx, embed)

    @owner_group.command(name="add")
    @require("bot_owner")
    async def owner_add(self, ctx, user: discord.Member):
        if ctx.author.id != self.bot.owner_id:
            return await self.send(ctx, error_embed(ctx, "Only the **primary** bot owner can add new owners."))
        if user.id in self.owners:
            return await self.send(ctx, error_embed(ctx, f"{user.mention} is already a bot owner."))
        self.owners.add(user.id)
        await self.db.execute("INSERT INTO owners (user_id) VALUES ($1) ON CONFLICT DO NOTHING", user.id)
        embed = make_embed(
            ctx, f"{EMOJI_OWNER} Owner Added",
            f"{user.mention} has been granted **Bot Owner** access.", WIN_COLOR
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        await self.send(ctx, embed)
        await self.log_action(ctx, "Add Owner", user)

    @owner_group.command(name="remove")
    @require("bot_owner")
    async def owner_remove(self, ctx, user: discord.Member):
        if ctx.author.id != self.bot.owner_id:
            return await self.send(ctx, error_embed(ctx, "Only the **primary** bot owner can remove owners."))
        if user.id not in self.owners:
            return await self.send(ctx, error_embed(ctx, f"{user.mention} is not a bot owner."))
        self.owners.discard(user.id)
        await self.db.execute("DELETE FROM owners WHERE user_id = $1", user.id)
        embed = make_embed(
            ctx, f"{EMOJI_OWNER} Owner Removed",
            f"{user.mention}'s **Bot Owner** access has been revoked.", LOSS_COLOR
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        await self.send(ctx, embed)
        await self.log_action(ctx, "Remove Owner", user)

    @owner_group.command(name="list")
    @require("bot_owner")
    async def owner_list(self, ctx):
        primary = self.bot.get_user(self.bot.owner_id)
        lines = [f"{EMOJI_OWNER} {primary.mention} *(primary)*" if primary else f"{EMOJI_OWNER} <@{self.bot.owner_id}> *(primary)*"]
        for uid in self.owners:
            m = ctx.guild.get_member(int(uid))
            lines.append(m.mention if m else f"<@{uid}> *(not in server)*")
        embed = make_embed(
            ctx, f"{EMOJI_OWNER} Bot Owners",
            "\n".join(lines) or "None", MAIN_COLOR
        )
        await self.send(ctx, embed)

    # ---------------------------------------------------------------
    #  BLACKLIST
    # ---------------------------------------------------------------
    @commands.hybrid_group(name="blacklist", aliases=["bl"])
    @require("bot_owner")
    async def blacklist_group(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send("Usage: `blacklist add @user [reason]` | `blacklist remove @user` | `blacklist list`")

    @blacklist_group.command(name="add")
    @require("bot_owner")
    async def blacklist_add(self, ctx, user: discord.Member, *, reason: str = "No reason provided"):
        await self.db.execute(
            "INSERT INTO blacklist (user_id, reason) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET reason = EXCLUDED.reason",
            user.id, reason
        )
        embed = make_embed(
            ctx, f"{EMOJI_BARRIER_BLOCK} Blacklisted",
            f"{user.mention} has been blacklisted.\n**Reason:** {reason}", LOSS_COLOR
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        await self.send(ctx, embed)
        await self.log_action(ctx, "Blacklist Add", user, reason)

    @blacklist_group.command(name="remove")
    @require("bot_owner")
    async def blacklist_remove(self, ctx, user: discord.Member):
        await self.db.execute("DELETE FROM blacklist WHERE user_id = $1", user.id)
        await self.send(ctx, success_embed(ctx, f"{user.mention} removed from the blacklist."))
        await self.log_action(ctx, "Blacklist Remove", user)

    @blacklist_group.command(name="list")
    @require("bot_owner")
    async def blacklist_list(self, ctx):
        rows = await self.db.fetch("SELECT user_id, reason FROM blacklist")
        if not rows:
            return await self.send(ctx, make_embed(ctx, "Blacklist", "No blacklisted users.", MAIN_COLOR))
        desc = "\n".join(f"• <@{r['user_id']}> — {r['reason']}" for r in rows)
        await self.send(ctx, make_embed(
            ctx, f"{EMOJI_BARRIER_BLOCK} Blacklisted Users", desc, LOSS_COLOR
        ))

    # ---------------------------------------------------------------
    #  BOT UTILITIES
    # ---------------------------------------------------------------
    @commands.command(aliases=["latency"])
    async def ping(self, ctx):
        await self.send(ctx, make_embed(
            ctx, f"{EMOJI_SUCCESS} Pong!",
            f"{EMOJI_PING} Latency: `{round(self.bot.latency * 1000)}ms`", MAIN_COLOR
        ))

    @commands.command(aliases=["up"])
    async def uptime(self, ctx):
        secs = int(time.time() - self.bot.uptime)
        h, r = divmod(secs, 3600)
        m, s = divmod(r, 60)
        await self.send(ctx, make_embed(
            ctx, f"{EMOJI_SUCCESS} Uptime", f"{EMOJI_COOLDOWN} `{h}h {m}m {s}s`", MAIN_COLOR
        ))

    # ---------------------------------------------------------------
    #  TIMEZONE COMMANDS
    # ---------------------------------------------------------------
    @commands.group(name="timezone", invoke_without_command=True)
    async def timezone_group(self, ctx):
        embed = make_embed(ctx, "Timezone Commands",
            "Use `/timezone set <timezone>` to set your timezone\n"
            "Use `/timezone remove` to remove your timezone\n"
            "Use `/timezone list` to see common timezones\n"
            "Use `/tz <user>` to see a user's current time", MAIN_COLOR)
        await self.send(ctx, embed)

    @timezone_group.command(name="set")
    async def timezone_set(self, ctx, *, timezone_name: str):
        if not TIMEZONE_AVAILABLE:
            return await self.send(ctx, error_embed(ctx, "Timezone support is not available."))
        try:
            pytz.timezone(timezone_name)
        except Exception:
            return await self.send(ctx, error_embed(ctx, f"Invalid timezone: `{timezone_name}`"))
        user_id = str(ctx.author.id)
        self.timezones[user_id] = timezone_name
        await self.db.execute(
            "INSERT INTO user_timezones (user_id, timezone) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET timezone = EXCLUDED.timezone",
            ctx.author.id, timezone_name
        )
        embed = make_embed(ctx, f"{EMOJI_SUCCESS} Timezone Set",
            f"Your timezone is now set to `{timezone_name}`", WIN_COLOR)
        await self.send(ctx, embed)

    @timezone_group.command(name="remove")
    async def timezone_remove(self, ctx):
        user_id = str(ctx.author.id)
        if user_id in self.timezones:
            del self.timezones[user_id]
            await self.db.execute("DELETE FROM user_timezones WHERE user_id = $1", ctx.author.id)
            embed = make_embed(ctx, f"{EMOJI_SUCCESS} Timezone Removed",
                "Your timezone has been removed.", WIN_COLOR)
        else:
            embed = error_embed(ctx, "You don't have a timezone set.")
        await self.send(ctx, embed)

    @timezone_group.command(name="list")
    async def timezone_list(self, ctx):
        if not TIMEZONE_AVAILABLE:
            return await self.send(ctx, error_embed(ctx, "Timezone support is not available."))
        common_timezones = [
            "US/Eastern", "US/Central", "US/Mountain", "US/Pacific",
            "Europe/London", "Europe/Paris", "Europe/Berlin",
            "Asia/Tokyo", "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Singapore",
            "Australia/Sydney", "Australia/Melbourne",
            "UTC", "GMT"
        ]
        tz_text = "\n".join(f"• `{tz}`" for tz in common_timezones)
        embed = make_embed(ctx, "Common Timezones", tz_text, MAIN_COLOR)
        await self.send(ctx, embed)

    @commands.command(name="tz")
    async def tz_command(self, ctx, user: discord.Member = None):
        if not TIMEZONE_AVAILABLE:
            return await self.send(ctx, error_embed(ctx, "Timezone support is not available."))
        target = user or ctx.author
        user_id = str(target.id)
        if user_id not in self.timezones:
            return await self.send(ctx, error_embed(ctx, f"{target.mention} doesn't have a timezone set."))
        tz_name = self.timezones[user_id]
        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            return await self.send(ctx, error_embed(ctx, "Invalid timezone for this user."))
        now = datetime.datetime.now(tz)
        time_str = now.strftime("%I:%M %p")
        date_str = now.strftime("%A, %B %d, %Y")
        embed = make_embed(ctx, f"{target.display_name}'s Time",
            f"**Time:** {time_str}\n**Date:** {date_str}", MAIN_COLOR)
        embed.set_thumbnail(url=target.display_avatar.url)
        await self.send(ctx, embed)

    # ---------------------------------------------------------------
    #  TRANSLATE COMMAND
    # ---------------------------------------------------------------
    @commands.command(name="tr", aliases=["translate"])
    async def translate_command(self, ctx, *, text_and_lang: str = None):
        if not TRANSLATOR_AVAILABLE:
            return await self.send(ctx, error_embed(ctx, "Translation support is not available."))
        text = None
        target_lang = "en"
        if ctx.message.reference:
            try:
                replied_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                text = replied_msg.content
                if text_and_lang:
                    target_lang = text_and_lang.strip().lower()
            except Exception:
                pass
        if not text and text_and_lang:
            parts = text_and_lang.rsplit(" ", 1)
            if len(parts) == 2:
                text, target_lang = parts
                target_lang = target_lang.lower()
            else:
                text = text_and_lang
                target_lang = "en"
        if not text:
            return await self.send(ctx, error_embed(ctx, "Please provide text to translate or reply to a message."))
        if target_lang not in LANGUAGES:
            lang_list = ", ".join(sorted(LANGUAGES.keys())[:20]) + ", ..."
            return await self.send(ctx, error_embed(ctx, f"Unknown language code: `{target_lang}`\nCommon codes: {lang_list}"))
        try:
            translator = GoogleTranslator(source='auto', target=target_lang)
            translated = translator.translate(text)
            embed = make_embed(ctx, f"{EMOJI_SUCCESS} Translated to {LANGUAGES.get(target_lang, target_lang)}",
                f"**Original:** {text}\n**Translated:** {translated}", MAIN_COLOR)
            await self.send(ctx, embed)
        except Exception as e:
            await self.send(ctx, error_embed(ctx, f"Translation failed: {str(e)[:100]}"))

    # ---------------------------------------------------------------
    #  PURGE (Advanced)
    # ---------------------------------------------------------------
    DEFAULT_PURGE_AMOUNT = 10

    async def _purge_logic(self, ctx, amount: int = 10, mode: str = "all", user: discord.Member = None,
                           text_filter: str = None, after_message: discord.Message = None,
                           before_message: discord.Message = None):
        if amount > 500:
            return await self.send(ctx, error_embed(ctx, "Maximum is **500** messages."))
        if amount < 1:
            return await self.send(ctx, error_embed(ctx, "Amount must be at least **1**."))

        invoking_message_id = None
        if isinstance(ctx, commands.Context) and ctx.message is not None:
            invoking_message_id = ctx.message.id

        def check(msg):
            if invoking_message_id is not None and msg.id == invoking_message_id:
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

        purge_limit = amount + 1 if invoking_message_id is not None else amount
        deleted = await ctx.channel.purge(limit=purge_limit, check=check)

        if invoking_message_id is not None:
            try:
                await ctx.message.delete()
            except Exception:
                pass

        await asyncio.sleep(0.5)
        mode_text = f" ({mode})" if mode != "all" else ""
        embed = make_embed(
            ctx, f"{EMOJI_DELETE} Purged{mode_text}",
            f"{EMOJI_TRASH} Deleted **{len(deleted)}** messages.", WIN_COLOR
        )
        try:
            await ctx.channel.send(embed=embed)
        except Exception:
            pass
        actor = ctx.author if not isinstance(ctx, discord.Interaction) else ctx.user
        await self._record_modstat(actor.id, "purge")

    @staticmethod
    def _split_trailing_amount(text: Optional[str]) -> tuple:
        if not text:
            return text, None
        parts = text.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return parts[0].strip() or None, int(parts[1])
        return text, None

    @commands.command(aliases=["clear", "clean", "prune", "cb", "c"])
    @require("trial_moderator")
    async def purge(self, ctx, mode_or_amount: str = None, amount: int = None, *, text_filter: str = None):
        mode = "all"
        parsed_amount = None
        user = None

        if mode_or_amount is None:
            return await self._purge_logic(ctx, self.DEFAULT_PURGE_AMOUNT, "all")

        if mode_or_amount.isdigit():
            parsed_amount = int(mode_or_amount)
            return await self._purge_logic(ctx, parsed_amount, "all")

        mode = mode_or_amount.lower()

        if mode in ("startswith", "endswith", "contains"):
            combined = " ".join(p for p in [str(amount) if amount is not None else None, text_filter] if p)
            remaining, trailing_amount = self._split_trailing_amount(combined)
            filt = remaining
            parsed_amount = trailing_amount if trailing_amount is not None else self.DEFAULT_PURGE_AMOUNT
            if not filt:
                return await self.send(ctx, error_embed(ctx, f"You must provide text for `{mode}` mode."))
            return await self._purge_logic(ctx, parsed_amount, mode, text_filter=filt)

        if mode == "user":
            combined = " ".join(p for p in [str(amount) if amount is not None else None, text_filter] if p)
            remaining, trailing_amount = self._split_trailing_amount(combined)
            parsed_amount = trailing_amount if trailing_amount is not None else self.DEFAULT_PURGE_AMOUNT
            if not remaining:
                return await self.send(ctx, error_embed(ctx, "You must mention a user for `user` mode."))
            user_id = re.sub(r'[<>@!]', '', remaining)
            try:
                user = await commands.MemberConverter().convert(ctx, user_id)
            except Exception:
                return await self.send(ctx, error_embed(ctx, "Invalid user."))
            return await self._purge_logic(ctx, parsed_amount, mode, user=user)

        if mode in ("bots", "embeds", "mentions", "attachments", "text"):
            parsed_amount = amount if amount is not None else self.DEFAULT_PURGE_AMOUNT
            return await self._purge_logic(ctx, parsed_amount, mode)

        if mode == "all":
            parsed_amount = amount if amount is not None else self.DEFAULT_PURGE_AMOUNT
            return await self._purge_logic(ctx, parsed_amount, "all")

        return await self._purge_logic(ctx, self.DEFAULT_PURGE_AMOUNT, "all")

    # ---------------------------------------------------------------
    #  CLEAR (bot / user subcommands)
    # ---------------------------------------------------------------
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

    # ---------------------------------------------------------------
    #  SLOWMODE
    # ---------------------------------------------------------------
    @commands.command(aliases=["sm"])
    @require("admin")
    async def slowmode(self, ctx, seconds: int):
        if seconds < 0:
            return await self.send(ctx, error_embed(ctx, "Seconds cannot be negative."))
        try:
            await ctx.channel.edit(slowmode_delay=seconds)
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack Manage Channels permission."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed to set slowmode: {e}"))
        msg = "**disabled**" if seconds == 0 else f"set to **{seconds}s**"
        await self.send(ctx, make_embed(ctx, f"{EMOJI_LOCK} Slowmode", f"Slowmode {msg}.", 0xF1C40F))

    # ---------------------------------------------------------------
    #  LOCK / UNLOCK
    # ---------------------------------------------------------------
    @commands.command()
    @require("admin")
    async def lock(self, ctx, channel: discord.TextChannel = None):
        chan = channel or ctx.channel
        guild = ctx.guild

        row = await self.db.fetchrow("SELECT data FROM lockstate WHERE channel_id = $1", chan.id)
        if row:
            return await self.send(ctx, error_embed(ctx, f"{chan.mention} is already locked."))

        snapshot = {}
        for role in guild.roles:
            if role.permissions.administrator:
                continue
            overwrite = chan.overwrites_for(role)
            pair = overwrite.pair()
            snapshot[str(role.id)] = {
                "allow": pair[0],
                "deny": pair[1],
            }
            new_overwrite = discord.PermissionOverwrite.from_pair(
                allow=pair[0],
                deny=pair[1],
            )
            new_overwrite.send_messages = False
            try:
                await chan.set_permissions(role, overwrite=new_overwrite, reason="Locked")
            except discord.Forbidden:
                logger.warning("Cannot set permissions for role %s in channel %s", role.id, chan.id)
            except discord.HTTPException as e:
                logger.error("Failed to set permissions for role %s: %s", role.id, e)

        await self.db.execute(
            "INSERT INTO lockstate (channel_id, data) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (channel_id) DO UPDATE SET data = EXCLUDED.data",
            chan.id, json.dumps(snapshot)
        )
        await self.send(ctx, make_embed(
            ctx, f"{EMOJI_LOCK} Locked",
            f"{chan.mention} is now **locked** for everyone except Administrators.", 0xE67E22
        ))

    @commands.command()
    @require("admin")
    async def unlock(self, ctx, channel: discord.TextChannel = None):
        chan = channel or ctx.channel
        guild = ctx.guild

        row = await self.db.fetchrow("SELECT data FROM lockstate WHERE channel_id = $1", chan.id)
        if row is None:
            try:
                await chan.set_permissions(guild.default_role, send_messages=None)
            except discord.Forbidden:
                logger.warning("Cannot reset default role permissions in channel %s", chan.id)
            except discord.HTTPException as e:
                logger.error("Failed to reset default role permissions: %s", e)
            return await self.send(ctx, make_embed(
                ctx, f"{EMOJI_UNLOCK} Unlocked",
                f"{chan.mention} is now **unlocked**. (No prior lock snapshot found, restored default.)", WIN_COLOR
            ))

        snapshot = json.loads(row["data"])
        for role_id_str, data in snapshot.items():
            role = guild.get_role(int(role_id_str))
            if role is None:
                continue
            restored = discord.PermissionOverwrite.from_pair(
                allow=data["allow"],
                deny=data["deny"],
            )
            try:
                if restored.is_empty():
                    await chan.set_permissions(role, overwrite=None, reason="Unlocked")
                else:
                    await chan.set_permissions(role, overwrite=restored, reason="Unlocked")
            except discord.Forbidden:
                logger.warning("Cannot restore permissions for role %s in channel %s", role.id, chan.id)
            except discord.HTTPException as e:
                logger.error("Failed to restore permissions for role %s: %s", role.id, e)

        await self.db.execute("DELETE FROM lockstate WHERE channel_id = $1", chan.id)
        await self.send(ctx, make_embed(
            ctx, f"{EMOJI_UNLOCK} Unlocked",
            f"{chan.mention} is now **unlocked**. Original permissions restored.", WIN_COLOR
        ))

    # ---------------------------------------------------------------
    #  WARNS (transaction‑safe, threshold crossing, guild‑scoped)
    # ---------------------------------------------------------------
    async def _warn_logic(self, ctx, member: discord.Member, reason: str = "No reason"):
        actor = self._get_actor(ctx)
        try:
            await self.check_mod_permission(ctx, "trial_moderator")
        except commands.CheckFailure as e:
            return await self.send(ctx, error_embed(ctx, str(e)))

        ok, err = self.can_target(actor, member)
        if not ok:
            return await self.send(ctx, error_embed(ctx, err))

        should_punish = False
        new_count = 0

        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                lock_key = ((ctx.guild.id << 22) ^ member.id) & 0x7FFFFFFFFFFFFFFF
                await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)

                previous_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM warnings "
                    "WHERE guild_id = $1 AND user_id = $2 AND countable = TRUE",
                    ctx.guild.id, member.id
                )

                await conn.execute(
                    "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, timestamp, countable) "
                    "VALUES ($1, $2, $3, $4, $5, TRUE)",
                    ctx.guild.id, member.id, actor.id, reason, int(time.time())
                )

                new_count = previous_count + 1
                should_punish = (
                    previous_count < self.warn_ban_threshold <= new_count
                )

        embed = make_embed(
            ctx, f"{EMOJI_WARN} Warning Issued",
            f"**User:** {member.mention}\n**Reason:** {reason}\n**Total Warns:** {new_count}",
            WARN_COLOR
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.send(ctx, embed)
        await self.log_action(ctx, "Warn", member, reason)
        await self._manual_dm(member, "Warning", reason)
        await self._record_modstat(actor.id, "warn")

        if should_punish:
            try:
                # CHANGED: Ban instead of kick
                await member.ban(reason=f"Automatic ban: reached {new_count} warnings")
                await self.log_action(ctx, "Automatic Ban", member, reason)
            except discord.Forbidden:
                logger.error("Failed to auto-ban %s in guild %s: missing permissions", member.id, ctx.guild.id)
            except discord.HTTPException as e:
                logger.error("Failed to auto-ban %s: HTTP %s", member.id, e.status)

    @commands.command(aliases=["w"])
    @require("trial_moderator")
    async def warn(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        await self._warn_logic(ctx, member, reason)

    @app_commands.command(name="warn", description="Issue a warning to a member")
    async def slash_warn(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
        await self._warn_logic(interaction, member, reason)

    async def _warnings_logic(self, ctx, member: discord.Member):
        rows = await self.db.fetch(
            "SELECT moderator_id, reason, timestamp, countable FROM warnings "
            "WHERE guild_id = $1 AND user_id = $2 ORDER BY timestamp ASC",
            ctx.guild.id, member.id
        )
        if not rows:
            return await self.send(ctx, make_embed(
                ctx, f"{EMOJI_ALERT} No Warnings",
                f"{member.mention} has no warnings.", WIN_COLOR
            ))
        desc = ""
        for i, w in enumerate(rows, 1):
            mod = self.bot.get_user(w["moderator_id"])
            mod_ = mod.mention if mod else "Unknown"
            tag = "" if w["countable"] else " *(not counted)*"
            desc += f"**{i}.** By {mod_} — <t:{w['timestamp']}:f>{tag}\n> {w['reason']}\n"
        countable_total = sum(1 for w in rows if w["countable"])
        embed = make_embed(
            ctx, f"{EMOJI_ALERT} {member.display_name}'s Warnings ({len(rows)} total, {countable_total} countable)", desc, WARN_COLOR
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.send(ctx, embed)

    @commands.command(aliases=["ws", "warnshow"])
    @require("trial_moderator")
    async def warnings(self, ctx, member: discord.Member):
        await self._warnings_logic(ctx, member)

    async def _clearwarns_logic(self, ctx, member: discord.Member, index: Optional[int] = None):
        rows = await self.db.fetch(
            "SELECT id, reason FROM warnings WHERE guild_id = $1 AND user_id = $2 ORDER BY id ASC",
            ctx.guild.id, member.id
        )
        if not rows:
            return await self.send(ctx, make_embed(ctx, f"{EMOJI_ALERT} No Warnings", "Nothing to clear.", 0x95A5A6))
        if index is None:
            await self.db.execute("DELETE FROM warnings WHERE guild_id = $1 AND user_id = $2", ctx.guild.id, member.id)
            await self.send(ctx, success_embed(ctx, f"All warnings for {member.mention} cleared."))
            await self.log_action(ctx, "Clear Warnings", member)
            return
        if index < 1 or index > len(rows):
            return await self.send(ctx, error_embed(ctx, f"Index out of range (1-{len(rows)})."))
        warn_id = rows[index - 1]["id"]
        reason = rows[index - 1]["reason"]
        await self.db.execute("DELETE FROM warnings WHERE id = $1", warn_id)
        await self.send(ctx, success_embed(
            ctx, f"Warning **#{index}** for {member.mention} cleared.\n> {reason}"
        ))
        await self.log_action(ctx, "Clear Warning (Indexed)", member, f"Removed warn #{index}: {reason}")

    # CHANGED: Added aliases for easier warning removal
    @commands.command(aliases=["cw", "warnremove", "removewarn", "deletewarn"])
    @require("admin")
    async def clearwarns(self, ctx, member: discord.Member, index: Optional[int] = None):
        await self._clearwarns_logic(ctx, member, index)

    # ---------------------------------------------------------------
    #  MODSTATS
    # ---------------------------------------------------------------
    @commands.group(name="modstats", invoke_without_command=True)
    @require("admin")
    async def modstats_group(self, ctx):
        if not self.modstats:
            return await self.send(ctx, make_embed(ctx, "Moderation Stats", "No stats recorded yet.", MAIN_COLOR))
        sorted_stats = sorted(
            self.modstats.items(),
            key=lambda x: sum(x[1].values()),
            reverse=True
        )
        desc = ""
        for i, (uid, cmds) in enumerate(sorted_stats[:10], 1):
            member = ctx.guild.get_member(int(uid))
            name = member.mention if member else f"<@{uid}>"
            total = sum(cmds.values())
            cmd_list = ", ".join(f"{cmd}: {count}" for cmd, count in sorted(cmds.items(), key=lambda x: x[1], reverse=True)[:3])
            desc += f"**{i}.** {name} — **{total}** total\n> {cmd_list}\n"
        embed = make_embed(ctx, f"{EMOJI_CHART} Moderation Stats — Top 10", desc, MAIN_COLOR)
        await self.send(ctx, embed)

    @modstats_group.command(name="add")
    @require("admin")
    async def modstats_add(self, ctx, command_name: str, count: int, user: discord.Member = None):
        target = user or ctx.author
        uid = str(target.id)
        if uid not in self.modstats:
            self.modstats[uid] = {}
        self.modstats[uid][command_name] = self.modstats[uid].get(command_name, 0) + count
        await self.db.execute(
            "INSERT INTO modstats (user_id, command, count) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id, command) DO UPDATE SET count = modstats.count + $3",
            target.id, command_name, count
        )
        await self.send(ctx, success_embed(ctx, f"Added **{count}** to `{command_name}` for {target.mention}."))

    @modstats_group.command(name="remove")
    @require("admin")
    async def modstats_remove(self, ctx, command_name: str, count: int, user: discord.Member = None):
        target = user or ctx.author
        uid = str(target.id)
        if uid not in self.modstats or command_name not in self.modstats[uid]:
            return await self.send(ctx, error_embed(ctx, "No stats found for that command/user."))
        self.modstats[uid][command_name] = max(0, self.modstats[uid].get(command_name, 0) - count)
        if self.modstats[uid][command_name] == 0:
            del self.modstats[uid][command_name]
        if not self.modstats[uid]:
            del self.modstats[uid]
        await self.db.execute(
            "UPDATE modstats SET count = GREATEST(count - $3, 0) WHERE user_id = $1 AND command = $2",
            target.id, command_name, count
        )
        await self.send(ctx, success_embed(ctx, f"Removed **{count}** from `{command_name}` for {target.mention}."))

    @modstats_group.command(name="user")
    @require("admin")
    async def modstats_user(self, ctx, user: discord.Member):
        uid = str(user.id)
        if uid not in self.modstats:
            return await self.send(ctx, make_embed(ctx, f"{user.display_name}'s Stats", "No stats recorded.", MAIN_COLOR))
        cmds = self.modstats[uid]
        total = sum(cmds.values())
        desc = "\n".join(f"• `{cmd}`: **{count}**" for cmd, count in sorted(cmds.items(), key=lambda x: x[1], reverse=True))
        embed = make_embed(ctx, f"{EMOJI_CHART} {user.display_name}'s Stats", f"**Total:** {total}\n{desc}", MAIN_COLOR)
        embed.set_thumbnail(url=user.display_avatar.url)
        await self.send(ctx, embed)

    # ---------------------------------------------------------------
    #  NICKNAME
    # ---------------------------------------------------------------
    @commands.command(aliases=["nick"])
    @require("trial_moderator")
    async def nickname(self, ctx, member: discord.Member, *, nick: str = None):
        if nick and len(nick) > 32:
            return await self.send(ctx, error_embed(ctx, "Nickname must be **32 characters** or fewer."))
        try:
            await member.edit(nick=nick)
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack Manage Nicknames permission or cannot edit this member."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed to change nickname: {e}"))
        if nick:
            await self.send(ctx, success_embed(ctx, f"{member.mention} → **{nick}**"))
        else:
            await self.send(ctx, success_embed(ctx, f"Nickname reset for {member.mention}."))

    # ---------------------------------------------------------------
    #  SAY
    # ---------------------------------------------------------------
    @commands.command(aliases=["echo"])
    @require("admin")
    async def say(self, ctx, channel: discord.TextChannel, *, text: str):
        try:
            await channel.send(text)
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I cannot send messages in that channel."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed to send message: {e}"))
        await self.send(ctx, success_embed(ctx, f"Message sent to {channel.mention}."))

    # ---------------------------------------------------------------
    #  MUTE / UNMUTE
    # ---------------------------------------------------------------
    async def _mute_logic(self, ctx, member: discord.Member, duration: str, reason: str = "No reason"):
        actor = self._get_actor(ctx)
        try:
            await self.check_mod_permission(ctx, "trial_moderator")
        except commands.CheckFailure as e:
            return await self.send(ctx, error_embed(ctx, str(e)))
        ok, err = self.can_target(actor, member)
        if not ok:
            return await self.send(ctx, error_embed(ctx, err))

        seconds = parse_time(duration)
        if seconds <= 0:
            return await self.send(ctx, error_embed(
                ctx, "Invalid duration. Use `10s`, `5m`, `2h`, `1d` etc."
            ))
        if seconds > 28 * 86400:
            return await self.send(ctx, error_embed(ctx, "Maximum timeout is **28 days**."))
        until = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
        try:
            await member.timeout(until, reason=reason)
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "Missing **Moderate Members** permission."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed to timeout: {e}"))
        embed = make_embed(
            ctx, f"{EMOJI_MUTE} Member Muted",
            (
                f"**User:** {member.mention}\n"
                f"**Duration:** `{fmt_duration(seconds)}`\n"
                f"**Expires:** {discord.utils.format_dt(until, style='R')} "
                f"({discord.utils.format_dt(until, style='f')})\n"
                f"**Reason:** {reason}"
            ),
            WARN_COLOR,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.send(ctx, embed)
        await self.log_action(ctx, "Mute", member, reason, f"Duration: {fmt_duration(seconds)}")
        await self._record_modstat(actor.id, "mute")

    @commands.command(aliases=["m"])
    @require("trial_moderator")
    async def mute(self, ctx, member: discord.Member, duration: str, *, reason: str = "No reason"):
        await self._mute_logic(ctx, member, duration, reason)

    @app_commands.command(name="mute", description="Timeout a member (e.g. 10m, 2h, 1d — max 28d)")
    async def slash_mute(self, interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason"):
        await self._mute_logic(interaction, member, duration, reason)

    async def _unmute_logic(self, ctx, member: discord.Member):
        if not member.is_timed_out():
            return await self.send(ctx, error_embed(ctx, f"{member.mention} is not timed out."))
        mod = ctx.author if not isinstance(ctx, discord.Interaction) else ctx.user
        try:
            await member.timeout(None, reason=f"Unmuted by {mod}")
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "Missing **Moderate Members** permission."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed: {e}"))
        embed = make_embed(
            ctx, f"{EMOJI_SUCCESS} Unmuted",
            f"{member.mention}'s timeout has been removed.", WIN_COLOR
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.send(ctx, embed)
        await self.log_action(ctx, "Unmute", member)
        await self._record_modstat(mod.id, "unmute")

    @commands.command(aliases=["um"])
    @require("trial_moderator")
    async def unmute(self, ctx, member: discord.Member):
        await self._unmute_logic(ctx, member)

    @app_commands.command(name="unmute", description="Remove a member's timeout early")
    async def slash_unmute(self, interaction: discord.Interaction, member: discord.Member):
        await self._unmute_logic(interaction, member)

    # ---------------------------------------------------------------
    #  KICK / BAN / UNBAN
    # ---------------------------------------------------------------
    async def _kick_logic(self, ctx, member: discord.Member, reason: str = "No reason"):
        actor = self._get_actor(ctx)
        try:
            await self.check_mod_permission(ctx, "moderator")
        except commands.CheckFailure as e:
            return await self.send(ctx, error_embed(ctx, str(e)))
        ok, err = self.can_target(actor, member)
        if not ok:
            return await self.send(ctx, error_embed(ctx, err))
        try:
            await member.kick(reason=reason)
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack Kick Members permission."))
        except discord.HTTPException as e:
            logger.error("Kick HTTP error: %s", e)
            return await self.send(ctx, error_embed(ctx, f"Kick failed: {e}"))
        embed = make_embed(
            ctx, f"{EMOJI_DELETE} Member Kicked",
            f"**User:** {member.mention}\n**Reason:** {reason}", LOSS_COLOR
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.send(ctx, embed)
        await self.log_action(ctx, "Kick", member, reason)
        await self._manual_dm(member, "Kick", reason)
        await self._record_modstat(actor.id, "kick")

    @commands.command(aliases=["k"])
    @require("moderator")
    async def kick(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        await self._kick_logic(ctx, member, reason)

    @app_commands.command(name="kick", description="Kick a member from the server")
    async def slash_kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
        await self._kick_logic(interaction, member, reason)

    async def _ban_logic(self, ctx, member: discord.Member, reason: str = "No reason"):
        actor = self._get_actor(ctx)
        try:
            await self.check_mod_permission(ctx, "admin")
        except commands.CheckFailure as e:
            return await self.send(ctx, error_embed(ctx, str(e)))
        ok, err = self.can_target(actor, member)
        if not ok:
            return await self.send(ctx, error_embed(ctx, err))
        try:
            await member.ban(reason=reason)
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack Ban Members permission."))
        except discord.HTTPException as e:
            logger.error("Ban HTTP error: %s", e)
            return await self.send(ctx, error_embed(ctx, f"Ban failed: {e}"))
        embed = make_embed(
            ctx, f"{EMOJI_DELETE} Member Banned",
            f"**User:** {member.mention}\n**Reason:** {reason}", LOSS_COLOR
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.send(ctx, embed)
        await self.log_action(ctx, "Ban", member, reason)
        await self._manual_dm(member, "Ban", reason)
        await self._record_modstat(actor.id, "ban")

    @commands.command(aliases=["b"])
    @require("admin")
    async def ban(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        await self._ban_logic(ctx, member, reason)

    @app_commands.command(name="ban", description="Ban a member from the server")
    async def slash_ban(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
        await self._ban_logic(interaction, member, reason)

    async def _unban_logic(self, ctx, user: discord.User, reason: str = "No reason"):
        try:
            bans = [e async for e in ctx.guild.bans()]
            entry = discord.utils.get(bans, user__id=user.id)
            if not entry:
                return await self.send(ctx, error_embed(ctx, "That user is not banned."))
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack Ban Members permission to view bans."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed to fetch bans: {e}"))
        try:
            await ctx.guild.unban(user, reason=reason)
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack Ban Members permission."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed to unban: {e}"))
        await self.send(ctx, success_embed(ctx, f"Unbanned **{user}** (`{user.id}`)"))
        await self.log_action(ctx, "Unban", user, reason)

    @commands.command(aliases=["ub"])
    @require("admin")
    async def unban(self, ctx, user: discord.User, *, reason: str = "No reason"):
        await self._unban_logic(ctx, user, reason)

    @app_commands.command(name="unban", description="Unban a user by Discord ID")
    async def slash_unban(self, interaction: discord.Interaction, user_id: str, reason: str = "No reason"):
        try:
            user = await self.bot.fetch_user(int(user_id))
        except (ValueError, discord.NotFound):
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Invalid user ID or user not found."), ephemeral=True
            )
        await self._unban_logic(interaction, user, reason)

    # ---------------------------------------------------------------
    #  SOFTBAN / HACKBAN
    # ---------------------------------------------------------------
    @commands.command(aliases=["sb"])
    @require("admin")
    async def softban(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        actor = ctx.author
        try:
            await self.check_mod_permission(ctx, "admin")
        except commands.CheckFailure as e:
            return await self.send(ctx, error_embed(ctx, str(e)))
        ok, err = self.can_target(actor, member)
        if not ok:
            return await self.send(ctx, error_embed(ctx, err))
        try:
            await member.ban(delete_message_days=7, reason=f"Softban: {reason}")
            await ctx.guild.unban(member, reason="Softban unban")
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack Ban Members permission."))
        except discord.HTTPException as e:
            logger.error("Softban failed: %s", e)
            return await self.send(ctx, error_embed(ctx, f"Softban failed: {e}"))
        embed = make_embed(
            ctx, f"{EMOJI_DELETE} Softbanned",
            f"**User:** {member.mention}\n**Reason:** {reason}\n> Messages from the past 7 days deleted.", WARN_COLOR
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        await self.send(ctx, embed)
        await self.log_action(ctx, "Softban", member, reason)
        await self._record_modstat(actor.id, "softban")

    @commands.command(aliases=["hb"])
    @require("admin")
    async def hackban(self, ctx, user_id: int, *, reason: str = "No reason"):
        try:
            user = await self.bot.fetch_user(user_id)
        except discord.NotFound:
            return await self.send(ctx, error_embed(ctx, "User not found."))
        try:
            await ctx.guild.ban(discord.Object(id=user_id), reason=reason)
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack Ban Members permission."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed: {e}"))
        await self.send(ctx, make_embed(
            ctx, f"{EMOJI_DELETE} Hackbanned",
            f"**User:** {user.mention} (`{user.id}`)\n**Reason:** {reason}", LOSS_COLOR
        ))
        await self.log_action(ctx, "Hackban", user, reason)

    # ---------------------------------------------------------------
    #  JAIL / UNJAIL
    # ---------------------------------------------------------------
    async def _jail_logic(self, ctx, member: discord.Member, reason: str = "No reason"):
        actor = self._get_actor(ctx)
        try:
            await self.check_mod_permission(ctx, "admin")
        except commands.CheckFailure as e:
            return await self.send(ctx, error_embed(ctx, str(e)))
        ok, err = self.can_target(actor, member)
        if not ok:
            return await self.send(ctx, error_embed(ctx, err))

        guild = ctx.guild
        jail_role = guild.get_role(self.jail_role_id)
        if jail_role is None:
            return await self.send(ctx, error_embed(ctx, "Jail role is not configured."))
        if jail_role in member.roles:
            return await self.send(ctx, error_embed(ctx, f"{member.mention} is already jailed."))

        old_roles = [role for role in member.roles if role != guild.default_role and role != jail_role]

        try:
            await member.edit(roles=[jail_role], reason=f"Jailed by {actor}: {reason}")
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack Manage Roles permission."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed to jail: {e}"))

        try:
            await self.db.execute(
                """
                INSERT INTO jail
                (guild_id, user_id, role_ids, channel_id, reason, moderator_id, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (guild_id, user_id)
                DO UPDATE SET
                    role_ids = EXCLUDED.role_ids,
                    channel_id = EXCLUDED.channel_id,
                    reason = EXCLUDED.reason,
                    moderator_id = EXCLUDED.moderator_id,
                    created_at = EXCLUDED.created_at
                """,
                guild.id, member.id,
                [r.id for r in old_roles],
                self.jail_channel_id,
                reason,
                actor.id,
                int(time.time())
            )
        except Exception:
            await member.edit(roles=old_roles, reason="Jail state save failed, restoring roles")
            logger.exception("Failed to persist jail state for guild=%s user=%s", guild.id, member.id)
            return await self.send(ctx, error_embed(ctx, "Failed to save jail state. Roles were restored."))

        # Send a message to the jail channel if configured
        if self.jail_channel_id:
            jail_channel = guild.get_channel(self.jail_channel_id)
            if jail_channel:
                try:
                    await jail_channel.send(f"{member.mention} has been jailed by {actor.mention}. Reason: {reason}")
                except discord.Forbidden:
                    logger.warning("Cannot send to jail channel %s", self.jail_channel_id)
                except discord.HTTPException as e:
                    logger.error("Failed to send to jail channel: %s", e)

        await self.send(ctx, make_embed(
            ctx, f"{EMOJI_LOCK} Jailed",
            f"**User:** {member.mention}\n**Reason:** {reason}",
            0x2C3E50,
        ))
        await self.log_action(ctx, "Jail", member, reason)
        await self._record_modstat(actor.id, "jail")

    @commands.command(aliases=["j"])
    @require("admin")
    async def jail(self, ctx, member: discord.Member, *, reason: str = "No reason"):
        await self._jail_logic(ctx, member, reason)

    @commands.command(aliases=["uj"])
    @require("admin")
    async def unjail(self, ctx, member: discord.Member):
        guild = ctx.guild
        row = await self.db.fetchrow(
            "SELECT role_ids, channel_id FROM jail WHERE guild_id = $1 AND user_id = $2",
            guild.id, member.id
        )
        if not row:
            return await self.send(ctx, error_embed(ctx, "User is not jailed."))

        roles = []
        for role_id in row["role_ids"]:
            role = guild.get_role(role_id)
            if role:
                roles.append(role)

        try:
            await member.edit(roles=roles)
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack Manage Roles permission to restore roles."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed to restore roles: {e}"))

        await self.db.execute("DELETE FROM jail WHERE guild_id = $1 AND user_id = $2", guild.id, member.id)

        await self.send(ctx, make_embed(
            ctx, f"{EMOJI_UNLOCK} Unjailed",
            f"{member.mention} has been released.", WIN_COLOR
        ))
        await self.log_action(ctx, "Unjail", member)

    # ---------------------------------------------------------------
    #  AUDIT LOG
    # ---------------------------------------------------------------
    async def _audit_logic(self, ctx, count: int):
        if not ctx.guild.me.guild_permissions.view_audit_log:
            return await self.send(ctx, error_embed(ctx, "I lack **View Audit Log** permission."))
        count = max(1, min(count, 25))
        try:
            entries = [e async for e in ctx.guild.audit_logs(limit=count)]
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack **View Audit Log** permission."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed to fetch audit log: {e}"))
        if not entries:
            return await self.send(ctx, error_embed(ctx, "No audit log entries found."))
        embed = discord.Embed(
            title=f"{EMOJI_SCROLL} Audit Log — last {count}",
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        for i, e in enumerate(entries, 1):
            action = str(e.action).replace("_", " ").title()
            embed.add_field(
                name=f"{i}. {action}",
                value=(
                    f"**By:** {e.user or 'Unknown'}\n"
                    f"**Target:** {e.target or 'Unknown'}\n"
                    f"**Reason:** {e.reason or 'None'}\n"
                    f"**When:** {discord.utils.format_dt(e.created_at, style='f')}"
                ),
                inline=False,
            )
        await self.send(ctx, embed)

    @commands.command(name="audit", aliases=["auditlog"])
    @require("admin")
    async def audit(self, ctx, count: int = 5):
        await self._audit_logic(ctx, count)

    # ---------------------------------------------------------------
    #  STAFF MANAGEMENT (guild‑scoped)
    # ---------------------------------------------------------------
    @commands.group(name="staff", invoke_without_command=True)
    @require("head_admin")
    async def staff_group(self, ctx):
        embed = make_embed(ctx, "Staff Management", (
            "`staff add <role> @user` – Add a user to a staff role\n"
            "`staff remove <role> @user` – Remove a user from a staff role\n"
            "`staff list` – Show all staff members (by Discord roles)\n"
            "Roles: founder, manager, head_admin, admin, bot_manager, event_manager, moderator, trial_moderator"
        ), MAIN_COLOR)
        await self.send(ctx, embed)

    @staff_group.command(name="add")
    @require("founder")
    async def staff_add(self, ctx, role: str, user: discord.Member):
        role = role.lower()
        plural = STAFF_ROLE_MAP.get(role)
        if not plural:
            return await self.send(ctx, error_embed(ctx, f"Invalid role `{role}`. Use one of: {', '.join(set(STAFF_ROLE_MAP.keys()))}"))
        adder_level = PERM_LEVELS[self.get_level(ctx.author.id, ctx.guild.id)]
        target_level = PERM_LEVELS[role] if role in PERM_LEVELS else 0
        if adder_level <= target_level:
            return await self.send(ctx, error_embed(ctx, "You cannot add someone to an equal or higher role."))
        gid = ctx.guild.id
        uid = str(user.id)
        if uid in self.staff_cache[gid][plural]:
            return await self.send(ctx, error_embed(ctx, f"{user.mention} is already a **{role}**."))
        self.staff_cache[gid][plural].append(uid)
        await self.db.execute(
            "INSERT INTO staff (guild_id, user_id, role) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
            gid, user.id, role
        )
        await self.send(ctx, success_embed(ctx, f"{user.mention} added to **{role.replace('_', ' ').title()}**."))

        role_id = self.staff_role_ids.get(role)
        disc_role = ctx.guild.get_role(role_id) if role_id else None
        if disc_role:
            try:
                await user.add_roles(disc_role)
            except discord.Forbidden:
                logger.warning("Cannot add role %s to %s in guild %s", role_id, user.id, gid)
            except discord.HTTPException as e:
                logger.error("Failed to add role: %s", e)

    @staff_group.command(name="remove")
    @require("founder")
    async def staff_remove(self, ctx, role: str, user: discord.Member):
        role = role.lower()
        plural = STAFF_ROLE_MAP.get(role)
        if not plural:
            return await self.send(ctx, error_embed(ctx, f"Invalid role `{role}`."))
        gid = ctx.guild.id
        uid = str(user.id)
        if uid not in self.staff_cache[gid][plural]:
            return await self.send(ctx, error_embed(ctx, f"{user.mention} is not a **{role}**."))
        self.staff_cache[gid][plural].remove(uid)
        await self.db.execute(
            "DELETE FROM staff WHERE guild_id = $1 AND user_id = $2 AND role = $3",
            gid, user.id, role
        )
        await self.send(ctx, success_embed(ctx, f"{user.mention} removed from **{role.replace('_', ' ').title()}**."))

        role_id = self.staff_role_ids.get(role)
        disc_role = ctx.guild.get_role(role_id) if role_id else None
        if disc_role:
            try:
                await user.remove_roles(disc_role)
            except discord.Forbidden:
                logger.warning("Cannot remove role %s from %s in guild %s", role_id, user.id, gid)
            except discord.HTTPException as e:
                logger.error("Failed to remove role: %s", e)

    @staff_group.command(name="list")
    @require("admin")
    async def staff_list(self, ctx):
        embed = make_embed(ctx, "Staff Members", "", MAIN_COLOR)
        order = [
            ("Admin", self.staff_role_ids.get("admin")),
            ("Moderator", self.staff_role_ids.get("moderator")),
            ("Trial Moderator", self.staff_role_ids.get("trial_moderator")),
            ("Event Manager", self.staff_role_ids.get("event_manager")),
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

    # ---------------------------------------------------------------
    #  MASS MODERATION
    # ---------------------------------------------------------------
    MAX_MASS_ACTION = 50

    class ConfirmView(discord.ui.View):
        def __init__(self, ctx, action, targets):
            super().__init__(timeout=60)
            self.ctx = ctx
            self.action = action
            self.targets = targets
            self.confirmed = False

        @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.confirmed = True
            self.stop()

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.stop()

    @commands.command(name="masskick")
    @require("admin")
    async def masskick(self, ctx, members: commands.Greedy[discord.Member], *, reason: str = "No reason"):
        if len(members) > self.MAX_MASS_ACTION:
            return await self.send(ctx, error_embed(ctx, f"Maximum {self.MAX_MASS_ACTION} targets allowed."))
        if len(members) > 10 and reason == "No reason":
            return await self.send(ctx, error_embed(ctx, "A reason is required for mass actions over 10 targets."))

        valid = []
        for m in members:
            ok, _ = self.can_target(ctx.author, m)
            if ok:
                valid.append(m)
        if not valid:
            return await self.send(ctx, error_embed(ctx, "No valid targets."))

        if len(valid) > 10:
            view = self.ConfirmView(ctx, "masskick", valid)
            await self.send(ctx, make_embed(ctx, "Confirm Mass Kick", f"Kick {len(valid)} members?", WARN_COLOR), view=view)
            await view.wait()
            if not view.confirmed:
                return await self.send(ctx, error_embed(ctx, "Mass kick cancelled."))

        msg = await self.send(ctx, make_embed(ctx, f"{EMOJI_COOLDOWN} Mass Kick", f"Kicking {len(valid)} members…", WARN_COLOR))
        done = failed = 0
        for m in valid:
            try:
                await m.kick(reason=reason)
                done += 1
            except discord.Forbidden:
                logger.warning("Cannot kick %s: missing permissions", m.id)
                failed += 1
            except discord.HTTPException as e:
                logger.error("Failed to kick %s: %s", m.id, e)
                failed += 1
            await asyncio.sleep(0.5)
        result_embed = make_embed(
            ctx, f"{EMOJI_SUCCESS} Mass Kick Complete",
            f"{EMOJI_SUCCESS} Kicked: **{done}**\n{EMOJI_WRONG} Failed: **{failed}**", WIN_COLOR
        )
        if msg:
            try:
                await msg.edit(embed=result_embed)
            except Exception:
                pass
        else:
            await self.send(ctx, result_embed)

    @commands.command(name="massban")
    @require("admin")
    async def massban(self, ctx, members: commands.Greedy[discord.Member], *, reason: str = "No reason"):
        if len(members) > self.MAX_MASS_ACTION:
            return await self.send(ctx, error_embed(ctx, f"Maximum {self.MAX_MASS_ACTION} targets allowed."))
        if len(members) > 10 and reason == "No reason":
            return await self.send(ctx, error_embed(ctx, "A reason is required for mass actions over 10 targets."))

        valid = []
        for m in members:
            ok, _ = self.can_target(ctx.author, m)
            if ok:
                valid.append(m)
        if not valid:
            return await self.send(ctx, error_embed(ctx, "No valid targets."))

        if len(valid) > 10:
            view = self.ConfirmView(ctx, "massban", valid)
            await self.send(ctx, make_embed(ctx, "Confirm Mass Ban", f"Ban {len(valid)} members?", WARN_COLOR), view=view)
            await view.wait()
            if not view.confirmed:
                return await self.send(ctx, error_embed(ctx, "Mass ban cancelled."))

        msg = await self.send(ctx, make_embed(ctx, f"{EMOJI_COOLDOWN} Mass Ban", f"Banning {len(valid)} members…", WARN_COLOR))
        done = failed = 0
        for m in valid:
            try:
                await m.ban(reason=reason)
                done += 1
            except discord.Forbidden:
                logger.warning("Cannot ban %s: missing permissions", m.id)
                failed += 1
            except discord.HTTPException as e:
                logger.error("Failed to ban %s: %s", m.id, e)
                failed += 1
            await asyncio.sleep(0.5)
        result_embed = make_embed(
            ctx, f"{EMOJI_SUCCESS} Mass Ban Complete",
            f"{EMOJI_SUCCESS} Banned: **{done}**\n{EMOJI_WRONG} Failed: **{failed}**", WIN_COLOR
        )
        if msg:
            try:
                await msg.edit(embed=result_embed)
            except Exception:
                pass
        else:
            await self.send(ctx, result_embed)

    @commands.command(name="massmute")
    @require("admin")
    async def massmute(self, ctx, duration: str, members: commands.Greedy[discord.Member], *, reason: str = "No reason"):
        if len(members) > self.MAX_MASS_ACTION:
            return await self.send(ctx, error_embed(ctx, f"Maximum {self.MAX_MASS_ACTION} targets allowed."))
        seconds = parse_time(duration)
        if seconds <= 0:
            return await self.send(ctx, error_embed(ctx, "Invalid duration. Use `10m`, `1h`, `2d` etc."))
        valid = []
        for m in members:
            ok, _ = self.can_target(ctx.author, m)
            if ok:
                valid.append(m)
        if not valid:
            return await self.send(ctx, error_embed(ctx, "No valid targets."))

        if len(valid) > 10:
            view = self.ConfirmView(ctx, "massmute", valid)
            await self.send(ctx, make_embed(ctx, "Confirm Mass Mute", f"Mute {len(valid)} members?", WARN_COLOR), view=view)
            await view.wait()
            if not view.confirmed:
                return await self.send(ctx, error_embed(ctx, "Mass mute cancelled."))

        until = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
        msg = await self.send(ctx, make_embed(
            ctx, f"{EMOJI_COOLDOWN} Mass Mute", f"Timing out {len(valid)} members for {fmt_duration(seconds)}…", WARN_COLOR
        ))
        done = failed = 0
        for m in valid:
            try:
                await m.timeout(until, reason=reason)
                done += 1
            except discord.Forbidden:
                logger.warning("Cannot mute %s: missing permissions", m.id)
                failed += 1
            except discord.HTTPException as e:
                logger.error("Failed to mute %s: %s", m.id, e)
                failed += 1
            await asyncio.sleep(0.5)
        result_embed = make_embed(
            ctx, f"{EMOJI_SUCCESS} Mass Mute Complete",
            (
                f"**Duration:** `{fmt_duration(seconds)}`\n"
                f"**Expires:** {discord.utils.format_dt(until, style='R')}\n"
                f"{EMOJI_SUCCESS} Muted: **{done}**\n{EMOJI_WRONG} Failed: **{failed}**"
            ),
            WIN_COLOR,
        )
        if msg:
            try:
                await msg.edit(embed=result_embed)
            except Exception:
                pass
        else:
            await self.send(ctx, result_embed)

    @commands.command(name="massunban")
    @require("admin")
    async def massunban(self, ctx, *user_ids: int):
        if not user_ids:
            return await self.send(ctx, error_embed(ctx, "Provide at least one user ID."))
        if len(user_ids) > self.MAX_MASS_ACTION:
            return await self.send(ctx, error_embed(ctx, f"Maximum {self.MAX_MASS_ACTION} targets allowed."))

        msg = await self.send(ctx, make_embed(ctx, f"{EMOJI_COOLDOWN} Mass Unban", f"Processing {len(user_ids)} IDs…", WARN_COLOR))
        done = failed = not_found = 0
        for uid in user_ids:
            try:
                await ctx.guild.unban(discord.Object(id=uid), reason=f"Mass unban by {ctx.author}")
                done += 1
            except discord.NotFound:
                not_found += 1
            except discord.Forbidden:
                logger.warning("Cannot unban %s: missing permissions", uid)
                failed += 1
            except discord.HTTPException as e:
                logger.error("Failed to unban %s: %s", uid, e)
                failed += 1
            await asyncio.sleep(0.5)
        result_embed = make_embed(
            ctx, f"{EMOJI_SUCCESS} Mass Unban Complete",
            f"{EMOJI_SUCCESS} Unbanned: **{done}**\n{EMOJI_QUESTION_MARK} Not found: **{not_found}**\n{EMOJI_WRONG} Failed: **{failed}**", WIN_COLOR
        )
        if msg:
            try:
                await msg.edit(embed=result_embed)
            except Exception:
                pass
        else:
            await self.send(ctx, result_embed)

    # ---------------------------------------------------------------
    #  MASS ROLE
    # ---------------------------------------------------------------
    @commands.command(name="massrole")
    @require("admin")
    async def massrole(self, ctx, action: str, role: discord.Role, members: commands.Greedy[discord.Member]):
        action = action.lower()
        if action not in ("add", "remove"):
            return await self.send(ctx, error_embed(ctx, "Action must be `add` or `remove`."))
        if not members:
            return await self.send(ctx, error_embed(ctx, "Mention at least one member."))
        if len(members) > self.MAX_MASS_ACTION:
            return await self.send(ctx, error_embed(ctx, f"Maximum {self.MAX_MASS_ACTION} targets allowed."))
        msg = await self.send(ctx, make_embed(
            ctx, f"{EMOJI_COOLDOWN} Mass Role", f"{'Adding' if action == 'add' else 'Removing'} role from {len(members)} members…", WARN_COLOR
        ))
        done = failed = 0
        for m in members:
            try:
                if action == "add":
                    await m.add_roles(role)
                else:
                    await m.remove_roles(role)
                done += 1
            except discord.Forbidden:
                logger.warning("Cannot %s role %s to %s: missing permissions", action, role.id, m.id)
                failed += 1
            except discord.HTTPException as e:
                logger.error("Failed to %s role %s to %s: %s", action, role.id, m.id, e)
                failed += 1
            await asyncio.sleep(0.3)
        result_embed = make_embed(
            ctx, f"{EMOJI_SUCCESS} Mass Role Complete",
            f"**Role:** {role.mention}\n{EMOJI_SUCCESS} Done: **{done}**\n{EMOJI_WRONG} Failed: **{failed}**", WIN_COLOR
        )
        if msg:
            try:
                await msg.edit(embed=result_embed)
            except Exception:
                pass
        else:
            await self.send(ctx, result_embed)

    # ---------------------------------------------------------------
    #  TEMPROLE
    # ---------------------------------------------------------------
    @commands.command(name="temprole")
    @require("admin")
    async def temprole(self, ctx, member: discord.Member, role: discord.Role, duration: str):
        seconds = parse_time(duration)
        if seconds <= 0:
            return await self.send(ctx, error_embed(ctx, "Invalid duration."))
        try:
            await member.add_roles(role)
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack Manage Roles permission."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed to add role: {e}"))
        expires = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
        await self.send(ctx, make_embed(
            ctx, f"{EMOJI_SUCCESS} Temp Role Assigned",
            (
                f"**User:** {member.mention}\n"
                f"**Role:** {role.mention}\n"
                f"**Duration:** `{fmt_duration(seconds)}`\n"
                f"**Expires:** {discord.utils.format_dt(expires, style='R')}"
            ),
            WIN_COLOR,
        ))

        async def _remove():
            await asyncio.sleep(seconds)
            try:
                await member.remove_roles(role, reason="Temprole expired")
            except discord.Forbidden:
                logger.warning("Cannot remove expired temp role %s from %s", role.id, member.id)
            except discord.HTTPException as e:
                logger.error("Failed to remove expired temp role %s from %s: %s", role.id, member.id, e)

        self.bot.loop.create_task(_remove())

    # ---------------------------------------------------------------
    #  INFO COMMANDS
    # ---------------------------------------------------------------
    @commands.command(aliases=["ui", "whois"])
    async def userinfo(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        roles = [r.mention for r in reversed(member.roles) if r != ctx.guild.default_role]
        roles_ = (
            " ".join(roles[:10]) + (f" +{len(roles)-10} more" if len(roles) > 10 else "")
            if roles else "None"
        )
        status_map = {
            discord.Status.online: f"{EMOJI_ONLINE} Online",
            discord.Status.idle: f"{EMOJI_IDLE} Idle",
            discord.Status.dnd: f"{EMOJI_DND} Do Not Disturb",
            discord.Status.offline: f"{EMOJI_OFFLINE} Offline",
        }
        status = status_map.get(member.status, "Unknown")
        activity = "None"
        if member.activity:
            if isinstance(member.activity, discord.Game):
                activity = f"Playing **{member.activity.name}**"
            elif isinstance(member.activity, discord.Streaming):
                activity = f"Streaming **{member.activity.name}**"
            elif isinstance(member.activity, discord.Spotify):
                activity = f"{EMOJI_NOTE} **{member.activity.title}** by {', '.join(member.activity.artists)}"
            else:
                activity = member.activity.name or "Unknown"
        flags = [f.name.replace("_", " ").title() for f, v in member.public_flags if v]
        badges = ", ".join(flags) if flags else "None"
        boosting = f"Yes {discord.utils.format_dt(member.premium_since, style='R')}" if member.premium_since else "No"

        embed = discord.Embed(
            color=member.color if member.color.value else MAIN_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=f"{member} — {member.id}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name=f"{EMOJI_CALENDAR} Joined Discord", value=discord.utils.format_dt(member.created_at, style="R"), inline=True)
        embed.add_field(name=f"{EMOJI_JOIN_SHIELD} Joined Server", value=discord.utils.format_dt(member.joined_at, style="R") if member.joined_at else "Unknown", inline=True)
        embed.add_field(name=f"{EMOJI_OWNER} Top Role", value=member.top_role.mention, inline=True)
        embed.add_field(name=f"{EMOJI_STATS} Status", value=status, inline=True)
        embed.add_field(name=f"{EMOJI_GAME_CONTROLLER} Activity", value=activity, inline=True)
        embed.add_field(name=f"{EMOJI_BOOST} Boosting", value=boosting, inline=True)
        embed.add_field(name=f"{EMOJI_GOLD_MEDAL} Badges", value=badges, inline=False)
        embed.add_field(name=f"{EMOJI_ROLE} Roles [{len(roles)}]", value=roles_, inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await self.send(ctx, embed)

    @commands.command(aliases=["si", "guildinfo"])
    async def serverinfo(self, ctx):
        guild = ctx.guild
        humans = sum(1 for m in guild.members if not m.bot)
        bots = guild.member_count - humans
        embed = discord.Embed(
            title=f"{guild.name} — {guild.id}",
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)
        embed.add_field(name=f"{EMOJI_CALENDAR} Created", value=discord.utils.format_dt(guild.created_at, style="R"), inline=True)
        embed.add_field(name=f"{EMOJI_USER_ID} Members", value=f"{EMOJI_USER_ID} {humans} · {EMOJI_ROBOT} {bots}", inline=True)
        embed.add_field(name=f"{EMOJI_MESSAGE} Channels",
            value=(
                f"{EMOJI_MESSAGE} {len(guild.text_channels)} text · "
                f"{EMOJI_SPEAKER} {len(guild.voice_channels)} voice · "
                f"{EMOJI_FILE} {len(guild.categories)} categories"
            ),
            inline=False,
        )
        embed.add_field(name=f"{EMOJI_BOOST} Boost", value=f"Level {guild.premium_tier} · {guild.premium_subscription_count} boosts", inline=True)
        embed.add_field(name=f"{EMOJI_LOCK} Verification", value=str(guild.verification_level).title(), inline=True)
        embed.add_field(name=f"{EMOJI_FIRE_ANIMATED} Emojis", value=f"{len(guild.emojis)}/{guild.emoji_limit}", inline=True)
        embed.add_field(name=f"{EMOJI_ROLE} Roles", value=str(len(guild.roles)), inline=True)
        if guild.features:
            embed.add_field(name=f"{EMOJI_SPARKLES} Features", value=", ".join(guild.features), inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await self.send(ctx, embed)

    @commands.command(aliases=["av", "pfp"])
    async def avatar(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(
            title=f"{member.display_name}'s Avatar",
            color=member.color if member.color.value else MAIN_COLOR,
        )
        embed.set_image(url=member.display_avatar.url)
        fmt = member.display_avatar
        links = (
            f"[PNG]({fmt.replace(format='png').url}) · "
            f"[JPG]({fmt.replace(format='jpg').url}) · "
            f"[WEBP]({fmt.replace(format='webp').url})"
        )
        if fmt.is_animated():
            links += f" · [GIF]({fmt.replace(format='gif').url})"
        embed.add_field(name=f"{EMOJI_DOWN} Download", value=links)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await self.send(ctx, embed)

    @commands.command(aliases=["userbanner"])
    async def banner(self, ctx, member: discord.Member = None):
        target = member or ctx.author
        fetched = await self.bot.fetch_user(target.id)
        if not fetched.banner:
            return await self.send(ctx, error_embed(ctx, f"{target.mention} has no banner."))
        embed = discord.Embed(title=f"{target.display_name}'s Banner", color=MAIN_COLOR)
        embed.set_image(url=fetched.banner.url)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await self.send(ctx, embed)

    @commands.command(aliases=["ri"])
    @require("moderator")
    async def roleinfo(self, ctx, role: discord.Role):
        perms = [p.replace("_", " ").title() for p, v in role.permissions if v]
        key_perms = {"Administrator", "Manage Guild", "Ban Members", "Kick Members", "Manage Channels", "Manage Roles", "Manage Messages", "Mention Everyone"}
        danger = [p for p in perms if p in key_perms]
        embed = discord.Embed(
            title=f"Role: {role.name} — {role.id}",
            color=role.color,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name=f"{EMOJI_COLORS} Color", value=str(role.color), inline=True)
        embed.add_field(name=f"{EMOJI_CALENDAR} Created", value=discord.utils.format_dt(role.created_at, style="R"), inline=True)
        embed.add_field(name=f"{EMOJI_USER_ID} Members", value=str(len(role.members)), inline=True)
        embed.add_field(name=f"{EMOJI_PINNED} Hoisted", value="Yes" if role.hoist else "No", inline=True)
        embed.add_field(name=f"{EMOJI_MESSAGE} Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        embed.add_field(name=f"{EMOJI_NUMBERS} Position", value=str(role.position), inline=True)
        if danger:
            embed.add_field(name=f"{EMOJI_WARN} Dangerous Perms", value=", ".join(danger), inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await self.send(ctx, embed)

    # ---------------------------------------------------------------
    #  POLL
    # ---------------------------------------------------------------
    @commands.command(name="poll")
    @require("moderator")
    async def poll(self, ctx, *, question: str):
        embed = discord.Embed(
            title=f"{EMOJI_STATS} Poll",
            description=question,
            color=INFO_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Poll by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        try:
            msg = await ctx.send(embed=embed)
            await msg.add_reaction(EMOJI_SUCCESS)
            await msg.add_reaction(EMOJI_WRONG)
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I cannot send messages or add reactions here."))
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Poll failed: {e}"))
        try:
            await ctx.message.delete()
        except Exception:
            pass

    # ---------------------------------------------------------------
    #  NUKE CHANNEL
    # ---------------------------------------------------------------
    @commands.command(name="nuke")
    @commands.is_owner()
    async def nuke(self, ctx, channel: discord.TextChannel = None):
        chan = channel or ctx.channel
        pos = chan.position
        try:
            new = await chan.clone(reason=f"Nuke by {ctx.author}")
            await new.edit(position=pos)
            await chan.delete()
            await new.send(
                embed=make_embed(
                    ctx,
                    f"{EMOJI_BOOM} Channel Nuked",
                    f"This channel was nuked by {ctx.author.mention}.",
                    LOSS_COLOR
                )
            )
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "I lack permissions to nuke this channel."))
        except discord.HTTPException as e:
            logger.error("Nuke failed: %s", e)
            return await self.send(ctx, error_embed(ctx, f"Nuke failed: {e}"))

    # ---------------------------------------------------------------
    #  EMOJI MANAGEMENT
    # ---------------------------------------------------------------
    def _build_emoji_list_embed(self, ctx, emojis, page_num: int, total_pages: int) -> discord.Embed:
        desc = "\n".join(f"{e} `:{e.name}:`" for e in emojis)
        embed = make_embed(ctx, f"{EMOJI_FIRE_ANIMATED} Server Emojis", desc, MAIN_COLOR)
        embed.set_footer(text=f"Page {page_num}/{total_pages} • {len(emojis)} shown")
        return embed

    async def _emojis_logic(self, ctx, animated: Optional[str] = None):
        guild = ctx.guild
        emojis = list(guild.emojis)
        if animated:
            al = animated.lower()
            if al in ("yes", "true", "1", "animated"):
                emojis = [e for e in emojis if e.animated]
            elif al in ("no", "false", "0", "static"):
                emojis = [e for e in emojis if not e.animated]
        if not emojis:
            return await self.send(ctx, error_embed(ctx, "No custom emojis found."))
        pages = [emojis[i:i+20] for i in range(0, len(emojis), 20)]
        if len(pages) == 1:
            return await self.send(ctx, self._build_emoji_list_embed(ctx, pages[0], 1, 1))
        view = self.EmojiListView(ctx, pages)
        await self.send(ctx, self._build_emoji_list_embed(ctx, pages[0], 1, len(pages)), view=view)

    @commands.command(name="emojis", aliases=["listemojis"])
    @require("moderator")
    async def emojis(self, ctx, animated: Optional[str] = None):
        await self._emojis_logic(ctx, animated)

    @app_commands.command(name="emojis", description="List all custom server emojis")
    async def slash_emojis(self, interaction: discord.Interaction, animated: Optional[str] = None):
        await self._emojis_logic(interaction, animated)

    async def _emojiinfo_logic(self, ctx, emoji: discord.Emoji):
        embed = make_embed(ctx, f"{emoji} Emoji Info", "", MAIN_COLOR)
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

    @app_commands.command(name="emojiinfo", description="Show info about a custom emoji")
    async def slash_emojiinfo(self, interaction: discord.Interaction, emoji: str):
        obj = await self._parse_emoji(interaction.guild, emoji)
        if not obj:
            return await interaction.response.send_message(embed=error_embed(interaction, "Invalid emoji."), ephemeral=True)
        await self._emojiinfo_logic(interaction, obj)

    async def _steal_logic(self, ctx, name: str = None, direct_url: str = None):
        guild = ctx.guild
        if not guild:
            return await self.send(ctx, error_embed(ctx, "Server only."))
        if not guild.me.guild_permissions.manage_emojis:
            return await self.send(ctx, error_embed(ctx, "I need **Manage Emojis** permission."))
        if not getattr(self.bot, "http_session", None):
            return await self.send(ctx, error_embed(ctx, "HTTP session not ready."))
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
                return await self.send(ctx, error_embed(ctx, "No custom emojis found in that message."))
            if len(matches) > 10:
                return await self.send(ctx, error_embed(ctx, "Too many emojis (max 10). Use `masssteal`."))
            if name:
                anim, ename, eid = matches[0]
                ext = "gif" if anim else "png"
                url = f"https://cdn.discordapp.com/emojis/{eid}.{ext}"
                async with self.bot.http_session.get(url) as resp:
                    if resp.status != 200:
                        return await self.send(ctx, error_embed(ctx, "Failed to download emoji."))
                    data = await resp.read()
                try:
                    emoji = await guild.create_custom_emoji(name=name, image=data)
                    await self.send(ctx, success_embed(ctx, f"Emoji {emoji} added as `:{name}:`"))
                except discord.HTTPException as e:
                    await self.send(ctx, error_embed(ctx, f"Failed: {e}"))
                return
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
                except discord.HTTPException as e:
                    await self.send(ctx, error_embed(ctx, f"Failed `:{ename}:` — {e}"))
                    return
            await self.send(ctx, success_embed(ctx, f"Stolen: {' '.join(added)}"))
        else:
            if not name:
                return await self.send(ctx, error_embed(ctx, "Provide a name for the new emoji."))
            if not direct_url:
                if isinstance(ctx, commands.Context) and ctx.message.attachments:
                    direct_url = ctx.message.attachments[0].url
                else:
                    return await self.send(ctx, error_embed(ctx, "Reply to a message, attach an image, or provide a URL."))
            async with self.bot.http_session.get(direct_url) as resp:
                if resp.status != 200:
                    return await self.send(ctx, error_embed(ctx, "Could not download image."))
                data = await resp.read()
            try:
                emoji = await guild.create_custom_emoji(name=name, image=data)
                await self.send(ctx, success_embed(ctx, f"Emoji {emoji} added!"))
            except discord.HTTPException as e:
                await self.send(ctx, error_embed(ctx, f"Failed: {e}"))

    @commands.command(name="esteal", aliases=["stealemoji", "se"])
    @require("moderator")
    async def steal(self, ctx, name: str = None):
        await self._steal_logic(ctx, name)

    @app_commands.command(name="steal", description="Steal an emoji (reply, attach, or URL)")
    async def slash_steal(self, interaction: discord.Interaction, name: str = None, image_url: str = None):
        await self._steal_logic(interaction, name, image_url)

    async def _masssteal_logic(self, ctx):
        guild = ctx.guild
        if not guild:
            return await self.send(ctx, error_embed(ctx, "Server only."))
        if not guild.me.guild_permissions.manage_emojis:
            return await self.send(ctx, error_embed(ctx, "I need **Manage Emojis** permission."))
        if not getattr(self.bot, "http_session", None):
            return await self.send(ctx, error_embed(ctx, "HTTP session not ready."))
        ref = None
        if isinstance(ctx, commands.Context) and ctx.message.reference:
            ref = ctx.message.reference.resolved
            if ref is None:
                try:
                    ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                except Exception:
                    pass
        if not ref:
            return await self.send(ctx, error_embed(ctx, "Reply to a message containing custom emojis."))
        matches = list(dict.fromkeys(re.findall(r"<(a?):(\w+):(\d+)>", ref.content)))
        if not matches:
            return await self.send(ctx, error_embed(ctx, "No custom emojis found."))
        if len(matches) > 20:
            return await self.send(ctx, error_embed(ctx, "Max 20 emojis at once."))
        prog = None
        if isinstance(ctx, discord.Interaction):
            await ctx.response.defer()
        else:
            prog = await self.send(ctx, make_embed(ctx, f"{EMOJI_COOLDOWN} Mass Steal", f"Stealing {len(matches)} emojis…", WARN_COLOR))
        added = []
        failed = 0
        for anim, ename, eid in matches:
            ext = "gif" if anim else "png"
            async with self.bot.http_session.get(f"https://cdn.discordapp.com/emojis/{eid}.{ext}") as resp:
                if resp.status != 200:
                    failed += 1
                    continue
                data = await resp.read()
            try:
                emoji = await guild.create_custom_emoji(name=ename, image=data)
                added.append(str(emoji))
            except discord.HTTPException:
                failed += 1
            await asyncio.sleep(0.3)
        result_text = (f"Stolen: {' '.join(added)}" if added else "No emojis added.") + (f"\n{EMOJI_WRONG} Failed: {failed}" if failed else "")
        result_embed = make_embed(ctx, f"{EMOJI_SUCCESS} Mass Steal Done", result_text, WIN_COLOR)
        if isinstance(ctx, discord.Interaction):
            try:
                await ctx.edit_original_response(embed=result_embed)
            except Exception:
                await self.send(ctx, result_embed)
        elif prog:
            try:
                await prog.edit(embed=result_embed)
            except Exception:
                await self.send(ctx, result_embed)
        else:
            await self.send(ctx, result_embed)

    @commands.command(name="masssteal", aliases=["ms"])
    @require("admin")
    async def masssteal(self, ctx):
        await self._masssteal_logic(ctx)

    @app_commands.command(name="masssteal", description="Steal every custom emoji from a replied message")
    async def slash_masssteal(self, interaction: discord.Interaction):
        await self._masssteal_logic(interaction)

    async def _renameemoji_logic(self, ctx, emoji: discord.Emoji, new_name: str):
        if emoji.guild != ctx.guild:
            return await self.send(ctx, error_embed(ctx, "That emoji isn't from this server."))
        old = emoji.name
        try:
            await emoji.edit(name=new_name)
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed: {e}"))
        await self.send(ctx, success_embed(ctx, f"Renamed {emoji} `:{old}:` → `:{new_name}:`"))

    @commands.command(name="renameemoji", aliases=["re"])
    @require("admin")
    async def renameemoji(self, ctx, emoji: discord.Emoji, *, new_name: str):
        await self._renameemoji_logic(ctx, emoji, new_name)

    @app_commands.command(name="renameemoji", description="Rename a custom emoji")
    async def slash_renameemoji(self, interaction: discord.Interaction, emoji: str, new_name: str):
        obj = await self._parse_emoji(interaction.guild, emoji)
        if not obj:
            return await interaction.response.send_message(embed=error_embed(interaction, "Invalid emoji."), ephemeral=True)
        await self._renameemoji_logic(interaction, obj, new_name)

    async def _changeemoji_logic(self, ctx, emoji: discord.Emoji, image_url: str = None):
        if emoji.guild != ctx.guild:
            return await self.send(ctx, error_embed(ctx, "That emoji isn't from this server."))
        if not getattr(self.bot, "http_session", None):
            return await self.send(ctx, error_embed(ctx, "HTTP session not ready."))
        if not image_url and isinstance(ctx, commands.Context) and ctx.message.attachments:
            image_url = ctx.message.attachments[0].url
        if not image_url:
            return await self.send(ctx, error_embed(ctx, "Provide an image URL or attachment."))
        async with self.bot.http_session.get(image_url) as resp:
            if resp.status != 200:
                return await self.send(ctx, error_embed(ctx, "Could not download image."))
            data = await resp.read()
        try:
            await emoji.edit(image=data)
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed: {e}"))
        await self.send(ctx, success_embed(ctx, f"Image of {emoji} updated."))

    @commands.command(name="changeemoji", aliases=["ce"])
    @require("admin")
    async def changeemoji(self, ctx, emoji: discord.Emoji, image_url: str = None):
        await self._changeemoji_logic(ctx, emoji, image_url)

    @app_commands.command(name="changeemoji", description="Change the image of a custom emoji")
    async def slash_changeemoji(self, interaction: discord.Interaction, emoji: str, image_url: str):
        obj = await self._parse_emoji(interaction.guild, emoji)
        if not obj:
            return await interaction.response.send_message(embed=error_embed(interaction, "Invalid emoji."), ephemeral=True)
        await self._changeemoji_logic(interaction, obj, image_url)

    async def _deleteemoji_logic(self, ctx, emoji: discord.Emoji):
        if emoji.guild != ctx.guild:
            return await self.send(ctx, error_embed(ctx, "That emoji isn't from this server."))
        name = emoji.name
        try:
            await emoji.delete()
        except discord.HTTPException as e:
            return await self.send(ctx, error_embed(ctx, f"Failed: {e}"))
        await self.send(ctx, success_embed(ctx, f"Emoji `:{name}:` deleted."))

    @commands.command(name="deleteemoji", aliases=["de"])
    @require("admin")
    async def deleteemoji(self, ctx, emoji: discord.Emoji):
        await self._deleteemoji_logic(ctx, emoji)

    @app_commands.command(name="deleteemoji", description="Delete a custom emoji")
    async def slash_deleteemoji(self, interaction: discord.Interaction, emoji: str):
        obj = await self._parse_emoji(interaction.guild, emoji)
        if not obj:
            return await interaction.response.send_message(embed=error_embed(interaction, "Invalid emoji."), ephemeral=True)
        await self._deleteemoji_logic(interaction, obj)

    async def _parse_emoji(self, guild: discord.Guild, emoji_str: str) -> Optional[discord.Emoji]:
        m = re.search(r"(\d+)", emoji_str)
        if m:
            obj = discord.utils.get(guild.emojis, id=int(m.group(1)))
            if obj:
                return obj
        return discord.utils.get(guild.emojis, name=emoji_str)

    # ---------------------------------------------------------------
    #  LOCKTEXT / RLOCKTEXT
    # ---------------------------------------------------------------
    @commands.command(name="locktext")
    @require("admin")
    async def locktext(self, ctx, channel: discord.TextChannel = None):
        chan = channel or ctx.channel
        self.locktext_channels.add(chan.id)
        await self.db.execute("INSERT INTO locktext (channel_id) VALUES ($1) ON CONFLICT DO NOTHING", chan.id)
        await self.send(ctx, make_embed(
            ctx, f"{EMOJI_LOCK} Locktext Enabled",
            f"{chan.mention} now only allows **images and links**. Staff bypass automatically.", 0xE67E22
        ))

    @commands.command(name="rlocktext")
    @require("admin")
    async def rlocktext(self, ctx, channel: discord.TextChannel = None):
        chan = channel or ctx.channel
        self.locktext_channels.discard(chan.id)
        await self.db.execute("DELETE FROM locktext WHERE channel_id = $1", chan.id)
        await self.send(ctx, make_embed(
            ctx, f"{EMOJI_UNLOCK} Locktext Disabled",
            f"{chan.mention} is back to normal.", WIN_COLOR
        ))

    # ---------------------------------------------------------------
    #  CLOWN
    # ---------------------------------------------------------------
    @commands.group(name="clown", invoke_without_command=True)
    @require("admin")
    async def clown_group(self, ctx):
        embed = make_embed(ctx, f"{EMOJI_CLOWN} Clown System", (
            "`clown add @user` — Force nickname to **Clown** + give clown role\n"
            "`clown remove @user` — Restore original nickname + remove role"
        ), MAIN_COLOR)
        await self.send(ctx, embed)

    @clown_group.command(name="add")
    @require("admin")
    async def clown_add(self, ctx, member: discord.Member):
        try:
            await self.check_mod_permission(ctx, "admin")
        except commands.CheckFailure as e:
            return await self.send(ctx, error_embed(ctx, str(e)))
        ok, err = self.can_target(ctx.author, member)
        if not ok:
            return await self.send(ctx, error_embed(ctx, err))
        gid = ctx.guild.id
        if gid in self.clowns and member.id in self.clowns[gid]:
            return await self.send(ctx, error_embed(ctx, f"{member.mention} is already a clown."))
        if gid not in self.clowns:
            self.clowns[gid] = {}
        self.clowns[gid][member.id] = member.nick
        await self.db.execute(
            "INSERT INTO clowns (guild_id, user_id, original_nick) VALUES ($1, $2, $3) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET original_nick = EXCLUDED.original_nick",
            gid, member.id, member.nick
        )
        role = ctx.guild.get_role(self.clown_role_id)
        if not role:
            return await self.send(ctx, error_embed(ctx, "Clown role is not configured."))
        try:
            await member.add_roles(role, reason="Clown mode")
        except discord.Forbidden:
            return await self.send(ctx, error_embed(ctx, "Cannot assign clown role (missing permissions)."))
        try:
            await member.edit(nick="Clown", reason="Clown mode")
        except discord.Forbidden:
            pass
        await self.send(ctx, success_embed(ctx, f"{member.mention} is now a {EMOJI_CLOWN} Clown!"))

    @clown_group.command(name="remove")
    @require("admin")
    async def clown_remove(self, ctx, member: discord.Member):
        gid = ctx.guild.id
        if gid not in self.clowns or member.id not in self.clowns[gid]:
            return await self.send(ctx, error_embed(ctx, f"{member.mention} is not a clown."))
        original = self.clowns[gid].pop(member.id)
        if not self.clowns[gid]:
            del self.clowns[gid]
        await self.db.execute("DELETE FROM clowns WHERE guild_id = $1 AND user_id = $2", gid, member.id)
        role = ctx.guild.get_role(self.clown_role_id)
        if role:
            try:
                await member.remove_roles(role, reason="Clown removed")
            except discord.Forbidden:
                pass
        try:
            await member.edit(nick=original, reason="Clown mode removed")
        except (discord.Forbidden, discord.HTTPException):
            pass
        await self.send(ctx, success_embed(ctx, f"{member.mention} is no longer a clown. Nickname restored."))

    # ---------------------------------------------------------------
    #  FORCENICK
    # ---------------------------------------------------------------
    @commands.group(name="forcenick", invoke_without_command=True)
    @require("admin")
    async def forcenick_group(self, ctx):
        embed = make_embed(ctx, f"{EMOJI_GEAR} Force Nickname", (
            "`forcenick add @user <nick>` — Lock a user to a nickname permanently\n"
            "`forcenick remove @user` — Release the forced nickname"
        ), MAIN_COLOR)
        await self.send(ctx, embed)

    @forcenick_group.command(name="add")
    @require("admin")
    async def forcenick_add(self, ctx, member: discord.Member, *, nick: str):
        if len(nick) > 32:
            return await self.send(ctx, error_embed(ctx, "Nickname must be ≤ 32 characters."))
        try:
            await self.check_mod_permission(ctx, "admin")
        except commands.CheckFailure as e:
            return await self.send(ctx, error_embed(ctx, str(e)))
        ok, err = self.can_target(ctx.author, member)
        if not ok:
            return await self.send(ctx, error_embed(ctx, err))
        gid = ctx.guild.id
        if gid in self.forcenicks and member.id in self.forcenicks[gid]:
            return await self.send(ctx, error_embed(ctx, f"{member.mention} already has a forced nickname."))
        if gid not in self.forcenicks:
            self.forcenicks[gid] = {}
        self.forcenicks[gid][member.id] = {"forced": nick, "original": member.nick}
        await self.db.execute(
            "INSERT INTO forcenicks (guild_id, user_id, forced_nick, original_nick) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET forced_nick = EXCLUDED.forced_nick, original_nick = EXCLUDED.original_nick",
            gid, member.id, nick, member.nick
        )
        try:
            await member.edit(nick=nick, reason="Forcenick applied")
        except discord.Forbidden:
            return await self.send(ctx, make_embed(
                ctx, f"{EMOJI_WARN} Forcenick Saved (Nick Locked)",
                f"{member.mention} forcenick saved but I couldn't change their nick right now (Forbidden). "
                "It will be enforced on their next nickname attempt.", WARN_COLOR
            ))
        await self.send(ctx, success_embed(ctx, f"{member.mention}'s nickname is now permanently locked to **{nick}**."))

    @forcenick_group.command(name="remove")
    @require("admin")
    async def forcenick_remove(self, ctx, member: discord.Member):
        gid = ctx.guild.id
        if gid not in self.forcenicks or member.id not in self.forcenicks[gid]:
            return await self.send(ctx, error_embed(ctx, f"{member.mention} does not have a forced nickname."))
        original = self.forcenicks[gid].pop(member.id).get("original")
        if not self.forcenicks[gid]:
            del self.forcenicks[gid]
        await self.db.execute("DELETE FROM forcenicks WHERE guild_id = $1 AND user_id = $2", gid, member.id)
        try:
            await member.edit(nick=original, reason="Forcenick removed")
        except (discord.Forbidden, discord.HTTPException):
            pass
        await self.send(ctx, success_embed(ctx, f"Forcenick removed from {member.mention}. Nickname restored."))

    # ---------------------------------------------------------------
    #  EMOJI LIST PAGINATION VIEW
    # ---------------------------------------------------------------
    class EmojiListView(discord.ui.View):
        def __init__(self, ctx, pages):
            super().__init__(timeout=120)
            self.ctx = ctx
            self.pages = pages
            self.current_page = 0
            self.previous.disabled = True
            if len(pages) <= 1:
                self.next.disabled = True

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            original = self.ctx.author if isinstance(self.ctx, commands.Context) else self.ctx.user
            if interaction.user.id != original.id:
                await interaction.response.send_message(
                    "This menu isn't for you!", ephemeral=True
                )
                return False
            return True

        async def _update(self, interaction: discord.Interaction):
            embed = self.ctx.cog._build_emoji_list_embed(
                self.ctx,
                self.pages[self.current_page],
                self.current_page + 1,
                len(self.pages),
            )
            self.previous.disabled = (self.current_page == 0)
            self.next.disabled = (self.current_page == len(self.pages) - 1)
            await interaction.response.edit_message(embed=embed, view=self)

        @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
        async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.current_page > 0:
                self.current_page -= 1
            await self._update(interaction)

        @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
        async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.current_page < len(self.pages) - 1:
                self.current_page += 1
            await self._update(interaction)


# ---------------------------------------------------------------
#  Setup function
# ---------------------------------------------------------------
async def setup(bot):
    cog = Moderation(bot)
    await cog._create_tables_if_not_exists()
    await cog._load_data_files()
    await bot.add_cog(cog)
    logger.info("Moderation cog loaded with corrected permissions and database scoping.")