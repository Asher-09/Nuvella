import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import time
import os
import re
import json
import asyncpg
from typing import Optional, List
import logging

from utils.emoji import EMOJIS
from utils.components import ComponentsV2View, prepare_v2_view, disable_all_components
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

# Admin/senior mod role IDs (for clan admin commands)
ADMIN_SENIOR_MOD_ROLE_IDS = [
    ROLES_CONFIG.get("admin_role_id"),
    ROLES_CONFIG.get("senior_moderator_role_id")
]
ADMIN_SENIOR_MOD_ROLE_IDS = [rid for rid in ADMIN_SENIOR_MOD_ROLE_IDS if rid]

# Blacklist roles
CLAN_BLACKLIST_ROLE = MASTER_CONFIG.get("clan_blacklist_role")  # single role ID
BLACKLIST_ROLES = MASTER_CONFIG.get("blacklist_roles", [])       # list of role IDs

# The primary bot initializes the single shared PostgreSQL pool before loading cogs.

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
EMOJI_LOCK = EMOJIS["lock"]
BREAKER_LINE    = EMOJIS["breaker"] * 10

# Additional emojis used in clan system
POINT_EMOJI = EMOJIS["purple_star"]   # for clan points display

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

# Level constants
MAX_LEVEL = 100
LEVEL_UP_BASE_COST = 100_000   # cost to go from level 1 to 2
LEVEL_UP_INCREMENT = 50_000    # additional cost per level after that

# Cosmetic unlock levels
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
    f"{EMOJIS['dragon_shield']}", f"{EMOJIS['swords']}", f"{EMOJIS['dragon']}", f"{EMOJIS['purple_crown']}", f"{EMOJIS['fire']}", f"{EMOJIS['moon']}", f"{EMOJIS['snowflake']}", f"{EMOJIS['lightning']}", f"{EMOJIS['black_skull']}", f"{EMOJIS['purple_flower']}", f"{EMOJIS['maple_leaf']}", f"{EMOJIS['golden_eagles']}"
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

# ------------------------- Custom exceptions -------------------------
class EconomyError(Exception):
    pass

class InsufficientFunds(EconomyError):
    pass

class ClanNameTaken(EconomyError):
    pass

class AlreadyInClan(EconomyError):
    pass

class ClanFull(EconomyError):
    pass

class NotInvited(EconomyError):
    pass

class PrivateClan(EconomyError):
    pass

class AlreadyClaimed(EconomyError):
    pass

class NoClan(EconomyError):
    pass

# ------------------------- Audit helper -------------------------
async def record_economy_transaction(conn, user_id: int, amount: int, transaction_type: str, source: str = None, reference_id: str = None):
    await conn.execute(
        "INSERT INTO economy_transactions (user_id, amount, transaction_type, source, reference_id) "
        "VALUES ($1, $2, $3, $4, $5)",
        user_id, amount, transaction_type, source, reference_id
    )

# ------------------------- Atomic economy functions -------------------------
async def eco_get_balance(bot, uid: int) -> int:
    """Return the wallet balance (spendable) of a user."""
    async with bot.db_pool.acquire() as conn:
        await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", uid)
        row = await conn.fetchval("SELECT wallet FROM users WHERE id=$1", uid)
        return row or 0

async def eco_add_coins(bot, uid: int, amount: int):
    """Add coins atomically and log."""
    async with bot.db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", uid)
            new_wallet = await conn.fetchval(
                "UPDATE users SET wallet = wallet + $1 WHERE id=$2 RETURNING wallet",
                amount, uid
            )
            await record_economy_transaction(conn, uid, amount, "add", "economy")
        await log_to_discord(
            bot,
            f"{EMOJIS["moon_dust"]} **Balance Change** | User: {uid} | +{format_coins(amount)} | New Wallet: {format_coins(new_wallet)}"
        )

async def eco_deduct_coins(bot, uid: int, amount: int) -> bool:
    """Deduct from wallet atomically if sufficient."""
    success = False
    new_wallet = 0
    async with bot.db_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", uid)
            row = await conn.fetchrow(
                "UPDATE users SET wallet = wallet - $1 WHERE id=$2 AND wallet >= $1 RETURNING wallet",
                amount, uid
            )
            if row:
                new_wallet = row['wallet']
                await record_economy_transaction(conn, uid, -amount, "deduct", "economy")
                success = True
            else:
                balance = await conn.fetchval("SELECT wallet FROM users WHERE id=$1", uid)
                new_wallet = balance or 0
        if success:
            await log_to_discord(
                bot,
                f"{EMOJIS["moon_dust"]} **Balance Change** | User: {uid} | -{format_coins(amount)} | New Wallet: {format_coins(new_wallet)}"
            )
        else:
            await log_to_discord(
                bot,
                f"{EMOJIS["moon_dust"]} **Balance Deduction Failed** | User: {uid} | Attempted -{format_coins(amount)} | Wallet: {format_coins(new_wallet)}"
            )
        return success

