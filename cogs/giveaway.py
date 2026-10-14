from __future__ import annotations

import asyncio
import enum
import os
import random
import re
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.emoji import EMOJIS
from utils.components import ComponentsV2View, prepare_v2_view, disable_all_components
from utils.database import run_transaction

_SECRET_PATTERN = re.compile(
    r"(postgres(?:ql)?://[^\s\"']+|Bot [A-Za-z0-9._-]{20,}|token[\"']?\s*[:=]\s*[\"']?[\w.\-]{20,})",
    re.IGNORECASE,
)


def _sanitize(text: str) -> str:
    return _SECRET_PATTERN.sub("[REDACTED]", text)


class GiveawayStatus(str, enum.Enum):
    ACTIVE = "active"
    ENDED = "ended"
    CANCELLED = "cancelled"


class MessageRequirementPeriod(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


class RoleListType(str, enum.Enum):
    REQUIRED = "required"
    BLACKLIST = "blacklist"
    BYPASS = "bypass"


BUTTON_STYLE_MAP = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}

# ─────────────────────────────────────────────
# Modern Discord grey theme colour — used for every embed
GREY_COLOR = 0x2B2D31
# Default reroll window (hours) when none is provided
DEFAULT_REROLL_HOURS = 24
# Additional role that may use /giveaway start, edit, cancel, end only.
GIVEAWAY_HOST_ROLE_ID = 1535961511275536477
# ─────────────────────────────────────────────


@dataclass
class GiveawaySettings:
    guild_id: int
    channel_id: int
    host_id: int
    author: str
    prize: str
    winner_count: int
    duration_seconds: int
    reroll_hours: int
    description: Optional[str] = None
    message: Optional[str] = None
    thumbnail_url: Optional[str] = None
    image_url: Optional[str] = None
    gif_url: Optional[str] = None
    video_url: Optional[str] = None
    color: int = GREY_COLOR
    button_style: str = "secondary"
    button_emoji: Optional[str] = f"{EMOJIS['confetti']}"
    button_label: str = "Enter Giveaway"
    required_message_count: int = 0
    required_message_period: str = MessageRequirementPeriod.DAILY.value
    message_from_giveaway: bool = False
    invite_mode: bool = False
    required_join_age_days: int = 0
    required_invite_count: Optional[int] = None
    required_level: Optional[int] = None
    required_booster: bool = False
    required_nitro: bool = False
    winner_role_id: Optional[int] = None
    custom_timestamp: bool = True
    required_roles: list[int] = field(default_factory=list)
    blacklist_roles: list[int] = field(default_factory=list)
    bypass_roles: list[int] = field(default_factory=list)
    sponsor: Optional[str] = None
    use_global_blacklist: bool = True


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id            BIGINT PRIMARY KEY,
    staff_role_id       BIGINT,
    staff_channel_id    BIGINT,
    log_channel_id      BIGINT,
    manager_role_id     BIGINT
);

