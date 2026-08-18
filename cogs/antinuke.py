import discord
from discord.ext import commands
from typing import Optional, Union
import time
import asyncio
import re
import datetime
import json
import os
from collections import defaultdict, deque

# ─── Constants and utilities ─────────────────────────────────────
MAIN_COLOR = 0x6A0DAD
WIN_COLOR = 0x57F287
LOSS_COLOR = 0xED4245
WARN_COLOR = 0xFFA500
INFO_COLOR = 0x5865F2

EMOJI_WARN = "<:purple_warning:1537916196601733241>"
EMOJI_SUCCESS = "<a:tick:1537915747454554222>"
EMOJI_WRONG = "<:cross:1537915734087303199>"
EMOJI_LOCK = "<:lock:1537916486532730890>"
EMOJI_ALERT = "<a:alert:1537916605298778143>"
EMOJI_RED_MOD_SHIELD = "<:shield:1537916746441166999>"
EMOJI_MESSAGE = "<:message:1537915592240275506>"
EMOJI_NEW_MAIL = "<a:mail:1537917503471095919>"
EMOJI_JOIN_SHIELD = "<:shield_1:1537917735122505778>"
EMOJI_FILE = "<:file:1537913812416729200>"
EMOJI_ROLE = "<:roles:1537917922536591450>"
EMOJI_GEAR = "<a:gear:1537918036688773171>"
EMOJI_LINK = "<:link:1537918123192361090>"
EMOJI_FISHING_POLE = "<:Fishing_Rod:1537918276326395984>"
EMOJI_INPUT_LATIN_LETTERS = "<:alphabet_a:1537918457117675530>"
EMOJI_COOLDOWN = "<a:cooldown:1537918560616185956>"
EMOJI_LAUGHING = "<a:laugh:1537918671639547994>"

PERM_LEVELS = {
    "user": 0,
    "moderator": 1,
    "admin": 2,
    "bot_owner": 3
}

INVITE_PATTERN = re.compile(
    r"(?:discord\.gg|discord(?:app)?\.com/invite|discordapp\.com/invite)/([A-Za-z0-9\-]+)"
)

def make_embed(ctx, title, description, color=MAIN_COLOR):
    return discord.Embed(title=title, description=description, color=color)

def error_embed(ctx, message):
    return discord.Embed(title=f"{EMOJI_WRONG} Error", description=message, color=LOSS_COLOR)

def success_embed(ctx, message):
    return discord.Embed(title=f"{EMOJI_SUCCESS} Success", description=message, color=WIN_COLOR)

def fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s" if sec else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"

def parse_time(time_str: str) -> int:
    match = re.match(r"(\d+)([smhd])", time_str.lower())
    if not match:
        raise ValueError("Invalid time format. Use e.g. 10s, 5m, 2h, 1d")
    value, unit = int(match.group(1)), match.group(2)
    if unit == "s":
        return value
    elif unit == "m":
        return value * 60
    elif unit == "h":
        return value * 3600
    elif unit == "d":
        return value * 86400
    return value

def require(level: str):
    if level not in PERM_LEVELS:
        raise ValueError(f"Invalid permission level: {level}")

    async def predicate(ctx: Union[commands.Context, discord.Interaction]):
        user = ctx.user if isinstance(ctx, discord.Interaction) else ctx.author
        cog = ctx.bot.get_cog("Antinuke")
        if cog:
            user_level = cog.get_level(user.id)
        else:
            user_level = "user"
        if PERM_LEVELS[user_level] >= PERM_LEVELS[level]:
            return True
        raise commands.CheckFailure(
            message=f"Only **{level.replace('_', ' ').title()}**+ can use this command."
        )

    def decorator(func):
        decorated = commands.check(predicate)(func)
        decorated.perm_level = level
        return decorated

    return decorator