# ------------------------- Database init -------------------------
async def init_db(bot):
    if getattr(bot, "pool", None) is None:
        raise RuntimeError("The shared PostgreSQL pool was not initialized by main.py.")
    bot.db_pool = bot.pool  # legacy attribute used by this cog; same pool as every other cog
    async with bot.db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS clans (
                id SERIAL PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                name TEXT NOT NULL,
                owner_id BIGINT NOT NULL,
                level INT DEFAULT 1,
                logo_url TEXT,
                banner_url TEXT,
                banner_type TEXT DEFAULT 'static',
                visibility TEXT DEFAULT 'public',
                clan_motto TEXT,
                clan_badge TEXT,
                embed_color TEXT DEFAULT '#9d4edd',
                clan_description TEXT,
                total_deposits BIGINT NOT NULL DEFAULT 0,
                member_count INT NOT NULL DEFAULT 0,
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
            CREATE TABLE IF NOT EXISTS deposits (
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
            CREATE TABLE IF NOT EXISTS users (
                id BIGINT PRIMARY KEY,
                wallet BIGINT NOT NULL DEFAULT 0,
                bank BIGINT NOT NULL DEFAULT 0,
                xp BIGINT NOT NULL DEFAULT 0
            )
        """)
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS economy_transactions (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount BIGINT NOT NULL,
                transaction_type TEXT NOT NULL,
                source TEXT,
                reference_id TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add columns if table existed previously
        await conn.execute("ALTER TABLE clans ADD COLUMN IF NOT EXISTS total_deposits BIGINT NOT NULL DEFAULT 0")
        await conn.execute("ALTER TABLE clans ADD COLUMN IF NOT EXISTS member_count INT NOT NULL DEFAULT 0")

        # Recompute stored aggregates for existing data
        await conn.execute("""
            UPDATE clans c
            SET total_deposits = COALESCE((SELECT SUM(amount) FROM deposits d WHERE d.clan_id = c.id), 0)
        """)
        await conn.execute("""
            UPDATE clans c
            SET member_count = (SELECT COUNT(*) FROM members m WHERE m.clan_id = c.id)
        """)

        # Indexes
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_deposits_clan_id ON deposits(clan_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_deposits_user_id ON deposits(user_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_deposits_guild_id ON deposits(guild_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_members_user_guild ON members(user_id, guild_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_members_clan ON members(clan_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_clans_guild ON clans(guild_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_clans_guild_name ON clans(guild_id, name)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_clans_guild_lower_name ON clans(guild_id, LOWER(name))")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_weekly_claims_user_guild_clan ON weekly_claims(user_id, guild_id, clan_id)")

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

def has_admin_or_senior_mod():
    async def pred(ctx):
        if ctx.author.id == ctx.bot.owner_id:
            return True
        if not ADMIN_SENIOR_MOD_ROLE_IDS:
            return False
        user_roles = [role.id for role in ctx.author.roles]
        return any(rid in user_roles for rid in ADMIN_SENIOR_MOD_ROLE_IDS)
    return commands.check(pred)

# ------------------------- Cooldown decorators -------------------------
def clan_cooldown():
    """1 use per 5 seconds per user."""
    return commands.cooldown(1, 5, commands.BucketType.user)

def guild_cooldown():
    """10 uses per 10 seconds per guild (global clan command throttle)."""
    return commands.cooldown(10, 10, commands.BucketType.guild)

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
                deposit_count = await conn.fetchval("SELECT COUNT(*) FROM deposits")
                user_count = await conn.fetchval("SELECT COUNT(*) FROM users")
                total_coins = await conn.fetchval("SELECT COALESCE(SUM(wallet + bank), 0) FROM users")

            log_msg = (
                f"Clans: {clan_count} | Members: {member_count} | Deposits: {deposit_count} | "
                f"Economy users: {user_count} | Total coins: {format_coins(total_coins)}"
            )
            await log_to_discord(self.bot, log_msg)
        except Exception as e:
            await log_to_discord(self.bot, f"Hourly status failed: {e}")

    @hourly_status.before_loop
    async def before_hourly(self):
        await self.bot.wait_until_ready()


    # ---------- Global check for clan group ----------
    async def cog_check(self, ctx):
        if ctx.command.qualified_name.startswith("clan"):
            return await clan_command_check(ctx)
        return True

    # ---------- Helper: level from total deposits ----------
    def get_level_from_deposits(self, total_deposits: int) -> int:
        """Calculate clan level based on total deposits."""
        if total_deposits <= 0:
            return 1
        n = 0  # number of level-ups
        while n < MAX_LEVEL - 1:
            cumulative = 25000 * n * (n + 3)
            if cumulative > total_deposits:
                break
            n += 1
        return n

    def get_threshold_for_level(self, level: int) -> int:
        """Return cumulative deposits required to reach a given level (1-based)."""
        if level <= 1:
            return 0
        n = level - 1
        return 25000 * n * (n + 3)

    # ---------- Clan group definition with rate limiting ----------
    @commands.group(name="clan", aliases=["clans"], invoke_without_command=True)
    @clan_cooldown()
    @guild_cooldown()
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
                title=f"{EMOJIS["coc"]} Clan System",
                description="Join or create a clan to gain powerful perks and weekly Moon Dust rewards!",
                color=0x9d4edd
            )
            embed.add_field(
                name=f"{EMOJIS["dust_bag"]} Weekly Rewards",
                value="Earn Moon Dust every week based on your clan's level. **Max 1,000,000 Moon Dust at level 100.**",
                inline=False
            )
            embed.add_field(
                name=f"{EMOJIS["chart"]} Level Up",
                value="Your clan level increases automatically when total deposits reach the required amount. Each level requires +50k more than the previous.",
                inline=False
            )
            embed.add_field(
                name=f"{EMOJIS["search"]} Quick Start",
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
    @guild_cooldown()
    async def clan_help(self, ctx):
        prefix = ctx.prefix
        embed = discord.Embed(
            title=f"{EMOJIS["coc"]} CLAN SYSTEM COMMANDS",
            color=0x9d4edd,
            description=f"Master the clan system and dominate the server! {EMOJIS["purple_crown"]}"
        )
        embed.set_thumbnail(url=ctx.guild.icon.url if ctx.guild.icon else None)

        embed.add_field(
            name=f"{EMOJIS["coc"]} CLAN MANAGEMENT",
            value=(
                f"`{prefix}clan create <name>` – Create a clan\n"
                f"`{prefix}clan delete` – Delete your owned clan\n"
                f"`{prefix}clan leave` – Leave your current clan\n"
                f"`{prefix}clan rename <name>` – Rename clan for\n"
                f"`{prefix}clan set <public/private>` – Toggle visibility"
            ),
            inline=False
        )

        embed.add_field(
            name=f"{EMOJIS["user_id"]} MEMBER MANAGEMENT",
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
            name=f"{EMOJIS["stats"]} INFO & VIEWING",
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
            name=f"{EMOJIS["purple_star"]} PROGRESSION & REWARDS",
            value=(
                f"`{prefix}clan deposit <amount>` – Deposit Moon Dust into clan bank\n"
                f"`{prefix}clan bank` – View total deposited Moon Dust\n"
                f"`{prefix}clan weekly` – Claim weekly bonus\n\n"
    
            ),
            inline=False
        )

        embed.add_field(
            name=f"{EMOJIS["color"]} CUSTOMIZATION",
            value=(
                f"`{prefix}clan cosmetics` – See what you can unlock\n"
                f"`{prefix}clan motto <text>` – Set clan motto (Lvl {COSMETIC_UNLOCKS['motto']}+)\n"
                f"`{prefix}clan badge` – Choose a badge (Lvl {COSMETIC_UNLOCKS['badge']}+)\n"
                f"`{prefix}clan color <hex>` – Set embed color (Lvl {COSMETIC_UNLOCKS['embed_color']}+)\n"
                f"`{prefix}clan description <text>` – Add a description (Lvl {COSMETIC_UNLOCKS['description']}+)"
            ),
            inline=False
        )

        embed.set_footer(text=f"{EMOJIS['light_bulb']} Tip: Deposit Moon Dust to increase your clan level!")
        await ctx.send(embed=embed)

    # ======================== CREATE ========================
    @clan_group.command(name="create")
    @clan_cooldown()
    @guild_cooldown()
    async def create_cmd(self, ctx, *, name: str):
        await self._create(ctx, name)

    async def _create(self, ctx, name: str):
        name = name.strip()
        if not name:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Invalid Name", "Clan name cannot be empty."))
        if len(name) > 30:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Invalid Name", "Clan name must be 30 characters or fewer."))

        try:
            async with self.bot.db_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", ctx.author.id)
                    wallet_row = await conn.fetchrow(
                        "UPDATE users SET wallet = wallet - $1 WHERE id=$2 AND wallet >= $1 RETURNING wallet",
                        5_000_000, ctx.author.id
                    )
                    if not wallet_row:
                        raise InsufficientFunds()

                    clan_id = await conn.fetchval(
                        "INSERT INTO clans (guild_id, name, owner_id, member_count, total_deposits) "
                        "VALUES ($1, $2, $3, 1, 0) "
                        "ON CONFLICT (guild_id, name) DO NOTHING RETURNING id",
                        ctx.guild.id, name, ctx.author.id
                    )
                    if not clan_id:
                        raise ClanNameTaken()

                    inserted_user = await conn.fetchval(
                        "INSERT INTO members (user_id, guild_id, clan_id, role, original_nick) "
                        "VALUES ($1, $2, $3, 'owner', $4) "
                        "ON CONFLICT (user_id, guild_id) DO NOTHING RETURNING user_id",
                        ctx.author.id, ctx.guild.id, clan_id, ctx.author.nick
                    )
                    if not inserted_user:
                        raise AlreadyInClan()

                    await record_economy_transaction(
                        conn, ctx.author.id, -5_000_000, "clan_create", "clan", str(clan_id)
                    )
        except InsufficientFunds:
            balance = await eco_get_balance(self.bot, ctx.author.id)
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Insufficient Funds",
                f"Creating a clan costs **5,000,000** Moon Dust. You only have **{format_coins(balance)}** Moon Dust."))
        except ClanNameTaken:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Name Taken", f"`{name}` already exists."))
        except AlreadyInClan:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Already in a Clan",
                                                   "You must leave or delete your current clan first."))

        await self._set_nick(ctx.author, name)
        await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Clan Created!",
                                        f"**{name}** is now yours. Invite members with `{ctx.prefix}clan invite @user`.",
                                        success=True))

    # ======================== DELETE ========================
    @clan_group.command(name="delete")
    @clan_owner_only()
    @clan_cooldown()
    @guild_cooldown()
    async def delete_cmd(self, ctx):
        await self._delete(ctx)

    async def _delete(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT c.id, c.name FROM clans c JOIN members m ON c.id=m.clan_id WHERE m.user_id=$1 AND m.guild_id=$2 AND m.role='owner'",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner", "You don't own a clan."))
            clan_id, name = row['id'], row['name']

        await self._confirm_action(ctx, f"Delete **{name}**? This cannot be undone.",
                                   self._do_delete, clan_id)

    async def _do_delete(self, ctx, clan_id):
        # First: perform all database deletions in a single transaction, collecting nick data
        records = []
        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                records = await conn.fetch(
                    "SELECT user_id, original_nick FROM members WHERE clan_id=$1",
                    clan_id
                )
                await conn.execute("DELETE FROM members WHERE clan_id=$1", clan_id)
                await conn.execute("DELETE FROM invites WHERE clan_id=$1", clan_id)
                await conn.execute("DELETE FROM deposits WHERE clan_id=$1", clan_id)
                await conn.execute("DELETE FROM weekly_claims WHERE clan_id=$1", clan_id)
                await conn.execute("DELETE FROM clans WHERE id=$1", clan_id)

        # Second: restore Discord nicknames outside DB transaction
        tasks = []
        for record in records:
            uid = record['user_id']
            original_nick = record['original_nick']
            member = ctx.guild.get_member(uid)
            if member:
                tasks.append(asyncio.create_task(member.edit(nick=original_nick)))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        await ctx.send(embed=make_embed(ctx, EMOJIS["delete"], "Clan Deleted",
                                        "The clan has been deleted.", success=True))

    # ======================== LEAVE ========================
    @clan_group.command(name="leave")
    @clan_cooldown()
    @guild_cooldown()
    async def leave_cmd(self, ctx):
        await self._leave(ctx)

    async def _leave(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "No Clan", "You aren't in a clan."))
            clan_id, role = row['clan_id'], row['role']

            if role == "owner":
                await self._confirm_action(ctx,
                    "You are the owner. Leaving will promote a Co‑Owner if one exists, otherwise the clan will be deleted. Are you sure?",
                    self._owner_leave, clan_id)
            else:
                await self._remove_member(clan_id, ctx.author)
                await ctx.send(embed=make_embed(ctx, EMOJIS["unlock"], "Left Clan",
                                                "You left the clan.", success=True))

    async def _owner_leave(self, ctx, clan_id):
        # Use transaction for ownership transfer or deletion
        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                co = await conn.fetchrow(
                    "SELECT user_id FROM members WHERE clan_id=$1 AND role='coowner' LIMIT 1 FOR UPDATE",
                    clan_id
                )
                if co:
                    new_owner = co['user_id']
                    await conn.execute("UPDATE members SET role='owner' WHERE clan_id=$1 AND user_id=$2", clan_id, new_owner)
                    await conn.execute("UPDATE clans SET owner_id=$1 WHERE id=$2", new_owner, clan_id)
                    await self._remove_member_atomic(conn, clan_id, ctx.author.id)
                    await ctx.send(embed=make_embed(ctx, EMOJIS["owner"], "Ownership Passed",
                        f"<@{new_owner}> is now the owner.", success=True))
                else:
                    await self._do_delete_atomic(conn, clan_id)
                    await ctx.send(embed=make_embed(ctx, EMOJIS["delete"], "Clan Deleted",
                                                    "The clan has been deleted.", success=True))

        # Restore nickname outside DB
        if co:
            member = ctx.guild.get_member(ctx.author.id)
            if member:
                original_nick = None
                async with self.bot.db_pool.acquire() as conn:
                    row = await conn.fetchrow("SELECT original_nick FROM members WHERE clan_id=$1 AND user_id=$2", clan_id, ctx.author.id)
                    if row:
                        original_nick = row['original_nick']
                if original_nick is not None:
                    try:
                        await member.edit(nick=original_nick)
                    except discord.Forbidden:
                        pass
        else:
            # _do_delete_atomic already deleted all; restore nicks happened in _do_delete_atomic? We'll handle separately
            # But _owner_leave currently uses _do_delete_atomic which should restore nicks.
            pass

    async def _do_delete_atomic(self, conn, clan_id):
        records = await conn.fetch("SELECT user_id, original_nick FROM members WHERE clan_id=$1", clan_id)
        await conn.execute("DELETE FROM members WHERE clan_id=$1", clan_id)
        await conn.execute("DELETE FROM invites WHERE clan_id=$1", clan_id)
        await conn.execute("DELETE FROM deposits WHERE clan_id=$1", clan_id)
        await conn.execute("DELETE FROM weekly_claims WHERE clan_id=$1", clan_id)
        await conn.execute("DELETE FROM clans WHERE id=$1", clan_id)
        # Store records for nickname restore after transaction if needed
        return records

    async def _remove_member_atomic(self, conn, clan_id, user_id):
        await conn.execute("DELETE FROM members WHERE clan_id=$1 AND user_id=$2", clan_id, user_id)
        await conn.execute("UPDATE clans SET member_count = member_count - 1 WHERE id=$1 AND member_count > 0", clan_id)

    async def _remove_member(self, clan_id, member: discord.Member):
        original_nick = None
        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT original_nick FROM members WHERE clan_id=$1 AND user_id=$2",
                    clan_id, member.id
                )
                original_nick = row['original_nick'] if row else None
                await self._remove_member_atomic(conn, clan_id, member.id)
        if original_nick is not None:
            try:
                await member.edit(nick=original_nick)
            except discord.Forbidden:
                pass

    # ======================== INVITE ========================
    @clan_group.command(name="invite")
    @clan_owner_only()
    @clan_cooldown()
    @guild_cooldown()
    async def invite_cmd(self, ctx, member: discord.Member):
        await self._invite(ctx, member)

    async def _invite(self, ctx, member: discord.Member):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row or row['role'] != 'owner':
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner",
                                                       "Only the clan owner can invite members."))
            clan_id = row['clan_id']
            clan_name = await conn.fetchval("SELECT name FROM clans WHERE id=$1", clan_id)

        view = InviteAcceptView(self.bot, member, ctx, clan_id, ctx.author)
        invite_embed = discord.Embed(
            title=f"{EMOJIS["mail"]} Clan Invitation",
            description=f"{ctx.author.mention} invited you to join the clan **{clan_name}** on {ctx.guild.name}!",
            color=0x3498db
        )
        try:
            prepare_v2_view(view, invite_embed)
            await member.send(view=view)
            await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Invited",
                f"{member.mention} has been invited via DM.", success=True))
        except discord.Forbidden:
            try:
                prepare_v2_view(view, invite_embed)
                await ctx.channel.send(view=view)
                await ctx.send(embed=make_embed(ctx, EMOJIS["warning"], "DM Failed",
                    f"{member.mention}'s DMs are closed. Invitation sent in this channel instead."))
            except:
                await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Failed",
                    f"Could not invite {member.mention}."))

    # ======================== JOIN ========================
    @clan_group.command(name="join")
    @clan_cooldown()
    @guild_cooldown()
    async def join_cmd(self, ctx, *, clan_name: str):
        await self._join_public(ctx, clan_name)

    async def _join_public(self, ctx, clan_name: str):
        await self._join_by_name(ctx, clan_name, require_invite=False)

    async def _join_by_name(self, ctx, clan_name: str, require_invite: bool):
        try:
            async with self.bot.db_pool.acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        "SELECT id, visibility FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2) FOR UPDATE",
                        ctx.guild.id, clan_name
                    )
                    if not row:
                        raise NoClan(f"No clan `{clan_name}`.")
                    clan_id, vis = row['id'], row['visibility']

                    if require_invite:
                        inv = await conn.fetchval(
                            "SELECT 1 FROM invites WHERE clan_id=$1 AND user_id=$2 FOR UPDATE",
                            clan_id, ctx.author.id
                        )
                        if not inv:
                            raise NotInvited(f"You haven't been invited to **{clan_name}**.")
                        await conn.execute("DELETE FROM invites WHERE clan_id=$1 AND user_id=$2", clan_id, ctx.author.id)
                    else:
                        if vis != "public":
                            raise PrivateClan("This clan is private – you need an invitation.")

                    # Add member with capacity check and uniqueness
                    updated = await conn.fetchval(
                        "UPDATE clans SET member_count = member_count + 1 WHERE id=$1 AND member_count < 20 RETURNING member_count",
                        clan_id
                    )
                    if updated is None:
                        raise ClanFull("This clan is full.")

                    inserted = await conn.fetchval(
                        "INSERT INTO members (user_id, guild_id, clan_id, role, original_nick) "
                        "VALUES ($1, $2, $3, 'member', $4) "
                        "ON CONFLICT (user_id, guild_id) DO NOTHING RETURNING user_id",
                        ctx.author.id, ctx.guild.id, clan_id, ctx.author.nick
                    )
                    if inserted is None:
                        raise AlreadyInClan("You're already in a clan.")

                    clan_name_fetched = await conn.fetchval("SELECT name FROM clans WHERE id=$1", clan_id)

        except (NoClan, NotInvited, PrivateClan, ClanFull, AlreadyInClan) as e:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Error", str(e)))

        await self._set_nick(ctx.author, clan_name_fetched)
        await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Welcome!",
                                        f"{ctx.author.mention} joined the clan.", success=True))

    # ======================== INFO ========================
    @clan_group.command(name="info", aliases=["profile"])
    @clan_cooldown()
    @guild_cooldown()
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
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["file"], "No Clan",
                                                           "Specify a clan name or join one first."))
                clan_id = row['clan_id']
            else:
                row = await conn.fetchrow(
                    "SELECT id FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                    ctx.guild.id, name
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Found",
                                                           f"`{name}` doesn't exist."))
                clan_id = row['id']

            clan = await conn.fetchrow(
                "SELECT name, owner_id, level, logo_url, banner_url, banner_type, visibility, clan_motto, clan_badge, embed_color, clan_description, total_deposits, member_count FROM clans WHERE id=$1",
                clan_id
            )
            cname = clan['name']
            owner_id = clan['owner_id']
            level = clan['level']
            logo = clan['logo_url']
            banner = clan['banner_url']
            banner_type = clan.get('banner_type', 'static')
            visibility = clan['visibility']
            motto = clan.get('clan_motto')
            badge = clan.get('clan_badge')
            embed_color = clan.get('embed_color', '#9d4edd')
            description = clan.get('clan_description')
            total_deposits = clan['total_deposits'] or 0
            member_count = clan['member_count'] or 0

            roles = {"owner": [], "coowner": [], "elder": [], "member": []}
            members = await conn.fetch(
                "SELECT user_id, role FROM members WHERE clan_id=$1", clan_id
            )
            for record in members:
                uid, role = record['user_id'], record['role']
                member = ctx.guild.get_member(uid)
                mention = member.mention if member else f"<@{uid}>"
                roles[role].append(mention)

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
                name=f"{EMOJIS["owner"]} Owner",
                value="\n".join(roles["owner"]) or "None",
                inline=True
            )
            if roles["coowner"]:
                embed.add_field(
                    name=f"{EMOJIS["admin"]} Co‑Owner",
                    value="\n".join(roles["coowner"]),
                    inline=True
                )
            if roles["elder"]:
                embed.add_field(
                    name=f"{EMOJIS["mod"]} Elder",
                    value="\n".join(roles["elder"]),
                    inline=True
                )
            if roles["member"]:
                embed.add_field(
                    name=f"Members ({len(roles['member'])})",
                    value="\n".join(roles["member"]),
                    inline=False
                )

            embed.add_field(name=f"{EMOJIS["chart"]} Level", value=f"**{level}**", inline=True)
            embed.add_field(name="Total Deposits", value=f"{EMOJIS["moon_dust"]} **{format_coins(total_deposits)}**", inline=True)

            if level < MAX_LEVEL:
                next_level = level + 1
                threshold = self.get_threshold_for_level(next_level)
                progress = total_deposits
                embed.add_field(
                    name="Next Level",
                    value=f"**{format_coins(progress)}** / **{format_coins(threshold)}**",
                    inline=True
                )
            else:
                embed.add_field(name="Next Level", value="**MAX**", inline=True)

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
    @guild_cooldown()
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
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "No Clan",
                                                           "Specify a clan name or join one first."))
                clan_id = row['clan_id']
            else:
                row = await conn.fetchrow(
                    "SELECT id FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                    ctx.guild.id, name
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Found",
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

        embed = discord.Embed(title=f"{EMOJIS["snipe"]} {cname} Members", color=0x3498db)
        if roles["owner"]:
            embed.add_field(name=f"{EMOJIS["owner"]} Owner", value="\n".join(roles["owner"]), inline=True)
        if roles["coowner"]:
            embed.add_field(name=f"{EMOJIS["admin"]} Co‑Owner", value="\n".join(roles["coowner"]), inline=True)
        if roles["elder"]:
            embed.add_field(name=f"{EMOJIS["mod"]} Elder", value="\n".join(roles["elder"]), inline=True)
        if roles["member"]:
            embed.add_field(name="Members", value="\n".join(roles["member"]), inline=True)
        embed.add_field(name="\u200b", value=BREAKER_LINE, inline=False)
        await ctx.send(embed=embed)

    # ======================== PROMOTE ========================
    @clan_group.command(name="promote")
    @clan_owner_only()
    @clan_cooldown()
    @guild_cooldown()
    async def promote_cmd(self, ctx, member: discord.Member, role: str):
        await self._promote(ctx, member, role)

    async def _promote(self, ctx, member: discord.Member, role: str):
        role = role.lower().replace("-", "")
        if role not in ("elder", "coowner"):
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Invalid Role", "Use `elder` or `coowner`."))
        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2 FOR UPDATE",
                    ctx.author.id, ctx.guild.id
                )
                if not row or row['role'] != 'owner':
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner",
                                                           "Only the clan owner can promote."))
                clan_id = row['clan_id']
                target = await conn.fetchrow(
                    "SELECT role FROM members WHERE clan_id=$1 AND user_id=$2 FOR UPDATE",
                    clan_id, member.id
                )
                if not target:
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not in Clan",
                                                           f"{member.mention} is not in your clan."))
                if target['role'] == "owner":
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Cannot Promote", "They are the owner."))
                if target['role'] == role:
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["warning"], "Already", "They already hold that role."))

                if role == "coowner":
                    count = await conn.fetchval("SELECT COUNT(*) FROM members WHERE clan_id=$1 AND role='coowner'", clan_id)
                    if count >= MAX_COOWNERS:
                        return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Limit Reached",
                                                               f"Maximum {MAX_COOWNERS} co‑owners allowed."))
                elif role == "elder":
                    count = await conn.fetchval("SELECT COUNT(*) FROM members WHERE clan_id=$1 AND role='elder'", clan_id)
                    if count >= MAX_ELDERS:
                        return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Limit Reached",
                                                               f"Maximum {MAX_ELDERS} elders allowed."))

                await conn.execute("UPDATE members SET role=$1 WHERE clan_id=$2 AND user_id=$3", role, clan_id, member.id)
        await ctx.send(embed=make_embed(ctx, EMOJIS["chart"], "Promoted",
                                        f"{member.mention} is now **{role}**.", success=True))

    # ======================== DEMOTE ========================
    @clan_group.command(name="demote")
    @clan_owner_only()
    @clan_cooldown()
    @guild_cooldown()
    async def demote_cmd(self, ctx, member: discord.Member):
        await self._demote(ctx, member)

    async def _demote(self, ctx, member: discord.Member):
        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2 FOR UPDATE",
                    ctx.author.id, ctx.guild.id
                )
                if not row or row['role'] != 'owner':
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner",
                                                           "Only the owner can demote."))
                clan_id = row['clan_id']
                target = await conn.fetchrow(
                    "SELECT role FROM members WHERE clan_id=$1 AND user_id=$2 FOR UPDATE",
                    clan_id, member.id
                )
                if not target:
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not in Clan",
                                                           f"{member.mention} is not in your clan."))
                if target['role'] == "owner":
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Cannot Demote", "They are the owner."))
                if target['role'] == "member":
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["warning"], "Already",
                                                           "They are already a regular member."))

                await conn.execute("UPDATE members SET role='member' WHERE clan_id=$1 AND user_id=$2", clan_id, member.id)
        await ctx.send(embed=make_embed(ctx, EMOJIS["delete"], "Demoted",
                                        f"{member.mention} is now a member.", success=True))

    # ======================== TRANSFER ========================
    @clan_group.command(name="transfer")
    @clan_owner_only()
    @clan_cooldown()
    @guild_cooldown()
    async def transfer_cmd(self, ctx, member: discord.Member):
        await self._transfer(ctx, member)

    async def _transfer(self, ctx, member: discord.Member):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id FROM members WHERE user_id=$1 AND guild_id=$2 AND role='owner'",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner", "You don't own a clan."))
            clan_id = row['clan_id']

        await self._confirm_action(ctx,
            f"Transfer ownership to {member.mention}? You will become a regular member.",
            self._do_transfer, clan_id, member)

    async def _do_transfer(self, ctx, clan_id, member: discord.Member):
        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE members SET role='member' WHERE clan_id=$1 AND user_id=$2", clan_id, ctx.author.id)
                await conn.execute("UPDATE members SET role='owner' WHERE clan_id=$1 AND user_id=$2", clan_id, member.id)
                await conn.execute("UPDATE clans SET owner_id=$1 WHERE id=$2", member.id, clan_id)
        await ctx.send(embed=make_embed(ctx, EMOJIS["owner"], "Ownership Transferred",
                                        f"{member.mention} is now the owner.", success=True))

    # ======================== KICK ========================
    @clan_group.command(name="kick")
    @clan_owner_only()
    @clan_cooldown()
    @guild_cooldown()
    async def kick_cmd(self, ctx, member: discord.Member):
        await self._kick(ctx, member)

    async def _kick(self, ctx, member: discord.Member):
        async with self.bot.db_pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT clan_id FROM members WHERE user_id=$1 AND guild_id=$2 AND role='owner' FOR UPDATE",
                    ctx.author.id, ctx.guild.id
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner", "You don't own a clan."))
                clan_id = row['clan_id']
                target = await conn.fetchrow(
                    "SELECT role FROM members WHERE clan_id=$1 AND user_id=$2 FOR UPDATE", clan_id, member.id
                )
                if not target:
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not in Clan",
                                                           "That member is not in your clan."))
                if target['role'] == "owner":
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Cannot Kick",
                                                           "You can't kick yourself."))
                await self._remove_member_atomic(conn, clan_id, member.id)

        await self._remove_member(clan_id, member)
        await ctx.send(embed=make_embed(ctx, EMOJIS["delete"], "Kicked",
                                        f"{member.mention} has been kicked from the clan.", success=True))

    # ======================== LOGO / BANNER ========================
    @clan_group.command(name="logo", aliases=["setlogo"])
    @clan_cooldown()
    @guild_cooldown()
    async def logo_cmd(self, ctx, url: Optional[str] = None):
        await self._set_media(ctx, "logo", url)

    @clan_group.command(name="banner", aliases=["setbanner"])
    @clan_cooldown()
    @guild_cooldown()
    async def banner_cmd(self, ctx, url: Optional[str] = None):
        await self._set_media(ctx, "banner", url)

    async def _set_media(self, ctx, media_type: str, url: Optional[str]):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "No Clan", "You aren't in a clan."))
            clan_id, role = row['clan_id'], row['role']

            col = "logo_url" if media_type == "logo" else "banner_url"

            if url is None and not (hasattr(ctx, 'message') and ctx.message and ctx.message.attachments):
                current = await conn.fetchval(f"SELECT {col} FROM clans WHERE id=$1", clan_id)
                if current:
                    embed = discord.Embed(title=f"{EMOJIS["snipe"]} Clan {media_type.title()}", color=0x3498db)
                    embed.set_image(url=current)
                    embed.set_footer(text=f"Requested by {ctx.author}",
                                     icon_url=ctx.author.display_avatar.url)
                    await ctx.send(embed=embed)
                else:
                    await ctx.send(embed=make_embed(ctx, EMOJIS["file"], f"No {media_type.title()}",
                                                    f"This clan has no {media_type} set. Use a URL or attach an image."))
                return

            if role != "owner":
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner",
                                                       "Only the clan owner can change the clan media."))
            if url is None and ctx.message and ctx.message.attachments:
                url = ctx.message.attachments[0].url

            clan = await conn.fetchrow("SELECT level FROM clans WHERE id=$1", clan_id)
            level = clan['level']

            if media_type == "logo":
                if level < COSMETIC_UNLOCKS["logo"]:
                    return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                        f"Logos unlock at **Clan Level {COSMETIC_UNLOCKS['logo']}**. Your clan is Level **{level}**."))
                await conn.execute("UPDATE clans SET logo_url=$1 WHERE id=$2", url, clan_id)
                await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Logo Updated", "Logo set!", success=True))

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
                    await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Banner Updated", "Animated banner set!", success=True))
                else:
                    if level < COSMETIC_UNLOCKS["static_banner"]:
                        return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                            f"Banners unlock at **Clan Level {COSMETIC_UNLOCKS['static_banner']}**. Your clan is Level **{level}**."))
                    await conn.execute(
                        "UPDATE clans SET banner_url=$1, banner_type='static' WHERE id=$2",
                        url, clan_id
                    )
                    await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Banner Updated", "Banner set!", success=True))

    # ======================== VISIBILITY ========================
    @clan_group.command(name="set")
    @clan_owner_only()
    @clan_cooldown()
    @guild_cooldown()
    async def set_cmd(self, ctx, visibility: str):
        await self._set_visibility(ctx, visibility)

    async def _set_visibility(self, ctx, vis: str):
        vis = vis.lower()
        if vis not in ("public", "private"):
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Invalid", "Use `public` or `private`."))
        async with self.bot.db_pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE clans SET visibility=$1 WHERE id IN (SELECT clan_id FROM members WHERE user_id=$2 AND guild_id=$3 AND role='owner')",
                vis, ctx.author.id, ctx.guild.id
            )
            if " 0" in result:
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner",
                                                       "You don't own a clan."))
        await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Visibility Updated",
                                        f"Clan is now **{vis}**.", success=True))

    # ======================== RENAME ========================
    @clan_group.command(name="rename")
    @clan_owner_only()
    @clan_cooldown()
    @guild_cooldown()
    async def rename_cmd(self, ctx, *, new_name: str):
        await self._rename(ctx, new_name)

    async def _rename(self, ctx, new_name: str):
        new_name = new_name.strip()
        if not new_name:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Invalid Name", "Name cannot be empty."))
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT c.id, c.name FROM clans c JOIN members m ON c.id=m.clan_id WHERE m.user_id=$1 AND m.guild_id=$2 AND m.role='owner'",
                ctx.author.id, ctx.guild.id
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner", "You don't own a clan."))
            clan_id, old_name = row['id'], row['name']

        if not await eco_deduct_coins(self.bot, ctx.author.id, 50_000):
            balance = await eco_get_balance(self.bot, ctx.author.id)
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Insufficient Funds",
                                                   f"Renaming costs {EMOJIS["moon_dust"]} **50,000** coins. You have {format_coins(balance)}."))

        async with self.bot.db_pool.acquire() as conn:
            try:
                await conn.execute("UPDATE clans SET name=$1 WHERE id=$2", new_name, clan_id)
            except asyncpg.UniqueViolationError:
                await eco_add_coins(self.bot, ctx.author.id, 50_000)
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Name Taken", f"`{new_name}` already exists."))

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

        await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Clan Renamed",
                                        f"**{old_name}** is now **{new_name}**.", success=True))

    # ======================== DEPOSIT ========================
    @clan_group.command(name="deposit", aliases=["donate", "give"])
    @clan_cooldown()
    @guild_cooldown()
    async def deposit_cmd(self, ctx, *, amount: str):
        await self._deposit(ctx, amount)

    async def _deposit(self, ctx, amount_str: str):
        try:
            amount = parse_amount(amount_str)
        except ValueError:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Invalid Format",
                                                   "Use a number like `1000`, `10k`, `1.5k`, `1m`."))
        if amount <= 0:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Invalid Amount", "Must be positive."))

        final_level = None
        try:
            async with self.bot.db_pool.acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        "SELECT clan_id FROM members WHERE user_id=$1 AND guild_id=$2 FOR UPDATE",
                        ctx.author.id, ctx.guild.id
                    )
                    if not row:
                        raise NoClan("You aren't in a clan.")
                    clan_id = row['clan_id']

                    await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", ctx.author.id)
                    wallet_row = await conn.fetchrow(
                        "UPDATE users SET wallet = wallet - $1 WHERE id=$2 AND wallet >= $1 RETURNING wallet",
                        amount, ctx.author.id
                    )
                    if not wallet_row:
                        raise InsufficientFunds("Insufficient funds")

                    await conn.execute(
                        "INSERT INTO deposits (user_id, guild_id, clan_id, amount) VALUES ($1, $2, $3, $4)",
                        ctx.author.id, ctx.guild.id, clan_id, amount
                    )
                    total = await conn.fetchval(
                        "UPDATE clans SET total_deposits = total_deposits + $1 WHERE id=$2 RETURNING total_deposits",
                        amount, clan_id
                    )
                    new_level = self.get_level_from_deposits(total)
                    await conn.execute(
                        "UPDATE clans SET level = GREATEST(level, $1) WHERE id=$2",
                        new_level, clan_id
                    )
                    await record_economy_transaction(conn, ctx.author.id, -amount, "clan_deposit", "deposit", str(clan_id))
                    final_level = await conn.fetchval("SELECT level FROM clans WHERE id=$1", clan_id)
        except NoClan as e:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "No Clan", str(e)))
        except InsufficientFunds:
            balance = await eco_get_balance(self.bot, ctx.author.id)
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Insufficient Funds",
                                                   f"You don't have {EMOJIS["moon_dust"]} **{format_coins(amount)}** coins. You have {format_coins(balance)}."))

        await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Deposit Successful",
                                        f"Deposited {EMOJIS["moon_dust"]} **{format_coins(amount)}** into your clan bank.\n"
                                        f"Current clan level: **{final_level}**",
                                        success=True))

    # ======================== BANK ========================
    @clan_group.command(name="bank")
    @clan_cooldown()
    @guild_cooldown()
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
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "No Clan",
                                                           "Specify a clan name or join one first."))
                clan_id = row['clan_id']
            else:
                row = await conn.fetchrow(
                    "SELECT id FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                    ctx.guild.id, name
                )
                if not row:
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Found",
                                                           f"No clan `{name}`."))
                clan_id = row['id']
            row = await conn.fetchrow(
                "SELECT name, total_deposits, level FROM clans WHERE id=$1",
                clan_id
            )
            cname, total, level = row['name'], row['total_deposits'] or 0, row['level']
        embed = make_embed(ctx, EMOJIS["snipe"], f"{cname} Bank",
                           f"Total deposited: {EMOJIS["moon_dust"]} **{format_coins(total)}**\n"
                           f"Current level: **{level}**")
        await ctx.send(embed=embed)

    # ======================== WEEKLY ========================
    @clan_group.command(name="weekly")
    @clan_cooldown()
    @guild_cooldown()
    async def weekly_cmd(self, ctx):
        await self._weekly(ctx)

    async def _weekly(self, ctx):
        reward = 0
        level = 0
        try:
            async with self.bot.db_pool.acquire() as conn:
                async with conn.transaction():
                    row = await conn.fetchrow(
                        "SELECT m.clan_id, m.role, c.level FROM members m JOIN clans c ON m.clan_id=c.id "
                        "WHERE m.user_id=$1 AND m.guild_id=$2 FOR UPDATE",
                        ctx.author.id, ctx.guild.id
                    )
                    if not row:
                        raise NoClan("You aren't in a clan.")
                    clan_id, role, level = row['clan_id'], row['role'], row['level']

                    claim_time = await conn.fetchval(
                        """
                        INSERT INTO weekly_claims (user_id, guild_id, clan_id, claim_time)
                        VALUES ($1, $2, $3, NOW())
                        ON CONFLICT (user_id, guild_id, clan_id)
                        DO UPDATE SET claim_time = EXCLUDED.claim_time
                        WHERE weekly_claims.claim_time < NOW() - INTERVAL '7 days'
                        RETURNING claim_time
                        """,
                        ctx.author.id, ctx.guild.id, clan_id
                    )
                    if claim_time is None:
                        raise AlreadyClaimed("You already claimed this week.")

                    reward = level * 10000 + WEEKLY_ROLE_BONUS.get(role, 0)
                    await conn.execute("UPDATE users SET wallet = wallet + $1 WHERE id=$2", reward, ctx.author.id)
                    await record_economy_transaction(conn, ctx.author.id, reward, "weekly_reward", "weekly", str(clan_id))
        except NoClan as e:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "No Clan", str(e)))
        except AlreadyClaimed as e:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["mute"], "Already Claimed", str(e)))

        await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Weekly Bonus",
                                        f"You received {EMOJIS["moon_dust"]} **{format_coins(reward)}** from your clan! (Level {level})",
                                        success=True))

    # ======================== TOP ========================
    @clan_group.command(name="top")
    @clan_cooldown()
    @guild_cooldown()
    async def top_cmd(self, ctx):
        await self._top(ctx)

    async def _top(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            clans = await conn.fetch(
                """
                SELECT name, level, member_count, total_deposits
                FROM clans
                WHERE guild_id=$1
                ORDER BY level DESC, total_deposits DESC
                LIMIT 10
                """,
                ctx.guild.id
            )
        if not clans:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["warning"], "No Clans", "The server has no clans yet."))
        embed = discord.Embed(title=f"{EMOJIS["chart"]} Clan Leaderboard", color=0xf1c40f)
        embed.set_author(name=ctx.guild.name,
                         icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        desc = ""
        for i, record in enumerate(clans, 1):
            name = record['name']
            lvl = record['level']
            mem = record['member_count']
            donated = record['total_deposits']
            desc += f"**{i}.** {name} — Lvl {lvl} | {mem} Members | Deposited: {EMOJIS["moon_dust"]} {format_coins(donated)}\n"
        embed.description = desc + f"\n{BREAKER_LINE}"
        await ctx.send(embed=embed)

    # ======================== ADMIN ========================
    @clan_group.group(name="admin", invoke_without_command=True)
    @clan_cooldown()
    @guild_cooldown()
    async def admin_group(self, ctx):
        if not await self._is_admin_or_senior_mod(ctx):
            try:
                await ctx.message.delete()
            except:
                pass
            return
        await ctx.send(embed=make_embed(ctx, EMOJIS["admin"], "Clan Admin Commands",
            "`clan admin delete <name>` – Delete a clan\n"
            "`clan admin setlevel <name> <level>` – Set clan level (1-100)\n"
            "`clan admin enable` – Enable clan commands\n"
            "`clan admin disable` – Disable clan commands\n"
            "`clan admin bypasschannel <user> <channel>` – Allow user to use clan commands in a channel"))

    async def _is_admin_or_senior_mod(self, ctx):
        if ctx.author.id == ctx.bot.owner_id:
            return True
        if not ADMIN_SENIOR_MOD_ROLE_IDS:
            return False
        user_roles = [role.id for role in ctx.author.roles]
        return any(rid in user_roles for rid in ADMIN_SENIOR_MOD_ROLE_IDS)

    @admin_group.command(name="delete")
    @has_admin_or_senior_mod()
    @clan_cooldown()
    @guild_cooldown()
    async def admin_delete_cmd(self, ctx, *, name: str):
        await self._admin_delete(ctx, name)

    async def _admin_delete(self, ctx, name: str):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, name FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                ctx.guild.id, name
            )
            if not row:
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Found", f"No clan `{name}`."))
            await self._do_delete(ctx, row['id'])

    @admin_group.command(name="setlevel")
    @has_admin_or_senior_mod()
    @clan_cooldown()
    @guild_cooldown()
    async def admin_setlevel_cmd(self, ctx, name: str, level: int):
        await self._admin_setlevel(ctx, name, level)

    async def _admin_setlevel(self, ctx, name: str, level: int):
        level = max(1, min(level, MAX_LEVEL))
        async with self.bot.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE clans SET level=$1 WHERE guild_id=$2 AND LOWER(name)=LOWER($3)",
                level, ctx.guild.id, name
            )
        await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Level Set",
                                        f"**{name}** is now level **{level}**.", success=True))

    @admin_group.command(name="enable")
    @has_admin_or_senior_mod()
    @clan_cooldown()
    @guild_cooldown()
    async def enable_cmd(self, ctx):
        await set_clan_enabled(self.bot, ctx.guild.id, True)
        await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Clan Commands Enabled",
                                        "Clan commands are now enabled for this server.", success=True))

    @admin_group.command(name="disable")
    @has_admin_or_senior_mod()
    @clan_cooldown()
    @guild_cooldown()
    async def disable_cmd(self, ctx):
        await set_clan_enabled(self.bot, ctx.guild.id, False)
        await ctx.send(embed=make_embed(ctx, EMOJIS["mute"], "Clan Commands Disabled",
                                        "Clan commands are now disabled for non-admin users.", success=True))

    @admin_group.command(name="bypasschannel")
    @has_admin_or_senior_mod()
    @clan_cooldown()
    @guild_cooldown()
    async def bypass_channel_cmd(self, ctx, user: discord.User, channel: discord.TextChannel):
        await add_bypass_channel(self.bot, ctx.guild.id, user.id, channel.id)
        await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Bypass Added",
                                        f"{user.mention} can now use clan commands in {channel.mention}.",
                                        success=True))

    # ======================== COSMETIC COMMANDS ========================
    @clan_group.command(name="cosmetics")
    @clan_cooldown()
    @guild_cooldown()
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
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "No Clan",
                                                           "Specify a clan name or join one first."))
                clan = await conn.fetchrow("SELECT * FROM clans WHERE id=$1", row['clan_id'])
            else:
                clan = await conn.fetchrow(
                    "SELECT * FROM clans WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                    ctx.guild.id, name
                )
                if not clan:
                    return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Found", f"No clan `{name}`."))

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
            status = f"{EMOJIS["tick"]} Unlocked" if item["unlocked"] else f"{EMOJI_LOCK} Locked"
            equipped = " *(equipped)*" if item["equipped"] else ""
            lines.append(f"**{item['name']}** — {status} (Lvl {item['level']}){equipped}")

        embed = discord.Embed(
            title=f"{EMOJIS["color"]} {clan['name']} — Cosmetics",
            description="\n".join(lines),
            color=0x9d4edd
        )
        embed.set_footer(text=f"Clan Level {level} • Use clan [motto|badge|color|description] to customize")
        await ctx.send(embed=embed)

    @clan_group.command(name="motto")
    @clan_cooldown()
    @guild_cooldown()
    async def motto_cmd(self, ctx, *, motto: str):
        await self._motto(ctx, motto)

    async def _motto(self, ctx, motto: str):
        motto = motto.strip()[:60]
        if not motto:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Invalid Motto", "Motto must be 1-60 characters."))
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row or row['role'] != 'owner':
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner", "Only the clan owner can set the motto."))
            clan = await conn.fetchrow("SELECT level FROM clans WHERE id=$1", row['clan_id'])
            if clan['level'] < COSMETIC_UNLOCKS['motto']:
                return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                    f"Clan Motto unlocks at **Level {COSMETIC_UNLOCKS['motto']}**. Your clan is Level **{clan['level']}**."))
            await conn.execute("UPDATE clans SET clan_motto=$1 WHERE id=$2", motto, row['clan_id'])
        await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Motto Set!", f"Clan motto: *\"{motto}\"*", success=True))

    @clan_group.command(name="badge")
    @clan_cooldown()
    @guild_cooldown()
    async def badge_cmd(self, ctx):
        await self._badge(ctx)

    async def _badge(self, ctx):
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row or row['role'] != 'owner':
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner", "Only the clan owner can set the badge."))
            clan = await conn.fetchrow("SELECT level FROM clans WHERE id=$1", row['clan_id'])
            if clan['level'] < COSMETIC_UNLOCKS['badge']:
                return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                    f"Clan Badge unlocks at **Level {COSMETIC_UNLOCKS['badge']}**. Your clan is Level **{clan['level']}**."))
            clan_id = row['clan_id']

        view = BadgeView(self, ctx, clan_id)
        embed = discord.Embed(
            title=f"{EMOJIS["color"]} Choose a Clan Badge",
            description="Select a badge from the dropdown menu below.",
            color=0x9d4edd
        )
        prepare_v2_view(view, embed)
        await ctx.send(view=view)

    @clan_group.command(name="color")
    @clan_cooldown()
    @guild_cooldown()
    async def color_cmd(self, ctx, color: str):
        await self._color(ctx, color)

    async def _color(self, ctx, color: str):
        color = color.strip()
        if not color.startswith('#'):
            color = '#' + color
        if not re.match(r'^#([A-Fa-f0-9]{6})$', color):
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Invalid Color", "Please provide a valid HEX color like `#9d4edd`."))
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row or row['role'] != 'owner':
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner", "Only the clan owner can set the embed color."))
            clan = await conn.fetchrow("SELECT level FROM clans WHERE id=$1", row['clan_id'])
            if clan['level'] < COSMETIC_UNLOCKS['embed_color']:
                return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                    f"Custom Embed Color unlocks at **Level {COSMETIC_UNLOCKS['embed_color']}**. Your clan is Level **{clan['level']}**."))
            await conn.execute("UPDATE clans SET embed_color=$1 WHERE id=$2", color, row['clan_id'])
        await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Color Set!", f"Clan embed color: {color}", success=True))

    @clan_group.command(name="description")
    @clan_cooldown()
    @guild_cooldown()
    async def description_cmd(self, ctx, *, description: str):
        await self._description(ctx, description)

    async def _description(self, ctx, description: str):
        description = description.strip()[:1000]
        if not description:
            return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Invalid Description", "Description must be 1-1000 characters."))
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2",
                ctx.author.id, ctx.guild.id
            )
            if not row or row['role'] != 'owner':
                return await ctx.send(embed=make_embed(ctx, EMOJIS["cross"], "Not Owner", "Only the clan owner can set the description."))
            clan = await conn.fetchrow("SELECT level FROM clans WHERE id=$1", row['clan_id'])
            if clan['level'] < COSMETIC_UNLOCKS['description']:
                return await ctx.send(embed=make_embed(ctx, EMOJI_LOCK, "Locked",
                    f"Clan Description unlocks at **Level {COSMETIC_UNLOCKS['description']}**. Your clan is Level **{clan['level']}**."))
            await conn.execute("UPDATE clans SET clan_description=$1 WHERE id=$2", description, row['clan_id'])
        await ctx.send(embed=make_embed(ctx, EMOJIS["tick"], "Description Set!", "Clan description updated.", success=True))

    # ======================== Helpers ========================
    async def _confirm_action(self, ctx, prompt, callback, *args):
        msg = await ctx.send(embed=make_embed(ctx, EMOJIS["alert"], "Confirmation", prompt, color=0xe67e22))
        await msg.add_reaction(EMOJIS["tick"])

        def check(reaction, user):
            return (user == ctx.author and str(reaction.emoji) == EMOJIS["tick"]
                    and reaction.message.id == msg.id)
        try:
            await self.bot.wait_for("reaction_add", timeout=30.0, check=check)
            await msg.delete()
            await callback(ctx, *args)
        except asyncio.TimeoutError:
            await msg.delete()
            await ctx.send(embed=make_embed(ctx, EMOJIS["mute"], "Cancelled", "Confirmation timed out."))

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
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT clan_id, role FROM members WHERE user_id=$1 AND guild_id=$2 FOR UPDATE",
                    member.id, member.guild.id
                )
                if not row:
                    return
                clan_id, role = row['clan_id'], row['role']
                if role == "owner":
                    co = await conn.fetchrow(
                        "SELECT user_id FROM members WHERE clan_id=$1 AND role='coowner' LIMIT 1 FOR UPDATE",
                        clan_id
                    )
                    if co:
                        await conn.execute("UPDATE members SET role='owner' WHERE clan_id=$1 AND user_id=$2", clan_id, co['user_id'])
                        await conn.execute("UPDATE clans SET owner_id=$1 WHERE id=$2", co['user_id'], clan_id)
                        await self._remove_member_atomic(conn, clan_id, member.id)
                    else:
                        # Delete clan and all members
                        records = await self._do_delete_atomic(conn, clan_id)
                        # Cannot restore nicks here; Discord member already left, no need
                else:
                    await self._remove_member_atomic(conn, clan_id, member.id)


class BadgeDropdown(discord.ui.Select):
    def __init__(self, cog, ctx, clan_id):
        self.cog = cog
        self.ctx = ctx
        self.clan_id = clan_id

        badge_names = {
            f"{EMOJIS['dragon_shield']}": "Dragon Shield",
            f"{EMOJIS['swords']}": "Swords",
            f"{EMOJIS['dragon']}": "Dragon",
            f"{EMOJIS['purple_crown']}": "Purple Crown",
            f"{EMOJIS['fire']}": "Purple Fire",
            f"{EMOJIS['moon']}": "Moon",
            f"{EMOJIS['snowflake']}": "Snowflake",
            f"{EMOJIS['lightning']}": "Lightning",
            f"{EMOJIS['black_skull']}": "Black Skull",
            f"{EMOJIS['purple_flower']}": "Purple Flower",
            f"{EMOJIS['maple_leaf']}": "Maple Leaf",
            f"{EMOJIS['golden_eagles']}": "Golden Eagles"
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
            embed=make_embed(self.ctx, EMOJIS["tick"], "Badge Set!",
                f"Clan badge set to: {selected_badge}", success=True)
        )


class BadgeView(ComponentsV2View):
    def __init__(self, cog, ctx, clan_id):
        super().__init__(timeout=60)
        row = discord.ui.ActionRow()
        row.add_item(BadgeDropdown(cog, ctx, clan_id))
        self.add_item(row)


class InviteAcceptView(ComponentsV2View):
    def __init__(self, bot, invitee: discord.Member, ctx, clan_id, inviter):
        super().__init__(timeout=86400)
        self.bot = bot
        self.invitee = invitee
        self.ctx = ctx
        self.clan_id = clan_id
        self.inviter = inviter
        self.buttons = discord.ui.ActionRow()
        accept_button = discord.ui.Button(label="Accept", style=discord.ButtonStyle.success, emoji=EMOJIS["tick"])
        decline_button = discord.ui.Button(label="Decline", style=discord.ButtonStyle.danger, emoji=EMOJIS["cross"])
        accept_button.callback = self.accept
        decline_button.callback = self.decline
        self.buttons.add_item(accept_button)
        self.buttons.add_item(decline_button)
        self.add_item(self.buttons)

    async def accept(self, interaction: discord.Interaction):
        if interaction.user.id != self.invitee.id:
            return await interaction.response.send_message("This invitation is not for you.", ephemeral=True)

        try:
            async with self.bot.db_pool.acquire() as conn:
                async with conn.transaction():
                    # Check if already in clan
                    existing = await conn.fetchval(
                        "SELECT 1 FROM members WHERE user_id=$1 AND guild_id=$2 FOR UPDATE",
                        self.invitee.id, self.ctx.guild.id
                    )
                    if existing:
                        raise AlreadyInClan("You're already in a clan.")

                    row = await conn.fetchrow("SELECT name FROM clans WHERE id=$1 FOR UPDATE", self.clan_id)
                    if not row:
                        raise NoClan("Clan no longer exists.")
                    clan_name = row['name']

                    # Capacity check and atomic increment
                    updated = await conn.fetchval(
                        "UPDATE clans SET member_count = member_count + 1 WHERE id=$1 AND member_count < 20 RETURNING member_count",
                        self.clan_id
                    )
                    if updated is None:
                        raise ClanFull("This clan is full.")

                    inserted = await conn.fetchval(
                        "INSERT INTO members (user_id, guild_id, clan_id, role, original_nick) "
                        "VALUES ($1, $2, $3, 'member', $4) "
                        "ON CONFLICT (user_id, guild_id) DO NOTHING RETURNING user_id",
                        self.invitee.id, self.ctx.guild.id, self.clan_id, self.invitee.nick
                    )
                    if inserted is None:
                        raise AlreadyInClan("You're already in a clan.")
        except (AlreadyInClan, NoClan, ClanFull) as e:
            return await interaction.response.send_message(str(e), ephemeral=True)

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

        await interaction.response.send_message(f"{EMOJIS['tick']} You've joined **{clan_name}**!", ephemeral=True)
        disable_all_components(self)
        await interaction.message.edit(view=self)

    async def decline(self, interaction: discord.Interaction):
        if interaction.user.id != self.invitee.id:
            return await interaction.response.send_message("This invitation is not for you.", ephemeral=True)

        await interaction.response.send_message(f"{EMOJIS['cross']} Invitation declined.", ephemeral=True)
        disable_all_components(self)
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