CREATE TABLE IF NOT EXISTS giveaways (
    id                          BIGSERIAL PRIMARY KEY,
    guild_id                    BIGINT NOT NULL,
    channel_id                  BIGINT NOT NULL,
    message_id                  BIGINT,
    thread_id                   BIGINT,
    host_id                     BIGINT NOT NULL,
    host_display_name           TEXT NOT NULL,
    prize                       TEXT NOT NULL,
    description                 TEXT,
    message                     TEXT,
    thumbnail_url               TEXT,
    image_url                   TEXT,
    gif_url                     TEXT,
    video_url                   TEXT,
    color                       INTEGER NOT NULL DEFAULT 6950317,
    button_style                TEXT NOT NULL DEFAULT 'primary',
    button_emoji                TEXT,
    button_label                TEXT NOT NULL DEFAULT 'Enter Giveaway',
    winner_count                INTEGER NOT NULL CHECK (winner_count > 0),
    required_message_count      INTEGER NOT NULL DEFAULT 0,
    required_message_period     TEXT NOT NULL DEFAULT 'daily',
    message_from_giveaway      BOOLEAN NOT NULL DEFAULT FALSE,
    invite_mode                BOOLEAN NOT NULL DEFAULT FALSE,
    required_join_age_days      INTEGER NOT NULL DEFAULT 0,
    required_invite_count       INTEGER,
    required_level              INTEGER,
    required_booster            BOOLEAN NOT NULL DEFAULT FALSE,
    required_nitro              BOOLEAN NOT NULL DEFAULT FALSE,
    winner_role_id              BIGINT,
    custom_timestamp            BOOLEAN NOT NULL DEFAULT TRUE,
    sponsor                     TEXT,
    status                      TEXT NOT NULL DEFAULT 'active',
    start_time                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    end_time                    TIMESTAMPTZ NOT NULL,
    ended_at                    TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    reroll_hours                INTEGER NOT NULL DEFAULT 24,
    use_global_blacklist        BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_giveaways_guild ON giveaways(guild_id);
CREATE INDEX IF NOT EXISTS idx_giveaways_status_end ON giveaways(status, end_time);
CREATE INDEX IF NOT EXISTS idx_giveaways_channel ON giveaways(channel_id);

CREATE TABLE IF NOT EXISTS giveaway_roles (
    giveaway_id  BIGINT NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
    role_id      BIGINT NOT NULL,
    list_type    TEXT NOT NULL CHECK (list_type IN ('required', 'blacklist', 'bypass')),
    PRIMARY KEY (giveaway_id, role_id, list_type)
);

CREATE INDEX IF NOT EXISTS idx_giveaway_roles_giveaway ON giveaway_roles(giveaway_id);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    id            BIGSERIAL PRIMARY KEY,
    giveaway_id   BIGINT NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
    user_id       BIGINT NOT NULL,
    entered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (giveaway_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_entries_giveaway ON giveaway_entries(giveaway_id);

CREATE TABLE IF NOT EXISTS giveaway_winners (
    id            BIGSERIAL PRIMARY KEY,
    giveaway_id   BIGINT NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
    user_id       BIGINT NOT NULL,
    won_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_reroll     BOOLEAN NOT NULL DEFAULT FALSE,
    dm_sent       BOOLEAN NOT NULL DEFAULT FALSE,
    role_assigned BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_winners_giveaway ON giveaway_winners(giveaway_id);

-- THIS TABLE IS NOT DELETED! Message counts are preserved
CREATE TABLE IF NOT EXISTS user_message_counts (
    guild_id       BIGINT NOT NULL,
    user_id        BIGINT NOT NULL,
    daily_count    INTEGER NOT NULL DEFAULT 0,
    weekly_count   INTEGER NOT NULL DEFAULT 0,
    lifetime_count INTEGER NOT NULL DEFAULT 0,
    daily_reset_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    weekly_reset_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS giveaway_logs (
    id           BIGSERIAL PRIMARY KEY,
    guild_id     BIGINT NOT NULL,
    giveaway_id  BIGINT,
    event_type   TEXT NOT NULL,
    data         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_logs_guild ON giveaway_logs(guild_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_logs_giveaway ON giveaway_logs(giveaway_id);

CREATE TABLE IF NOT EXISTS giveaway_message_counts (
    giveaway_id BIGINT NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (giveaway_id, user_id)
);
"""


def _format_error(title: str, message: str) -> str:
    """Format a structured error for the central logging handlers."""
    return f"{EMOJIS['x']} {title} | {_sanitize(str(message))}"


def _config_get(key: str, default: Any = None, config: Any = None) -> Any:
    """Read configuration from the bot's loaded config without relying on a global."""
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _get_global_blacklist(config: Any = None) -> list[int]:
    """Return configured global giveaway blacklist role IDs as a list."""
    blacklist = _config_get("giveaway_blacklist", None, config)
    if isinstance(blacklist, int):
        return [blacklist]
    if isinstance(blacklist, (list, tuple, set)):
        result = []
        for role_id in blacklist:
            try:
                if role_id is not None:
                    result.append(int(role_id))
            except (TypeError, ValueError):
                continue
        return result
    return []


def _get_giveaway_permission_role_ids(config: Any = None) -> set[int]:
    """
    Collect only the role IDs that are permitted to manage giveaways:
    event manager, senior moderator, and admin.

    Reads from the ``roles`` section of config.json (and falls back to legacy
    flat keys). Never includes moderator/trial/level roles.
    """
    ids: set[int] = set()

    roles_section = _config_get("roles", {}, config)
    if isinstance(roles_section, dict):
        for key in ("admin_role_id", "senior_moderator_role_id", "event_manager_role_id"):
            val = roles_section.get(key)
            if isinstance(val, int):
                ids.add(val)
            elif isinstance(val, list):
                ids.update(v for v in val if isinstance(v, int))

    # Legacy flat names
    for attr in ("admin_role", "sr_event_team_role", "head_staff_role"):
        val = _config_get(attr, None, config)
        if isinstance(val, int):
            ids.add(val)
        elif isinstance(val, list):
            ids.update(v for v in val if isinstance(v, int))

    return ids


class GiveawayRepo:
    """All persistence goes through here. Every query is parameterized."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def bootstrap(self) -> None:
        try:
            async def operation(conn):
                await conn.execute(SCHEMA_SQL)
                await self._ensure_columns(conn)
            await run_transaction(self.pool, operation, label="giveaway.bootstrap")
            logging.getLogger("nuvella.database").info("Giveaway schema bootstrap complete")
        except Exception:
            logging.getLogger("nuvella.database").exception("Giveaway schema bootstrap failed")
            raise

    async def _ensure_columns(self, conn: asyncpg.Connection) -> None:
        # Additive migrations preserve existing giveaway data and are safe to rerun.
        await conn.execute("ALTER TABLE giveaways ADD COLUMN IF NOT EXISTS sponsor TEXT")
        await conn.execute("ALTER TABLE giveaways ADD COLUMN IF NOT EXISTS reroll_hours INTEGER NOT NULL DEFAULT 24")
        await conn.execute("ALTER TABLE giveaways ADD COLUMN IF NOT EXISTS use_global_blacklist BOOLEAN NOT NULL DEFAULT TRUE")
        await conn.execute("ALTER TABLE giveaways ADD COLUMN IF NOT EXISTS message_from_giveaway BOOLEAN NOT NULL DEFAULT FALSE")
        await conn.execute("ALTER TABLE giveaways ADD COLUMN IF NOT EXISTS invite_mode BOOLEAN NOT NULL DEFAULT FALSE")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_message_counts (
                giveaway_id BIGINT NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (giveaway_id, user_id)
            )
        """)

    async def get_guild_settings(self, guild_id: int) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM guild_settings WHERE guild_id = $1", guild_id)

    async def upsert_guild_settings(self, guild_id: int, **kwargs: Any) -> None:
        if not kwargs:
            return
        cols = list(kwargs.keys())
        placeholders = ", ".join(f"${i + 2}" for i in range(len(cols)))
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        query = (
            f"INSERT INTO guild_settings (guild_id, {', '.join(cols)}) "
            f"VALUES ($1, {placeholders}) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {updates}"
        )
        async with self.pool.acquire() as conn:
            await conn.execute(query, guild_id, *kwargs.values())

    async def create_giveaway(self, s: GiveawaySettings, end_time: datetime) -> int:
        async def operation(conn):
            row = await conn.fetchrow(
                """
                INSERT INTO giveaways (
                    guild_id, channel_id, host_id, host_display_name, prize, description,
                    message, thumbnail_url, image_url, gif_url, video_url, color,
                    button_style, button_emoji, button_label, winner_count,
                    required_message_count, required_message_period,
                    required_join_age_days, message_from_giveaway, invite_mode,
                    required_invite_count, required_level, required_booster, required_nitro,
                    winner_role_id, custom_timestamp, end_time,
                    sponsor, reroll_hours, use_global_blacklist
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
                    $20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31
                ) RETURNING id
                """,
                s.guild_id, s.channel_id, s.host_id, s.author, s.prize,
                s.description, s.message, s.thumbnail_url, s.image_url,
                s.gif_url, s.video_url, s.color, s.button_style, s.button_emoji,
                s.button_label, s.winner_count, s.required_message_count,
                s.required_message_period, s.required_join_age_days,
                s.message_from_giveaway, s.invite_mode,
                s.required_invite_count, s.required_level, s.required_booster,
                s.required_nitro, s.winner_role_id, s.custom_timestamp, end_time,
                s.sponsor, s.reroll_hours, s.use_global_blacklist,
            )
            giveaway_id = row["id"]
            await self._insert_role_lists(conn, giveaway_id, s)
            return giveaway_id
        return await run_transaction(self.pool, operation, label="giveaway.create")

    async def _insert_role_lists(
        self, conn: asyncpg.Connection, giveaway_id: int, s: GiveawaySettings
    ) -> None:
        entries: list[tuple[int, int, str]] = []
        entries += [(giveaway_id, r, RoleListType.REQUIRED.value) for r in s.required_roles]
        entries += [(giveaway_id, r, RoleListType.BLACKLIST.value) for r in s.blacklist_roles]
        entries += [(giveaway_id, r, RoleListType.BYPASS.value) for r in s.bypass_roles]
        if entries:
            await conn.executemany(
                "INSERT INTO giveaway_roles (giveaway_id, role_id, list_type) "
                "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                entries,
            )

    async def set_message_id(
        self, giveaway_id: int, message_id: int, thread_id: Optional[int] = None
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE giveaways SET message_id = $2, thread_id = $3 WHERE id = $1",
                giveaway_id, message_id, thread_id,
            )

    async def get_giveaway(self, giveaway_id: int) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM giveaways WHERE id = $1", giveaway_id)

    async def get_role_lists(self, giveaway_id: int) -> dict[str, list[int]]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT role_id, list_type FROM giveaway_roles WHERE giveaway_id = $1",
                giveaway_id,
            )
        result: dict[str, list[int]] = {"required": [], "blacklist": [], "bypass": []}
        for r in rows:
            result[r["list_type"]].append(r["role_id"])
        return result

    async def list_active_giveaways(self, guild_id: Optional[int] = None) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            if guild_id is not None:
                return await conn.fetch(
                    "SELECT * FROM giveaways WHERE status = 'active' AND guild_id = $1 "
                    "ORDER BY end_time ASC",
                    guild_id,
                )
            return await conn.fetch(
                "SELECT * FROM giveaways WHERE status = 'active' ORDER BY end_time ASC"
            )

    async def list_due_giveaways(self) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM giveaways WHERE status = 'active' AND end_time <= now()"
            )

    async def update_giveaway_fields(self, giveaway_id: int, **kwargs: Any) -> None:
        if not kwargs:
            return
        cols = list(kwargs.keys())
        set_clause = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
        query = f"UPDATE giveaways SET {set_clause} WHERE id = $1"
        async with self.pool.acquire() as conn:
            await conn.execute(query, giveaway_id, *kwargs.values())

    async def replace_role_list(
        self, giveaway_id: int, list_type: RoleListType, role_ids: Sequence[int]
    ) -> None:
        async def operation(conn):
            await conn.execute(
                "DELETE FROM giveaway_roles WHERE giveaway_id=$1 AND list_type=$2",
                giveaway_id, list_type.value,
            )
            if role_ids:
                await conn.executemany(
                    "INSERT INTO giveaway_roles (giveaway_id, role_id, list_type) "
                    "VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                    [(giveaway_id, rid, list_type.value) for rid in sorted(set(role_ids))],
                )
        await run_transaction(self.pool, operation, label="giveaway.replace_role_list")

    async def set_status(self, giveaway_id: int, status: GiveawayStatus) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE giveaways SET status = $2, ended_at = CASE WHEN $2 != 'active' "
                "THEN now() ELSE ended_at END WHERE id = $1",
                giveaway_id, status.value,
            )

    async def add_entry(self, giveaway_id: int, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(
                    "INSERT INTO giveaway_entries (giveaway_id, user_id) VALUES ($1, $2)",
                    giveaway_id, user_id,
                )
                return True
            except asyncpg.UniqueViolationError:
                return False

    async def remove_entry(self, giveaway_id: int, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                "DELETE FROM giveaway_entries WHERE giveaway_id = $1 AND user_id = $2",
                giveaway_id, user_id,
            )
            return res != "DELETE 0"

    async def is_entered(self, giveaway_id: int, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM giveaway_entries WHERE giveaway_id = $1 AND user_id = $2",
                giveaway_id, user_id,
            )
            return row is not None

    async def count_entries(self, giveaway_id: int) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM giveaway_entries WHERE giveaway_id = $1", giveaway_id
            )

    async def get_entrant_ids(self, giveaway_id: int) -> list[int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id FROM giveaway_entries WHERE giveaway_id = $1", giveaway_id
            )
            return [r["user_id"] for r in rows]

    async def paginate_entrants(self, giveaway_id: int, limit: int, offset: int) -> list[int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT user_id FROM giveaway_entries WHERE giveaway_id = $1 "
                "ORDER BY entered_at ASC LIMIT $2 OFFSET $3",
                giveaway_id, limit, offset,
            )
            return [r["user_id"] for r in rows]

    async def record_winners(
        self, giveaway_id: int, user_ids: Sequence[int], is_reroll: bool
    ) -> None:
        if not user_ids:
            return
        async with self.pool.acquire() as conn:
            await conn.executemany(
                "INSERT INTO giveaway_winners (giveaway_id, user_id, is_reroll) VALUES ($1, $2, $3)",
                [(giveaway_id, uid, is_reroll) for uid in user_ids],
            )

    async def mark_winner_dm(self, giveaway_id: int, user_id: int, sent: bool) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE giveaway_winners SET dm_sent = $3 "
                "WHERE giveaway_id = $1 AND user_id = $2 AND id = ("
                "  SELECT id FROM giveaway_winners WHERE giveaway_id = $1 AND user_id = $2 "
                "  ORDER BY won_at DESC LIMIT 1)",
                giveaway_id, user_id, sent,
            )

    async def mark_winner_role(self, giveaway_id: int, user_id: int, assigned: bool) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE giveaway_winners SET role_assigned = $3 "
                "WHERE giveaway_id = $1 AND user_id = $2 AND id = ("
                "  SELECT id FROM giveaway_winners WHERE giveaway_id = $1 AND user_id = $2 "
                "  ORDER BY won_at DESC LIMIT 1)",
                giveaway_id, user_id, assigned,
            )

    async def get_all_winner_ids(self, giveaway_id: int) -> list[int]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT user_id FROM giveaway_winners WHERE giveaway_id = $1",
                giveaway_id,
            )
            return [r["user_id"] for r in rows]

    async def get_latest_winners(self, giveaway_id: int) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM giveaway_winners WHERE giveaway_id = $1 "
                "ORDER BY won_at DESC LIMIT 50",
                giveaway_id,
            )

    async def flush_message_counts(self, deltas: dict[tuple[int, int], int]) -> None:
        if not deltas:
            return
        # Stable ordering reduces lock inversion risk for batches that overlap.
        rows = sorted((guild_id, user_id, count)
                      for (guild_id, user_id), count in deltas.items() if count > 0)
        if not rows:
            return

        async def operation(conn):
            await conn.executemany(
                """INSERT INTO user_message_counts
                       (guild_id, user_id, daily_count, weekly_count, lifetime_count)
                   VALUES ($1, $2, $3, $3, $3)
                   ON CONFLICT (guild_id, user_id) DO UPDATE SET
                       daily_count = user_message_counts.daily_count + EXCLUDED.daily_count,
                       weekly_count = user_message_counts.weekly_count + EXCLUDED.weekly_count,
                       lifetime_count = user_message_counts.lifetime_count + EXCLUDED.lifetime_count""",
                rows,
            )

        await run_transaction(self.pool, operation, label="giveaway.flush_message_counts")

    async def flush_giveaway_message_counts(self, deltas: dict[tuple[int, int], int]) -> None:
        if not deltas:
            return
        rows = sorted((giveaway_id, user_id, count)
                      for (giveaway_id, user_id), count in deltas.items() if count > 0)
        if not rows:
            return

        async def operation(conn):
            await conn.executemany(
                """INSERT INTO giveaway_message_counts (giveaway_id, user_id, message_count)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (giveaway_id, user_id) DO UPDATE
                   SET message_count = giveaway_message_counts.message_count + EXCLUDED.message_count""",
                rows,
            )

        await run_transaction(self.pool, operation, label="giveaway.flush_giveaway_message_counts")

    async def get_giveaway_message_count(self, giveaway_id: int, user_id: int) -> int:
        async with self.pool.acquire() as conn:
            return int(await conn.fetchval(
                "SELECT message_count FROM giveaway_message_counts WHERE giveaway_id=$1 AND user_id=$2",
                giveaway_id, user_id,
            ) or 0)

    async def get_message_count(self, guild_id: int, user_id: int, period: str) -> int:
        col = {"daily": "daily_count", "weekly": "weekly_count", "lifetime": "lifetime_count"}[period]
        async with self.pool.acquire() as conn:
            val = await conn.fetchval(
                f"SELECT {col} FROM user_message_counts WHERE guild_id = $1 AND user_id = $2",
                guild_id, user_id,
            )
            return val or 0

    async def get_message_counts(self, guild_id: int, user_id: int) -> dict[str, int]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT daily_count, weekly_count, lifetime_count FROM user_message_counts "
                "WHERE guild_id = $1 AND user_id = $2",
                guild_id, user_id,
            )
            if row:
                return {
                    "daily": row["daily_count"] or 0,
                    "weekly": row["weekly_count"] or 0,
                    "lifetime": row["lifetime_count"] or 0,
                }
            return {"daily": 0, "weekly": 0, "lifetime": 0}

    async def reset_daily_counts(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE user_message_counts SET daily_count = 0, daily_reset_at = now() "
                "WHERE daily_reset_at < now() - INTERVAL '24 hours'"
            )

    async def reset_weekly_counts(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE user_message_counts SET weekly_count = 0, weekly_reset_at = now() "
                "WHERE weekly_reset_at < now() - INTERVAL '7 days'"
            )

    async def log(
        self,
        guild_id: int,
        event_type: str,
        giveaway_id: Optional[int] = None,
        **data: Any,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO giveaway_logs (guild_id, giveaway_id, event_type, data) "
                "VALUES ($1, $2, $3, $4::jsonb)",
                guild_id, giveaway_id, event_type, json.dumps(data, default=str),
            )

    async def get_logs(self, guild_id: int, limit: int = 25) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM giveaway_logs WHERE guild_id = $1 ORDER BY created_at DESC LIMIT $2",
                guild_id, limit,
            )

    async def clear_all_giveaway_data(self) -> None:
        """Clear all giveaway rows except user_message_counts atomically."""
        async def operation(conn):
            # Child/association tables first, parent last.
            for table in ("giveaway_entries", "giveaway_winners", "giveaway_roles",
                          "giveaway_message_counts", "giveaway_logs", "giveaways"):
                await conn.execute(f"DELETE FROM {table}")
            await conn.execute("ALTER SEQUENCE giveaways_id_seq RESTART WITH 1")
            await conn.execute("ALTER SEQUENCE giveaway_entries_id_seq RESTART WITH 1")
            await conn.execute("ALTER SEQUENCE giveaway_winners_id_seq RESTART WITH 1")
            await conn.execute("ALTER SEQUENCE giveaway_logs_id_seq RESTART WITH 1")
        await run_transaction(self.pool, operation, label="giveaway.clear_all")


