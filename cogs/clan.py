import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import time
import os
import re
import random
import json
import asyncpg
from typing import Optional, List
import logging
from pathlib import Path

# ------------------------- Setup logger -------------------------
logger = logging.getLogger(__name__)

# ------------------------- Load master config (flat structure) -------------------------
with open("config.json", "r", encoding="utf-8") as f:
    MASTER_CONFIG = json.load(f)

ALLOWED_CHANNEL_ID = MASTER_CONFIG.get("allowed_channel_id")
DISCORD_LOG_CHANNEL_ID = MASTER_CONFIG.get("logging_channel_id")
ROLES_CONFIG = MASTER_CONFIG.get("roles", {})

# Staff role IDs from config (your existing key names)
STAFF_ROLE_IDS = []
staff_role_keys = [
    "admin_role_id",
    "senior_moderator_role_id",
    "moderator_role_id",
    "trial_moderator_role_id",
    "developer_role_id",
    "event_manager_role_id",
    "trial_event_manager_role_id"
]
for key in staff_role_keys:
    rid = ROLES_CONFIG.get(key)
    if rid:
        STAFF_ROLE_IDS.append(rid)

# Blacklist roles
CLAN_BLACKLIST_ROLE = MASTER_CONFIG.get("clan_blacklist_role")  # single role ID
BLACKLIST_ROLES = MASTER_CONFIG.get("blacklist_roles", [])       # list of role IDs

# PostgreSQL connection string (from .env only)
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable must be set in .env")

async def log_to_discord(bot, message: str):
    """Send a log message to the configured Discord logging channel."""
    if not DISCORD_LOG_CHANNEL_ID:
        return
    try:
        channel = bot.get_channel(DISCORD_LOG_CHANNEL_ID)
        if channel:
            if len(message) > 2000:
                for i in range(0, len(message), 2000):
                    await channel.send(message[i:i+2000])
            else:
                await channel.send(message)
    except Exception as e:
        print(f"Failed to log to Discord: {e}")

# ------------------------- Superscript map -------------------------
SUPERSCRIPT_MAP = {
    'a': 'ᵃ', 'b': 'ᵇ', 'c': 'ᶜ', 'd': 'ᵈ', 'e': 'ᵉ', 'f': 'ᶠ',
    'g': 'ᵍ', 'h': 'ʰ', 'i': 'ⁱ', 'j': 'ʲ', 'k': 'ᵏ', 'l': 'ˡ',
    'm': 'ᵐ', 'n': 'ⁿ', 'o': 'ᵒ', 'p': 'ᵖ', 'q': 'ᵠ', 'r': 'ʳ',
    's': 'ˢ', 't': 'ᵗ', 'u': 'ᵘ', 'v': 'ᵛ', 'w': 'ʷ', 'x': 'ˣ',
    'y': 'ʸ', 'z': 'ᶻ',
    '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
    '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹'
}

def to_superscript(s: str) -> str:
    return ''.join(SUPERSCRIPT_MAP.get(ch, ch) for ch in s.lower())

# ------------------------- Emoji constants -------------------------
EMOJI_TICK      = "<a:tick:1537915747454554222>"
EMOJI_WRONG     = "<:cross:1537915734087303199>"
EMOJI_FILE      = "<:file:1537913812416729200>"
EMOJI_SNIPE     = "<:1b_snipe:1537919015715082420>"
EMOJI_DELETE    = "<:Deleted:1537919221969846413>"
EMOJI_LOCK      = "<:lock:1537916486532730890>"
EMOJI_UNLOCK    = "<:unlock:1537916484532183040>"
EMOJI_WARN      = "<:purple_warning:1537916196601733241>"
EMOJI_ALERT     = "<a:alert:1537916605298778143>"
EMOJI_COLOR     = "<:color:1537919527856111686>"
EMOJI_UP        = "<:chart:1537919996984959046>"
EMOJI_MOD       = "<:mod:1537920117503955094>"
EMOJI_OWNER     = "<a:owner:1537913450754473994>"
EMOJI_ADMIN     = "<:admin:1537920215399006229>"
EMOJI_MUTE      = "<a:mute:1537920441627185274>"
COIN_EMOJI      = "<:moon_dust:1537920654349828267>"
BREAKER         = "<:breaker:1537913026643238932>"
BREAKER_LINE    = BREAKER * 10

# Additional emojis used in clan system
CLASH_OF_CLANS = "<:coc:1537920842867019847>"
TB_MONEY_BAG = "<:dust_bag:1537921004532277288>"
SWORD = "<a:sword:1537921132894625943>"
SEARCH = "<:search:1537921248368132177>"
CROWN_CROWN = "<:_purple_crown:1537921342354100254>"
CLASH_OF_CLANS_2 = "<:coc:1537920842867019847>"
USER_ID = "<:userid:1537914514736025740>"
STATS = "<a:stats:1537921547774205972>"
GREEN_SPARKLING_STAR = "<a:aa_star_purple:1537921654435348681>"
LIGHT_BULB = "<a:light_bulb:1537921770357657620>"
Q_NOTES = "<:q_notes:1537922139431370854>"
NEW_MAIL = "<a:mail:1537917503471095919>"
POINT_EMOJI = GREEN_SPARKLING_STAR   # for clan points display

# Role limits
MAX_COOWNERS = 2
MAX_ELDERS = 3

# Weekly role bonus
WEEKLY_ROLE_BONUS = {
    "member": 0,
    "elder": 2000,
    "coowner": 5000,
    "owner": 10000
}

# XP per level and max level
XP_PER_LEVEL = 100_000
MAX_LEVEL = 100

# Cosmetic unlock levels (from snippet)
COSMETIC_UNLOCKS = {
    "clan_creation": 1,
    "motto": 4,
    "badge": 8,
    "logo": 10,
    "static_banner": 13,
    "animated_banner": 19,
    "embed_color": 26,
    "description": 35,
}

BADGE_OPTIONS = [
    "<:dragon_sheild:1537922312060276827>", "<:swords:1537922420776767679>", "<:Dragon:1537922944884408453>", "<:_purple_crown:1537921342354100254>", "<a:purple_fire:1537923927106527304>", "<:moon:1537924074703945778>", "<a:Snowflake:1537924373011370034>", "<a:lightning:1537924519178674226>", "<:black_skull:1537924724208967762>", "<:Purple_flower:1537924809722437753>", "<a:Maple_Leaf:1537924913782984714>", "<:GOLDEN_EAGLES:1537925001183891516>"
]

# ------------------------- Economy helpers (now using wallet/bank) -------------------------
def format_coins(amount: int) -> str:
    if amount >= 1_000_000_000:
        return f"{amount/1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"{amount/1_000_000:.1f}M"
    if amount >= 1_000:
        return f"{amount/1_000:.1f}k"
    return str(amount)

def parse_amount(s: str) -> int:
    s = s.lower().replace(",", "").strip()
    match = re.match(r"^([\d.]+)([kmb]?)$", s)
    if not match:
        raise ValueError("Invalid amount format")
    num = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    elif suffix == "b":
        num *= 1_000_000_000
    return int(num)

async def eco_get_balance(bot, uid: int) -> int:
    """Return the wallet balance (spendable) of a user."""
    async with bot.db_pool.acquire() as conn:
        await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", uid)
        row = await conn.fetchval("SELECT wallet FROM users WHERE id=$1", uid)
        return row or 0

async def eco_add_coins(bot, uid: int, amount: int):
    """Add coins to a user's wallet and log before/after."""
    async with bot.db_pool.acquire() as conn:
        await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", uid)
        old_wallet = await conn.fetchval("SELECT wallet FROM users WHERE id=$1", uid) or 0
        await conn.execute("UPDATE users SET wallet = wallet + $1 WHERE id=$2", amount, uid)
        new_wallet = await conn.fetchval("SELECT wallet FROM users WHERE id=$1", uid)
        await log_to_discord(bot, f"<:moon_dust:1537920654349828267> **Balance Change** | User: {uid} | +{format_coins(amount)} | Wallet Before: {format_coins(old_wallet)} | After: {format_coins(new_wallet)}")

async def eco_deduct_coins(bot, uid: int, amount: int) -> bool:
    """Deduct from wallet if sufficient; log before/after."""
    async with bot.db_pool.acquire() as conn:
        await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", uid)
        old_wallet = await conn.fetchval("SELECT wallet FROM users WHERE id=$1", uid) or 0
        if old_wallet >= amount:
            await conn.execute("UPDATE users SET wallet = wallet - $1 WHERE id=$2", amount, uid)
            new_wallet = await conn.fetchval("SELECT wallet FROM users WHERE id=$1", uid)
            await log_to_discord(bot, f"<:moon_dust:1537920654349828267> **Balance Change** | User: {uid} | -{format_coins(amount)} | Wallet Before: {format_coins(old_wallet)} | After: {format_coins(new_wallet)}")
            return True
        else:
            await log_to_discord(bot, f"<:moon_dust:1537920654349828267> **Balance Deduction Failed** | User: {uid} | Attempted -{format_coins(amount)} | Wallet: {format_coins(old_wallet)}")
            return False