# ─── Config loading (mimics main.py but focused on antinuke) ────
def load_antinuke_config():
    """Load config.json and extract antinuke-related settings."""
    config = {
        "enabled": True,
        "message_spam_limit": 7,
        "mention_limit": 5,
        "same_user_ping_limit": 8,
        "emoji_limit": 10,
        "join_raid_limit": 8,
        "channel_limit": 3,
        "role_limit": 3,
        "ban_limit": 3,
        "window": 5,
        "invite_timeout_seconds": 300,
        "phish_timeout_seconds": 600,
        "word_timeout_seconds": 300,
        "soft_mute_seconds": 60,
        "warn_kick_threshold": 5,
        "gateway_threshold": 10,
        "gateway_window": 10,
        "antiphish_enabled": True,
        "wordfilter_enabled": True,
        "log_channel_id": None,
        "allowed_link_role_ids": [],
    }

    if not os.path.exists("config.json"):
        return config

    try:
        with open("config.json", "r") as f:
            data = json.load(f)
    except Exception:
        return config

    # Merge antinuke section
    antinuke_cfg = data.get("antinuke", {})
    for key in config.keys():
        if key in antinuke_cfg:
            config[key] = antinuke_cfg[key]

    # Top-level flags
    if "antiphish_enabled" in data:
        config["antiphish_enabled"] = data["antiphish_enabled"]
    if "wordfilter_enabled" in data:
        config["wordfilter_enabled"] = data["wordfilter_enabled"]
    if "log_channel_id" in data:
        config["log_channel_id"] = data["log_channel_id"]
    if "allowed_link_role_ids" in data:
        config["allowed_link_role_ids"] = data["allowed_link_role_ids"]

    # Also check environment variables (optional)
    if os.getenv("LOG_CHANNEL_ID"):
        config["log_channel_id"] = int(os.getenv("LOG_CHANNEL_ID"))
    if os.getenv("ANTINUKE_ENABLED"):
        config["enabled"] = os.getenv("ANTINUKE_ENABLED").lower() == "true"

    return config

# ─── Database helper (PostgreSQL via bot.pool) ────────────────────
class Database:
    def __init__(self, bot):
        self.bot = bot

    async def fetch(self, query: str, *args):
        async with self.bot.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchval(self, query: str, *args):
        async with self.bot.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def execute(self, query: str, *args):
        async with self.bot.pool.acquire() as conn:
            return await conn.execute(query, *args)

