import discord
from discord.ext import commands
from typing import Optional, Union
import time
import asyncio
import re
import datetime
import json
import os
from difflib import SequenceMatcher
from collections import defaultdict, deque
from pathlib import Path

from utils.components import ComponentsV2View, disable_all_components

# ─── Load emojis from ./data/emoji.json ─────────────────────────
EMOJI_PATH = os.path.join(".", "data", "emoji.json")


def _load_emojis() -> dict:
    if not os.path.exists(EMOJI_PATH):
        return {}
    try:
        with open(EMOJI_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


EMOJIS = _load_emojis()


def emoji(name: str, fallback: str = "") -> str:
    """Safe emoji getter — always reads from ./data/emoji.json."""
    return EMOJIS.get(name, fallback)


# ─── Constants ────────────────────────────────────────────────────
GREY_COLOR = 0x2B2D31  # modern Discord grey
MAIN_COLOR = GREY_COLOR
WIN_COLOR = GREY_COLOR
LOSS_COLOR = GREY_COLOR
WARN_COLOR = GREY_COLOR
INFO_COLOR = GREY_COLOR

PERM_LEVELS = {
    "user": 0,
    "moderator": 1,
    "admin": 2,
    "bot_owner": 3,
}

INVITE_PATTERN = re.compile(
    r"(?:discord\.gg|discord(?:app)?\.com/invite|discordapp\.com/invite)/([A-Za-z0-9\-]+)"
)

CONFIG_PATH = "config.json"


# ─── Embed helpers ────────────────────────────────────────────────
def make_embed(ctx, title, description, color=GREY_COLOR):
    return discord.Embed(title=title, description=description, color=GREY_COLOR)


def error_embed(ctx, message):
    return discord.Embed(
        title=f"{emoji('cross')} Error",
        description=message,
        color=GREY_COLOR,
    )


def success_embed(ctx, message):
    return discord.Embed(
        title=f"{emoji('tick')} Success",
        description=message,
        color=GREY_COLOR,
    )


def fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s" if sec else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


# ─── Config loader (single source of truth: config.json) ─────────
def load_config() -> dict:
    """Read config.json. If missing, return an empty dict — no hardcoded values."""
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ─── Permission decorator ────────────────────────────────────────
def require(level: str):
    if level not in PERM_LEVELS:
        raise ValueError(f"Invalid permission level: {level}")

    async def predicate(ctx: Union[commands.Context, discord.Interaction]):
        user = ctx.user if isinstance(ctx, discord.Interaction) else ctx.author
        cog = ctx.bot.get_cog("Antinuke")
        user_level = cog.get_level(user.id) if cog else "user"
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


# ─── Database helper ─────────────────────────────────────────────
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


# Settings exposed by the Components V2 panel. Values persist to config.json.
ANTINUKE_PANEL_OPTIONS = {
    "message_spam_limit": {"attribute": "msg_spam_limit", "label": "Messages per window", "description": "Messages that trigger spam protection", "minimum": 2, "maximum": 20, "step": 1},
    "window": {"attribute": "window", "label": "Spam window (seconds)", "description": "Rolling time window for spam checks", "minimum": 1, "maximum": 30, "step": 1},
    "dup_msg_limit": {"attribute": "dup_msg_limit", "label": "Similar-message limit", "description": "Near-duplicate messages that trigger protection", "minimum": 2, "maximum": 10, "step": 1},
    "similarity_threshold": {"attribute": "similarity_threshold", "label": "Similarity sensitivity", "description": "Higher values require closer text matches", "minimum": 0.50, "maximum": 1.00, "step": 0.05},
    "mention_limit": {"attribute": "mention_limit", "label": "Mentions per message", "description": "Mass-mention detection threshold", "minimum": 2, "maximum": 25, "step": 1},
    "emoji_limit": {"attribute": "emoji_limit", "label": "Custom emojis per message", "description": "Emoji-spam detection threshold", "minimum": 2, "maximum": 30, "step": 1},
    "gateway_threshold": {"attribute": "gateway_threshold", "label": "Joins per raid window", "description": "Member-join burst that activates raid lock", "minimum": 3, "maximum": 50, "step": 1},
    "gateway_window": {"attribute": "gateway_window", "label": "Raid window (seconds)", "description": "Time window for member-join burst detection", "minimum": 3, "maximum": 60, "step": 1},
    "channel_limit": {"attribute": "channel_limit", "label": "Channel changes per window", "description": "Channel create/delete burst threshold", "minimum": 1, "maximum": 20, "step": 1},
    "role_limit": {"attribute": "role_limit", "label": "Role changes per window", "description": "Role create/delete burst threshold", "minimum": 1, "maximum": 20, "step": 1},
    "ban_limit": {"attribute": "ban_limit", "label": "Bans per window", "description": "Mass-ban burst threshold", "minimum": 1, "maximum": 20, "step": 1},
}


class AntinukePanelView(ComponentsV2View):
    """Owner-bound Components V2 panel for safe, persistent AntiNuke tuning."""

    def __init__(self, cog: "Antinuke", author_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.author_id = author_id
        self.selected_key = "message_spam_limit"
        self.message: Optional[discord.Message] = None

        self.setting_row = discord.ui.ActionRow()
        self.setting_select = discord.ui.Select(
            placeholder="Choose a protection setting…", min_values=1, max_values=1, row=0,
            options=[discord.SelectOption(label=item["label"], value=key, description=item["description"][:100])
                     for key, item in ANTINUKE_PANEL_OPTIONS.items()],
        )
        self.setting_select.callback = self._select_setting
        self.setting_row.add_item(self.setting_select)
        self.add_item(self.setting_row)

        self.actions = discord.ui.ActionRow()
        self.lower_button = discord.ui.Button(label="Lower", style=discord.ButtonStyle.secondary, emoji=emoji("arrow_left", "◀"))
        self.raise_button = discord.ui.Button(label="Raise", style=discord.ButtonStyle.primary, emoji=emoji("arrow_right", "▶"))
        self.toggle_button = discord.ui.Button(label="Enable / Disable", style=discord.ButtonStyle.success, emoji=emoji("shield", "🛡️"))
        self.refresh_button = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.secondary, emoji=emoji("refresh", "🔄"))
        self.exit_button = discord.ui.Button(label="Exit", style=discord.ButtonStyle.danger, emoji=emoji("cross", "✖"))
        self.lower_button.callback = self._lower
        self.raise_button.callback = self._raise
        self.toggle_button.callback = self._toggle
        self.refresh_button.callback = self._refresh
        self.exit_button.callback = self._exit
        for button in (self.lower_button, self.raise_button, self.toggle_button, self.refresh_button, self.exit_button):
            self.actions.add_item(button)
        self.add_item(self.actions)
        self.set_embed(self.render_embed())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This AntiNuke panel belongs to another administrator.", ephemeral=True)
            return False
        return True

    def render_embed(self) -> discord.Embed:
        option = ANTINUKE_PANEL_OPTIONS[self.selected_key]
        current = getattr(self.cog, option["attribute"])
        if isinstance(current, float):
            shown = f"{current:.2f}"
        else:
            shown = str(current)
        embed = discord.Embed(
            title=f"{emoji('shield', '🛡️')} AntiNuke Control Panel",
            description=(
                f"**Protection:** {'Enabled' if self.cog.antinuke_enabled else 'Disabled'}\n"
                f"**Selected:** {option['label']}\n{option['description']}\n\n"
                f"**Current value:** `{shown}`\n"
                f"**Allowed range:** `{option['minimum']}` – `{option['maximum']}`\n\n"
                "Use **Lower** and **Raise** to change the selected value. Updates are saved to `config.json` and survive restarts.\n"
                f"**Message flood rule:** {self.cog.msg_spam_limit} messages in {self.cog.window}s; "
                f"near-duplicate threshold: {self.cog.dup_msg_limit}; similarity: {self.cog.similarity_threshold:.2f}."
            ),
            color=GREY_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Only the administrator who opened this panel can use its controls.")
        return embed

    async def _save_and_refresh(self, interaction: discord.Interaction, value):
        try:
            await self.cog.persist_antinuke_setting(self.selected_key, value)
            self.set_embed(self.render_embed())
            await interaction.response.edit_message(view=self)
        except Exception:
            logging.getLogger("nuvella.antinuke").exception(
                "Could not persist AntiNuke setting | key=%s | actor_id=%s",
                self.selected_key, interaction.user.id,
            )
            await interaction.response.send_message(
                "Could not save this setting. Check `logs/errors.log`.", ephemeral=True
            )

    async def _select_setting(self, interaction: discord.Interaction):
        self.selected_key = interaction.data["values"][0]
        self.set_embed(self.render_embed())
        await interaction.response.edit_message(view=self)

    async def _adjust(self, interaction: discord.Interaction, direction: int):
        option = ANTINUKE_PANEL_OPTIONS[self.selected_key]
        current = getattr(self.cog, option["attribute"])
        value = round(float(current) + direction * float(option["step"]), 2)
        value = max(float(option["minimum"]), min(float(option["maximum"]), value))
        if option["step"] == 1:
            value = int(value)
        await self._save_and_refresh(interaction, value)

    async def _lower(self, interaction: discord.Interaction):
        await self._adjust(interaction, -1)

    async def _raise(self, interaction: discord.Interaction):
        await self._adjust(interaction, 1)

    async def _toggle(self, interaction: discord.Interaction):
        try:
            await self.cog.persist_antinuke_setting("enabled", not self.cog.antinuke_enabled)
            self.set_embed(self.render_embed())
            await interaction.response.edit_message(view=self)
        except Exception:
            logging.getLogger("nuvella.antinuke").exception(
                "Could not toggle AntiNuke | actor_id=%s", interaction.user.id
            )
            await interaction.response.send_message(
                "Could not save AntiNuke state. Check `logs/errors.log`.", ephemeral=True
            )

    async def _refresh(self, interaction: discord.Interaction):
        self.set_embed(self.render_embed())
        await interaction.response.edit_message(view=self)

    async def _exit(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(view=None)

    async def on_timeout(self) -> None:
        disable_all_components(self)
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ─── AntiNuke Cog ─────────────────────────────────────────────────
class Antinuke(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Prefer bot.config if it's already loaded; otherwise read config.json directly.
        if hasattr(bot, "config") and isinstance(bot.config, dict) and bot.config:
            self.config = bot.config
        else:
            self.config = load_config()

        # Top-level values
        self.log_channel_id = self.config.get("log_channel_id")
        self.allowed_link_role_ids = self.config.get("allowed_link_role_ids", [])
        self.antiphish_enabled = self.config.get("antiphish_enabled", True)
        self.wordfilter_enabled = self.config.get("wordfilter_enabled", True)

        # Antinuke sub-config — pull every value from config.json
        an = self.config.get("antinuke", {}) or {}
        self.antinuke_enabled        = an.get("enabled", True)

        # ── Spam (advanced) ──
        self.msg_spam_limit          = an.get("message_spam_limit", 3)     # 3 msgs / window
        self.window                  = an.get("window", 5)                 # 5s window
        self.dup_msg_limit           = an.get("dup_msg_limit", 2)          # 2 identical/near-identical
        self.similarity_threshold    = an.get("similarity_threshold", 0.85)
        self.min_chars_for_similarity = an.get("min_chars_for_similarity", 5)
        self.spam_escalation         = an.get("spam_escalation", [60, 300, 1800, 3600])
        self.spam_kick_strikes       = an.get("spam_kick_strikes", 5)
        self.strike_decay_seconds    = an.get("strike_decay_seconds", 900)

        # ── Other thresholds ──
        self.mention_limit           = an.get("mention_limit", 5)
        self.same_user_ping_limit    = an.get("same_user_ping_limit", 8)
        self.emoji_limit             = an.get("emoji_limit", 10)
        self.join_raid_limit         = an.get("join_raid_limit", 8)
        self.channel_limit           = an.get("channel_limit", 3)
        self.role_limit              = an.get("role_limit", 3)
        self.ban_limit               = an.get("ban_limit", 3)
        self.invite_timeout_seconds  = an.get("invite_timeout_seconds", 300)
        self.phish_timeout_seconds   = an.get("phish_timeout_seconds", 600)
        self.word_timeout_seconds    = an.get("word_timeout_seconds", 300)
        self.soft_mute_seconds       = an.get("soft_mute_seconds", 60)
        self.warn_kick_threshold     = an.get("warn_kick_threshold", 5)
        self.gateway_threshold       = an.get("gateway_threshold", an.get("join_raid_limit", 8))
        self.gateway_window          = an.get("gateway_window", 10)

        # State
        self._msg_times: dict[int, deque] = defaultdict(deque)
        self._msg_content: dict[int, deque] = defaultdict(deque)   # NEW: rolling content history
        self._spam_strikes: dict[int, list] = {}                   # NEW: uid -> [count, last_ts]
        self._join_times: deque = deque()
        self._audit_times: dict[int, dict[str, deque]] = defaultdict(lambda: defaultdict(deque))
        self._raid_locked: set[int] = set()

        self._spam_handling: set[int] = set()
        self._invite_handling: set[int] = set()
        self._phish_handling: set[int] = set()
        self._word_handling: set[int] = set()
        self._bot_handling: set[int] = set()

        self.db = Database(bot)
        self.phish_domains: list[str] = []
        self.banned_words: list[str] = []
        self.trusted_admins: set[int] = set()
        self.trusted_users: set[int] = set()
        self.trusted_mods: set[int] = set()
        self._init_task: asyncio.Task | None = None
        self._config_write_lock = asyncio.Lock()
        self._config_path = Path(__file__).resolve().parent.parent / "config.json"

    async def persist_antinuke_setting(self, key: str, value) -> None:
        """Persist a validated setting atomically and update this cog's live value."""
        allowed = {"enabled", *ANTINUKE_PANEL_OPTIONS.keys()}
        if key not in allowed:
            raise ValueError(f"Unsupported AntiNuke setting: {key}")
        if key != "enabled":
            option = ANTINUKE_PANEL_OPTIONS[key]
            number = float(value)
            if not float(option["minimum"]) <= number <= float(option["maximum"]):
                raise ValueError(f"{key} is outside its supported range")
            value = round(number, 2) if isinstance(option["step"], float) else int(number)
        else:
            value = bool(value)

        async with self._config_write_lock:
            cfg = self.config
            section = cfg.setdefault("antinuke", {})
            previous = section.get(key)
            section[key] = value
            tmp_path = self._config_path.with_suffix(".json.tmp")
            try:
                with tmp_path.open("w", encoding="utf-8", newline="\n") as fp:
                    json.dump(cfg, fp, ensure_ascii=False, indent=2)
                    fp.write("\n")
                os.replace(tmp_path, self._config_path)
            except Exception:
                if previous is None:
                    section.pop(key, None)
                else:
                    section[key] = previous
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

        if key == "enabled":
            self.antinuke_enabled = value
        else:
            setattr(self, ANTINUKE_PANEL_OPTIONS[key]["attribute"], value)
        self.bot.config = cfg
        logging.getLogger("nuvella.antinuke").info(
            "AntiNuke setting persisted | key=%s | value=%s", key, value
        )

    async def cog_load(self):
        # Complete DB bootstrap before the gateway starts dispatching commands.
        # _init_and_load performs database-only work and must not wait for READY.
        await self._init_and_load()

    async def cog_unload(self):
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()
            try:
                await self._init_task
            except asyncio.CancelledError:
                pass
        self._init_task = None

    # ─── Init / data loading ─────────────────────────────────────
    async def _init_and_load(self):
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
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS trusted_admins (user_id BIGINT PRIMARY KEY)"
        )
        await self.db.execute("CREATE TABLE IF NOT EXISTS trusted_users (user_id BIGINT PRIMARY KEY)")
        await self.db.execute("CREATE TABLE IF NOT EXISTS trusted_mods (user_id BIGINT PRIMARY KEY)")

        rows = await self.db.fetch("SELECT domain FROM phish_domains")
        self.phish_domains = [r["domain"] for r in rows]

        rows = await self.db.fetch("SELECT word FROM banned_words")
        self.banned_words = [r["word"] for r in rows]

        rows = await self.db.fetch("SELECT user_id FROM trusted_admins")
        self.trusted_admins = {int(r["user_id"]) for r in rows}
        rows = await self.db.fetch("SELECT user_id FROM trusted_users")
        self.trusted_users = {int(r["user_id"]) for r in rows}
        rows = await self.db.fetch("SELECT user_id FROM trusted_mods")
        self.trusted_mods = {int(r["user_id"]) for r in rows}

    # ─── Config helpers (all pulled from config.json) ────────────
    def _roles_cfg(self) -> dict:
        return (self.config.get("roles") or {})

    def _int_or_none(self, value) -> Optional[int]:
        try:
            v = int(value)
            return v or None
        except Exception:
            return None

    def get_admin_role_id(self) -> Optional[int]:
        return self._int_or_none(self._roles_cfg().get("admin_role_id"))

    def get_staff_role_ids(self) -> set:
        roles_cfg = self._roles_cfg()
        ids = set()
        for key in (
            "admin_role_id",
            "senior_moderator_role_id",
            "moderator_role_id",
            "trial_moderator_role_id",
            "developer_role_id",
            "event_manager_role_id",
            "trial_event_manager_role_id",
        ):
            rid = self._int_or_none(roles_cfg.get(key))
            if rid:
                ids.add(rid)
        return ids

    # ─── Permission helpers ──────────────────────────────────────
    def is_staff(self, member: discord.Member) -> bool:
        if not member or not getattr(member, "guild", None):
            return False

        if member.id == member.guild.owner_id or member.id == self.bot.owner_id:
            return True

        staff_role_ids = self.get_staff_role_ids()
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
        if user_id == self.bot.owner_id:
            return "bot_owner"

        member = None
        for guild in self.bot.guilds:
            m = guild.get_member(user_id)
            if m:
                member = m
                break

        if not member:
            return "user"

        if member.id == member.guild.owner_id:
            return "admin"

        roles_cfg = self._roles_cfg()
        admin_role_ids = set()
        for key in ("admin_role_id", "developer_role_id"):
            rid = self._int_or_none(roles_cfg.get(key))
            if rid:
                admin_role_ids.add(rid)

        mod_role_ids = set()
        for key in (
            "senior_moderator_role_id",
            "moderator_role_id",
            "trial_moderator_role_id",
            "event_manager_role_id",
            "trial_event_manager_role_id",
        ):
            rid = self._int_or_none(roles_cfg.get(key))
            if rid:
                mod_role_ids.add(rid)

        member_role_ids = {r.id for r in member.roles}

        if member_role_ids & admin_role_ids:
            return "admin"
        if member_role_ids & mod_role_ids:
            return "moderator"
        return "user"

    # ─── Logging ─────────────────────────────────────────────────
    def get_log_channel(self):
        if not self.log_channel_id:
            return None
        return self.bot.get_channel(self.log_channel_id)

    async def log_antinuke(self, guild: discord.Guild, title: str, desc: str):
        channel = self.get_log_channel()
        if not channel and self.log_channel_id:
            try:
                channel = await self.bot.fetch_channel(int(self.log_channel_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError, TypeError):
                channel = None
        if not channel:
            return
        embed = discord.Embed(
            title=f"{emoji('shield')} AntiNuke — {title}",
            description=(desc or "No additional details supplied.")[:4000],
            color=GREY_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Guild", value=f"{guild.name} (`{guild.id}`)", inline=False)
        embed.set_footer(text="AntiNuke System • action audit")
        try:
            await channel.send(view=ComponentsV2View(timeout=120, embed=embed), allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ─── Moderation helpers ──────────────────────────────────────
    async def _antinuke_notice(self, message: discord.Message, member: discord.Member, reason: str):
        try:
            notice = await message.channel.send(f"{member.mention} {emoji('warning')} {reason}")
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

    async def _add_warning(self, user_id: int, moderator_id: int, reason: str, countable: bool):
        await self.db.execute(
            "INSERT INTO warnings (user_id, moderator_id, reason, timestamp, countable) "
            "VALUES ($1, $2, $3, $4, $5)",
            user_id, moderator_id, reason, int(time.time()), countable,
        )
        count = await self.db.fetchval(
            "SELECT COUNT(*) FROM warnings WHERE user_id = $1 AND countable = $2",
            user_id, True,
        )
        if count and count % self.warn_kick_threshold == 0:
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

    # ─── Spam detection helpers ──────────────────────────────────
    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").lower()).strip()

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b).ratio()

    def _caps_ratio(self, text: str) -> float:
        letters = [c for c in text if c.isalpha()]
        if len(letters) < 8:
            return 0.0
        upper = sum(1 for c in letters if c.isupper())
        return upper / len(letters)

    def _register_strike(self, uid: int) -> int:
        """Increment a user's spam strike counter (decays after clean period)."""
        now = time.time()
        entry = self._spam_strikes.get(uid)
        if entry and (now - entry[1]) > self.strike_decay_seconds:
            entry = None  # decayed
        if not entry:
            self._spam_strikes[uid] = [1, now]
            return 1
        entry[0] += 1
        entry[1] = now
        return entry[0]

    def _punish_for_strike(self, strike: int) -> tuple[str, int]:
        """Return (action_label, seconds) for a given strike number."""
        if strike <= 1:
            return ("warn", 0)
        idx = strike - 2
        if idx < len(self.spam_escalation):
            return ("timeout", self.spam_escalation[idx])
        return ("kick", 0)

    # ─── on_message protections ──────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot or not self.antinuke_enabled:
            return
        if self.is_staff(message.author):
            return

        member = message.author
        uid = member.id
        now = time.time()

        # ─── Invite filter ───────────────────────────────────────
        if not any(role.id in self.allowed_link_role_ids for role in member.roles):
            invite_match = INVITE_PATTERN.search(message.content)
            if invite_match:
                code = invite_match.group(1)
                try:
                    invites = await message.guild.invites()
                    own = any(inv.code == code for inv in invites)
                    if not own:
                        fetched = await self.bot.fetch_invite(code)
                        own = bool(fetched.guild and fetched.guild.id == message.guild.id)
                except Exception:
                    own = False
                if not own:
                    if uid in self._invite_handling:
                        return
                    self._invite_handling.add(uid)
                    try:
                        try:
                            await message.delete()
                        except Exception:
                            pass
                        reason = "Sending Discord invite link"
                        await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", True)
                        await self._auto_timeout(member, self.invite_timeout_seconds, f"AntiNuke: {reason}")
                        await self._antinuke_notice(message, member, f"{reason} — muted {fmt_duration(self.invite_timeout_seconds)}.")
                        await self.log_antinuke(message.guild, "Invite Link", f"**User:** {member} (`{member.id}`)\n**Channel:** {message.channel.mention} (`{message.channel.id}`)\n**Deleted content:** {message.content[:1200] or '[empty]'}\n**Action:** message deleted, warning stored, timeout={self.invite_timeout_seconds}s")
                        return
                    finally:
                        self._invite_handling.discard(uid)

        # ─── Phishing ────────────────────────────────────────────
        if self.antiphish_enabled and self.phish_domains:
            content_lower = message.content.lower()
            for domain in self.phish_domains:
                if domain in content_lower:
                    if uid in self._phish_handling:
                        return
                    self._phish_handling.add(uid)
                    try:
                        try:
                            await message.delete()
                        except Exception:
                            pass
                        reason = f"Phishing link detected: {domain}"
                        await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", True)
                        await self._auto_timeout(member, self.phish_timeout_seconds, f"AntiNuke: {reason}")
                        await self._antinuke_notice(message, member, f"{reason} — muted {fmt_duration(self.phish_timeout_seconds)}.")
                        await self.log_antinuke(message.guild, "Phishing Link", f"**User:** {member} (`{member.id}`)\n**Channel:** {message.channel.mention} (`{message.channel.id}`)\n**Matched domain:** `{domain}`\n**Deleted content:** {message.content[:1200] or '[empty]'}\n**Action:** message deleted, warning stored, timeout={self.phish_timeout_seconds}s")
                        return
                    finally:
                        self._phish_handling.discard(uid)

        # ─── Word filter ─────────────────────────────────────────
        if self.wordfilter_enabled and self.banned_words:
            content_lower = message.content.lower()
            for word in self.banned_words:
                if word in content_lower:
                    if uid in self._word_handling:
                        return
                    self._word_handling.add(uid)
                    try:
                        try:
                            await message.delete()
                        except Exception:
                            pass
                        reason = f"Used banned word: '{word}'"
                        await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", False)
                        await self._auto_timeout(member, self.word_timeout_seconds, f"AntiNuke: {reason}")
                        await self._antinuke_notice(message, member, f"{reason} — muted {fmt_duration(self.word_timeout_seconds)}.")
                        await self.log_antinuke(message.guild, "Word Filter", f"**User:** {member} (`{member.id}`)\n**Channel:** {message.channel.mention} (`{message.channel.id}`)\n**Matched term:** `{word}`\n**Deleted content:** {message.content[:1200] or '[empty]'}\n**Action:** message deleted, warning stored")
                        return
                    finally:
                        self._word_handling.discard(uid)

        # ─── Repeated single-user pings ──────────────────────────
        if uid not in self.trusted_mods and uid not in self.trusted_admins and message.mentions:
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
                await self.log_antinuke(message.guild, "Mass Ping (Same User)", f"**User:** {member} (`{member.id}`)\n**Channel:** {message.channel.mention}\n**Target:** <@{worst_target}> ({worst_target})\n**Repeated ping count:** {worst_count}\n**Content:** {message.content[:1200] or '[empty]'}\n**Action:** message deleted, warning stored, timeout={self.soft_mute_seconds}s")
                return

        # ─── Message spam (advanced) ─────────────────────────────
        norm = self._normalize(message.content)

        if uid not in self._spam_handling:
            # Count every user message for flood detection, including very short
            # or attachment-only messages. Only compare text similarity when the
            # current message is long enough to produce a meaningful comparison.
            tq = self._msg_times[uid]
            tq.append(now)
            while tq and now - tq[0] > self.window:
                tq.popleft()

            cq = self._msg_content[uid]
            comparable_text = norm if len(norm) >= self.min_chars_for_similarity else ""
            cq.append((now, comparable_text))
            while cq and now - cq[0][0] > self.window:
                cq.popleft()

            msg_count = len(tq)

            # Duplicate / near-duplicate detection is separate from the all-message
            # flood count, so different short texts still trigger the 3-in-window rule.
            dup_count = 0
            if comparable_text:
                for _ts, prev in list(cq)[:-1]:
                    if not prev:
                        continue
                    if prev == comparable_text or self._similarity(prev, comparable_text) >= self.similarity_threshold:
                        dup_count += 1

            # Secondary signals (for the log only)
            caps = self._caps_ratio(message.content)
            mentions = len(message.mentions) + len(message.role_mentions)
            emojis = len(re.findall(r"<a?:\w+:\d+>", message.content))

            triggered = (
                msg_count >= self.msg_spam_limit
                or dup_count >= self.dup_msg_limit
            )

            if triggered:
                self._spam_handling.add(uid)
                try:
                    tq.clear()
                    cq.clear()

                    try:
                        await message.delete()
                        await message.channel.purge(
                            limit=self.msg_spam_limit + 5,
                            check=lambda m: m.author == member,
                        )
                    except Exception:
                        pass

                    strike = self._register_strike(uid)
                    action, seconds = self._punish_for_strike(strike)

                    if msg_count >= self.msg_spam_limit:
                        reason = f"Message flood ({msg_count} msgs in {self.window}s)"
                    else:
                        reason = f"Repeated spam ({dup_count + 1} near-identical msgs)"

                    if action == "warn":
                        await self._antinuke_notice(
                            message, member,
                            f"{reason}. Please don't spam — next offense is a mute.",
                        )
                    elif action == "timeout":
                        await self._add_warning(
                            member.id, self.bot.user.id, f"[AntiNuke] {reason}", False
                        )
                        await self._auto_timeout(member, seconds, reason=reason)
                        await self._antinuke_notice(
                            message, member,
                            f"{reason}. Muted for {fmt_duration(seconds)} (strike {strike}).",
                        )
                    else:  # kick
                        await self._add_warning(
                            member.id, self.bot.user.id, f"[AntiNuke] {reason}", True
                        )
                        try:
                            await member.kick(reason=f"AntiNuke: {reason} (strike {strike})")
                        except Exception:
                            pass
                        await self._antinuke_notice(
                            message, member,
                            f"{reason}. Kicked (strike {strike}).",
                        )

                    await self.log_antinuke(
                        message.guild,
                        "Message Spam",
                        f"**User:** {member} (`{member.id}`)\n"
                        f"**Channel:** {message.channel.mention} (`{message.channel.id}`)\n"
                        f"**Messages in {self.window}s:** {msg_count}\n"
                        f"**Duplicate / near-duplicate:** {dup_count}\n"
                        f"**Mentions:** {mentions}  •  **Emojis:** {emojis}  •  **Caps:** {caps:.0%}\n"
                        f"**Strike:** {strike}  •  **Action:** {action}"
                        + (f" ({seconds}s)" if seconds else "")
                        + f"\n**Last content:** {message.content[:800] or '[empty]'}",
                    )

                    return
                finally:
                    self._spam_handling.discard(uid)

        # ─── Mass mention ────────────────────────────────────────
        mention_count = len(set(message.mentions + message.role_mentions))
        if uid not in self.trusted_mods and uid not in self.trusted_admins and mention_count >= self.mention_limit:
            try:
                await message.delete()
            except Exception:
                pass
            reason = f"Mass mention ({mention_count} mentions)"
            await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", True)
            await self._auto_timeout(member, self.soft_mute_seconds, reason=reason)
            await self._antinuke_notice(message, member, f"{reason} — muted {fmt_duration(self.soft_mute_seconds)}.")
            await self.log_antinuke(message.guild, "Mass Mention", f"**User:** {member} (`{member.id}`)\n**Channel:** {message.channel.mention} (`{message.channel.id}`)\n**Mention count:** {mention_count}\n**Members:** {', '.join(m.mention for m in message.mentions[:20]) or 'None'}\n**Roles:** {', '.join(r.mention for r in message.role_mentions[:20]) or 'None'}\n**Content:** {message.content[:1200] or '[empty]'}\n**Action:** message deleted, warning stored, timeout={self.soft_mute_seconds}s")
            return

        # ─── Emoji spam ──────────────────────────────────────────
        emojis_found = re.findall(r"<a?:\w+:\d+>", message.content)
        if uid not in self.trusted_users and uid not in self.trusted_admins and len(emojis_found) >= self.emoji_limit:
            try:
                await message.delete()
            except Exception:
                pass
            reason = f"Emoji spam ({len(emojis_found)} custom emojis)"
            strike = self._register_strike(uid)
            if strike > 1:
                await self._add_warning(member.id, self.bot.user.id, f"[AntiNuke] {reason}", False)
                await self._auto_timeout(member, self.soft_mute_seconds, reason=reason)
                await self._antinuke_notice(message, member, f"{reason} — muted {fmt_duration(self.soft_mute_seconds)}.")
            else:
                await self._antinuke_notice(message, member, f"{reason} — this is a warning, next time you'll be muted.")
            await self.log_antinuke(message.guild, "Emoji Spam", f"**User:** {member} (`{member.id}`)\n**Channel:** {message.channel.mention} (`{message.channel.id}`)\n**Custom emoji count:** {len(emojis_found)} (limit {self.emoji_limit})\n**Content:** {message.content[:1200] or '[empty]'}\n**Action:** message deleted; timeout/warning behavior applied")

    # ─── Join raid / bot add protection ──────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not self.antinuke_enabled:
            return
        guild = member.guild

        if member.bot:
            asyncio.create_task(self._handle_bot_add(member))
            return

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
                    "Verification set to Highest and all channels locked.",
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

    async def _handle_bot_add(self, member: discord.Member):
        """If a bot is added by an admin who isn't trusted, kick the bot and strip the admin role."""
        guild = member.guild
        if guild.id in self._bot_handling:
            return
        self._bot_handling.add(guild.id)

        try:
            await asyncio.sleep(1.5)  # let the audit log populate

            if not guild.me.guild_permissions.view_audit_log:
                return

            adder = None
            try:
                async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.bot_add):
                    if entry.target and entry.target.id == member.id:
                        adder = entry.user
                        break
            except Exception:
                return

            if not adder:
                return

            if adder.id in self.trusted_admins:
                await self.log_antinuke(
                    guild,
                    "Bot Added (Trusted)",
                    f"{adder.mention} (`{adder.id}`) added {member.mention} (`{member.id}`) — trusted, allowed.",
                )
                return

            if adder.id == self.bot.owner_id or adder.id == guild.owner_id:
                return

            admin_role_id = self.get_admin_role_id()
            adder_member = guild.get_member(adder.id)
            if not adder_member:
                return

            has_admin_role = admin_role_id is not None and any(
                r.id == admin_role_id for r in adder_member.roles
            )

            if not has_admin_role:
                try:
                    await member.kick(reason="AntiNuke: Bot added by untrusted user")
                except Exception:
                    pass
                await self.log_antinuke(
                    guild,
                    "Untrusted Bot Add",
                    f"{adder.mention} (`{adder.id}`) added {member.mention} (`{member.id}`) — bot kicked (not trusted, not admin).",
                )
                return

            try:
                await member.kick(reason="AntiNuke: Bot added by untrusted admin")
            except Exception:
                pass

            try:
                admin_role = guild.get_role(admin_role_id)
                if admin_role:
                    await adder_member.remove_roles(
                        admin_role, reason="AntiNuke: Added untrusted bot"
                    )
            except Exception:
                pass

            await self.log_antinuke(
                guild,
                "Untrusted Admin Bot Add",
                (
                    f"{adder.mention} (`{adder.id}`) added {member.mention} (`{member.id}`).\n"
                    f"**Bot kicked** and **admin role removed** (not in trusted admins list).\n"
                    f"Use `atrust add {adder.id}` to trust them."
                ),
            )
        finally:
            self._bot_handling.discard(guild.id)

    # ─── Audit-log flood checks ──────────────────────────────────
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
                    await member.edit(
                        roles=[r for r in member.roles if r == guild.default_role],
                        reason=f"AntiNuke: {reason}",
                    )
                except Exception:
                    pass
                await self._auto_timeout(member, 600, reason=f"AntiNuke: {reason}")

    # ─── Commands: antinuke ──────────────────────────────────────
    @commands.group(name="antinuke", invoke_without_command=True)
    @require("admin")
    async def antinuke_group(self, ctx):
        status = f"{emoji('tick')} Enabled" if self.antinuke_enabled else f"{emoji('cross')} Disabled"
        embed = make_embed(
            ctx,
            f"{emoji('shield')} AntiNuke System",
            f"**Status:** {status}\n\n"
            "`antinuke enable` — Activate\n"
            "`antinuke disable` — Deactivate\n"
            "`antinuke status` — View thresholds\n"
            "`antinuke panel` — Interactive Components V2 settings\n"
            "`antinuke threshold <type> <value>` — Adjust and save a threshold\n"
            "`antinuke unraidlock` — Restore after raid lock\n"
            "`antinuke spamreset <user>` — Clear a user's spam strikes\n"
            "`atrust add/remove <user>` — Manage trusted admins",
            GREY_COLOR,
        )
        await ctx.reply(embed=embed)

    @antinuke_group.command(name="panel")
    @require("admin")
    async def antinuke_panel(self, ctx):
        """Open an interactive Components V2 panel to customize AntiNuke."""
        panel = AntinukePanelView(self, ctx.author.id)
        message = await ctx.reply(view=panel, mention_author=False)
        panel.message = message

    @antinuke_group.command(name="enable")
    @require("admin")
    async def antinuke_enable(self, ctx):
        await self.persist_antinuke_setting("enabled", True)
        await ctx.reply(embed=success_embed(ctx, "AntiNuke protection **enabled**."))

    @antinuke_group.command(name="disable")
    @require("admin")
    async def antinuke_disable(self, ctx):
        await self.persist_antinuke_setting("enabled", False)
        await ctx.reply(embed=make_embed(
            ctx,
            f"{emoji('shield')} AntiNuke Disabled",
            "All automated protections are now **off**.",
            GREY_COLOR,
        ))

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

    @antinuke_group.command(name="spamreset")
    @require("admin")
    async def antinuke_spamreset(self, ctx, user: discord.Member):
        self._spam_strikes.pop(user.id, None)
        self._msg_times.pop(user.id, None)
        self._msg_content.pop(user.id, None)
        await ctx.reply(embed=success_embed(ctx, f"Cleared spam strikes and history for {user.mention}."))

    @antinuke_group.command(name="status")
    @require("moderator")
    async def antinuke_status(self, ctx):
        status = f"{emoji('tick')} Active" if self.antinuke_enabled else f"{emoji('cross')} Inactive"
        embed = make_embed(ctx, f"{emoji('shield')} AntiNuke Status", f"**Status:** {status}", GREY_COLOR)
        thresholds = [
            (f"{emoji('message')} Message Spam", f"≥{self.msg_spam_limit} msgs in {self.window}s  •  ≥{self.dup_msg_limit} duplicates"),
            (f"{emoji('mail')} Mass Mention", f"≥{self.mention_limit} mentions/msg"),
            (f"{emoji('laugh')} Emoji Spam", f"≥{self.emoji_limit} custom emojis/msg"),
            (f"{emoji('red_shield')} Gateway (Raid)", f"≥{self.gateway_threshold} joins in {self.gateway_window}s"),
            (f"{emoji('file')} Channel Flood", f"≥{self.channel_limit} creates/deletes in {self.window}s"),
            (f"{emoji('roles')} Role Flood", f"≥{self.role_limit} creates/deletes in {self.window}s"),
            (f"{emoji('gear')} Mass Ban", f"≥{self.ban_limit} bans in {self.window}s"),
            (f"{emoji('lock')} Raid Locked Guilds", str(len(self._raid_locked))),
            (f"{emoji('link')} Invite Links", f"External invites → delete + {self.invite_timeout_seconds//60}‑min mute"),
            (
                f"{emoji('fishing_rod')} Anti‑Phishing",
                f"{emoji('tick') if self.antiphish_enabled else emoji('cross')} → delete + {self.phish_timeout_seconds//60}‑min mute",
            ),
            (
                f"{emoji('alphabet')} Word Filter",
                f"{emoji('tick') if self.wordfilter_enabled else emoji('cross')} → delete + {self.word_timeout_seconds//60}‑min mute",
            ),
            (f"{emoji('handshake')} Trusted Admins", str(len(self.trusted_admins))),
            (f"{emoji('laugh')} Trusted Users (emoji cap bypass)", str(len(self.trusted_users))),
            (f"{emoji('handshake')} Trusted Mods (mass-ping bypass)", str(len(self.trusted_mods))),
        ]
        for name, val in thresholds:
            embed.add_field(name=name, value=val, inline=False)
        await ctx.reply(embed=embed)

    @antinuke_group.command(name="threshold")
    @require("admin")
    async def antinuke_threshold(self, ctx, threshold_type: str, value: int):
        mapping = {
            "msg_spam": ("message_spam_limit", 2, 20),
            "dup": ("dup_msg_limit", 2, 10),
            "mention": ("mention_limit", 2, 25),
            "emoji": ("emoji_limit", 2, 30),
            "gateway": ("gateway_threshold", 3, 50),
            "channel": ("channel_limit", 1, 20),
            "role": ("role_limit", 1, 20),
            "ban": ("ban_limit", 1, 20),
        }
        t = threshold_type.lower()
        if t not in mapping:
            return await ctx.reply(
                embed=error_embed(ctx, f"Invalid type. Options: `{'`, `'.join(mapping)}`")
            )
        key, minimum, maximum = mapping[t]
        if not minimum <= value <= maximum:
            return await ctx.reply(embed=error_embed(ctx, f"Value for `{t}` must be from {minimum} to {maximum}."))
        try:
            await self.persist_antinuke_setting(key, value)
            await ctx.reply(embed=success_embed(ctx, f"Threshold **{t}** updated and saved to **{value}**."))
        except Exception:
            logging.getLogger("nuvella.antinuke").exception("Failed to save threshold | type=%s", t)
            await ctx.reply(embed=error_embed(ctx, "Could not save that setting. Check `logs/errors.log`."))

    # ─── Commands: atrust ────────────────────────────────────────
    @commands.group(name="atrust", invoke_without_command=True)
    @require("admin")
    async def atrust_group(self, ctx):
        embed = make_embed(
            ctx,
            f"{emoji('handshake')} Trusted Admins",
            "Manage which admins are allowed to add bots without triggering protection.\n\n"
            "`atrust add <user>` — Trust an admin to add bots\n"
            "`atrust remove <user>` — Remove trust\n"
            "`atrust list` — View trusted admins\n\n"
            "> If a **non-trusted admin** adds a bot, the bot will be kicked and the admin role revoked.",
            GREY_COLOR,
        )
        await ctx.reply(embed=embed)

    @atrust_group.command(name="add")
    @require("admin")
    async def atrust_add(self, ctx, user: discord.Member):
        if user.id in self.trusted_admins:
            return await ctx.reply(embed=error_embed(ctx, f"{user.mention} is already trusted."))

        self.trusted_admins.add(user.id)
        try:
            await self.db.execute(
                "INSERT INTO trusted_admins (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
                user.id,
            )
        except Exception:
            pass

        await ctx.reply(embed=success_embed(
            ctx,
            f"Trusted {user.mention} (`{user.id}`). They can now add bots without triggering protection.",
        ))
        await self.log_antinuke(
            ctx.guild,
            "Trusted Admin Added",
            f"{user.mention} (`{user.id}`) trusted by {ctx.author.mention} (`{ctx.author.id}`).",
        )

    @atrust_group.command(name="remove")
    @require("admin")
    async def atrust_remove(self, ctx, user: discord.Member):
        if user.id not in self.trusted_admins:
            return await ctx.reply(embed=error_embed(ctx, f"{user.mention} is not trusted."))

        self.trusted_admins.discard(user.id)
        try:
            await self.db.execute("DELETE FROM trusted_admins WHERE user_id = $1", user.id)
        except Exception:
            pass

        await ctx.reply(embed=success_embed(
            ctx,
            f"Removed trust from {user.mention} (`{user.id}`).",
        ))
        await self.log_antinuke(
            ctx.guild,
            "Trusted Admin Removed",
            f"{user.mention} (`{user.id}`) untrusted by {ctx.author.mention} (`{ctx.author.id}`).",
        )

    @atrust_group.command(name="list")
    @require("admin")
    async def atrust_list(self, ctx):
        if not self.trusted_admins:
            return await ctx.reply(embed=make_embed(
                ctx,
                f"{emoji('handshake')} Trusted Admins",
                "No trusted admins configured.",
                GREY_COLOR,
            ))

        lines = []
        for uid in self.trusted_admins:
            member = ctx.guild.get_member(uid)
            if member:
                lines.append(f"• {member.mention} (`{uid}`)")
            else:
                lines.append(f"• Unknown (`{uid}`)")

        embed = make_embed(
            ctx,
            f"{emoji('handshake')} Trusted Admins",
            "\n".join(lines),
            GREY_COLOR,
        )
        await ctx.reply(embed=embed)


    @commands.group(name="atrustuser", aliases=["trusteduser"], invoke_without_command=True)
    @require("admin")
    async def atrust_user_group(self, ctx):
        await ctx.reply(embed=make_embed(ctx, "Trusted Users", "Trusted users bypass the custom-emoji cap.\n`atrustuser add @user`\n`atrustuser remove @user`\n`atrustuser list`", GREY_COLOR))

    @atrust_user_group.command(name="add")
    @require("admin")
    async def atrust_user_add(self, ctx, user: discord.Member):
        self.trusted_users.add(user.id)
        await self.db.execute("INSERT INTO trusted_users(user_id) VALUES($1) ON CONFLICT DO NOTHING", user.id)
        await ctx.reply(embed=success_embed(ctx, f"Trusted {user.mention} to bypass the custom-emoji cap."))
        await self.log_antinuke(ctx.guild, "Trusted User Added", f"**Target:** {user} (`{user.id}`)\n**Changed by:** {ctx.author} (`{ctx.author.id}`)\n**Benefit:** emoji-cap bypass")

    @atrust_user_group.command(name="remove")
    @require("admin")
    async def atrust_user_remove(self, ctx, user: discord.Member):
        self.trusted_users.discard(user.id)
        await self.db.execute("DELETE FROM trusted_users WHERE user_id=$1", user.id)
        await ctx.reply(embed=success_embed(ctx, f"Removed {user.mention} from trusted users."))
        await self.log_antinuke(ctx.guild, "Trusted User Removed", f"**Target:** {user} (`{user.id}`)\n**Changed by:** {ctx.author} (`{ctx.author.id}`)\n**Benefit removed:** emoji-cap bypass")

    @atrust_user_group.command(name="list")
    @require("admin")
    async def atrust_user_list(self, ctx):
        rows = [f"• {ctx.guild.get_member(uid).mention if ctx.guild.get_member(uid) else f'Unknown (`{uid}`)'}" for uid in sorted(self.trusted_users)]
        await ctx.reply(embed=make_embed(ctx, "Trusted Users", "\n".join(rows) if rows else "No trusted users configured.", GREY_COLOR))

    @commands.group(name="atrustmod", aliases=["trustedmod"], invoke_without_command=True)
    @require("admin")
    async def atrust_mod_group(self, ctx):
        await ctx.reply(embed=make_embed(ctx, "Trusted Moderators", "Trusted moderators bypass mass-mention and repeated-user-ping caps.\n`atrustmod add @user`\n`atrustmod remove @user`\n`atrustmod list`", GREY_COLOR))

    @atrust_mod_group.command(name="add")
    @require("admin")
    async def atrust_mod_add(self, ctx, user: discord.Member):
        self.trusted_mods.add(user.id)
        await self.db.execute("INSERT INTO trusted_mods(user_id) VALUES($1) ON CONFLICT DO NOTHING", user.id)
        await ctx.reply(embed=success_embed(ctx, f"Trusted {user.mention} for controlled mass-ping moderation actions."))
        await self.log_antinuke(ctx.guild, "Trusted Moderator Added", f"**Target:** {user} (`{user.id}`)\n**Changed by:** {ctx.author} (`{ctx.author.id}`)\n**Benefit:** mass-mention / repeated-ping bypass")

    @atrust_mod_group.command(name="remove")
    @require("admin")
    async def atrust_mod_remove(self, ctx, user: discord.Member):
        self.trusted_mods.discard(user.id)
        await self.db.execute("DELETE FROM trusted_mods WHERE user_id=$1", user.id)
        await ctx.reply(embed=success_embed(ctx, f"Removed {user.mention} from trusted moderators."))
        await self.log_antinuke(ctx.guild, "Trusted Moderator Removed", f"**Target:** {user} (`{user.id}`)\n**Changed by:** {ctx.author} (`{ctx.author.id}`)\n**Benefit removed:** mass-mention / repeated-ping bypass")

    @atrust_mod_group.command(name="list")
    @require("admin")
    async def atrust_mod_list(self, ctx):
        rows = [f"• {ctx.guild.get_member(uid).mention if ctx.guild.get_member(uid) else f'Unknown (`{uid}`)'}" for uid in sorted(self.trusted_mods)]
        await ctx.reply(embed=make_embed(ctx, "Trusted Moderators", "\n".join(rows) if rows else "No trusted moderators configured.", GREY_COLOR))


async def setup(bot):
    await bot.add_cog(Antinuke(bot))