def build_giveaway_embed(
    row: asyncpg.Record,
    entry_count: int,
    role_lists: dict[str, list[int]],
    guild: discord.Guild,
    winner_mentions: Optional[list[str]] = None,
) -> discord.Embed:
    ends_at = row["end_time"]
    status = row["status"]
    prize = row["prize"]

    if status == GiveawayStatus.ACTIVE.value:
        title = f"{EMOJIS['confetti']} {prize}"
        desc_lines = [row["description"]] if row["description"] else []
        desc_lines.append("")
        desc_lines.append("Click the button below to enter!")
        desc_lines.append(
            f"Ends: <t:{int(ends_at.timestamp())}:R> (<t:{int(ends_at.timestamp())}:f>)"
        )
    elif status == GiveawayStatus.ENDED.value:
        title = f"{EMOJIS['confetti']} {prize} — Ended"
        desc_lines = [row["description"]] if row["description"] else []
        desc_lines.append("")
        desc_lines.append("This giveaway has ended.")
    else:
        title = f"{EMOJIS['confetti']} {prize} — Cancelled"
        desc_lines = ["This giveaway was cancelled."]

    embed = discord.Embed(
        title=title,
        description="\n".join(desc_lines),
        color=discord.Color(GREY_COLOR),
    )

    host_name = row.get("host_display_name") or f"<@{row['host_id']}>"
    embed.add_field(name="Host", value=host_name, inline=True)

    if row.get("sponsor"):
        embed.add_field(name="Sponsor", value=row["sponsor"], inline=True)

    if winner_mentions and status == GiveawayStatus.ENDED.value:
        embed.add_field(name="Winner(s)", value="\n".join(winner_mentions), inline=True)
    else:
        embed.add_field(name="Winners", value=str(row["winner_count"]), inline=True)

    embed.add_field(name="Entries", value=str(entry_count), inline=True)

    if role_lists.get("required"):
        embed.add_field(
            name="Required Roles",
            value=", ".join(f"<@&{r}>" for r in role_lists["required"]),
            inline=False,
        )
    if role_lists.get("blacklist"):
        embed.add_field(
            name="Blacklisted Roles",
            value=", ".join(f"<@&{r}>" for r in role_lists["blacklist"]),
            inline=False,
        )
    if role_lists.get("bypass"):
        embed.add_field(
            name="Bypass Roles",
            value=", ".join(f"<@&{r}>" for r in role_lists["bypass"]),
            inline=False,
        )

    if row["required_message_count"] > 0:
        embed.add_field(
            name="Message Requirement",
            value=f"{row['required_message_count']} messages ({row['required_message_period']})",
            inline=True,
        )
    if row["required_join_age_days"] > 0:
        embed.add_field(
            name="Min. Server Membership",
            value=f"{row['required_join_age_days']} days",
            inline=True,
        )
    if row["required_booster"]:
        embed.add_field(name="Requirement", value="Must be a Server Booster", inline=True)
    if row["winner_role_id"]:
        embed.add_field(name="Winner Role", value=f"<@&{row['winner_role_id']}>", inline=True)

    if row["thumbnail_url"]:
        embed.set_thumbnail(url=row["thumbnail_url"])
    if row["image_url"]:
        embed.set_image(url=row["image_url"])
    elif row["gif_url"]:
        embed.set_image(url=row["gif_url"])

    footer_text = f"{guild.name} • Giveaway #{row['id']:06d}"
    embed.set_footer(text=footer_text, icon_url=guild.icon.url if guild.icon else None)

    if row["custom_timestamp"]:
        embed.timestamp = row["start_time"]

    return embed


def build_winner_announcement_embed(
    row: asyncpg.Record, winners: list[discord.Member], guild: discord.Guild
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{EMOJIS['confetti']} {row['prize']}",
        color=discord.Color(GREY_COLOR),
        timestamp=row["ended_at"] or discord.utils.utcnow(),
    )
    host_display = row.get("host_display_name") or f"<@{row['host_id']}>"
    embed.add_field(name="Hosted by", value=host_display, inline=True)
    winner_text = ", ".join(w.mention for w in winners) if winners else "No eligible winners"
    embed.add_field(name="Winner" + ("s" if len(winners) != 1 else ""), value=winner_text, inline=True)
    embed.add_field(name="ID", value=f"`#{row['id']:06d}`", inline=True)
    if row["description"]:
        embed.add_field(name="Description", value=row["description"], inline=False)
    embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
    return embed