# ─── AntiNuke Cog ─────────────────────────────────────────────────
class Antinuke(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Load config (prefer bot.config if present, else read config.json)
        if hasattr(bot, "config") and isinstance(bot.config, dict):
            cfg = bot.config
        else:
            cfg = load_antinuke_config()

        # Thresholds
        self.antinuke_enabled = cfg.get("enabled", True)
        self.msg_spam_limit = cfg.get("message_spam_limit", 7)
        self.mention_limit = cfg.get("mention_limit", 5)
        self.same_user_ping_limit = cfg.get("same_user_ping_limit", 8)
        self.emoji_limit = cfg.get("emoji_limit", 10)
        self.join_raid_limit = cfg.get("join_raid_limit", 8)
        self.channel_limit = cfg.get("channel_limit", 3)
        self.role_limit = cfg.get("role_limit", 3)
        self.ban_limit = cfg.get("ban_limit", 3)
        self.window = cfg.get("window", 5)
        self.invite_timeout_seconds = cfg.get("invite_timeout_seconds", 300)
        self.phish_timeout_seconds = cfg.get("phish_timeout_seconds", 600)
        self.word_timeout_seconds = cfg.get("word_timeout_seconds", 300)
        self.soft_mute_seconds = cfg.get("soft_mute_seconds", 60)
        self.warn_kick_threshold = cfg.get("warn_kick_threshold", 5)
        self.gateway_threshold = cfg.get("gateway_threshold", 10)
        self.gateway_window = cfg.get("gateway_window", 10)

        self.antiphish_enabled = cfg.get("antiphish_enabled", True)
        self.wordfilter_enabled = cfg.get("wordfilter_enabled", True)

        self.allowed_link_role_ids = cfg.get("allowed_link_role_ids", [])
        self.log_channel_id = cfg.get("log_channel_id")

        # State
        self._msg_times: dict[int, deque] = defaultdict(deque)
        self._join_times: deque = deque()
        self._audit_times: dict[int, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))
        self._raid_locked: set[int] = set()

        self._spam_handling: set[int] = set()
        self._invite_handling: set[int] = set()
        self._phish_handling: set[int] = set()
        self._word_handling: set[int] = set()
        self._soft_offense_seen: set[int] = set()

        self.db = Database(bot)
        self.phish_domains: list[str] = []
        self.banned_words: list[str] = []

        # Schedule combined initialization and data load
        bot.loop.create_task(self._init_and_load())

    async def _init_and_load(self):
        await self.bot.wait_until_ready()
        # Create tables first
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS phish_domains (domain TEXT PRIMARY KEY)"
        )
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS banned_words (word TEXT PRIMARY KEY)"
        )
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS warnings "
            "(user_id BIGINT, moderator_id BIGINT, reason TEXT, "
            "timestamp BIGINT, countable BOOLEAN)"
        )
        # Now load data
        rows = await self.db.fetch("SELECT domain FROM phish_domains")
        self.phish_domains = [r["domain"] for r in rows]
        rows = await self.db.fetch("SELECT word FROM banned_words")
        self.banned_words = [r["word"] for r in rows]

    def is_staff(self, member: discord.Member) -> bool:
        """Check if a member is staff (same logic as main.py)."""
        if not member or not getattr(member, "guild", None):
            return False

        if member.id == member.guild.owner_id or member.id == self.bot.owner_id:
            return True

        staff_role_ids = set()
        if hasattr(self.bot, "config") and "staff_role_ids" in self.bot.config:
            staff_role_ids = set(self.bot.config["staff_role_ids"])
        else:
            try:
                with open("config.json") as f:
                    data = json.load(f)
                roles = data.get("roles", {})
                staff_keys = [
                    "admin_role_id",
                    "senior_moderator_role_id",
                    "moderator_role_id",
                    "trial_moderator_role_id",
                    "developer_role_id",
                    "event_manager_role_id",
                    "trial_event_manager_role_id",
                ]
                for key in staff_keys:
                    if key in roles:
                        staff_role_ids.add(int(roles[key]))
            except Exception:
                pass

        if staff_role_ids and any(role.id in staff_role_ids for role in member.roles):
            return True

        staff_file = "data/staff.json"
        if not os.path.exists(staff_file):
            return False

        try:
            with open(staff_file) as handle:
                staff_data = json.load(handle)
        except Exception:
            return False

        uid = str(member.id)
        for values in staff_data.values():
            if isinstance(values, list) and uid in values:
                return True
        return False

    def get_level(self, user_id: int) -> str:
        member = None
        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if member:
                break

        if member and self.is_staff(member):
            try:
                with open("config.json") as f:
                    data = json.load(f)
                roles = data.get("roles", {})
                admin_roles = {
                    int(roles.get("admin_role_id", 0)),
                    int(roles.get("developer_role_id", 0)),
                }
                mod_roles = {
                    int(roles.get("senior_moderator_role_id", 0)),
                    int(roles.get("moderator_role_id", 0)),
                    int(roles.get("trial_moderator_role_id", 0)),
                    int(roles.get("event_manager_role_id", 0)),
                    int(roles.get("trial_event_manager_role_id", 0)),
                }
                member_roles = {role.id for role in member.roles}
                if member_roles & admin_roles:
                    return "admin"
                if member_roles & mod_roles:
                    return "moderator"
            except Exception:
                pass

            if user_id == self.bot.owner_id or user_id == member.guild.owner_id:
                return "bot_owner"
            return "moderator"

        if user_id == self.bot.owner_id:
            return "bot_owner"
        return "user"

    def get_log_channel(self):
        if not self.log_channel_id:
            return None
        return self.bot.get_channel(self.log_channel_id)

    async def log_antinuke(self, guild: discord.Guild, title: str, desc: str):
        channel = self.get_log_channel()
        if not channel:
            return
        embed = discord.Embed(
            title=f"{EMOJI_RED_MOD_SHIELD} AntiNuke — {title}",
            description=desc,
            color=0xFF6B00,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="AntiNuke System")
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass

    async def _antinuke_notice(self, message: discord.Message, member: discord.Member, reason: str):
        try:
            notice = await message.channel.send(f"{member.mention} {EMOJI_WARN} {reason}")
        except Exception:
            return
        await asyncio.sleep(10)
        try:
            await notice.delete()
        except Exception:
            pass

    async def _auto_timeout(self, member: discord.Member, seconds: int, reason: str):
        try:
            until = discord.utils.utcnow() + datetime.timedelta(seconds=seconds)
            await member.timeout(until, reason=reason)
        except Exception:
            pass

    def _has_used_soft_pass(self, uid: int) -> bool:
        if uid in self._soft_offense_seen:
            return True
        self._soft_offense_seen.add(uid)
        return False

    async def _add_warning(self, user_id: int, moderator_id: int, reason: str, countable: bool):
        await self.db.execute(
            "INSERT INTO warnings (user_id, moderator_id, reason, timestamp, countable) "
            "VALUES ($1, $2, $3, $4, $5)",
            user_id, moderator_id, reason, int(time.time()), countable
        )
        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM warnings WHERE user_id = $1 AND countable = $2",
            user_id, True
        )
        if count % self.warn_kick_threshold == 0:
            member = None
            for guild in self.bot.guilds:
                m = guild.get_member(user_id)
                if m:
                    member = m
                    break
            if member:
                try:
                    await member.kick(reason=f"Auto-kick: {count} warnings")
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot or not self.antinuke_enabled:
            return
        if self.is_staff(message.author):
            return

        member = message.author
        uid = member.id
        now = time.time()

        # Invite filter
        if not any(role.id in self.allowed_link_role_ids for role in member.roles):
            invite_match = INVITE_PATTERN.search(message.content)
            if invite_match:
                code = invite_match.group(1)
                try:
                    invites = await message.guild.invites()
                    if any(inv.code == code for inv in invites):
                        own = True
                    else:
                        fetched = await self.bot.fetch_invite(code)
                        own = (fetched.guild and fetched.guild.id == message.guild.id)
                except Exception:
                    own = False
                if not own:
                    if uid in self._invite_handling:
                        return
                    self._invite_handling.add(uid)
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    reason = "Sending Discord invite link"
                    await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", True)
                    await self._auto_timeout(member, self.invite_timeout_seconds, f"AntiNuke: {reason}")
                    await self._antinuke_notice(message, member, f"{reason} — muted {fmt_duration(self.invite_timeout_seconds)}.")
                    await self.log_antinuke(message.guild, "Invite Link", f"{member.mention} (`{member.id}`)")
                    self._invite_handling.discard(uid)
                    return

        # Phishing
        if self.antiphish_enabled and self.phish_domains:
            content_lower = message.content.lower()
            for domain in self.phish_domains:
                if domain in content_lower:
                    if uid in self._phish_handling:
                        return
                    self._phish_handling.add(uid)
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    reason = f"Phishing link detected: {domain}"
                    await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", True)
                    await self._auto_timeout(member, self.phish_timeout_seconds, f"AntiNuke: {reason}")
                    await self._antinuke_notice(message, member, f"{reason} — muted {fmt_duration(self.phish_timeout_seconds)}.")
                    await self.log_antinuke(message.guild, "Phishing Link", f"{member.mention} (`{member.id}`)")
                    self._phish_handling.discard(uid)
                    return

        # Word filter
        if self.wordfilter_enabled and self.banned_words:
            content_lower = message.content.lower()
            for word in self.banned_words:
                if word in content_lower:
                    if uid in self._word_handling:
                        return
                    self._word_handling.add(uid)
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    reason = f"Used banned word: '{word}'"
                    await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", False)
                    await self._auto_timeout(member, self.word_timeout_seconds, f"AntiNuke: {reason}")
                    await self._antinuke_notice(message, member, f"{reason} — muted {fmt_duration(self.word_timeout_seconds)}.")
                    await self.log_antinuke(message.guild, "Word Filter", f"{member.mention} (`{member.id}`)")
                    self._word_handling.discard(uid)
                    return

        # Same-user mass ping
        if message.mentions:
            ping_counts = {}
            for u in message.mentions:
                ping_counts[u.id] = ping_counts.get(u.id, 0) + 1
            worst_target, worst_count = max(ping_counts.items(), key=lambda kv: kv[1])
            if worst_count > self.same_user_ping_limit:
                try:
                    await message.delete()
                except Exception:
                    pass
                reason = f"Mass pinging a single user ({worst_count}x)"
                await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", True)
                await self._auto_timeout(member, self.soft_mute_seconds, reason=reason)
                await self._antinuke_notice(message, member, f"{reason} — muted {fmt_duration(self.soft_mute_seconds)}.")
                await self.log_antinuke(message.guild, "Mass Ping (Same User)", f"{member.mention} (`{member.id}`)")
                return

        # Message spam
        if uid not in self._spam_handling:
            dq = self._msg_times[uid]
            dq.append(now)
            while dq and now - dq[0] > self.window:
                dq.popleft()
            if len(dq) >= self.msg_spam_limit:
                self._spam_handling.add(uid)
                dq.clear()
                try:
                    await message.delete()
                    await message.channel.purge(limit=self.msg_spam_limit + 3, check=lambda m: m.author == member)
                except Exception:
                    pass
                reason = f"Message spam (≥{self.msg_spam_limit} msgs in {self.window}s)"
                if self._has_used_soft_pass(uid):
                    await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", False)
                    await self._auto_timeout(member, self.soft_mute_seconds, reason=reason)
                    await self._antinuke_notice(message, member, f"{reason} — muted {fmt_duration(self.soft_mute_seconds)}.")
                else:
                    await self._antinuke_notice(message, member, f"{reason} — this is a warning, next time you'll be muted.")
                await self.log_antinuke(message.guild, "Message Spam", f"{member.mention} (`{member.id}`)")
                self._spam_handling.discard(uid)
                return

        # Mass mention
        mention_count = len(set(message.mentions + message.role_mentions))
        if mention_count >= self.mention_limit:
            try:
                await message.delete()
            except Exception:
                pass
            reason = f"Mass mention ({mention_count} mentions)"
            await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", True)
            await self._auto_timeout(member, self.soft_mute_seconds, reason=reason)
            await self._antinuke_notice(message, member, f"{reason} — muted {fmt_duration(self.soft_mute_seconds)}.")
            await self.log_antinuke(message.guild, "Mass Mention", f"{member.mention} (`{member.id}`)")
            return

        # Emoji spam
        emojis_found = re.findall(r"<a?:\w+:\d+>", message.content)
        if len(emojis_found) >= self.emoji_limit:
            try:
                await message.delete()
            except Exception:
                pass
            reason = f"Emoji spam ({len(emojis_found)} custom emojis)"
            if self._has_used_soft_pass(uid):
                await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", False)
                await self._auto_timeout(member, self.soft_mute_seconds, reason=reason)
                await self._antinuke_notice(message, member, f"{reason} — muted {fmt_duration(self.soft_mute_seconds)}.")
            else:
                await self._antinuke_notice(message, member, f"{reason} — this is a warning, next time you'll be muted.")
            await self.log_antinuke(message.guild, "Emoji Spam", f"{member.mention} (`{member.id}`)")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not self.antinuke_enabled:
            return
        guild = member.guild
        self._join_times.append(time.time())
        while self._join_times and time.time() - self._join_times[0] > self.gateway_window:
            self._join_times.popleft()
        if len(self._join_times) >= self.gateway_threshold:
            if guild.id not in self._raid_locked:
                self._raid_locked.add(guild.id)
                await self.log_antinuke(
                    guild,
                    "Possible Raid",
                    f"≥{self.gateway_threshold} users joined in {self.gateway_window}s.\n"
                    "Verification set to Highest and all channels locked."
                )
                try:
                    await guild.edit(verification_level=discord.VerificationLevel.highest)
                except Exception:
                    pass
                for channel in guild.text_channels:
                    try:
                        await channel.set_permissions(guild.default_role, send_messages=False)
                    except Exception:
                        pass
                self._join_times.clear()

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        if self.antinuke_enabled:
            await self._audit_check(channel.guild, "channel_create", self.channel_limit, "Mass Channel Create")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if self.antinuke_enabled:
            await self._audit_check(channel.guild, "channel_delete", self.channel_limit, "Mass Channel Delete")

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        if self.antinuke_enabled:
            await self._audit_check(role.guild, "role_create", self.role_limit, "Mass Role Create")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        if self.antinuke_enabled:
            await self._audit_check(role.guild, "role_delete", self.role_limit, "Mass Role Delete")

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        if self.antinuke_enabled:
            await self._audit_check(guild, "ban", self.ban_limit, "Mass Ban")

    async def _audit_check(self, guild, action_key, limit, label):
        if not guild.me.guild_permissions.view_audit_log:
            return
        action_map = {
            "channel_create": discord.AuditLogAction.channel_create,
            "channel_delete": discord.AuditLogAction.channel_delete,
            "role_create": discord.AuditLogAction.role_create,
            "role_delete": discord.AuditLogAction.role_delete,
            "ban": discord.AuditLogAction.ban,
        }
        action = action_map.get(action_key)
        if not action:
            return
        try:
            entries = [e async for e in guild.audit_logs(limit=1, action=action)]
        except Exception:
            return
        if not entries:
            return
        entry = entries[0]
        executor = entry.user
        if not executor or executor.bot:
            return
        if self.get_level(executor.id) in ("bot_owner", "admin"):
            return
        dq = self._audit_times[executor.id][action_key]
        dq.append(time.time())
        while dq and time.time() - dq[0] > self.window:
            dq.popleft()
        if len(dq) >= limit:
            dq.clear()
            reason = f"{label} (≥{limit} in {self.window}s)"
            await self.log_antinuke(guild, label, f"{executor.mention} (`{executor.id}`)\n{reason}")
            member = guild.get_member(executor.id)
            if member:
                await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", False)
                try:
                    await member.edit(roles=[r for r in member.roles if r == guild.default_role], reason=f"AntiNuke: {reason}")
                except Exception:
                    pass
                await self._auto_timeout(member, 600, reason=f"AntiNuke: {reason}")

    # ─── Commands ─────────────────────────────────────────────────
    @commands.group(name="antinuke", invoke_without_command=True)
    @require("admin")
    async def antinuke_group(self, ctx):
        status = f"{EMOJI_SUCCESS} Enabled" if self.antinuke_enabled else f"{EMOJI_WRONG} Disabled"
        embed = make_embed(ctx, f"{EMOJI_RED_MOD_SHIELD} AntiNuke System",
            f"**Status:** {status}\n\n"
            "`antinuke enable` — Activate\n"
            "`antinuke disable` — Deactivate\n"
            "`antinuke status` — View thresholds\n"
            "`antinuke threshold <type> <value>` — Adjust a threshold\n"
            "`antinuke unraidlock` — Restore after raid lock",
            MAIN_COLOR)
        await ctx.reply(embed=embed)

    @antinuke_group.command(name="enable")
    @require("admin")
    async def antinuke_enable(self, ctx):
        self.antinuke_enabled = True
        await ctx.reply(embed=success_embed(ctx, "AntiNuke protection **enabled**."))

    @antinuke_group.command(name="disable")
    @require("admin")
    async def antinuke_disable(self, ctx):
        self.antinuke_enabled = False
        await ctx.reply(embed=make_embed(ctx, f"{EMOJI_RED_MOD_SHIELD} AntiNuke Disabled",
            "All automated protections are now **off**.", LOSS_COLOR))

    @antinuke_group.command(name="unraidlock")
    @require("admin")
    async def antinuke_unraidlock(self, ctx):
        self._raid_locked.discard(ctx.guild.id)
        try:
            await ctx.guild.edit(verification_level=discord.VerificationLevel.medium)
        except Exception:
            pass
        for channel in ctx.guild.text_channels:
            try:
                await channel.set_permissions(ctx.guild.default_role, send_messages=True)
            except Exception:
                pass
        await ctx.reply(embed=success_embed(ctx, "Raid lock removed. Verification restored and channels unlocked."))

    @antinuke_group.command(name="status")
    @require("moderator")
    async def antinuke_status(self, ctx):
        status = f"{EMOJI_SUCCESS} Active" if self.antinuke_enabled else f"{EMOJI_WRONG} Inactive"
        embed = make_embed(ctx, f"{EMOJI_RED_MOD_SHIELD} AntiNuke Status", f"**Status:** {status}", MAIN_COLOR)
        thresholds = [
            (f"{EMOJI_MESSAGE} Message Spam", f"≥{self.msg_spam_limit} msgs in {self.window}s"),
            (f"{EMOJI_NEW_MAIL} Mass Mention", f"≥{self.mention_limit} mentions/msg"),
            (f"{EMOJI_LAUGHING} Emoji Spam", f"≥{self.emoji_limit} custom emojis/msg"),
            (f"{EMOJI_JOIN_SHIELD} Gateway (Raid)", f"≥{self.gateway_threshold} joins in {self.gateway_window}s"),
            (f"{EMOJI_FILE} Channel Flood", f"≥{self.channel_limit} creates/deletes in {self.window}s"),
            (f"{EMOJI_ROLE} Role Flood", f"≥{self.role_limit} creates/deletes in {self.window}s"),
            (f"{EMOJI_GEAR} Mass Ban", f"≥{self.ban_limit} bans in {self.window}s"),
            (f"{EMOJI_LOCK} Raid Locked Guilds", str(len(self._raid_locked))),
            (f"{EMOJI_LINK} Invite Links", f"External invites → delete + {self.invite_timeout_seconds//60}‑min mute"),
            (f"{EMOJI_FISHING_POLE} Anti‑Phishing", f"{EMOJI_SUCCESS if self.antiphish_enabled else EMOJI_WRONG} → delete + {self.phish_timeout_seconds//60}‑min mute"),
            (f"{EMOJI_INPUT_LATIN_LETTERS} Word Filter", f"{EMOJI_SUCCESS if self.wordfilter_enabled else EMOJI_WRONG} → delete + {self.word_timeout_seconds//60}‑min mute"),
        ]
        for name, val in thresholds:
            embed.add_field(name=name, value=val, inline=False)
        await ctx.reply(embed=embed)

    @antinuke_group.command(name="threshold")
    @require("admin")
    async def antinuke_threshold(self, ctx, threshold_type: str, value: int):
        mapping = {
            "msg_spam": "msg_spam_limit",
            "mention": "mention_limit",
            "emoji": "emoji_limit",
            "gateway": "gateway_threshold",
            "channel": "channel_limit",
            "role": "role_limit",
            "ban": "ban_limit",
        }
        t = threshold_type.lower()
        if t not in mapping:
            return await ctx.reply(embed=error_embed(ctx, f"Invalid type. Options: `{'`, `'.join(mapping)}`"))
        setattr(self, mapping[t], value)
        await ctx.reply(embed=success_embed(ctx, f"Threshold **{t}** updated to **{value}**."))

async def setup(bot):
    await bot.add_cog(Antinuke(bot))