# ------------------------- Database init -------------------------
async def init_db(bot):
    bot.db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with bot.db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS clans (
                id SERIAL PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                owner_id BIGINT NOT NULL,
                level INT DEFAULT 1,
                xp INT DEFAULT 0,
                logo_url TEXT,
                banner_url TEXT,
                banner_type TEXT DEFAULT 'static',
                visibility TEXT DEFAULT 'public',
                clan_motto TEXT,
                clan_badge TEXT,
                embed_color TEXT DEFAULT '#9d4edd',
                clan_description TEXT,
                UNIQUE(guild_id, name)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS members (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                clan_id INT NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                role TEXT DEFAULT 'member',
                original_nick TEXT,
                PRIMARY KEY (user_id, guild_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS invites (
                guild_id BIGINT NOT NULL,
                clan_id INT NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                PRIMARY KEY (guild_id, clan_id, user_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contributions (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                clan_id INT NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                amount INT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS weekly_claims (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                clan_id INT NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                claim_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, guild_id, clan_id)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS quests (
                clan_id INT NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                guild_id BIGINT NOT NULL,
                description TEXT,
                target INT,
                progress INT DEFAULT 0,
                reward_xp INT,
                expires_at TIMESTAMP,
                quest_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                start_value INT DEFAULT 0,
                quest_date TEXT,
                PRIMARY KEY (clan_id, guild_id)
            )
        """)
        # IMPORTANT: The users table must match the economy cog's schema.
        # We create it with wallet/bank, not balance.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                wallet BIGINT DEFAULT 1000,
                bank BIGINT DEFAULT 0
            )
        """)
        # If economy cog already created users, we don't touch it.
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id BIGINT PRIMARY KEY,
                clan_enabled BOOLEAN DEFAULT TRUE
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS bypass_channels (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                PRIMARY KEY (guild_id, user_id, channel_id)
            )
        """)

# ------------------------- Helpers for settings -------------------------
async def is_clan_enabled(bot, guild_id: int) -> bool:
    async with bot.db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT clan_enabled FROM guild_settings WHERE guild_id=$1", guild_id
        )
        if row is None:
            await conn.execute(
                "INSERT INTO guild_settings (guild_id, clan_enabled) VALUES ($1, TRUE) ON CONFLICT DO NOTHING",
                guild_id
            )
            return True
        return row['clan_enabled']

async def set_clan_enabled(bot, guild_id: int, enabled: bool):
    async with bot.db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO guild_settings (guild_id, clan_enabled) VALUES ($1, $2) "
            "ON CONFLICT (guild_id) DO UPDATE SET clan_enabled = $2",
            guild_id, enabled
        )

async def get_user_bypass_channels(bot, guild_id: int, user_id: int):
    async with bot.db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT channel_id FROM bypass_channels WHERE guild_id=$1 AND user_id=$2",
            guild_id, user_id
        )
        return [r['channel_id'] for r in rows]

async def add_bypass_channel(bot, guild_id: int, user_id: int, channel_id: int):
    async with bot.db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO bypass_channels (guild_id, user_id, channel_id) VALUES ($1, $2, $3) "
            "ON CONFLICT DO NOTHING",
            guild_id, user_id, channel_id
        )

# ------------------------- Blacklist helpers -------------------------
async def is_clan_blacklisted(member: discord.Member) -> bool:
    """Check if a user has the clan blacklist role."""
    if CLAN_BLACKLIST_ROLE:
        try:
            role_id = int(CLAN_BLACKLIST_ROLE)
            for role in member.roles:
                if role.id == role_id:
                    return True
        except (TypeError, ValueError):
            logger.warning(f"clan_blacklist_role is not a valid integer: {CLAN_BLACKLIST_ROLE!r}")
    return False

async def is_blacklisted(member: discord.Member) -> bool:
    """Check if a user has any of the general blacklist roles."""
    if BLACKLIST_ROLES:
        blacklist_set = set(int(r) for r in BLACKLIST_ROLES if isinstance(r, (int, str)) and str(r).isdigit())
        for role in member.roles:
            if role.id in blacklist_set:
                return True
    return False

# ------------------------- Embed builder -------------------------
def make_embed(ctx, emoji, title, desc, color=0xe74c3c, success=False):
    embed = discord.Embed(
        title=f"{emoji} {title}",
        description=f"{desc}\n{BREAKER_LINE}" if desc else None,
        color=0x2ecc71 if success else color,
        timestamp=discord.utils.utcnow()
    )
    if hasattr(ctx, 'guild') and ctx.guild:
        embed.set_author(name=ctx.guild.name,
                         icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        embed.set_footer(text=f"Requested by {ctx.author}",
                         icon_url=ctx.author.display_avatar.url)
    return embed

# ------------------------- Custom checks -------------------------
def is_bot_owner():
    async def pred(ctx):
        return ctx.author.id == ctx.bot.owner_id
    return commands.check(pred)

def clan_owner_only():
    async def pred(ctx):
        async with ctx.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                raise commands.CheckFailure("You aren't in a clan.")
            if row['role'] != 'owner':
                raise commands.CheckFailure("Only the clan owner can use this command.")
        return True
    return commands.check(pred)

def has_staff_role():
    async def pred(ctx):
        if not STAFF_ROLE_IDS:
            return False
        user_roles = [role.id for role in ctx.author.roles]
        return any(rid in user_roles for rid in STAFF_ROLE_IDS)
    return commands.check(pred)

# ------------------------- Cooldown decorator -------------------------
def clan_cooldown():
    """1 use per 5 seconds per user for all clan commands."""
    return commands.cooldown(1, 5, commands.BucketType.user)

# ------------------------- Main clan check (custom) -------------------------
async def clan_command_check(ctx):
    """Global check for all clan commands (including subcommands)."""
    if ctx.author.id == ctx.bot.owner_id:
        return True
    if not ctx.guild:
        raise commands.CheckFailure("Clan commands are only available in servers.")
    # Check blacklist
    if await is_clan_blacklisted(ctx.author):
        raise commands.CheckFailure("You are blacklisted from using clan commands.")
    if await is_blacklisted(ctx.author):
        raise commands.CheckFailure("You are blacklisted from using clan commands.")
    # Check if clan is disabled
    enabled = await is_clan_enabled(ctx.bot, ctx.guild.id)
    if not enabled:
        raise commands.CheckFailure("Clan commands are currently disabled.")
    # Staff can use anywhere
    if STAFF_ROLE_IDS:
        user_roles = [role.id for role in ctx.author.roles]
        if any(rid in user_roles for rid in STAFF_ROLE_IDS):
            return True
    # Bypass channels
    bypass_channels = await get_user_bypass_channels(ctx.bot, ctx.guild.id, ctx.author.id)
    if ctx.channel.id in bypass_channels:
        return True
    # Allowed channel
    if ALLOWED_CHANNEL_ID is not None and ctx.channel.id == ALLOWED_CHANNEL_ID:
        return True
    # Deny
    if ALLOWED_CHANNEL_ID is not None:
        channel = ctx.guild.get_channel(ALLOWED_CHANNEL_ID)
        ch_name = channel.mention if channel else f"<#{ALLOWED_CHANNEL_ID}>"
        raise commands.CheckFailure(f"Clan commands can only be used in {ch_name}.")
    else:
        raise commands.CheckFailure("Clan commands are not allowed in this channel.")

# ======================== MAIN CLAN COG ========================
class Clans(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._nick_lock = set()
        self.hourly_status.start()

    def cog_unload(self):
        self.hourly_status.cancel()

    @tasks.loop(hours=1)
    async def hourly_status(self):
        try:
            async with self.bot.db_pool.acquire() as conn:
                clan_count = await conn.fetchval("SELECT COUNT(*) FROM clans")
                member_count = await conn.fetchval("SELECT COUNT(*) FROM members")
                contrib_count = await conn.fetchval("SELECT COUNT(*) FROM contributions")
                user_count = await conn.fetchval("SELECT COUNT(*) FROM users")
                total_coins = await conn.fetchval("SELECT COALESCE(SUM(wallet + bank), 0) FROM users")

            log_msg = (
                f"Clans: {clan_count} | Members: {member_count} | Contributions: {contrib_count} | "
                f"Economy users: {user_count} | Total coins: {format_coins(total_coins)}"
            )
            await log_to_discord(self.bot, log_msg)
        except Exception as e:
            await log_to_discord(self.bot, f"Hourly status failed: {e}")

    @hourly_status.before_loop
    async def before_hourly(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        if ctx.cog is self:
            log_msg = f"Guild: {ctx.guild.id if ctx.guild else 'DM'} | User: {ctx.author.id} ({ctx.author}) | Command: {ctx.command.qualified_name}"
            await log_to_discord(self.bot, log_msg)

    # ---------- Error handler to suppress non-syntax errors ----------
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if ctx.command is None or not ctx.command.qualified_name.startswith("clan"):
            return
        if isinstance(error, commands.CommandInvokeError):
            error = error.original
        if isinstance(error, (commands.CheckFailure, commands.UserInputError)):
            allowed = (
                (ALLOWED_CHANNEL_ID is not None and ctx.channel.id == ALLOWED_CHANNEL_ID) or
                (STAFF_ROLE_IDS and any(rid in [r.id for r in ctx.author.roles] for rid in STAFF_ROLE_IDS)) or
                ctx.author.id == ctx.bot.owner_id
            )
            if allowed:
                await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Error", str(error)))
        else:
            await log_to_discord(self.bot, f"Error in {ctx.command}: {error}")

    # ---------- Global check for clan group ----------
    async def cog_check(self, ctx):
        if ctx.command.qualified_name.startswith("clan"):
            return await clan_command_check(ctx)
        return True

    # ---------- Utility to get XP-based level ----------
    def get_level_from_xp(self, xp: int) -> int:
        return min((xp // XP_PER_LEVEL) + 1, MAX_LEVEL)

    def points_to_next_level(self, xp: int) -> Optional[int]:
        level = self.get_level_from_xp(xp)
        if level >= MAX_LEVEL:
            return None
        return (level * XP_PER_LEVEL) - xp

    async def _add_xp(self, clan_id: int, amount: int):
        """Add XP to a clan and update its level accordingly."""
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT xp FROM clans WHERE id=$1", clan_id)
            if not row:
                return
            old_xp = row['xp']
            new_xp = old_xp + amount
            new_level = self.get_level_from_xp(new_xp)
            await conn.execute("UPDATE clans SET xp=$1, level=$2 WHERE id=$3", new_xp, new_level, clan_id)

    # ---------- Group definition ----------
    @commands.group(name="clan", aliases=["clans"], invoke_without_command=True)
    @clan_cooldown()
    async def clan_group(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
        if row:
            await self._info(ctx)
        else:
            embed = discord.Embed(
                title=f"{CLASH_OF_CLANS} Clan System",
                description="Join or create a clan to gain powerful perks and weekly Moon Dust rewards!",
                color=0x9d4edd
            )
            embed.add_field(
                name=f"{TB_MONEY_BAG} Weekly Rewards",
                value="Earn Moon Dust every week based on your clan's XP level. **Max 1,000,000 Moon Dust at level 100.**",
                inline=False
            )
            embed.add_field(
                name=f"{EMOJI_UP} Level Up",
                value="Complete daily quests and earn XP to level your clan. Each level increases weekly payouts.",
                inline=False
            )
            embed.add_field(
                name=f"{SWORD} Daily Quests",
                value="A new quest appears every day – finish it to gain XP and rise through the ranks.",
                inline=False
            )
            embed.add_field(
                name=f"{SEARCH} Quick Start",
                value=(
                    f"`{ctx.prefix}clan create <name>` – Start your own clan (5M Moon Dust)\n"
                    f"`{ctx.prefix}clan join <name>` – Join a public clan\n"
                    f"`{ctx.prefix}clan help` – See all commands"
                ),
                inline=False
            )
            embed.set_footer(text=f"Use {ctx.prefix}clan help for a full list.")
            await ctx.send(embed=embed)

    @clan_group.command(name="help")
    @clan_cooldown()
    async def clan_help(self, ctx):
        prefix = ctx.prefix
        embed = discord.Embed(
            title=f"{CLASH_OF_CLANS} CLAN SYSTEM COMMANDS",
            color=0x9d4edd,
            description=f"Master the clan system and dominate the server! {CROWN_CROWN}"
        )
        embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)

        embed.add_field(
            name=f"{CLASH_OF_CLANS_2} CLAN MANAGEMENT",
            value=(
                f"`{prefix}clan create <name>` – Create a clan for 5,000,000 Moon Dust \n"
                f"`{prefix}clan delete` – Delete your owned clan\n"
                f"`{prefix}clan leave` – Leave your current clan\n"
                f"`{prefix}clan rename <name>` – Rename clan for 50,000 Moon Dust \n"
                f"`{prefix}clan set <public/private>` – Toggle visibility"
            ),
            inline=False
        )

        embed.add_field(
            name=f"{USER_ID} MEMBER MANAGEMENT",
            value=(
                f"`{prefix}clan invite @user` – Invite a member\n"
                f"`{prefix}clan accept <name>` – Accept an invitation\n"
                f"`{prefix}clan join <name>` – Join a public clan\n"
                f"`{prefix}clan kick @user` – Kick a member\n"
                f"`{prefix}clan promote @user <elder/coowner>` – Promote member\n"
                f"`{prefix}clan demote @user` – Demote a member\n"
                f"`{prefix}clan transfer @user` – Transfer ownership"
            ),
            inline=False
        )

        embed.add_field(
            name=f"{STATS} INFO & VIEWING",
            value=(
                f"`{prefix}clan info [name]` – View clan information\n"
                f"`{prefix}clan members [name]` – List clan members\n"
                f"`{prefix}clan top` – Clan leaderboard\n"
                f"`{prefix}clan logo [url]` – View/set clan logo\n"
                f"`{prefix}clan banner [url]` – View/set clan banner"
            ),
            inline=False
        )

        embed.add_field(
            name=f"{GREEN_SPARKLING_STAR} PROGRESSION & REWARDS",
            value=(
                f"`{prefix}clan boost` – Buy 500 XP (200,000 Moon Dust)\n"
                f"`{prefix}clan contribute <amount>` – Donate coins (fills bank, no levels)\n"
                f"`{prefix}clan bank` – View total donations\n"
                f"`{prefix}clan contributions [name]` – Top contributors\n"
                f"`{prefix}clan quest` – View/start today's daily quest\n"
                f"`{prefix}clan quest complete` – Complete the daily quest\n"
                f"`{prefix}clan weekly` – Claim weekly bonus (**scales with level, max 1M at lvl 100**)"
            ),
            inline=False
        )

        embed.add_field(
            name=f"{EMOJI_COLOR} CUSTOMIZATION",
            value=(
                f"`{prefix}clan cosmetics` – See what you can unlock\n"
                f"`{prefix}clan motto <text>` – Set clan motto (Lvl {COSMETIC_UNLOCKS['motto']}+)\n"
                f"`{prefix}clan badge` – Choose a badge (Lvl {COSMETIC_UNLOCKS['badge']}+)\n"
                f"`{prefix}clan color <hex>` – Set embed color (Lvl {COSMETIC_UNLOCKS['embed_color']}+)\n"
                f"`{prefix}clan description <text>` – Add a description (Lvl {COSMETIC_UNLOCKS['description']}+)"
            ),
            inline=False
        )

        embed.add_field(
            name=f"{EMOJI_ADMIN} ADMIN",
            value=f"`{prefix}clan admin` – Bot owner only",
            inline=False
        )

        embed.set_footer(text=f"{LIGHT_BULB} Tip: Complete daily quests to earn XP and level up your clan!")
        embed.add_field(
            name=f"{Q_NOTES} QUICK TIPS",
            value=(
                "• Create your clan to become the owner\n"
                "• Invite friends to grow your clan\n"
                "• Complete daily quests for XP and level-ups\n"
                "• Claim weekly bonuses for free rewards (bigger at higher levels)!\n"
                "• Donations fill the clan bank but don't give XP"
            ),
            inline=False
        )

        await ctx.send(embed=embed)

    # ======================== CREATE ========================
    @clan_group.command(name="create")
    @clan_cooldown()
    async def create_cmd(self, ctx, *, name: str):
        await self._create(ctx, name)

    async def _create(self, ctx, name: str):
        name = name.strip()
        if not name:
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Invalid Name", "Clan name cannot be empty."))

        if not await eco_deduct_coins(self.bot, ctx.author.id, 5_000_000):
            balance = await eco_get_balance(self.bot, ctx.author.id)
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Insufficient Funds",
                f"Creating a clan costs **5,000,000** Moon Dust. You only have **{format_coins(balance)}** Moon Dust."))

        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if row:
                await eco_add_coins(self.bot, ctx.author.id, 5_000_000)
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Already in a Clan",
                                                       "You must leave or delete your current clan first."))

            try:
                clan_id = await conn.fetchval(
                    "INSERT INTO clans (guild_id, name, owner_id) VALUES ($1, $2, $3) RETURNING id",
                    ctx.guild.id, name, ctx.author.id
                )
                await conn.execute(
                    "INSERT INTO members (user_id, guild_id, clan_id, role, original_nick) VALUES ($1, $2, $3, 'owner', $4)",
                    ctx.author.id, ctx.guild.id, clan_id, ctx.author.nick
                )
            except asyncpg.UniqueViolationError:
                await eco_add_coins(self.bot, ctx.author.id, 5_000_000)
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Name Taken", f"`{name}` already exists."))

        await self._set_nick(ctx.author, name)
        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Clan Created!",
                                        f"**{name}** is now yours. Invite members with `{ctx.prefix}clan invite @user`.",
                                        success=True))

    # ======================== DELETE ========================
    @clan_group.command(name="delete")
    @clan_owner_only()
    @clan_cooldown()
    async def delete_cmd(self, ctx):
        await self._delete(ctx)

    async def _delete(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT c.id, c.name FROM clans c JOIN members m ON c.id=m.clan_id WHERE m.user_id=$1 AND m.guild_id=$2 AND m.role='owner'",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner", "You don't own a clan."))
            clan_id, name = row['id'], row['name']

        await self._confirm_action(ctx, f"Delete **{name}**? This cannot be undone.",
                                   self._do_delete, clan_id)

    async def _do_delete(self, ctx, clan_id):
        async with self.bot.db_pool.acquire() as conn:
            members = await conn.fetch(
                "SELECT user_id, original_nick FROM members WHERE clan_id=$1", clan_id
            )

            tasks = []
            for record in members:
                uid = record['user_id']
                original_nick = record['original_nick']
                member = ctx.guild.get_member(uid)
                if member:
                    tasks.append(asyncio.create_task(member.edit(nick=original_nick)))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            await conn.execute("DELETE FROM members WHERE clan_id=$1", clan_id)
            await conn.execute("DELETE FROM invites WHERE clan_id=$1", clan_id)
            await conn.execute("DELETE FROM contributions WHERE clan_id=$1", clan_id)
            await conn.execute("DELETE FROM weekly_claims WHERE clan_id=$1", clan_id)
            await conn.execute("DELETE FROM quests WHERE clan_id=$1", clan_id)
            await conn.execute("DELETE FROM clans WHERE id=$1", clan_id)
        await ctx.send(embed=make_embed(ctx, EMOJI_DELETE, "Clan Deleted",
                                        "The clan has been deleted.", success=True))

    # ======================== LEAVE ========================
    @clan_group.command(name="leave")
    @clan_cooldown()
    async def leave_cmd(self, ctx):
        await self._leave(ctx)

    async def _leave(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "No Clan", "You aren't in a clan."))
            clan_id, role = row['clan_id'], row['role']

            if role == "owner":
                await self._confirm_action(ctx,
                    "You are the owner. Leaving will promote a Co‑Owner if one exists, otherwise the clan will be deleted. Are you sure?",
                    self._owner_leave, clan_id)
            else:
                await self._remove_member(clan_id, ctx.author)
                await ctx.send(embed=make_embed(ctx, EMOJI_UNLOCK, "Left Clan",
                                                "You left the clan.", success=True))

    async def _owner_leave(self, ctx, clan_id):
        async with self.bot.db_pool.acquire() as conn:
            co = await conn.fetchrow(
                "SELECT user_id FROM members WHERE clan_id=$1 AND role='coowner' LIMIT 1",
                clan_id
            )
            if co:
                new_owner = co['user_id']
                await conn.execute("UPDATE members SET role='owner' WHERE clan_id=$1 AND user_id=$2", clan_id, new_owner)
                await conn.execute("UPDATE clans SET owner_id=$1 WHERE id=$2", new_owner, clan_id)
                member = ctx.guild.get_member(new_owner)
                await self._remove_member(clan_id, ctx.author)
                await ctx.send(embed=make_embed(ctx, EMOJI_OWNER, "Ownership Passed",
                    f"{member.mention if member else f'<@{new_owner}>'} is now the owner.", success=True))
            else:
                await self._do_delete(ctx, clan_id)

    async def _remove_member(self, clan_id, member: discord.Member):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT original_nick FROM members WHERE clan_id=$1 AND user_id=$2",
                clan_id, member.id
            )
            original_nick = row['original_nick'] if row else None
            await conn.execute("DELETE FROM members WHERE clan_id=$1 AND user_id=$2", clan_id, member.id)
        if original_nick is not None:
            try:
                await member.edit(nick=original_nick)
            except discord.Forbidden:
                pass

    # ======================== INVITE ========================
    @clan_group.command(name="invite")
    @clan_owner_only()
    @clan_cooldown()
    async def invite_cmd(self, ctx, member: discord.Member):
        await self._invite(ctx, member)

    async def _invite(self, ctx, member: discord.Member):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row or row['role'] != 'owner':
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner",
                                                       "Only the clan owner can invite members."))
            clan_id = row['clan_id']
            clan_name = await conn.fetchval("SELECT name FROM clans WHERE id=$1", clan_id)

        view = InviteAcceptView(self.bot, member, ctx, clan_id, ctx.author)
        invite_embed = discord.Embed(
            title=f"{NEW_MAIL} Clan Invitation",
            description=f"{ctx.author.mention} invited you to join the clan **{clan_name}** on {ctx.guild.name}!",
            color=0x3498db
        )
        try:
            await member.send(embed=invite_embed, view=view)
            await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Invited",
                f"{member.mention} has been invited via DM.", success=True))
        except discord.Forbidden:
            try:
                await ctx.channel.send(embed=invite_embed, view=view)
                await ctx.send(embed=make_embed(ctx, EMOJI_WARN, "DM Failed",
                    f"{member.mention}'s DMs are closed. Invitation sent in this channel instead."))
            except:
                await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Failed",
                    f"Could not invite {member.mention}."))

    # ======================== JOIN ========================
    @clan_group.command(name="join")
    @clan_cooldown()
    async def join_cmd(self, ctx, *, clan_name: str):
        await self._join_public(ctx, clan_name)

    async def _join_public(self, ctx, clan_name: str):
        await self._join_by_name(ctx, clan_name, require_invite=False)

    async def _join_by_name(self, ctx, clan_name: str, require_invite: bool):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, visibility FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                ctx.guild.id, clan_name
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Found", f"No clan `{clan_name}`."))
            clan_id, vis = row['id'], row['visibility']

            if require_invite:
                inv = await conn.fetchval(
                    "SELECT 1 FROM invites WHERE clan_id=$1 AND user_id=$2",
                    clan_id, ctx.author.id
                )
                if not inv:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Invited",
                                                           f"You haven't been invited to **{clan_name}**."))
                await conn.execute("DELETE FROM invites WHERE clan_id=$1 AND user_id=$2", clan_id, ctx.author.id)
            else:
                if vis != "public":
                    return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Private Clan",
                                                           "This clan is private – you need an invitation."))
            await self._add_member(ctx, clan_id, ctx.author)

    async def _add_member(self, ctx, clan_id, member: discord.Member):
        async with self.bot.db_pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT 1 FROM members WHERE user_id=$1 AND guild_id=$2",
                member.id, ctx.guild.id
            )
            if existing:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Already in a Clan",
                                                       f"{member.mention} is already in a clan."))
            count = await conn.fetchval("SELECT COUNT(*) FROM members WHERE clan_id=$1", clan_id)
            if count >= 20:
                return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Clan Full", "This clan is full."))

            await conn.execute(
                "INSERT INTO members (user_id, guild_id, clan_id, role, original_nick) VALUES ($1, $2, $3, 'member', $4)",
                member.id, ctx.guild.id, clan_id, member.nick
            )
            name = await conn.fetchval("SELECT name FROM clans WHERE id=$1", clan_id)

        await self._set_nick(member, name)
        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Welcome!",
                                        f"{member.mention} joined the clan.", success=True))
        await self._update_quest_progress(ctx.guild, clan_id)

    # ======================== INFO ========================
    @clan_group.command(name="info", aliases=["profile"])
    @clan_cooldown()
    async def info_cmd(self, ctx, *, name: Optional[str] = None):
        await self._info(ctx, name)

    async def _info(self, ctx, name: Optional[str] = None):
        async with self.bot.db_pool.acquire() as conn:
            if name is None:
                row = await conn.fetchrow(
                    "SELECT clan_id FROM members WHERE user_id=$1 AND guild_id=$2",
                    ctx.author.id, ctx.guild.id
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_FILE, "No Clan",
                                                           "Specify a clan name or join one first."))
                clan_id = row['clan_id']
            else:
                row = await conn.fetchrow(
                    "SELECT id FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                    ctx.guild.id, name
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Found",
                                                           f"`{name}` doesn't exist."))
                clan_id = row['id']

            clan = await conn.fetchrow(
                "SELECT name, owner_id, level, xp, logo_url, banner_url, banner_type, visibility, clan_motto, clan_badge, embed_color, clan_description FROM clans WHERE id=$1",
                clan_id
            )
            cname = clan['name']
            owner_id = clan['owner_id']
            level = clan['level']
            xp = clan['xp']
            logo = clan['logo_url']
            banner = clan['banner_url']
            banner_type = clan.get('banner_type', 'static')
            visibility = clan['visibility']
            motto = clan.get('clan_motto')
            badge = clan.get('clan_badge')
            embed_color = clan.get('embed_color', '#9d4edd')
            description = clan.get('clan_description')

            roles = {"owner": [], "coowner": [], "elder": [], "member": []}
            members = await conn.fetch(
                "SELECT user_id, role FROM members WHERE clan_id=$1", clan_id
            )
            for record in members:
                uid, role = record['user_id'], record['role']
                member = ctx.guild.get_member(uid)
                mention = member.mention if member else f"<@{uid}>"
                roles[role].append(mention)

            # Determine next level progress
            next_level_pts = self.points_to_next_level(xp)

            try:
                color = int(embed_color.replace('#', ''), 16)
            except (ValueError, AttributeError):
                color = 0x9d4edd

            embed = discord.Embed(
                title=f"{badge + ' ' if badge else ''}{cname}",
                description=f"*{motto}*" if motto and level >= COSMETIC_UNLOCKS['motto'] else None,
                color=color,
                timestamp=discord.utils.utcnow()
            )
            embed.set_author(name=ctx.guild.name,
                             icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

            embed.add_field(
                name=f"{EMOJI_OWNER} Owner",
                value="\n".join(roles["owner"]) or "None",
                inline=True
            )
            if roles["coowner"]:
                embed.add_field(
                    name=f"{EMOJI_ADMIN} Co‑Owner",
                    value="\n".join(roles["coowner"]),
                    inline=True
                )
            if roles["elder"]:
                embed.add_field(
                    name=f"{EMOJI_MOD} Elder",
                    value="\n".join(roles["elder"]),
                    inline=True
                )
            if roles["member"]:
                embed.add_field(
                    name=f"Members ({len(roles['member'])})",
                    value="\n".join(roles["member"]),
                    inline=False
                )

            embed.add_field(name=f"{EMOJI_UP} Level", value=f"**{level}**", inline=True)
            embed.add_field(name="XP", value=f"**{xp:,}**", inline=True)
            if next_level_pts is not None:
                embed.add_field(name="Next Level", value=f"**{next_level_pts:,}** XP", inline=True)
            else:
                embed.add_field(name="Next Level", value="**MAX**", inline=True)

            total = await conn.fetchval("SELECT SUM(amount) FROM contributions WHERE clan_id=$1", clan_id) or 0
            embed.add_field(
                name=f"{COIN_EMOJI} Clan Bank",
                value=f"**{format_coins(total)}** donated",
                inline=True
            )

            if logo and level >= COSMETIC_UNLOCKS['logo']:
                embed.set_thumbnail(url=logo)
            if banner and level >= COSMETIC_UNLOCKS['static_banner']:
                if banner_type == 'animated' and level >= COSMETIC_UNLOCKS['animated_banner']:
                    embed.set_image(url=banner)
                elif banner_type == 'static':
                    embed.set_image(url=banner)

            if description and level >= COSMETIC_UNLOCKS['description']:
                embed.add_field(
                    name="Description",
                    value=description[:1000],
                    inline=False
                )

            embed.set_footer(text=f"Requested by {ctx.author}",
                             icon_url=ctx.author.display_avatar.url)
            await ctx.send(embed=embed)

    # ======================== MEMBERS ========================
    @clan_group.command(name="members")
    @clan_cooldown()
    async def members_cmd(self, ctx, *, name: Optional[str] = None):
        await self._members(ctx, name)

    async def _members(self, ctx, name: Optional[str] = None):
        async with self.bot.db_pool.acquire() as conn:
            if name is None:
                row = await conn.fetchrow(
                    "SELECT clan_id FROM members WHERE user_id=$1 AND guild_id=$2",
                    ctx.author.id, ctx.guild.id
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "No Clan",
                                                           "Specify a clan name or join one first."))
                clan_id = row['clan_id']
            else:
                row = await conn.fetchrow(
                    "SELECT id FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                    ctx.guild.id, name
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Found",
                                                           f"No clan `{name}`."))
                clan_id = row['id']
            cname = await conn.fetchval("SELECT name FROM clans WHERE id=$1", clan_id)
            roles = {"owner": [], "coowner": [], "elder": [], "member": []}
            members = await conn.fetch(
                "SELECT user_id, role FROM members WHERE clan_id=$1 ORDER BY role='owner' DESC, role='coowner' DESC, role='elder' DESC, role='member'",
                clan_id
            )
            for record in members:
                uid, role = record['user_id'], record['role']
                member = ctx.guild.get_member(uid)
                mention = member.mention if member else f"<@{uid}>"
                roles[role].append(mention)

        embed = discord.Embed(title=f"{EMOJI_SNIPE} {cname} Members", color=0x3498db)
        if roles["owner"]:
            embed.add_field(name=f"{EMOJI_OWNER} Owner", value="\n".join(roles["owner"]), inline=True)
        if roles["coowner"]:
            embed.add_field(name=f"{EMOJI_ADMIN} Co‑Owner", value="\n".join(roles["coowner"]), inline=True)
        if roles["elder"]:
            embed.add_field(name=f"{EMOJI_MOD} Elder", value="\n".join(roles["elder"]), inline=True)
        if roles["member"]:
            embed.add_field(name="Members", value="\n".join(roles["member"]), inline=True)
        embed.add_field(name="\u200b", value=BREAKER_LINE, inline=False)
        await ctx.send(embed=embed)

    # ======================== PROMOTE ========================
    @clan_group.command(name="promote")
    @clan_owner_only()
    @clan_cooldown()
    async def promote_cmd(self, ctx, member: discord.Member, role: str):
        await self._promote(ctx, member, role)

    async def _promote(self, ctx, member: discord.Member, role: str):
        role = role.lower().replace("-", "")
        if role not in ("elder", "coowner"):
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Invalid Role", "Use `elder` or `coowner`."))
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row or row['role'] != 'owner':
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner",
                                                       "Only the clan owner can promote."))
            clan_id = row['clan_id']
            target = await conn.fetchrow(
                "SELECT role FROM members WHERE clan_id=$1 AND user_id=$2",
                clan_id, member.id
            )
            if not target:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not in Clan",
                                                       f"{member.mention} is not in your clan."))
            if target['role'] == "owner":
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Cannot Promote", "They are the owner."))
            if target['role'] == role:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WARN, "Already", "They already hold that role."))

            # Enforce limits
            if role == "coowner":
                count = await conn.fetchval("SELECT COUNT(*) FROM members WHERE clan_id=$1 AND role='coowner'", clan_id)
                if count >= MAX_COOWNERS:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Limit Reached",
                                                           f"Maximum {MAX_COOWNERS} co‑owners allowed."))
            elif role == "elder":
                count = await conn.fetchval("SELECT COUNT(*) FROM members WHERE clan_id=$1 AND role='elder'", clan_id)
                if count >= MAX_ELDERS:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Limit Reached",
                                                           f"Maximum {MAX_ELDERS} elders allowed."))

            await conn.execute("UPDATE members SET role=$1 WHERE clan_id=$2 AND user_id=$3", role, clan_id, member.id)
        await ctx.send(embed=make_embed(ctx, EMOJI_UP, "Promoted",
                                        f"{member.mention} is now **{role}**.", success=True))

    # ======================== DEMOTE ========================
    @clan_group.command(name="demote")
    @clan_owner_only()
    @clan_cooldown()
    async def demote_cmd(self, ctx, member: discord.Member):
        await self._demote(ctx, member)

    async def _demote(self, ctx, member: discord.Member):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row or row['role'] != 'owner':
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner",
                                                       "Only the owner can demote."))
            clan_id = row['clan_id']
            target = await conn.fetchrow(
                "SELECT role FROM members WHERE clan_id=$1 AND user_id=$2",
                clan_id, member.id
            )
            if not target:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not in Clan",
                                                       f"{member.mention} is not in your clan."))
            if target['role'] == "owner":
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Cannot Demote", "They are the owner."))
            if target['role'] == "member":
                return await ctx.send(embed=make_embed(ctx, EMOJI_WARN, "Already",
                                                       "They are already a regular member."))

            await conn.execute("UPDATE members SET role='member' WHERE clan_id=$1 AND user_id=$2", clan_id, member.id)
        await ctx.send(embed=make_embed(ctx, EMOJI_DELETE, "Demoted",
                                        f"{member.mention} is now a member.", success=True))

    # ======================== TRANSFER ========================
    @clan_group.command(name="transfer")
    @clan_owner_only()
    @clan_cooldown()
    async def transfer_cmd(self, ctx, member: discord.Member):
        await self._transfer(ctx, member)

    async def _transfer(self, ctx, member: discord.Member):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id FROM members WHERE user_id=$1 AND guild_id=$2 AND role='owner'",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner", "You don't own a clan."))
            clan_id = row['clan_id']

        await self._confirm_action(ctx,
            f"Transfer ownership to {member.mention}? You will become a regular member.",
            self._do_transfer, clan_id, member)

    async def _do_transfer(self, ctx, clan_id, member: discord.Member):
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute("UPDATE members SET role='member' WHERE clan_id=$1 AND user_id=$2", clan_id, ctx.author.id)
            await conn.execute("UPDATE members SET role='owner' WHERE clan_id=$1 AND user_id=$2", clan_id, member.id)
            await conn.execute("UPDATE clans SET owner_id=$1 WHERE id=$2", member.id, clan_id)
        await ctx.send(embed=make_embed(ctx, EMOJI_OWNER, "Ownership Transferred",
                                        f"{member.mention} is now the owner.", success=True))

    # ======================== KICK ========================
    @clan_group.command(name="kick")
    @clan_owner_only()
    @clan_cooldown()
    async def kick_cmd(self, ctx, member: discord.Member):
        await self._kick(ctx, member)

    async def _kick(self, ctx, member: discord.Member):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id FROM members WHERE user_id=$1 AND guild_id=$2 AND role='owner'",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner", "You don't own a clan."))
            clan_id = row['clan_id']
            target = await conn.fetchrow(
                "SELECT role FROM members WHERE clan_id=$1 AND user_id=$2", clan_id, member.id
            )
            if not target:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not in Clan",
                                                       "That member is not in your clan."))
            if target['role'] == "owner":
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Cannot Kick",
                                                       "You can't kick yourself."))
            await self._remove_member(clan_id, member)

        await ctx.send(embed=make_embed(ctx, EMOJI_DELETE, "Kicked",
                                        f"{member.mention} has been kicked from the clan.", success=True))

    # ======================== LOGO / BANNER ========================
    @clan_group.command(name="logo", aliases=["setlogo"])
    @clan_cooldown()
    async def logo_cmd(self, ctx, url: Optional[str] = None):
        await self._set_media(ctx, "logo", url)

    @clan_group.command(name="banner", aliases=["setbanner"])
    @clan_cooldown()
    async def banner_cmd(self, ctx, url: Optional[str] = None):
        await self._set_media(ctx, "banner", url)

    async def _set_media(self, ctx, media_type: str, url: Optional[str]):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "No Clan", "You aren't in a clan."))
            clan_id, role = row['clan_id'], row['role']

            col = "logo_url" if media_type == "logo" else "banner_url"

            # Viewing current media
            if url is None and not (hasattr(ctx, 'message') and ctx.message and ctx.message.attachments):
                current = await conn.fetchval(f"SELECT {col} FROM clans WHERE id=$1", clan_id)
                if current:
                    embed = discord.Embed(title=f"{EMOJI_SNIPE} Clan {media_type.title()}", color=0x3498db)
                    embed.set_image(url=current)
                    embed.set_footer(text=f"Requested by {ctx.author}",
                                     icon_url=ctx.author.display_avatar.url)
                    await ctx.send(embed=embed)
                else:
                    await ctx.send(embed=make_embed(ctx, EMOJI_FILE, f"No {media_type.title()}",
                                                    f"This clan has no {media_type} set. Use a URL or attach an image."))
                return

            if role != "owner":
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner",
                                                       "Only the clan owner can change the clan media."))
            if url is None and ctx.message and ctx.message.attachments:
                url = ctx.message.attachments[0].url

            # Level checks
            clan = await conn.fetchrow("SELECT level, xp FROM clans WHERE id=$1", clan_id)
            level = clan['level']

            if media_type == "logo":
                if level < COSMETIC_UNLOCKS["logo"]:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                        f"Logos unlock at **Clan Level {COSMETIC_UNLOCKS['logo']}**. Your clan is Level **{level}**."))
                await conn.execute("UPDATE clans SET logo_url=$1 WHERE id=$2", url, clan_id)
                await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Logo Updated", "Logo set!", success=True))

            elif media_type == "banner":
                is_animated = url.lower().endswith('.gif')
                if is_animated:
                    if level < COSMETIC_UNLOCKS["animated_banner"]:
                        return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                            f"Animated banners unlock at **Clan Level {COSMETIC_UNLOCKS['animated_banner']}**. Your clan is Level **{level}**."))
                    await conn.execute(
                        "UPDATE clans SET banner_url=$1, banner_type='animated' WHERE id=$2",
                        url, clan_id
                    )
                    await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Banner Updated", "Animated banner set!", success=True))
                else:
                    if level < COSMETIC_UNLOCKS["static_banner"]:
                        return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                            f"Banners unlock at **Clan Level {COSMETIC_UNLOCKS['static_banner']}**. Your clan is Level **{level}**."))
                    await conn.execute(
                        "UPDATE clans SET banner_url=$1, banner_type='static' WHERE id=$2",
                        url, clan_id
                    )
                    await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Banner Updated", "Banner set!", success=True))

    # ======================== VISIBILITY ========================
    @clan_group.command(name="set")
    @clan_owner_only()
    @clan_cooldown()
    async def set_cmd(self, ctx, visibility: str):
        await self._set_visibility(ctx, visibility)

    async def _set_visibility(self, ctx, vis: str):
        vis = vis.lower()
        if vis not in ("public", "private"):
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Invalid", "Use `public` or `private`."))
        async with self.bot.db_pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE clans SET visibility=$1 WHERE id IN (SELECT clan_id FROM members WHERE user_id=$2 AND guild_id=$3 AND role='owner')",
                vis, ctx.author.id, ctx.guild.id
            )
            if " 0" in result:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner",
                                                       "You don't own a clan."))
        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Visibility Updated",
                                        f"Clan is now **{vis}**.", success=True))

    # ======================== RENAME ========================
    @clan_group.command(name="rename")
    @clan_owner_only()
    @clan_cooldown()
    async def rename_cmd(self, ctx, *, new_name: str):
        await self._rename(ctx, new_name)

    async def _rename(self, ctx, new_name: str):
        new_name = new_name.strip()
        if not new_name:
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Invalid Name", "Name cannot be empty."))
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT c.id, c.name FROM clans c JOIN members m ON c.id=m.clan_id WHERE m.user_id=$1 AND m.guild_id=$2 AND m.role='owner'",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner", "You don't own a clan."))
            clan_id, old_name = row['id'], row['name']

        if not await eco_deduct_coins(self.bot, ctx.author.id, 50_000):
            balance = await eco_get_balance(self.bot, ctx.author.id)
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Insufficient Funds",
                                                   f"Renaming costs {COIN_EMOJI} **50,000** coins. You have {format_coins(balance)}."))

        async with self.bot.db_pool.acquire() as conn:
            try:
                await conn.execute("UPDATE clans SET name=$1 WHERE id=$2", new_name, clan_id)
            except asyncpg.UniqueViolationError:
                await eco_add_coins(self.bot, ctx.author.id, 50_000)
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Name Taken", f"`{new_name}` already exists."))

            members = await conn.fetch(
                "SELECT user_id, original_nick FROM members WHERE clan_id=$1", clan_id
            )

        new_suffix = to_superscript(new_name)
        tasks = []
        for record in members:
            uid = record['user_id']
            original_nick = record['original_nick']
            member = ctx.guild.get_member(uid)
            if member:
                base = original_nick if original_nick else member.name
                new_nick = f"{base}{new_suffix}"
                if len(new_nick) > 32:
                    max_base = 32 - len(new_suffix)
                    base = base[:max_base]
                    new_nick = f"{base}{new_suffix}"
                tasks.append(asyncio.create_task(member.edit(nick=new_nick)))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Clan Renamed",
                                        f"**{old_name}** is now **{new_name}**.", success=True))

    # ======================== BOOST (XP) ========================
    @clan_group.command(name="boost")
    @clan_owner_only()
    @clan_cooldown()
    async def boost_cmd(self, ctx):
        await self._boost(ctx)

    async def _boost(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT c.id, c.name FROM clans c JOIN members m ON c.id=m.clan_id WHERE m.user_id=$1 AND m.guild_id=$2 AND m.role='owner'",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner",
                                                       "You don't own a clan."))
            clan_id, name = row['id'], row['name']

        if not await eco_deduct_coins(self.bot, ctx.author.id, 200_000):
            balance = await eco_get_balance(self.bot, ctx.author.id)
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Insufficient Funds",
                                                   f"You need {COIN_EMOJI} **200,000** coins to buy an XP boost. You have {format_coins(balance)}."))

        await self._add_xp(clan_id, 500)
        await ctx.send(embed=make_embed(ctx, EMOJI_UP, "XP Boost Applied!",
                                        f"**{name}** gained 500 XP.", success=True))
        await self._update_quest_progress(ctx.guild, clan_id)

    # ======================== CONTRIBUTE / DONATE ========================
    @clan_group.command(name="contribute", aliases=["donate", "give"])
    @clan_cooldown()
    async def contribute_cmd(self, ctx, *, amount: str):
        await self._contribute(ctx, amount)

    async def _contribute(self, ctx, amount_str: str):
        try:
            amount = parse_amount(amount_str)
        except ValueError:
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Invalid Format",
                                                   "Use a number like `1000`, `10k`, `1.5k`, `1m`."))
        if amount <= 0:
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Invalid Amount", "Must be positive."))
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "No Clan", "You aren't in a clan."))
            clan_id = row['clan_id']

        if not await eco_deduct_coins(self.bot, ctx.author.id, amount):
            balance = await eco_get_balance(self.bot, ctx.author.id)
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Insufficient Funds",
                                                   f"You don't have {COIN_EMOJI} **{format_coins(amount)}** coins. You have {format_coins(balance)}."))

        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO contributions (user_id, guild_id, clan_id, amount) VALUES ($1, $2, $3, $4)",
                ctx.author.id, ctx.guild.id, clan_id, amount
            )

        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Contribution",
                                        f"Donated {COIN_EMOJI} **{format_coins(amount)}** to your clan!",
                                        success=True))
        await self._update_quest_progress(ctx.guild, clan_id)

    # ======================== BANK ========================
    @clan_group.command(name="bank")
    @clan_cooldown()
    async def bank_cmd(self, ctx, *, name: Optional[str] = None):
        await self._bank(ctx, name)

    async def _bank(self, ctx, name: Optional[str] = None):
        async with self.bot.db_pool.acquire() as conn:
            if name is None:
                row = await conn.fetchrow(
                    "SELECT clan_id FROM members WHERE user_id=$1 AND guild_id=$2",
                    ctx.author.id, ctx.guild.id
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "No Clan",
                                                           "Specify a clan name or join one first."))
                clan_id = row['clan_id']
            else:
                row = await conn.fetchrow(
                    "SELECT id FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                    ctx.guild.id, name
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Found",
                                                           f"No clan `{name}`."))
                clan_id = row['id']
            row = await conn.fetchrow(
                "SELECT name, COALESCE(SUM(amount),0) AS total FROM clans LEFT JOIN contributions ON clans.id=contributions.clan_id WHERE clans.id=$1 GROUP BY clans.id, clans.name",
                clan_id
            )
            cname, total = row['name'], row['total']
        embed = make_embed(ctx, EMOJI_SNIPE, f"{cname} Bank",
                           f"Total donations: {COIN_EMOJI} **{format_coins(total)}**")
        await ctx.send(embed=embed)

    # ======================== CONTRIBUTIONS ========================
    @clan_group.command(name="contributions")
    @clan_cooldown()
    async def contributions_cmd(self, ctx, *, name: Optional[str] = None):
        await self._contributions(ctx, name)

    async def _contributions(self, ctx, name: Optional[str] = None):
        async with self.bot.db_pool.acquire() as conn:
            if name is None:
                row = await conn.fetchrow(
                    "SELECT clan_id FROM members WHERE user_id=$1 AND guild_id=$2",
                    ctx.author.id, ctx.guild.id
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "No Clan",
                                                           "Specify a clan name or join one first."))
                clan_id = row['clan_id']
            else:
                row = await conn.fetchrow(
                    "SELECT id FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                    ctx.guild.id, name
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Found",
                                                           f"No clan `{name}`."))
                clan_id = row['id']
            cname = await conn.fetchval("SELECT name FROM clans WHERE id=$1", clan_id)
            donors = await conn.fetch(
                "SELECT user_id, SUM(amount) AS total FROM contributions WHERE guild_id=$1 AND clan_id=$2 GROUP BY user_id ORDER BY total DESC LIMIT 10",
                ctx.guild.id, clan_id
            )

        embed = discord.Embed(title=f"{EMOJI_SNIPE} {cname} – Top Contributors", color=0xf1c40f)
        embed.set_author(name=ctx.guild.name,
                         icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        desc = ""
        if donors:
            for i, record in enumerate(donors, 1):
                uid = record['user_id']
                total = record['total']
                member = ctx.guild.get_member(uid)
                name = member.display_name if member else str(uid)
                desc += f"**{i}.** {name} – {COIN_EMOJI} `{format_coins(total)}`\n"
        else:
            desc = "No contributions yet."
        embed.description = desc + f"\n{BREAKER_LINE}"
        await ctx.send(embed=embed)

    # ======================== QUEST (daily, XP‑based) ========================
    @clan_group.command(name="quest")
    @clan_cooldown()
    async def quest_cmd(self, ctx, subcommand: Optional[str] = None):
        if subcommand and subcommand.lower() == "complete":
            await self._complete_quest(ctx)
        else:
            await self._quest(ctx)

    async def _quest(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "No Clan", "You aren't in a clan."))
            clan_id, role = row['clan_id'], row['role']
            if role not in ("owner", "coowner"):
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "No Permission", "Only owner/co‑owner can view/start quests."))

            today = time.strftime("%Y-%m-%d", time.gmtime())
            quest = await conn.fetchrow(
                "SELECT description, progress, target, reward_xp FROM quests WHERE clan_id=$1 AND guild_id=$2 AND quest_date=$3",
                clan_id, ctx.guild.id, today
            )

        if quest:
            desc, progress, target, reward_xp = quest['description'], quest['progress'], quest['target'], quest['reward_xp']
            embed = make_embed(ctx, EMOJI_FILE, "Today's Quest",
                               f"**{desc}**\nProgress: {progress}/{target}\nReward: {reward_xp} XP")
            return await ctx.send(embed=embed)

        # No quest for today → generate random new one
        quests_pool = [
            ("Collect 10,000 coins total donations", "donations", 10000, 200),
            ("Recruit 3 new members", "recruit", 3, 150),
            ("Gain 500 XP", "xp", 500, 250),
            ("Each member contributes 500 coins", "all_contribute", 500, 100),
            ("Each member claims their weekly bonus", "all_weekly", 1, 300),
            ("Clan levels up", "level_up", 1, 200),
            ("Reach 10 members", "member_absolute", 10, 250)
        ]
        desc, qtype, target, reward = random.choice(quests_pool)
        expires = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 86400))

        async with self.bot.db_pool.acquire() as conn:
            if qtype == "donations":
                start_val = await conn.fetchval(
                    "SELECT COALESCE(SUM(amount),0) FROM contributions WHERE clan_id=$1 AND guild_id=$2",
                    clan_id, ctx.guild.id
                )
            elif qtype == "recruit":
                start_val = await conn.fetchval("SELECT COUNT(*) FROM members WHERE clan_id=$1", clan_id)
            elif qtype == "xp":
                start_val = await conn.fetchval("SELECT xp FROM clans WHERE id=$1", clan_id)
            elif qtype == "level_up":
                start_val = await conn.fetchval("SELECT level FROM clans WHERE id=$1", clan_id)
            else:
                start_val = 0

            await conn.execute(
                """
                INSERT INTO quests (clan_id, guild_id, description, target, progress, reward_xp, expires_at, quest_type, start_value, quest_date)
                VALUES ($1, $2, $3, $4, 0, $5, $6, $7, $8, $9)
                ON CONFLICT (clan_id, guild_id) DO UPDATE SET
                    description = EXCLUDED.description,
                    target = EXCLUDED.target,
                    progress = EXCLUDED.progress,
                    reward_xp = EXCLUDED.reward_xp,
                    expires_at = EXCLUDED.expires_at,
                    quest_type = EXCLUDED.quest_type,
                    start_value = EXCLUDED.start_value,
                    quest_date = EXCLUDED.quest_date
                """,
                clan_id, ctx.guild.id, desc, target, reward, expires, qtype, start_val, today
            )

        embed = make_embed(ctx, EMOJI_FILE, "New Daily Quest!",
                           f"**{desc}**\nTarget: {target}\nReward: {reward} XP\nExpires in 24h")
        await ctx.send(embed=embed)

    async def _complete_quest(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "No Clan", "You aren't in a clan."))
            clan_id, role = row['clan_id'], row['role']
            if role not in ("owner", "coowner"):
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "No Permission", "Only owner/co‑owner can complete quests."))

            today = time.strftime("%Y-%m-%d", time.gmtime())
            quest = await conn.fetchrow(
                "SELECT * FROM quests WHERE clan_id=$1 AND guild_id=$2 AND quest_date=$3",
                clan_id, ctx.guild.id, today
            )
            if not quest:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WARN, "No Quest", "There is no active quest today."))

            q_clan_id = quest['clan_id']
            q_guild_id = quest['guild_id']
            desc = quest['description']
            target = quest['target']
            progress = quest['progress']
            reward_xp = quest['reward_xp']
            expires_at = quest['expires_at']
            qtype = quest['quest_type']
            created_at = quest['created_at']
            start_val = quest['start_value']
            qdate = quest['quest_date']

            if qtype == "donations":
                current_total = await conn.fetchval(
                    "SELECT COALESCE(SUM(amount),0) FROM contributions WHERE clan_id=$1 AND guild_id=$2 AND timestamp >= $3",
                    clan_id, ctx.guild.id, created_at
                )
                progress = current_total - start_val
                target_met = progress >= target
            elif qtype == "recruit":
                current_count = await conn.fetchval("SELECT COUNT(*) FROM members WHERE clan_id=$1", clan_id)
                progress = current_count - start_val
                target_met = progress >= target
            elif qtype == "xp":
                current_xp = await conn.fetchval("SELECT xp FROM clans WHERE id=$1", clan_id)
                progress = current_xp - start_val
                target_met = progress >= target
            elif qtype == "all_contribute":
                members = await conn.fetch("SELECT user_id FROM members WHERE clan_id=$1", clan_id)
                all_met = True
                for mem in members:
                    uid = mem['user_id']
                    mem_total = await conn.fetchval(
                        "SELECT COALESCE(SUM(amount),0) FROM contributions WHERE user_id=$1 AND clan_id=$2 AND guild_id=$3 AND timestamp >= $4",
                        uid, clan_id, ctx.guild.id, created_at
                    )
                    if mem_total < target:
                        all_met = False
                        break
                progress = 1 if all_met else 0
                target_met = all_met
            elif qtype == "all_weekly":
                members = await conn.fetch("SELECT user_id FROM members WHERE clan_id=$1", clan_id)
                all_met = True
                for mem in members:
                    uid = mem['user_id']
                    claimed = await conn.fetchval(
                        "SELECT 1 FROM weekly_claims WHERE user_id=$1 AND clan_id=$2 AND guild_id=$3 AND claim_time >= $4",
                        uid, clan_id, ctx.guild.id, created_at
                    )
                    if not claimed:
                        all_met = False
                        break
                progress = 1 if all_met else 0
                target_met = all_met
            elif qtype == "level_up":
                current_level = await conn.fetchval("SELECT level FROM clans WHERE id=$1", clan_id)
                progress = current_level - start_val
                target_met = progress >= target
            elif qtype == "member_absolute":
                current_count = await conn.fetchval("SELECT COUNT(*) FROM members WHERE clan_id=$1", clan_id)
                target_met = current_count >= target
                progress = 1 if target_met else 0
            else:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Error", "Unknown quest type."))

            if target_met:
                await self._add_xp(clan_id, reward_xp)
                await conn.execute(
                    "DELETE FROM quests WHERE clan_id=$1 AND guild_id=$2 AND quest_date=$3",
                    clan_id, ctx.guild.id, today
                )
                embed = make_embed(ctx, EMOJI_TICK, "Quest Completed!",
                                   f"**{desc}**\nReward: {reward_xp} XP added to the clan.", success=True)
            else:
                await conn.execute(
                    "UPDATE quests SET progress=$1 WHERE clan_id=$2 AND guild_id=$3 AND quest_date=$4",
                    progress, clan_id, ctx.guild.id, today
                )
                embed = make_embed(ctx, EMOJI_FILE, "Quest Progress",
                                   f"**{desc}**\nProgress: {progress}/{target}\nNot yet complete.")
            await ctx.send(embed=embed)

    async def _update_quest_progress(self, guild, clan_id):
        async with self.bot.db_pool.acquire() as conn:
            today = time.strftime("%Y-%m-%d", time.gmtime())
            quest = await conn.fetchrow(
                "SELECT * FROM quests WHERE clan_id=$1 AND guild_id=$2 AND quest_date=$3",
                clan_id, guild.id, today
            )
            if not quest:
                return

            q_clan_id = quest['clan_id']
            q_guild_id = quest['guild_id']
            desc = quest['description']
            target = quest['target']
            progress = quest['progress']
            reward_xp = quest['reward_xp']
            expires_at = quest['expires_at']
            qtype = quest['quest_type']
            created_at = quest['created_at']
            start_val = quest['start_value']
            qdate = quest['quest_date']

            if qtype == "donations":
                current_total = await conn.fetchval(
                    "SELECT COALESCE(SUM(amount),0) FROM contributions WHERE clan_id=$1 AND guild_id=$2 AND timestamp >= $3",
                    clan_id, guild.id, created_at
                )
                progress = current_total - start_val
                target_met = progress >= target
            elif qtype == "recruit":
                current_count = await conn.fetchval("SELECT COUNT(*) FROM members WHERE clan_id=$1", clan_id)
                progress = current_count - start_val
                target_met = progress >= target
            elif qtype == "xp":
                current_xp = await conn.fetchval("SELECT xp FROM clans WHERE id=$1", clan_id)
                progress = current_xp - start_val
                target_met = progress >= target
            elif qtype == "all_contribute":
                members = await conn.fetch("SELECT user_id FROM members WHERE clan_id=$1", clan_id)
                all_met = True
                for mem in members:
                    uid = mem['user_id']
                    mem_total = await conn.fetchval(
                        "SELECT COALESCE(SUM(amount),0) FROM contributions WHERE user_id=$1 AND clan_id=$2 AND guild_id=$3 AND timestamp >= $4",
                        uid, clan_id, guild.id, created_at
                    )
                    if mem_total < target:
                        all_met = False
                        break
                progress = 1 if all_met else 0
                target_met = all_met
            elif qtype == "all_weekly":
                members = await conn.fetch("SELECT user_id FROM members WHERE clan_id=$1", clan_id)
                all_met = True
                for mem in members:
                    uid = mem['user_id']
                    claimed = await conn.fetchval(
                        "SELECT 1 FROM weekly_claims WHERE user_id=$1 AND clan_id=$2 AND guild_id=$3 AND claim_time >= $4",
                        uid, clan_id, guild.id, created_at
                    )
                    if not claimed:
                        all_met = False
                        break
                progress = 1 if all_met else 0
                target_met = all_met
            elif qtype == "level_up":
                current_level = await conn.fetchval("SELECT level FROM clans WHERE id=$1", clan_id)
                progress = current_level - start_val
                target_met = progress >= target
            elif qtype == "member_absolute":
                current_count = await conn.fetchval("SELECT COUNT(*) FROM members WHERE clan_id=$1", clan_id)
                target_met = current_count >= target
                progress = 1 if target_met else 0
            else:
                return

            if target_met:
                await self._add_xp(clan_id, reward_xp)
                await conn.execute(
                    "DELETE FROM quests WHERE clan_id=$1 AND guild_id=$2 AND quest_date=$3",
                    clan_id, guild.id, today
                )
            else:
                await conn.execute(
                    "UPDATE quests SET progress=$1 WHERE clan_id=$2 AND guild_id=$3 AND quest_date=$4",
                    progress, clan_id, guild.id, today
                )

    # ======================== WEEKLY ========================
    @clan_group.command(name="weekly")
    @clan_cooldown()
    async def weekly_cmd(self, ctx):
        await self._weekly(ctx)

    async def _weekly(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT m.clan_id, m.role, c.xp FROM members m JOIN clans c ON m.clan_id=c.id "
                "WHERE m.user_id=$1 AND m.guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "No Clan", "You aren't in a clan."))
            clan_id, role, xp = row['clan_id'], row['role'], row['xp']

            last = await conn.fetchval(
                "SELECT claim_time FROM weekly_claims WHERE user_id=$1 AND clan_id=$2 AND guild_id=$3",
                ctx.author.id, clan_id, ctx.guild.id
            )
            now = time.time()
            if last:
                last_time = time.mktime(time.strptime(str(last), "%Y-%m-%d %H:%M:%S"))
                diff = now - last_time
                if diff < 604800:
                    hours = int((604800 - diff) // 3600)
                    return await ctx.send(embed=make_embed(ctx, EMOJI_MUTE, "Already Claimed",
                                                           f"You can claim again in `{hours}` hours."))

            level = self.get_level_from_xp(xp)
            base = min(level, MAX_LEVEL) * 10000
            role_bonus = WEEKLY_ROLE_BONUS.get(role, 0)
            reward = base + role_bonus
            await eco_add_coins(self.bot, ctx.author.id, reward)

            await conn.execute(
                "INSERT INTO weekly_claims (user_id, guild_id, clan_id, claim_time) VALUES ($1, $2, $3, NOW()) "
                "ON CONFLICT (user_id, guild_id, clan_id) DO UPDATE SET claim_time = EXCLUDED.claim_time",
                ctx.author.id, ctx.guild.id, clan_id
            )

        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Weekly Bonus",
                                        f"You received {COIN_EMOJI} **{format_coins(reward)}** from your clan! (Level {level})",
                                        success=True))
        await self._update_quest_progress(ctx.guild, clan_id)

    # ======================== TOP ========================
    @clan_group.command(name="top")
    @clan_cooldown()
    async def top_cmd(self, ctx):
        await self._top(ctx)

    async def _top(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            clans = await conn.fetch(
                """
                SELECT name, level, xp,
                       (SELECT COUNT(*) FROM members WHERE clan_id=c.id) as mem,
                       COALESCE((SELECT SUM(amount) FROM contributions WHERE clan_id=c.id), 0) as total
                FROM clans c
                WHERE guild_id=$1
                ORDER BY level DESC, xp DESC
                LIMIT 10
                """,
                ctx.guild.id
            )
        if not clans:
            return await ctx.send(embed=make_embed(ctx, EMOJI_WARN, "No Clans", "The server has no clans yet."))
        embed = discord.Embed(title=f"{EMOJI_UP} Clan Leaderboard", color=0xf1c40f)
        embed.set_author(name=ctx.guild.name,
                         icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        desc = ""
        for i, record in enumerate(clans, 1):
            name = record['name']
            lvl = record['level']
            xp = record['xp']
            mem = record['mem']
            donated = record['total']
            desc += f"**{i}.** {name} — Lvl {lvl} ({xp} XP) | {mem} Members | Donated: {COIN_EMOJI} {format_coins(donated)}\n"
        embed.description = desc + f"\n{BREAKER_LINE}"
        await ctx.send(embed=embed)

    # ======================== ADMIN ========================
    @clan_group.group(name="admin", invoke_without_command=True)
    @is_bot_owner()
    async def admin_group(self, ctx):
        await ctx.send(embed=make_embed(ctx, EMOJI_ADMIN, "Clan Admin",
            "`delete <name>` | `givexp <name> <amount>` | `setlevel <name> <level>`"))

    @admin_group.command(name="delete")
    @is_bot_owner()
    async def admin_delete_cmd(self, ctx, *, name: str):
        await self._admin_delete(ctx, name)

    async def _admin_delete(self, ctx, name: str):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                ctx.guild.id, name
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Found", f"No clan `{name}`."))
            await self._do_delete(ctx, row['id'])

    @admin_group.command(name="givexp")
    @is_bot_owner()
    async def admin_givexp_cmd(self, ctx, name: str, amount: int):
        await self._admin_givexp(ctx, name, amount)

    async def _admin_givexp(self, ctx, name: str, amount: int):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                ctx.guild.id, name
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Found", f"No clan `{name}`."))
            clan_id = row['id']
            await self._add_xp(clan_id, amount)
        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "XP Given",
                                        f"Gave **{amount}** XP to **{name}**.", success=True))

    @admin_group.command(name="setlevel")
    @is_bot_owner()
    async def admin_setlevel_cmd(self, ctx, name: str, level: int):
        await self._admin_setlevel(ctx, name, level)

    async def _admin_setlevel(self, ctx, name: str, level: int):
        level = max(1, min(level, MAX_LEVEL))
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE clans SET level=$1, xp = ($1-1)*$2 WHERE guild_id=$3 AND LOWER(name)=LOWER($4)",
                level, XP_PER_LEVEL, ctx.guild.id, name
            )
        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Level Set",
                                        f"**{name}** is now level **{level}** (capped at 100).", success=True))

    # ======================== ENABLE / DISABLE ========================
    @clan_group.command(name="enable")
    @is_bot_owner()
    async def enable_cmd(self, ctx):
        await set_clan_enabled(self.bot, ctx.guild.id, True)
        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Clan Commands Enabled",
                                        "Clan commands are now enabled for this server.", success=True))

    @clan_group.command(name="disable")
    @is_bot_owner()
    async def disable_cmd(self, ctx):
        await set_clan_enabled(self.bot, ctx.guild.id, False)
        await ctx.send(embed=make_embed(ctx, EMOJI_MUTE, "Clan Commands Disabled",
                                        "Clan commands are now disabled for non-owner users.", success=True))

    # ======================== BYPASS CHANNEL ========================
    @clan_group.command(name="bypasschannel")
    @is_bot_owner()
    async def bypass_channel_cmd(self, ctx, user: discord.User, channel: discord.TextChannel):
        await add_bypass_channel(self.bot, ctx.guild.id, user.id, channel.id)
        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Bypass Added",
                                        f"{user.mention} can now use clan commands in {channel.mention}.",
                                        success=True))

    # ======================== COSMETIC COMMANDS ========================
    @clan_group.command(name="cosmetics")
    @clan_cooldown()
    async def cosmetics_cmd(self, ctx, *, name: Optional[str] = None):
        await self._cosmetics(ctx, name)

    async def _cosmetics(self, ctx, name: Optional[str] = None):
        async with self.bot.db_pool.acquire() as conn:
            if name is None:
                row = await conn.fetchrow(
                    "SELECT clan_id FROM members WHERE user_id=$1 AND guild_id=$2",
                    ctx.author.id, ctx.guild.id
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "No Clan",
                                                           "Specify a clan name or join one first."))
                clan = await conn.fetchrow("SELECT * FROM clans WHERE id=$1", row['clan_id'])
            else:
                clan = await conn.fetchrow(
                    "SELECT * FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                    ctx.guild.id, name
                )
                if not clan:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Found", f"No clan `{name}`."))

        level = clan['level']
        motto = clan.get('clan_motto')
        badge = clan.get('clan_badge')
        logo_url = clan.get('logo_url')
        banner_url = clan.get('banner_url')
        banner_type = clan.get('banner_type', 'static')
        embed_color = clan.get('embed_color', '#9d4edd')
        description = clan.get('clan_description')

        cosmetics_info = [
            {"name": "Clan Motto", "level": COSMETIC_UNLOCKS['motto'], "unlocked": level >= COSMETIC_UNLOCKS['motto'], "equipped": motto is not None},
            {"name": "Clan Badge", "level": COSMETIC_UNLOCKS['badge'], "unlocked": level >= COSMETIC_UNLOCKS['badge'], "equipped": badge is not None},
            {"name": "Clan Logo", "level": COSMETIC_UNLOCKS['logo'], "unlocked": level >= COSMETIC_UNLOCKS['logo'], "equipped": logo_url is not None},
            {"name": "Static Banner", "level": COSMETIC_UNLOCKS['static_banner'], "unlocked": level >= COSMETIC_UNLOCKS['static_banner'], "equipped": banner_url is not None and banner_type == 'static'},
            {"name": "Animated Banner", "level": COSMETIC_UNLOCKS['animated_banner'], "unlocked": level >= COSMETIC_UNLOCKS['animated_banner'], "equipped": banner_url is not None and banner_type == 'animated'},
            {"name": "Custom Color", "level": COSMETIC_UNLOCKS['embed_color'], "unlocked": level >= COSMETIC_UNLOCKS['embed_color'], "equipped": embed_color != '#9d4edd'},
            {"name": "Description", "level": COSMETIC_UNLOCKS['description'], "unlocked": level >= COSMETIC_UNLOCKS['description'], "equipped": description is not None},
        ]

        lines = []
        for item in cosmetics_info:
            status = f"{EMOJI_TICK} Unlocked" if item["unlocked"] else f"{EMOJI_LOCK} Locked"
            equipped = " *(equipped)*" if item["equipped"] else ""
            lines.append(f"**{item['name']}** — {status} (Lvl {item['level']}){equipped}")

        embed = discord.Embed(
            title=f"{EMOJI_COLOR} {clan['name']} — Cosmetics",
            description="\n".join(lines),
            color=0x9d4edd
        )
        embed.set_footer(text=f"Clan Level {level} • Use clan [motto|badge|color|description] to customize")
        await ctx.send(embed=embed)

    @clan_group.command(name="motto")
    @clan_cooldown()
    async def motto_cmd(self, ctx, *, motto: str):
        await self._motto(ctx, motto)

    async def _motto(self, ctx, motto: str):
        motto = motto.strip()[:60]
        if not motto:
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Invalid Motto", "Motto must be 1-60 characters."))
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row or row['role'] != 'owner':
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner", "Only the clan owner can set the motto."))
            clan = await conn.fetchrow("SELECT level FROM clans WHERE id=$1", row['clan_id'])
            if clan['level'] < COSMETIC_UNLOCKS['motto']:
                return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                    f"Clan Motto unlocks at **Level {COSMETIC_UNLOCKS['motto']}**. Your clan is Level **{clan['level']}**."))
            await conn.execute("UPDATE clans SET clan_motto=$1 WHERE id=$2", motto, row['clan_id'])
        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Motto Set!", f"Clan motto: *\"{motto}\"*", success=True))

    @clan_group.command(name="badge")
    @clan_cooldown()
    async def badge_cmd(self, ctx):
        await self._badge(ctx)

    async def _badge(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row or row['role'] != 'owner':
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner", "Only the clan owner can set the badge."))
            clan = await conn.fetchrow("SELECT level FROM clans WHERE id=$1", row['clan_id'])
            if clan['level'] < COSMETIC_UNLOCKS['badge']:
                return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                    f"Clan Badge unlocks at **Level {COSMETIC_UNLOCKS['badge']}**. Your clan is Level **{clan['level']}**."))
            clan_id = row['clan_id']

        view = BadgeView(self, ctx, clan_id)
        embed = discord.Embed(
            title=f"{EMOJI_COLOR} Choose a Clan Badge",
            description="Select a badge from the dropdown menu below.",
            color=0x9d4edd
        )
        await ctx.send(embed=embed, view=view)

    @clan_group.command(name="color")
    @clan_cooldown()
    async def color_cmd(self, ctx, color: str):
        await self._color(ctx, color)

    async def _color(self, ctx, color: str):
        color = color.strip()
        if not color.startswith('#'):
            color = '#' + color
        if not re.match(r'^#([A-Fa-f0-9]{6})$', color):
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Invalid Color", "Please provide a valid HEX color like `#9d4edd`."))
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row or row['role'] != 'owner':
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner", "Only the clan owner can set the embed color."))
            clan = await conn.fetchrow("SELECT level FROM clans WHERE id=$1", row['clan_id'])
            if clan['level'] < COSMETIC_UNLOCKS['embed_color']:
                return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                    f"Custom Embed Color unlocks at **Level {COSMETIC_UNLOCKS['embed_color']}**. Your clan is Level **{clan['level']}**."))
            await conn.execute("UPDATE clans SET embed_color=$1 WHERE id=$2", color, row['clan_id'])
        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Color Set!", f"Clan embed color: {color}", success=True))

    @clan_group.command(name="description")
    @clan_cooldown()
    async def description_cmd(self, ctx, *, description: str):
        await self._description(ctx, description)

    async def _description(self, ctx, description: str):
        description = description.strip()[:1000]
        if not description:
            return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Invalid Description", "Description must be 1-1000 characters."))
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row or row['role'] != 'owner':
                return await ctx.send(embed=make_embed(ctx, EMOJI_WRONG, "Not Owner", "Only the clan owner can set the description."))
            clan = await conn.fetchrow("SELECT level FROM clans WHERE id=$1", row['clan_id'])
            if clan['level'] < COSMETIC_UNLOCKS['description']:
                return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                    f"Clan Description unlocks at **Level {COSMETIC_UNLOCKS['description']}**. Your clan is Level **{clan['level']}**."))
            await conn.execute("UPDATE clans SET clan_description=$1 WHERE id=$2", description, row['clan_id'])
        await ctx.send(embed=make_embed(ctx, EMOJI_TICK, "Description Set!", "Clan description updated.", success=True))

    # ======================== Helpers ========================
    async def _confirm_action(self, ctx, prompt, callback, *args):
        msg = await ctx.send(embed=make_embed(ctx, EMOJI_ALERT, "Confirmation", prompt, color=0xe67e22))
        await msg.add_reaction(EMOJI_TICK)

        def check(reaction, user):
            return (user == ctx.author and str(reaction.emoji) == EMOJI_TICK
                    and reaction.message.id == msg.id)
        try:
            await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
            await msg.delete()
            await callback(ctx, *args)
        except asyncio.TimeoutError:
            await msg.delete()
            await ctx.send(embed=make_embed(ctx, EMOJI_MUTE, "Cancelled", "Confirmation timed out."))

    async def _set_nick(self, member, clan_name):
        suffix = to_superscript(clan_name)
        base = member.nick if member.nick else member.name
        new = f"{base}{suffix}"
        if len(new) > 32:
            max_base = 32 - len(suffix)
            base = base[:max_base]
            new = f"{base}{suffix}"
        try:
            await member.edit(nick=new)
        except discord.Forbidden:
            pass

    # ======================== Event listeners ========================
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.nick == after.nick:
            return
        if after.id in self._nick_lock:
            return
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT c.name FROM members m JOIN clans c ON m.clan_id=c.id WHERE m.user_id=$1 AND m.guild_id=$2",
                after.id, after.guild.id
            )
            if not row:
                return
            suffix = to_superscript(row['name'])
            if after.nick and after.nick.endswith(suffix):
                return
            base = after.nick if after.nick else after.name
            new = f"{base}{suffix}"
            if len(new) > 32:
                max_base = 32 - len(suffix)
                base = base[:max_base]
                new = f"{base}{suffix}"
            self._nick_lock.add(after.id)
            try:
                await after.edit(nick=new)
            except discord.Forbidden:
                pass
            finally:
                self._nick_lock.discard(after.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                member.id, member.guild.id
            )
            if not row:
                return
            clan_id, role = row['clan_id'], row['role']
            if role == "owner":
                co = await conn.fetchrow(
                    "SELECT user_id FROM members WHERE clan_id=$1 AND role='coowner' LIMIT 1",
                    clan_id
                )
                if co:
                    await conn.execute("UPDATE members SET role='owner' WHERE clan_id=$1 AND user_id=$2", clan_id, co['user_id'])
                    await conn.execute("UPDATE clans SET owner_id=$1 WHERE id=$2", co['user_id'], clan_id)
                else:
                    await conn.execute("DELETE FROM members WHERE clan_id=$1", clan_id)
                    await conn.execute("DELETE FROM clans WHERE id=$1", clan_id)
            else:
                await conn.execute("DELETE FROM members WHERE user_id=$1 AND guild_id=$2", member.id, member.guild.id)


class BadgeDropdown(discord.ui.Select):
    def __init__(self, cog, ctx, clan_id):
        self.cog = cog
        self.ctx = ctx
        self.clan_id = clan_id
        
        badge_names = {
            "<:sheild:1530744086859088104>": "Shield",
            "<a:sword_cf:1530746163693879366>": "Sword",
            "<:Dragon:1530747574494036038>": "Dragon",
            "<:elder_crown:1530175402495053924>": "Elder Crown",
            "<a:purple_fire:1530747727065907262>": "Purple Fire",
            "<:moon:1530747865968939118>": "Moon",
            "<a:Snowflake:1530748000521949285>": "Snowflake",
            "<a:lightning:1530748179081854987>": "Lightning",
            "<a:skull:1530748316814409870>": "Skull",
            "<:purple_flowers:1530748462214283337>": "Purple Flowers",
            "<a:Maple_Leaf:1530750075117109270>": "Maple Leaf",
            "<:Eagles:1530750290234708040>": "Eagles"
        }
        
        options = []
        for badge in BADGE_OPTIONS:
            name = badge_names.get(badge, "Badge")
            options.append(
                discord.SelectOption(
                    label=name,
                    value=badge,
                    description=f"Select {name} as your clan badge",
                )
            )
        
        super().__init__(
            placeholder="Choose a clan badge...",
            min_values=1,
            max_values=1,
            options=options[:25]
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("This isn't for you.", ephemeral=True)
            return
        
        selected_badge = self.values[0]
        
        async with self.cog.bot.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE clans SET clan_badge=$1 WHERE id=$2",
                selected_badge, self.clan_id
            )
        
        await interaction.response.send_message(
            embed=make_embed(self.ctx, EMOJI_TICK, "Badge Set!",
                f"Clan badge set to: {selected_badge}", success=True)
        )


class BadgeView(discord.ui.View):
    def __init__(self, cog, ctx, clan_id):
        super().__init__(timeout=60)
        self.add_item(BadgeDropdown(cog, ctx, clan_id))


class InviteAcceptView(discord.ui.View):
    def __init__(self, bot, invitee: discord.Member, ctx, clan_id, inviter):
        super().__init__(timeout=86400)
        self.bot = bot
        self.invitee = invitee
        self.ctx = ctx
        self.clan_id = clan_id
        self.inviter = inviter

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, emoji="<a:tick:1501584376540954665>")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.invitee.id:
            return await interaction.response.send_message("This invitation is not for you.", ephemeral=True)

        async with self.bot.db_pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT 1 FROM members WHERE user_id=$1 AND guild_id=$2",
                self.invitee.id, self.ctx.guild.id
            )
            if existing:
                return await interaction.response.send_message("You're already in a clan.", ephemeral=True)

            row = await conn.fetchrow("SELECT name FROM clans WHERE id=$1", self.clan_id)
            clan_name = row['name'] if row else "Unknown"

            await conn.execute(
                "INSERT INTO members (user_id, guild_id, clan_id, role, original_nick) VALUES ($1, $2, $3, 'member', $4)",
                self.invitee.id, self.ctx.guild.id, self.clan_id, self.invitee.nick
            )

        suffix = to_superscript(clan_name)
        base = self.invitee.nick if self.invitee.nick else self.invitee.name
        new_nick = f"{base}{suffix}"
        if len(new_nick) > 32:
            max_base = 32 - len(suffix)
            base = base[:max_base]
            new_nick = f"{base}{suffix}"
        try:
            await self.invitee.edit(nick=new_nick)
        except discord.Forbidden:
            pass

        await interaction.response.send_message(f"{EMOJI_TICK} You've joined **{clan_name}**!", ephemeral=True)
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red, emoji="<:wrong:1501538221530808464>")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.invitee.id:
            return await interaction.response.send_message("This invitation is not for you.", ephemeral=True)

        await interaction.response.send_message(f"{EMOJI_WRONG} Invitation declined.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)


class SlashContext:
    def __init__(self, interaction):
        self.bot = interaction.client
        self.author = interaction.user
        self.guild = interaction.guild
        self.channel = interaction.channel
        self.message = None
        self.interaction = interaction
        self.prefix = "/"

    async def send(self, *args, **kwargs):
        if not self.interaction.response.is_done():
            return await self.interaction.response.send_message(*args, **kwargs)
        else:
            return await self.interaction.followup.send(*args, **kwargs)


async def setup(bot):
    await init_db(bot)
    cog = Clans(bot)
    await bot.add_cog(cog)

    @bot.tree.command(name="clan_admin_delete", description="(Bot owner) Force delete a clan")
    @app_commands.describe(name="Clan name")
    async def slash_admin_delete(interaction: discord.Interaction, name: str):
        if interaction.user.id != bot.owner_id:
            return await interaction.response.send_message("Only the bot owner.", ephemeral=True)
        sc = SlashContext(interaction)
        await cog._admin_delete(sc, name)

    @bot.tree.command(name="clan_admin_givexp", description="(Bot owner) Give XP to a clan")
    @app_commands.describe(name="Clan name", amount="XP amount")
    async def slash_admin_givexp(interaction: discord.Interaction, name: str, amount: int):
        if interaction.user.id != bot.owner_id:
            return await interaction.response.send_message("Only the bot owner.", ephemeral=True)
        sc = SlashContext(interaction)
        await cog._admin_givexp(sc, name, amount)

    @bot.tree.command(name="clan_admin_setlevel", description="(Bot owner) Set clan level")
    @app_commands.describe(name="Clan name", level="New level")
    async def slash_admin_setlevel(interaction: discord.Interaction, name: str, level: int):
        if interaction.user.id != bot.owner_id:
            return await interaction.response.send_message("Only the bot owner.", ephemeral=True)
        sc = SlashContext(interaction)
        await cog._admin_setlevel(sc, name, level)