class LeaveView(discord.ui.View):
    """
    Ephemeral prompt shown to a user who is already entered.

    Uses a plain (non-V2) ``discord.ui.View`` because it is delivered through an
    ephemeral followup and does not need an embed container. Sending a
    Components-V2 view without ``prepare_v2_view`` produces an empty
    ``components.0.components`` payload that Discord rejects with error 50035.
    """

    def __init__(self, cog: "GiveawayCog", giveaway_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.giveaway_id = giveaway_id

        button = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Leave Giveaway",
            custom_id=f"giveaway_leave:{giveaway_id}",
        )
        button.callback = self._on_leave_click
        # Add the button directly — do NOT wrap in ActionRow on a plain View.
        self.add_item(button)

    async def _on_leave_click(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_leave_click(interaction, self.giveaway_id)


class GiveawayJoinView(ComponentsV2View):
    """
    Persistent V2 view for the giveaway message itself. Sent via
    ``prepare_v2_view(view, embed)`` so an ActionRow container is valid here.
    """

    def __init__(
        self,
        cog: "GiveawayCog",
        giveaway_id: int,
        style: str,
        emoji: Optional[str],
        label: str,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.giveaway_id = giveaway_id

        button = discord.ui.Button(
            style=BUTTON_STYLE_MAP.get(style, discord.ButtonStyle.secondary),
            label=label,
            emoji=emoji or None,
            custom_id=f"giveaway_join:{giveaway_id}",
        )
        button.callback = self._on_click
        row = discord.ui.ActionRow()
        row.add_item(button)
        self.add_item(row)

    async def _on_click(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_join_click(interaction, self.giveaway_id)


class WinnerConfirmView(discord.ui.View):
    """
    DM view presented to a giveaway winner. Uses plain (non-V2) UI components
    because Components-V2 items (such as ``discord.ui.ActionRow``) cannot be
    added to a standard ``discord.ui.View`` — Discord rejects the payload with
    "v2 items cannot be added to this view". Buttons must be added directly.
    Lifetime is driven by the giveaway's ``reroll_hours``.
    """

    def __init__(
        self,
        cog: "GiveawayCog",
        row: asyncpg.Record,
        member: discord.Member,
        timeout: int,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.row = row
        self.member = member
        self.message: Optional[discord.Message] = None

        yes_button = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="Yes, I'll keep it",
            custom_id=f"winner_keep:{row['id']}",
        )
        yes_button.callback = self._on_yes

        no_button = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="No, reroll it",
            custom_id=f"winner_reroll:{row['id']}",
        )
        no_button.callback = self._on_no

        # Add buttons directly — do NOT wrap in ActionRow on a plain View.
        self.add_item(yes_button)
        self.add_item(no_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            try:
                await interaction.response.send_message(
                    "This prompt is not for you.", ephemeral=True
                )
            except discord.HTTPException:
                pass
            return False
        return True

    async def _on_yes(self, interaction: discord.Interaction) -> None:
        row = self.row
        guild = self.member.guild
        server_icon_url = guild.icon.url if guild.icon else None
        server_name = guild.name

        second_embed = discord.Embed(
            title="Hey there!!",
            description=(
                "It seems you'd like to keep the prize.\n\n"
                f"Please contact the {EMOJIS['mail']} Event Team by opening an event "
                "ticket for your winning prize.\n\n"
                "**Objective** — Open a ticket through <#1535962074415505510>"
            ),
            color=discord.Color(GREY_COLOR),
        )
        second_embed.set_footer(text=server_name, icon_url=server_icon_url)

        try:
            await interaction.response.edit_message(embed=second_embed, view=None)
        except (discord.NotFound, discord.HTTPException) as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Winner DM Edit #{row['id']}", str(e)))

        try:
            await self.cog.repo.mark_winner_dm(row["id"], self.member.id, True)
            await self.cog.repo.log(
                row["guild_id"], "winner_kept",
                giveaway_id=row["id"], user_id=self.member.id,
            )
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Winner DM Mark #{row['id']}", str(e)))

        self.stop()

    async def _on_no(self, interaction: discord.Interaction) -> None:
        row = self.row
        guild = self.member.guild

        # Notify the host / staff that the winner declined
        try:
            await self.cog._notify_host_cancellation(guild, row, self.member, "reroll")
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Reroll Notify #{row['id']}", str(e)))

        response_embed = discord.Embed(
            title="Response recorded",
            description=(
                "Your response has been sent to the giveaway host.\n"
                "They will handle the reroll manually."
            ),
            color=discord.Color(GREY_COLOR),
        )

        try:
            await interaction.response.edit_message(embed=response_embed, view=None)
        except (discord.NotFound, discord.HTTPException) as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Winner DM Edit #{row['id']}", str(e)))

        try:
            await self.cog.repo.mark_winner_dm(row["id"], self.member.id, True)
            await self.cog.repo.log(
                row["guild_id"], "winner_cancelled",
                giveaway_id=row["id"], user_id=self.member.id, action="reroll",
            )
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Winner DM Log #{row['id']}", str(e)))

        self.stop()

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(view=None)
            except (discord.NotFound, discord.HTTPException):
                pass


FLUSH_INTERVAL_SECONDS = 30
SCHEDULER_INTERVAL_SECONDS = 15


class GiveawayCog(commands.GroupCog, group_name="giveaway", group_description="Manage server giveaways"):
    def __init__(self, bot: commands.Bot, pool: asyncpg.Pool):
        super().__init__()
        self.bot = bot
        self.pool = pool
        self.repo = GiveawayRepo(pool)
        self._message_cache: dict[tuple[int, int], int] = {}
        self._giveaway_message_cache: dict[tuple[int, int], int] = {}
        self._message_cache_lock = asyncio.Lock()
        self._ending_locks: dict[int, asyncio.Lock] = {}
        self._active_message_giveaways: dict[int, dict[int, int]] = {}

    async def cog_load(self) -> None:
        await self.repo.bootstrap()
        await self._restore_persistent_views()
        self.flush_message_cache_task.start()
        self.scheduler_task.start()
        self.reset_counts_task.start()

    async def cog_unload(self) -> None:
        self.flush_message_cache_task.cancel()
        self.scheduler_task.cancel()
        self.reset_counts_task.cancel()
        await self._flush_message_cache()

    async def _has_permission(
        self, interaction: discord.Interaction, allow_host_role: bool = False
    ) -> bool:
        """
        Permission gate for giveaway commands.

        Base managers (always allowed): event manager, senior moderator, admin
        roles from config, or any user with the Discord Administrator permission.

        When ``allow_host_role`` is True (used only by ``start``, ``edit``,
        ``cancel``, and ``end``), members holding ``GIVEAWAY_HOST_ROLE_ID`` are
        also permitted.
        """
        if not isinstance(interaction.user, discord.Member):
            return False

        allowed_role_ids = _get_giveaway_permission_role_ids(self.bot.config)
        if any(role.id in allowed_role_ids for role in interaction.user.roles):
            return True

        if interaction.user.guild_permissions.administrator:
            return True

        if allow_host_role and any(
            role.id == GIVEAWAY_HOST_ROLE_ID for role in interaction.user.roles
        ):
            return True

        return False

    async def _restore_persistent_views(self) -> None:
        try:
            rows = await self.repo.list_active_giveaways()
            for row in rows:
                if row.get("message_from_giveaway") and int(row.get("required_message_count") or 0) > 0:
                    self._active_message_giveaways.setdefault(int(row["guild_id"]), {})[int(row["id"])] = int(row["required_message_count"])
                view = GiveawayJoinView(
                    self, row["id"], row["button_style"], row["button_emoji"], row["button_label"]
                )
                self.bot.add_view(
                    view, message_id=row["message_id"] if row["message_id"] else None
                )
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error("View Restoration Failed", str(e)))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        key = (message.guild.id, message.author.id)
        async with self._message_cache_lock:
            self._message_cache[key] = self._message_cache.get(key, 0) + 1
            for giveaway_id in self._active_message_giveaways.get(message.guild.id, {}):
                gw_key = (giveaway_id, message.author.id)
                self._giveaway_message_cache[gw_key] = self._giveaway_message_cache.get(gw_key, 0) + 1

    @tasks.loop(seconds=FLUSH_INTERVAL_SECONDS)
    async def flush_message_cache_task(self) -> None:
        await self._flush_message_cache()

    async def _flush_message_cache(self) -> None:
        """Flush both caches without a temporary under-count or dropped increments.

        The shared lock is held until persistence finishes. Message listeners wait
        briefly instead of adding new deltas while a previous snapshot is being
        committed, so a successful flush can clear exactly the values it wrote.
        On failure the cache stays intact for the next retry.
        """
        async with self._message_cache_lock:
            if self._message_cache:
                try:
                    await self.repo.flush_message_counts(dict(self._message_cache))
                    self._message_cache.clear()
                except Exception:
                    logging.getLogger("nuvella.database").exception(
                        "General giveaway-message count flush failed; cache retained"
                    )
                    logging.getLogger("nuvella.giveaway").error("%s", _format_error(
                        "Message Cache Flush Failed",
                        "General message counts were retained for retry; see logs/errors.log",
                    ))
            if self._giveaway_message_cache:
                try:
                    await self.repo.flush_giveaway_message_counts(dict(self._giveaway_message_cache))
                    self._giveaway_message_cache.clear()
                except Exception:
                    logging.getLogger("nuvella.database").exception(
                        "Giveaway-specific message count flush failed; cache retained"
                    )
                    logging.getLogger("nuvella.giveaway").error("%s", _format_error(
                        "Giveaway Message Cache Flush Failed",
                        "Giveaway message counts were retained for retry; see logs/errors.log",
                    ))

    @tasks.loop(hours=1)
    async def reset_counts_task(self) -> None:
        try:
            await self.repo.reset_daily_counts()
            await self.repo.reset_weekly_counts()
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error("Count Reset Failed", str(e)))

    @tasks.loop(seconds=SCHEDULER_INTERVAL_SECONDS)
    async def scheduler_task(self) -> None:
        try:
            due = await self.repo.list_due_giveaways()
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error("Scheduler Fetch Failed", str(e)))
            return
        for row in due:
            asyncio.create_task(self._end_giveaway_safe(row["id"]))

    @scheduler_task.before_loop
    @flush_message_cache_task.before_loop
    @reset_counts_task.before_loop
    async def _wait_ready(self) -> None:
        await self.bot.wait_until_ready()

    async def _end_giveaway_safe(self, giveaway_id: int) -> None:
        # Keep the per-giveaway lock in the map while waiters may still hold a
        # reference to it. Removing it in a finally block can let a third task
        # create a new lock and overlap the task already waiting on the old one.
        lock = self._ending_locks.setdefault(giveaway_id, asyncio.Lock())
        if lock.locked():
            return  # another scheduler/manual end is already handling this ID
        async with lock:
            try:
                await self.end_giveaway(giveaway_id, is_manual=False)
            except Exception:
                logging.getLogger("nuvella.giveaway").exception(
                    "Automatic end failed | giveaway_id=%s", giveaway_id
                )
                logging.getLogger("nuvella.giveaway").error("%s", _format_error(
                    f"End Giveaway #{giveaway_id}",
                    "Automatic end failed; see logs/errors.log",
                ))

    async def _check_eligibility(
        self, member: discord.Member, row: asyncpg.Record, role_lists: dict[str, list[int]]
    ) -> tuple[bool, Optional[str]]:
        member_role_ids = {r.id for r in member.roles}

        global_blacklist = _get_global_blacklist(self.bot.config)

        if row.get("use_global_blacklist", True):
            for blacklist_role in global_blacklist:
                if blacklist_role in member_role_ids:
                    return False, "You are blacklisted from entering giveaways."

        if any(r in member_role_ids for r in role_lists.get("blacklist", [])):
            return False, "You are blacklisted from entering this giveaway."

        if any(r in member_role_ids for r in role_lists.get("bypass", [])):
            return True, None

        required = role_lists.get("required", [])
        if required and not any(r in member_role_ids for r in required):
            return False, "You do not possess the required role for this giveaway."

        if row["required_booster"] and member.premium_since is None:
            return False, "Boost the server to enter."

        if row["required_join_age_days"] > 0 and member.joined_at:
            age = datetime.now(timezone.utc) - member.joined_at
            if age < timedelta(days=row["required_join_age_days"]):
                return False, "You have not been in this server long enough."

        if row["required_invite_count"] is not None and int(row["required_invite_count"]) > 0:
            tracker = self.bot.get_cog("InviteTracker")
            invite_count = await tracker.get_invite_count(member.guild.id, member.id) if tracker else 0
            if invite_count < int(row["required_invite_count"]):
                return False, f"You need at least {row['required_invite_count']} tracked invite(s) to enter."

        if row["required_message_count"] > 0:
            # Read the persisted count and its in-memory delta under the same lock
            # used by the flusher. Otherwise a join click could observe the DB
            # after its update but before the cache clears and count messages twice.
            async with self._message_cache_lock:
                if row.get("message_from_giveaway", False):
                    giveaway_id = int(row["id"])
                    count = await self.repo.get_giveaway_message_count(giveaway_id, member.id)
                    count += self._giveaway_message_cache.get((giveaway_id, member.id), 0)
                    message_rule = "since this giveaway started"
                else:
                    count = await self.repo.get_message_count(
                        member.guild.id, member.id, row["required_message_period"]
                    )
                    count += self._message_cache.get((member.guild.id, member.id), 0)
                    message_rule = row["required_message_period"]
            if count < row["required_message_count"]:
                return False, (
                    f"You need at least {row['required_message_count']} messages "
                    f"({message_rule}) to enter. You have {count}."
                )

        return True, None

    async def handle_join_click(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        try:
            await interaction.response.defer(ephemeral=True, thinking=False)
        except discord.NotFound:
            return
        except discord.HTTPException as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error("Join Defer Failed", str(e)))
            return

        try:
            row = await self.repo.get_giveaway(giveaway_id)
            if row is None or row["status"] != GiveawayStatus.ACTIVE.value:
                await interaction.followup.send("This giveaway is no longer active.", ephemeral=True)
                return

            member = interaction.user
            if not isinstance(member, discord.Member):
                await interaction.followup.send("Could not verify your membership.", ephemeral=True)
                return

            if await self.repo.is_entered(giveaway_id, member.id):
                view = LeaveView(self, giveaway_id)
                await interaction.followup.send(
                    "You are already entered in this giveaway. Click the button below to leave.",
                    ephemeral=True,
                    view=view,
                )
                return

            role_lists = await self.repo.get_role_lists(giveaway_id)
            eligible, reason = await self._check_eligibility(member, row, role_lists)
            if not eligible:
                await interaction.followup.send(reason or "You are not eligible to enter.", ephemeral=True)
                return

            is_new = await self.repo.add_entry(giveaway_id, member.id)
            if not is_new:
                await interaction.followup.send("You have already entered this giveaway.", ephemeral=True)
                return

            await self.repo.log(row["guild_id"], "entered", giveaway_id=giveaway_id, user_id=member.id)
            await interaction.followup.send(
                f"You're entered! Good luck {EMOJIS['confetti']}", ephemeral=True
            )
            await self._refresh_message(giveaway_id)
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Join Giveaway #{giveaway_id}", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    async def handle_leave_click(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        try:
            await interaction.response.defer(ephemeral=True, thinking=False)
        except discord.NotFound:
            return
        except discord.HTTPException as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error("Leave Defer Failed", str(e)))
            return

        try:
            row = await self.repo.get_giveaway(giveaway_id)
            if row is None or row["status"] != GiveawayStatus.ACTIVE.value:
                await interaction.followup.send("This giveaway is no longer active.", ephemeral=True)
                return

            member = interaction.user
            if not isinstance(member, discord.Member):
                await interaction.followup.send("Could not verify your membership.", ephemeral=True)
                return

            removed = await self.repo.remove_entry(giveaway_id, member.id)
            if removed:
                await self.repo.log(row["guild_id"], "left", giveaway_id=giveaway_id, user_id=member.id)
                await interaction.followup.send("You have left the giveaway.", ephemeral=True)
                await self._refresh_message(giveaway_id)
            else:
                await interaction.followup.send("You were not entered in this giveaway.", ephemeral=True)
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Leave Giveaway #{giveaway_id}", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    async def _refresh_message(self, giveaway_id: int) -> None:
        try:
            row = await self.repo.get_giveaway(giveaway_id)
            if row is None or not row["message_id"]:
                return
            guild = self.bot.get_guild(row["guild_id"])
            if guild is None:
                return
            channel = self.bot.get_channel(row["channel_id"])
            if channel is None:
                return
            try:
                message = await channel.fetch_message(row["message_id"])
            except discord.NotFound:
                return
            except discord.HTTPException as e:
                logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Refresh Fetch #{giveaway_id}", str(e)))
                return

            role_lists = await self.repo.get_role_lists(giveaway_id)
            entry_count = await self.repo.count_entries(giveaway_id)

            winner_mentions = None
            if row["status"] == GiveawayStatus.ENDED.value:
                latest = await self.repo.get_latest_winners(giveaway_id)
                if latest:
                    winner_mentions = [f"<@{w['user_id']}>" for w in latest]

            embed = build_giveaway_embed(row, entry_count, role_lists, guild, winner_mentions)

            # Always build a V2 view — even for ended/cancelled giveaways we
            # need a container to hold the updated embed. The buttons are
            # disabled when the giveaway is no longer active.
            view = GiveawayJoinView(
                self,
                giveaway_id,
                row["button_style"],
                row["button_emoji"],
                row["button_label"],
            )
            if row["status"] != GiveawayStatus.ACTIVE.value:
                disable_all_components(view)

            try:
                prepare_v2_view(view, embed)
                await message.edit(view=view)
            except discord.HTTPException as e:
                logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Refresh Edit #{giveaway_id}", str(e)))
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Refresh Message #{giveaway_id}", str(e)))

    async def _select_winners(
        self,
        guild: discord.Guild,
        giveaway_id: int,
        row: asyncpg.Record,
        count: int,
        exclude: set[int],
    ) -> list[discord.Member]:
        entrant_ids = await self.repo.get_entrant_ids(giveaway_id)
        role_lists = await self.repo.get_role_lists(giveaway_id)

        candidates: list[discord.Member] = []
        for uid in entrant_ids:
            if uid in exclude:
                continue
            member = guild.get_member(uid)
            if member is None or member.bot:
                continue
            eligible, _ = await self._check_eligibility(member, row, role_lists)
            if eligible:
                candidates.append(member)

        if not candidates:
            return []

        rng = random.SystemRandom()
        if not row.get("invite_mode", False):
            return rng.sample(candidates, min(count, len(candidates)))

        # Each recorded invite contributes two additional tickets to the pool.
        # A member can only win once per draw; after selection their name is removed.
        tracker = self.bot.get_cog("InviteTracker")
        pool = list(candidates)
        winners: list[discord.Member] = []
        while pool and len(winners) < count:
            weights = []
            for member in pool:
                invite_count = await tracker.get_invite_count(guild.id, member.id) if tracker else 0
                weights.append(1 + (2 * max(0, int(invite_count))))
            picked = rng.choices(pool, weights=weights, k=1)[0]
            winners.append(picked)
            pool.remove(picked)
        return winners

    async def end_giveaway(self, giveaway_id: int, is_manual: bool) -> list[discord.Member]:
        try:
            row = await self.repo.get_giveaway(giveaway_id)
            if row is None or row["status"] != GiveawayStatus.ACTIVE.value:
                return []

            guild = self.bot.get_guild(row["guild_id"])
            if guild is None:
                await self.repo.set_status(giveaway_id, GiveawayStatus.ENDED)
                async with self._message_cache_lock:
                    self._active_message_giveaways.get(int(row["guild_id"]), {}).pop(giveaway_id, None)
                return []

            winners = await self._select_winners(
                guild, giveaway_id, row, row["winner_count"], exclude=set()
            )
            await self.repo.set_status(giveaway_id, GiveawayStatus.ENDED)
            async with self._message_cache_lock:
                self._active_message_giveaways.get(int(row["guild_id"]), {}).pop(giveaway_id, None)
            if winners:
                await self.repo.record_winners(giveaway_id, [w.id for w in winners], is_reroll=False)

            channel = guild.get_channel(row["channel_id"])
            if channel and row["message_id"]:
                try:
                    msg = await channel.fetch_message(row["message_id"])
                    await msg.delete()
                except (discord.NotFound, discord.HTTPException) as e:
                    logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Delete Message #{giveaway_id}", str(e)))

            if channel is not None:
                if winners:
                    reroll_hours = row.get("reroll_hours") or DEFAULT_REROLL_HOURS

                    if len(winners) == 1:
                        mentions = winners[0].mention
                        congrats_text = f"Congratulations to {mentions}"
                    elif len(winners) == 2:
                        mentions = f"{winners[0].mention} and {winners[1].mention}"
                        congrats_text = f"Congratulations to {mentions}"
                    else:
                        all_mentions = ", ".join(w.mention for w in winners[:-1])
                        mentions = f"{all_mentions}, and {winners[-1].mention}"
                        congrats_text = f"Congratulations to {mentions}"

                    await channel.send(
                        f"{congrats_text} for winning the giveaway! Please create an "
                        f"**Event Ticket** to claim your prize or request a reroll! Failure "
                        f"to make a ticket within {reroll_hours} hour(s) will result in a reroll!\n"
                        f"-# If you do not know how to make a ticket please read <#1355434545285042216>",
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )
                    announcement_embed = build_winner_announcement_embed(row, winners, guild)
                    await channel.send(embed=announcement_embed)
                else:
                    await channel.send(
                        f"No valid entrants for **{row['prize']}** to be selected."
                    )

            for w in winners:
                asyncio.create_task(self._handle_winner_dm_interaction(w, row))
                if row["winner_role_id"]:
                    await self._assign_winner_role(w, row["winner_role_id"])

            await self._notify_staff(guild, row, winners, event="ended")
            await self.repo.log(
                row["guild_id"], "ended", giveaway_id=giveaway_id,
                winners=[w.id for w in winners], manual=is_manual,
            )
            return winners
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"End Giveaway #{giveaway_id}", str(e)))
            return []

    async def _handle_winner_dm_interaction(
        self, member: discord.Member, row: asyncpg.Record
    ) -> None:
        """DM the winner with Yes/No buttons. Lifetime = reroll_hours."""
        try:
            guild = member.guild
            server_icon_url = guild.icon.url if guild.icon else None
            server_name = guild.name

            reroll_hours = row.get("reroll_hours") or DEFAULT_REROLL_HOURS
            if reroll_hours <= 0:
                reroll_hours = DEFAULT_REROLL_HOURS
            timeout_seconds = max(60, int(reroll_hours * 3600))

            prompt_embed = discord.Embed(
                title=f"{EMOJIS['confetti']} Congratulations on winning {row['prize']}!",
                description=(
                    "Would you like to keep it?\n\n"
                    "**Yes** — Keep the prize and open an event ticket to claim it.\n"
                    "**No** — Request a reroll so someone else can win.\n\n"
                    f"You have **{reroll_hours} hour(s)** to respond."
                ),
                color=discord.Color(GREY_COLOR),
            )
            prompt_embed.set_footer(text=server_name, icon_url=server_icon_url)

            view = WinnerConfirmView(self, row, member, timeout_seconds)

            try:
                dm_message = await member.send(embed=prompt_embed, view=view)
                view.message = dm_message
            except (discord.Forbidden, discord.HTTPException) as e:
                logging.getLogger("nuvella.giveaway").error("%s",
                    _format_error(f"DM Prompt #{row['id']}", f"Failed to DM {member}: {e}")
                )
                await self._notify_staff_dm_failed(guild, row, member)
                return
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Winner DM #{row['id']}", str(e)))

    async def _notify_staff_dm_failed(
        self, guild: discord.Guild, row: asyncpg.Record, member: discord.Member
    ) -> None:
        try:
            settings = await self.repo.get_guild_settings(guild.id)
            if settings and settings.get("staff_channel_id"):
                channel = guild.get_channel(settings["staff_channel_id"])
                if channel:
                    embed = discord.Embed(
                        title=f"{EMOJIS['warning']} DM Failed — Giveaway #{row['id']}",
                        description=(
                            f"Failed to send DM to winner {member.mention} for **{row['prize']}**.\n"
                            f"Reason: User has DMs disabled or blocked the bot.\n"
                            f"Please contact them manually or reroll if needed."
                        ),
                        color=discord.Color(GREY_COLOR),
                    )
                    await channel.send(embed=embed)
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Staff DM Failed #{row['id']}", str(e)))

    async def _notify_host_cancellation(
        self,
        guild: discord.Guild,
        row: asyncpg.Record,
        winner: discord.Member,
        action: str,
    ) -> None:
        try:
            host = guild.get_member(row["host_id"])
            embed = discord.Embed(
                title=f"Winner Response — Giveaway #{row['id']}",
                description=(
                    f"{winner.mention} has responded with **{action}** for their winning of "
                    f"**{row['prize']}**.\n"
                    f"Action: `{action}`\n"
                    f"Please manually handle this response (reroll or reassign the prize)."
                ),
                color=discord.Color(GREY_COLOR),
            )
            if host:
                try:
                    await host.send(embed=embed)
                    return
                except (discord.Forbidden, discord.HTTPException) as e:
                    logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Host DM #{row['id']}", str(e)))

            settings = await self.repo.get_guild_settings(guild.id)
            if settings and settings.get("staff_channel_id"):
                channel = guild.get_channel(settings["staff_channel_id"])
                if channel:
                    await channel.send(
                        embed=embed, allowed_mentions=discord.AllowedMentions(users=True)
                    )
                    return
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Response Notify #{row['id']}", str(e)))

    async def _assign_winner_role(self, member: discord.Member, role_id: int) -> bool:
        try:
            role = member.guild.get_role(role_id)
            if role is None:
                return False
            try:
                await member.add_roles(role, reason="Giveaway winner role assignment")
                return True
            except discord.Forbidden:
                logging.getLogger("nuvella.giveaway").error("%s",
                    _format_error("Role Assign Forbidden", f"Could not assign role {role_id} to {member}")
                )
                return False
            except discord.HTTPException as e:
                logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Role Assign {role_id}", str(e)))
                return False
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Role Assign {role_id}", str(e)))
            return False

    async def _notify_staff(
        self,
        guild: discord.Guild,
        row: asyncpg.Record,
        winners: list[discord.Member],
        event: str,
    ) -> None:
        try:
            settings = await self.repo.get_guild_settings(guild.id)
            if settings is None or not settings.get("staff_channel_id"):
                return
            channel = guild.get_channel(settings["staff_channel_id"])
            if channel is None:
                return

            jump_link = None
            if row["message_id"]:
                jump_link = (
                    f"https://discord.com/channels/{guild.id}/{row['channel_id']}/{row['message_id']}"
                )

            embed = discord.Embed(
                title=f"Giveaway {event.title()} — #{row['id']}",
                color=discord.Color(GREY_COLOR),
            )
            embed.add_field(name="Prize", value=row["prize"], inline=True)
            embed.add_field(name="Host", value=f"<@{row['host_id']}>", inline=True)
            embed.add_field(
                name="Winner(s)",
                value=", ".join(w.mention for w in winners) if winners else "None",
                inline=False,
            )
            if jump_link:
                embed.add_field(name="Jump Link", value=jump_link, inline=False)
            embed.add_field(name="Giveaway ID", value=str(row["id"]), inline=True)
            embed.timestamp = discord.utils.utcnow()

            try:
                await channel.send(embed=embed)
            except discord.HTTPException as e:
                logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Notify Staff #{row['id']}", str(e)))
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Notify Staff #{row['id']}", str(e)))

    def _parse_duration(self, text: str) -> Optional[int]:
        pattern = re.compile(r"(\d+)\s*(d|h|m|s)", re.IGNORECASE)
        matches = pattern.findall(text.strip())
        if not matches:
            return None
        unit_seconds = {"d": 86400, "h": 3600, "m": 60, "s": 1}
        total = sum(int(qty) * unit_seconds[unit.lower()] for qty, unit in matches)
        return total if total > 0 else None

    def _parse_role_list(self, guild: discord.Guild, text: Optional[str]) -> list[int]:
        if not text:
            return []
        ids: list[int] = []
        for token in re.split(r"[,\s]+", text.strip()):
            if not token:
                continue
            match = re.search(r"\d+", token)
            if match:
                ids.append(int(match.group()))
        return ids

    @app_commands.command(
        name="messages",
        description="View your or another user's daily, weekly, and total message count",
    )
    async def messages(
        self, interaction: discord.Interaction, user: Optional[discord.Member] = None
    ) -> None:
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("This command must be used in a server.", ephemeral=True)
            return

        target = user or interaction.user

        counts = await self.repo.get_message_counts(guild.id, target.id)

        cached = self._message_cache.get((guild.id, target.id), 0)
        daily_total = counts["daily"] + cached
        weekly_total = counts["weekly"] + cached
        lifetime_total = counts["lifetime"] + cached

        embed = discord.Embed(
            title=f"{EMOJIS['stats']} Message Counts for {target.display_name}",
            color=discord.Color(GREY_COLOR),
            timestamp=discord.utils.utcnow(),
        )

        embed.add_field(
            name=f"{EMOJIS['calendar']} Daily",
            value=f"**{daily_total}** messages\n*(Resets in 24 hours)*",
            inline=True,
        )
        embed.add_field(
            name=f"{EMOJIS['calendar']} Weekly",
            value=f"**{weekly_total}** messages\n*(Resets in 7 days)*",
            inline=True,
        )
        embed.add_field(
            name=f"{EMOJIS['chart']} Lifetime",
            value=f"**{lifetime_total}** messages",
            inline=True,
        )

        embed.set_footer(text=f"Requested by {interaction.user.display_name}")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="start", description="Start a new giveaway")
    @app_commands.describe(
        prize="What is being given away",
        winners="Number of winners",
        duration="Duration, e.g. '1d12h', '30m', '2h'",
        reroll_hours="Hours before reroll if winner doesn't claim",
        author="The host name shown on the embed (any text) [OPTIONAL]",
        channel="Channel to post the giveaway in",
        description="Extra description text",
        required_roles="Space/comma-separated role mentions or IDs required to enter",
        blacklist_roles="Roles that cannot enter",
        bypass_roles="Roles that bypass all requirements",
        required_messages="Minimum message count required",
        message_period="Period for the message requirement",
        required_join_age_days="Minimum server membership age in days",
        required_booster="Require the user to be a Server Booster",
        winner_role="Role automatically given to winners",
        image_url="Image URL for the embed",
        thumbnail_url="Thumbnail URL for the embed",
        button_label="Text on the entry button",
        button_emoji="Emoji on the entry button",
        button_style="Style of the entry button",
        sponsor="Name of the sponsor (displayed in embed)",
        mention_role="Role to ping in a separate message before the embed",
        use_global_blacklist="Whether to apply the server-wide blacklist (default: True)",
        invite_mode="Give each tracked invite two extra winner tickets",
        message_from_giveaway="Count only messages sent after this giveaway starts",
    )
    @app_commands.choices(
        message_period=[
            app_commands.Choice(name="Daily", value="daily"),
            app_commands.Choice(name="Weekly", value="weekly"),
        ],
        button_style=[
            app_commands.Choice(name="Primary", value="primary"),
            app_commands.Choice(name="Secondary", value="secondary"),
            app_commands.Choice(name="Success", value="success"),
            app_commands.Choice(name="Danger", value="danger"),
        ],
    )
    async def start(
        self,
        interaction: discord.Interaction,
        prize: str,
        winners: app_commands.Range[int, 1, 100],
        duration: str,
        reroll_hours: app_commands.Range[int, 1, 168] = DEFAULT_REROLL_HOURS,
        author: Optional[str] = None,
        channel: Optional[discord.TextChannel] = None,
        description: Optional[str] = None,
        required_roles: Optional[str] = None,
        blacklist_roles: Optional[str] = None,
        bypass_roles: Optional[str] = None,
        required_messages: Optional[app_commands.Range[int, 0, 1_000_000]] = 0,
        message_period: Optional[app_commands.Choice[str]] = None,
        required_join_age_days: Optional[app_commands.Range[int, 0, 3650]] = 0,
        required_booster: Optional[bool] = False,
        winner_role: Optional[discord.Role] = None,
        image_url: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
        button_label: Optional[str] = "Enter Giveaway",
        button_emoji: Optional[str] = f"{EMOJIS['confetti']}",
        button_style: Optional[app_commands.Choice[str]] = None,
        sponsor: Optional[str] = None,
        mention_role: Optional[discord.Role] = None,
        use_global_blacklist: Optional[bool] = True,
        invite_mode: Optional[bool] = False,
        message_from_giveaway: Optional[bool] = False,
    ) -> None:
        # ``start`` also accepts the giveaway host role (see _has_permission).
        if not await self._has_permission(interaction, allow_host_role=True):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return
        except discord.HTTPException as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error("Start Defer", str(e)))
            return

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send("This command must be used in a server.", ephemeral=True)
            return

        try:
            seconds = self._parse_duration(duration)
            if seconds is None or seconds < 10:
                await interaction.followup.send(
                    "Invalid duration. Use a format like `1d12h`, `30m`, or `2h`.", ephemeral=True
                )
                return

            target_channel = channel or interaction.channel
            if not isinstance(target_channel, discord.TextChannel):
                await interaction.followup.send("Please choose a valid text channel.", ephemeral=True)
                return

            if author is None:
                author = interaction.user.display_name

            if message_from_giveaway and not (required_messages or 0):
                await interaction.followup.send(
                    "Set `required_messages` above 0 when using `message_from_giveaway`.", ephemeral=True
                )
                return

            global_blacklist = _get_global_blacklist(self.bot.config)
            parsed_blacklist = self._parse_role_list(guild, blacklist_roles)

            if use_global_blacklist:
                parsed_blacklist = list(set(parsed_blacklist + global_blacklist))

            settings = GiveawaySettings(
                guild_id=guild.id,
                channel_id=target_channel.id,
                host_id=interaction.user.id,
                author=author,
                prize=prize,
                winner_count=winners,
                duration_seconds=seconds,
                reroll_hours=reroll_hours,
                description=description,
                color=GREY_COLOR,
                button_style=(button_style.value if button_style else "secondary"),
                button_emoji=button_emoji,
                button_label=button_label or "Enter Giveaway",
                required_message_count=required_messages or 0,
                message_from_giveaway=bool(message_from_giveaway),
                invite_mode=bool(invite_mode),
                required_message_period=(
                    message_period.value if message_period else MessageRequirementPeriod.DAILY.value
                ),
                required_join_age_days=required_join_age_days or 0,
                required_booster=bool(required_booster),
                winner_role_id=winner_role.id if winner_role else None,
                image_url=image_url,
                thumbnail_url=thumbnail_url,
                required_roles=self._parse_role_list(guild, required_roles),
                blacklist_roles=parsed_blacklist,
                bypass_roles=self._parse_role_list(guild, bypass_roles),
                sponsor=sponsor,
                use_global_blacklist=use_global_blacklist if use_global_blacklist is not None else True,
            )

            end_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            giveaway_id = await self.repo.create_giveaway(settings, end_time)

            fake_row = await self.repo.get_giveaway(giveaway_id)
            role_lists = await self.repo.get_role_lists(giveaway_id)
            embed = build_giveaway_embed(fake_row, 0, role_lists, guild)

            if mention_role:
                try:
                    await target_channel.send(f"{mention_role.mention} A new giveaway has started!")
                except discord.HTTPException as e:
                    logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Mention Role #{giveaway_id}", str(e)))

            view = GiveawayJoinView(
                self, giveaway_id, settings.button_style, settings.button_emoji, settings.button_label
            )

            try:
                prepare_v2_view(view, embed)
                message = await target_channel.send(view=view)
            except discord.HTTPException as e:
                logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Start Send #{giveaway_id}", str(e)))
                await interaction.followup.send(
                    "I couldn't post the giveaway message — check my permissions in that channel.",
                    ephemeral=True,
                )
                return

            if fake_row and fake_row.get("message_from_giveaway") and int(fake_row.get("required_message_count") or 0) > 0:
                # The requirement starts only after the public giveaway message exists.
                async with self._message_cache_lock:
                    self._active_message_giveaways.setdefault(guild.id, {})[giveaway_id] = int(fake_row["required_message_count"])
            await self.repo.set_message_id(giveaway_id, message.id)
            await self.repo.log(guild.id, "started", giveaway_id=giveaway_id, host=interaction.user.id)

            await interaction.followup.send(
                f"Giveaway **#{giveaway_id}** started in {target_channel.mention}.", ephemeral=True
            )
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error("Start Giveaway", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    @app_commands.command(name="end", description="End a giveaway immediately")
    async def end(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        # ``end`` also accepts the giveaway host role (see _has_permission).
        if not await self._has_permission(interaction, allow_host_role=True):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        try:
            row = await self.repo.get_giveaway(giveaway_id)
            if row is None or row["guild_id"] != interaction.guild_id:
                await interaction.followup.send("Giveaway not found in this server.", ephemeral=True)
                return
            if row["status"] != GiveawayStatus.ACTIVE.value:
                await interaction.followup.send("That giveaway is not active.", ephemeral=True)
                return
            winners = await self.end_giveaway(giveaway_id, is_manual=True)
            summary = ", ".join(w.mention for w in winners) if winners else "no eligible entrants"
            await interaction.followup.send(
                f"Giveaway **#{giveaway_id}** ended. Winners: {summary}", ephemeral=True
            )
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"End Command #{giveaway_id}", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    @app_commands.command(name="cancel", description="Cancel a giveaway without selecting winners")
    async def cancel(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        # ``cancel`` also accepts the giveaway host role (see _has_permission).
        if not await self._has_permission(interaction, allow_host_role=True):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        try:
            row = await self.repo.get_giveaway(giveaway_id)
            if row is None or row["guild_id"] != interaction.guild_id:
                await interaction.followup.send("Giveaway not found in this server.", ephemeral=True)
                return
            if row["status"] != GiveawayStatus.ACTIVE.value:
                await interaction.followup.send("That giveaway is not active.", ephemeral=True)
                return
            await self.repo.set_status(giveaway_id, GiveawayStatus.CANCELLED)
            async with self._message_cache_lock:
                self._active_message_giveaways.get(int(row["guild_id"]), {}).pop(giveaway_id, None)
            await self._refresh_message(giveaway_id)
            await self.repo.log(
                interaction.guild_id, "cancelled", giveaway_id=giveaway_id, by=interaction.user.id
            )
            await interaction.followup.send(f"Giveaway **#{giveaway_id}** cancelled.", ephemeral=True)
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Cancel Command #{giveaway_id}", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    @app_commands.command(name="reroll", description="Reroll winners for an ended giveaway")
    async def reroll(
        self,
        interaction: discord.Interaction,
        giveaway_id: int,
        count: Optional[app_commands.Range[int, 1, 100]] = None,
    ) -> None:
        # NOTE: reroll does NOT accept the giveaway host role.
        if not await self._has_permission(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        try:
            row = await self.repo.get_giveaway(giveaway_id)
            if row is None or row["guild_id"] != interaction.guild_id:
                await interaction.followup.send("Giveaway not found in this server.", ephemeral=True)
                return
            if row["status"] != GiveawayStatus.ENDED.value:
                await interaction.followup.send("Only ended giveaways can be rerolled.", ephemeral=True)
                return

            guild = interaction.guild
            assert guild is not None
            previous_winners = set(await self.repo.get_all_winner_ids(giveaway_id))
            new_winners = await self._select_winners(
                guild, giveaway_id, row, count or row["winner_count"], exclude=previous_winners
            )
            if not new_winners:
                await interaction.followup.send("No eligible entrants remain to reroll.", ephemeral=True)
                return

            await self.repo.record_winners(giveaway_id, [w.id for w in new_winners], is_reroll=True)
            await self._refresh_message(giveaway_id)

            channel = guild.get_channel(row["channel_id"])
            if channel is not None:
                mentions = ", ".join(w.mention for w in new_winners)
                await channel.send(
                    f"{EMOJIS['reroll']} New winner(s) for **{row['prize']}**: {mentions}"
                )

            for w in new_winners:
                asyncio.create_task(self._handle_winner_dm_interaction(w, row))

            await self.repo.log(
                interaction.guild_id, "rerolled", giveaway_id=giveaway_id,
                new_winners=[w.id for w in new_winners], by=interaction.user.id,
            )
            await interaction.followup.send(
                f"Rerolled giveaway **#{giveaway_id}**. New winners: "
                + ", ".join(w.mention for w in new_winners),
                ephemeral=True,
            )
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Reroll Command #{giveaway_id}", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    @app_commands.command(name="edit", description="Edit an active giveaway")
    @app_commands.describe(
        giveaway_id="The ID of the giveaway to edit",
        prize="New prize name",
        description="New description",
        winners="New number of winners",
        duration="New duration, e.g. '1d12h', '30m', '2h'",
        image_url="New image URL for the embed",
        required_roles="New required roles",
        blacklist_roles="New blacklist roles (use 'clear' to remove all)",
        bypass_roles="New bypass roles",
        winner_role="New winner role",
        use_global_blacklist="Whether to apply the server-wide blacklist",
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        giveaway_id: int,
        prize: Optional[str] = None,
        description: Optional[str] = None,
        winners: Optional[app_commands.Range[int, 1, 100]] = None,
        duration: Optional[str] = None,
        image_url: Optional[str] = None,
        required_roles: Optional[str] = None,
        blacklist_roles: Optional[str] = None,
        bypass_roles: Optional[str] = None,
        winner_role: Optional[discord.Role] = None,
        use_global_blacklist: Optional[bool] = None,
    ) -> None:
        # ``edit`` also accepts the giveaway host role (see _has_permission).
        if not await self._has_permission(interaction, allow_host_role=True):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        try:
            row = await self.repo.get_giveaway(giveaway_id)
            if row is None or row["guild_id"] != interaction.guild_id:
                await interaction.followup.send("Giveaway not found in this server.", ephemeral=True)
                return
            if row["status"] != GiveawayStatus.ACTIVE.value:
                await interaction.followup.send("Only active giveaways can be edited.", ephemeral=True)
                return

            updates: dict[str, Any] = {}
            if prize is not None:
                updates["prize"] = prize
            if description is not None:
                updates["description"] = description
            if winners is not None:
                updates["winner_count"] = winners
            if image_url is not None:
                updates["image_url"] = image_url
            if winner_role is not None:
                updates["winner_role_id"] = winner_role.id
            if use_global_blacklist is not None:
                updates["use_global_blacklist"] = use_global_blacklist
            if duration is not None:
                seconds = self._parse_duration(duration)
                if seconds is None:
                    await interaction.followup.send("Invalid duration format.", ephemeral=True)
                    return
                updates["end_time"] = datetime.now(timezone.utc) + timedelta(seconds=seconds)

            if updates:
                await self.repo.update_giveaway_fields(giveaway_id, **updates)

            guild = interaction.guild
            assert guild is not None

            global_blacklist = _get_global_blacklist(self.bot.config)

            if required_roles is not None:
                await self.repo.replace_role_list(
                    giveaway_id, RoleListType.REQUIRED, self._parse_role_list(guild, required_roles)
                )

            if blacklist_roles is not None:
                if blacklist_roles.strip().lower() == "clear":
                    parsed_blacklist = []
                else:
                    parsed_blacklist = self._parse_role_list(guild, blacklist_roles)

                current_row = await self.repo.get_giveaway(giveaway_id)
                should_use_global = (
                    use_global_blacklist
                    if use_global_blacklist is not None
                    else current_row.get("use_global_blacklist", True)
                )

                if should_use_global:
                    parsed_blacklist = list(set(parsed_blacklist + global_blacklist))
                else:
                    parsed_blacklist = [r for r in parsed_blacklist if r not in global_blacklist]

                await self.repo.replace_role_list(
                    giveaway_id, RoleListType.BLACKLIST, parsed_blacklist
                )
            elif use_global_blacklist is not None and use_global_blacklist is False:
                current_blacklist = await self.repo.get_role_lists(giveaway_id)
                existing_blacklist = current_blacklist.get("blacklist", [])
                updated_blacklist = [r for r in existing_blacklist if r not in global_blacklist]
                if updated_blacklist != existing_blacklist:
                    await self.repo.replace_role_list(
                        giveaway_id, RoleListType.BLACKLIST, updated_blacklist
                    )

            if bypass_roles is not None:
                await self.repo.replace_role_list(
                    giveaway_id, RoleListType.BYPASS, self._parse_role_list(guild, bypass_roles)
                )

            await self._refresh_message(giveaway_id)
            await self.repo.log(
                interaction.guild_id, "edited", giveaway_id=giveaway_id, by=interaction.user.id
            )
            await interaction.followup.send(f"Giveaway **#{giveaway_id}** updated.", ephemeral=True)
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Edit Command #{giveaway_id}", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    @app_commands.command(name="list", description="List active giveaways in this server")
    async def list_giveaways(self, interaction: discord.Interaction) -> None:
        if not await self._has_permission(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        try:
            rows = await self.repo.list_active_giveaways(interaction.guild_id)
            if not rows:
                await interaction.followup.send("No active giveaways.", ephemeral=True)
                return
            lines = [
                f"**#{r['id']}** — {r['prize']} — ends <t:{int(r['end_time'].timestamp())}:R>"
                for r in rows
            ]
            await interaction.followup.send("\n".join(lines), ephemeral=True)
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error("List Giveaways", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    @app_commands.command(name="info", description="Show details about a giveaway")
    async def info(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        if not await self._has_permission(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        try:
            row = await self.repo.get_giveaway(giveaway_id)
            if row is None or row["guild_id"] != interaction.guild_id:
                await interaction.followup.send("Giveaway not found in this server.", ephemeral=True)
                return
            role_lists = await self.repo.get_role_lists(giveaway_id)
            entry_count = await self.repo.count_entries(giveaway_id)
            winner_mentions = None
            if row["status"] == GiveawayStatus.ENDED.value:
                latest = await self.repo.get_latest_winners(giveaway_id)
                if latest:
                    winner_mentions = [f"<@{w['user_id']}>" for w in latest]
            embed = build_giveaway_embed(
                row, entry_count, role_lists, interaction.guild, winner_mentions
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Info Command #{giveaway_id}", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    @app_commands.command(name="entrants", description="List entrants for a giveaway (paginated)")
    async def entrants(
        self,
        interaction: discord.Interaction,
        giveaway_id: int,
        page: app_commands.Range[int, 1, 100000] = 1,
    ) -> None:
        if not await self._has_permission(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        try:
            row = await self.repo.get_giveaway(giveaway_id)
            if row is None or row["guild_id"] != interaction.guild_id:
                await interaction.followup.send("Giveaway not found in this server.", ephemeral=True)
                return
            page_size = 25
            offset = (page - 1) * page_size
            ids = await self.repo.paginate_entrants(giveaway_id, page_size, offset)
            total = await self.repo.count_entries(giveaway_id)
            if not ids:
                await interaction.followup.send("No entrants on this page.", ephemeral=True)
                return
            lines = [f"{offset + i + 1}. <@{uid}>" for i, uid in enumerate(ids)]
            await interaction.followup.send(
                f"**Entrants for #{giveaway_id}** (page {page}, {total} total)\n" + "\n".join(lines),
                ephemeral=True,
            )
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Entrants Command #{giveaway_id}", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    @app_commands.command(name="refresh", description="Force-refresh a giveaway's embed message")
    async def refresh(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        if not await self._has_permission(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        try:
            row = await self.repo.get_giveaway(giveaway_id)
            if row is None or row["guild_id"] != interaction.guild_id:
                await interaction.followup.send("Giveaway not found in this server.", ephemeral=True)
                return
            await self._refresh_message(giveaway_id)
            await interaction.followup.send("Refreshed.", ephemeral=True)
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error(f"Refresh Command #{giveaway_id}", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    @app_commands.command(
        name="settings",
        description="Configure staff notification settings for this server",
    )
    async def settings_cmd(
        self,
        interaction: discord.Interaction,
        staff_channel: Optional[discord.TextChannel] = None,
        staff_role: Optional[discord.Role] = None,
        manager_role: Optional[discord.Role] = None,
    ) -> None:
        if not await self._has_permission(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        try:
            guild = interaction.guild
            if guild is None:
                await interaction.followup.send("This command must be used in a server.", ephemeral=True)
                return
            updates: dict[str, Any] = {}
            if staff_channel is not None:
                updates["staff_channel_id"] = staff_channel.id
            if staff_role is not None:
                updates["staff_role_id"] = staff_role.id
            if manager_role is not None:
                updates["manager_role_id"] = manager_role.id
            if updates:
                await self.repo.upsert_guild_settings(guild.id, **updates)
            await interaction.followup.send("Settings updated.", ephemeral=True)
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error("Settings Command", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    @app_commands.command(name="logs", description="Show recent giveaway activity logs")
    async def logs_cmd(
        self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 50] = 25
    ) -> None:
        if not await self._has_permission(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        try:
            rows = await self.repo.get_logs(interaction.guild_id, limit)
            if not rows:
                await interaction.followup.send("No logs found.", ephemeral=True)
                return
            lines = [
                f"<t:{int(r['created_at'].timestamp())}:R> — **{r['event_type']}** "
                f"(giveaway #{r['giveaway_id']})"
                for r in rows
            ]
            await interaction.followup.send("\n".join(lines), ephemeral=True)
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error("Logs Command", str(e)))
            await interaction.followup.send("Something went wrong. Please try again.", ephemeral=True)

    @app_commands.command(
        name="clear", description="Clear all giveaway data (preserves message counts)"
    )
    async def clear(self, interaction: discord.Interaction) -> None:
        if not await self._has_permission(interaction):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return

        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        try:
            await self.repo.clear_all_giveaway_data()
            await interaction.followup.send(
                f"{EMOJIS['tick']} All giveaway data cleared!\n"
                f"{EMOJIS['stats']} Message counts preserved.",
                ephemeral=True,
            )
        except Exception as e:
            logging.getLogger("nuvella.giveaway").error("%s", _format_error("Clear All Data", str(e)))
            await interaction.followup.send("Failed to clear data. Check logs.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    pool: asyncpg.Pool = getattr(bot, "pool", None)
    if pool is None:
        raise RuntimeError("The shared PostgreSQL pool was not initialized by main.py.")
    bot.pg_pool = pool  # compatibility alias; never create or close a second pool here

    cog = GiveawayCog(bot, pool)
    await bot.add_cog(cog)
