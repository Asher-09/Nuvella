from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import discord
from discord.ext import commands

from utils.emoji import EMOJIS
from utils.components import ComponentsV2View

log = logging.getLogger("nuvella.invites")
COLOR = 0x6A0DAD

_INVITE_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/([A-Za-z0-9\-_]+)",
    re.IGNORECASE,
)
_BARE_CODE_RE = re.compile(r"^[A-Za-z0-9\-_]{2,32}$")


def _extract_invite_code(value: str) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    match = _INVITE_LINK_RE.search(value)
    if match:
        return match.group(1)
    if _BARE_CODE_RE.match(value):
        return value
    return None


def _fmt_ts(dt: Optional[datetime], style: str = "R") -> str:
    if dt is None:
        return "—"
    return f"<t:{int(dt.timestamp())}:{style}>"


class InviteTracker(commands.Cog):
    """Server invite attribution plus auditable manual invite adjustments."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pool: asyncpg.Pool = bot.pool
        self.invite_cache: dict[int, dict[str, int]] = {}
        self.vanity_cache: dict[int, int] = {}
        self._guild_locks: dict[int, asyncio.Lock] = {}
        self._unavailable_logged: set[int] = set()
        self._count_cache: dict[tuple[int, int], tuple[float, int]] = {}
        self._count_cache_ttl = 10.0

    async def cog_load(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS invite_tracker_joins (
                    guild_id BIGINT NOT NULL,
                    invitee_id BIGINT NOT NULL,
                    inviter_id BIGINT,
                    invite_code TEXT,
                    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (guild_id, invitee_id)
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_invite_tracker_inviter
                ON invite_tracker_joins (guild_id, inviter_id)
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS invite_tracker_adjustments (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    delta INTEGER NOT NULL CHECK (delta <> 0),
                    actor_id BIGINT NOT NULL,
                    reason TEXT NOT NULL DEFAULT 'No reason provided',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_invite_adjustments_user
                ON invite_tracker_adjustments (guild_id, user_id, created_at DESC)
            """)
        log.info("Invite tracker tables ready")

    # ------------------------------------------------------------------ #
    # Snapshots
    # ------------------------------------------------------------------ #

    async def refresh_all_invites(self) -> None:
        await self.bot.wait_until_ready()
        for guild in list(self.bot.guilds):
            await self.refresh_guild_invites(guild)

    async def refresh_guild_invites(self, guild: discord.Guild) -> None:
        lock = self._guild_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            try:
                invites = await guild.invites()
                self.invite_cache[guild.id] = {
                    invite.code: int(invite.uses or 0) for invite in invites
                }
                self._unavailable_logged.discard(guild.id)
            except (discord.Forbidden, discord.HTTPException):
                if guild.id not in self._unavailable_logged:
                    log.warning(
                        "Cannot inspect invites for guild_id=%s; grant Manage Guild",
                        guild.id,
                    )
                    self._unavailable_logged.add(guild.id)
            await self._snapshot_vanity(guild)

    async def _snapshot_vanity(self, guild: discord.Guild) -> Optional[int]:
        try:
            vanity = await guild.vanity_invite()
        except (discord.Forbidden, discord.HTTPException):
            self.vanity_cache.pop(guild.id, None)
            return None
        if vanity is None:
            self.vanity_cache.pop(guild.id, None)
            return None
        uses = int(vanity.uses or 0)
        self.vanity_cache[guild.id] = uses
        return uses

    async def _fetch_invite(self, code_or_url: str) -> Optional[discord.Invite]:
        code = _extract_invite_code(code_or_url) or code_or_url
        try:
            return await self.bot.fetch_invite(f"https://discord.gg/{code}", with_counts=True)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
            log.debug("fetch_invite failed for %s: %s", code, exc)
            return None

    # ------------------------------------------------------------------ #
    # Live usage — sum EVERY current guild invite owned by the user
    # ------------------------------------------------------------------ #

    async def _live_uses_for_user(self, guild_id: int, user_id: int) -> Optional[int]:
        """Sum `uses` of every CURRENT invite in the guild whose inviter == user_id.

        This mirrors what Discord shows on the user's profile. Returns None only
        when we cannot read the guild's invites at all (missing permission).
        """
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None

        total = 0
        for invite in invites:
            inviter = invite.inviter
            if inviter is None:
                # guild.invites() sometimes omits inviter; fetch the link directly.
                fetched = await self._fetch_invite(invite.code)
                inviter = fetched.inviter if fetched else None
            if inviter and inviter.id == user_id:
                total += int(invite.uses or 0)
        return total

    async def get_invite_count(self, guild_id: int, user_id: int, *, live: bool = True) -> int:
        """Net invites = live uses of the user's invites + manual adjustments."""
        key = (int(guild_id), int(user_id))
        cached = self._count_cache.get(key)
        now = time.monotonic()
        if cached and now - cached[0] < self._count_cache_ttl:
            return cached[1]

        async with self.pool.acquire() as conn:
            adjustments = await conn.fetchval("""
                SELECT COALESCE(SUM(delta), 0) FROM invite_tracker_adjustments
                WHERE guild_id=$1 AND user_id=$2
            """, guild_id, user_id)
            db_joins = await conn.fetchval("""
                SELECT COUNT(*) FROM invite_tracker_joins
                WHERE guild_id=$1 AND inviter_id=$2
            """, guild_id, user_id)

        live_uses = await self._live_uses_for_user(guild_id, user_id) if live else None
        if live_uses is not None:
            result = max(0, live_uses + int(adjustments or 0))
        else:
            result = max(0, int(db_joins or 0) + int(adjustments or 0))

        self._count_cache[key] = (now, result)
        return result

    # ------------------------------------------------------------------ #
    # Join attribution (unchanged logic, kept for history)
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.refresh_all_invites()

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        lock = self._guild_locks.setdefault(invite.guild.id, asyncio.Lock())
        async with lock:
            cache = self.invite_cache.setdefault(invite.guild.id, {})
            cache[invite.code] = int(invite.uses or 0)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        lock = self._guild_locks.setdefault(invite.guild.id, asyncio.Lock())
        async with lock:
            self.invite_cache.setdefault(invite.guild.id, {}).pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        guild = member.guild
        lock = self._guild_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            previous_cache = self.invite_cache.get(guild.id)
            previous_vanity = self.vanity_cache.get(guild.id)

            used_invite = None
            try:
                current_invites = await guild.invites()
            except (discord.Forbidden, discord.HTTPException):
                current_invites = None

            if current_invites is not None:
                current = {inv.code: int(inv.uses or 0) for inv in current_invites}
                if previous_cache is not None:
                    changed = [
                        inv for inv in current_invites
                        if int(inv.uses or 0) > previous_cache.get(inv.code, 0)
                    ]
                    if changed:
                        used_invite = max(
                            changed,
                            key=lambda inv: int(inv.uses or 0) - previous_cache.get(inv.code, 0),
                        )
                self.invite_cache[guild.id] = current

            inviter_id = None
            code = None
            if used_invite is not None:
                inviter = used_invite.inviter or (await self._fetch_invite(used_invite.code)).inviter
                inviter_id = inviter.id if inviter else None
                code = used_invite.code
            else:
                current_vanity = await self._snapshot_vanity(guild)
                if (
                    current_vanity is not None
                    and previous_vanity is not None
                    and current_vanity > previous_vanity
                ):
                    try:
                        vanity = await guild.vanity_invite()
                        code = vanity.code if vanity else None
                    except (discord.Forbidden, discord.HTTPException):
                        code = None

            try:
                async with self.pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO invite_tracker_joins (guild_id, invitee_id, inviter_id, invite_code, joined_at)
                        VALUES ($1, $2, $3, $4, now())
                        ON CONFLICT (guild_id, invitee_id) DO NOTHING
                    """, guild.id, member.id, inviter_id, code)
            except Exception:
                log.exception("Failed to persist invite join guild_id=%s invitee_id=%s", guild.id, member.id)
                return
            if inviter_id is not None:
                self._count_cache.pop((guild.id, inviter_id), None)
            log.info(
                "Member invite attributed | guild_id=%s | invitee_id=%s | inviter_id=%s | code=%s",
                guild.id, member.id, inviter_id, code or "unknown",
            )

    # ------------------------------------------------------------------ #
    # Permissions
    # ------------------------------------------------------------------ #

    async def _staff_allowed(self, member: discord.Member) -> bool:
        if member.guild_permissions.administrator or member.id == member.guild.owner_id:
            return True
        configured_owner = getattr(self.bot, "owner_id", None) or (getattr(self.bot, "config", {}) or {}).get("owner_id")
        owner_ids = configured_owner if isinstance(configured_owner, (list, tuple, set)) else [configured_owner]
        for owner_id in owner_ids:
            try:
                if owner_id and member.id == int(owner_id):
                    return True
            except (TypeError, ValueError):
                continue
        roles_cfg = (getattr(self.bot, "config", {}) or {}).get("roles", {}) or {}
        ids: set[int] = set()
        for key in ("admin_role_id", "senior_moderator_role_id"):
            try:
                value = int(roles_cfg.get(key) or 0)
                if value:
                    ids.add(value)
            except (ValueError, TypeError):
                pass
        if any(role.id in ids for role in member.roles):
            return True
        try:
            return await self.bot.is_owner(member)
        except Exception:
            return False

    async def _need_staff(self, ctx: commands.Context) -> bool:
        if isinstance(ctx.author, discord.Member) and await self._staff_allowed(ctx.author):
            return True
        await ctx.reply("You need the bot owner, Admin, or Senior Moderator permission for that action.", mention_author=False, delete_after=8)
        return False

    async def _adjust(self, ctx: commands.Context, member: discord.Member, delta: int, reason: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO invite_tracker_adjustments (guild_id, user_id, delta, actor_id, reason)
                VALUES ($1, $2, $3, $4, $5)
            """, ctx.guild.id, member.id, delta, ctx.author.id, reason[:500])
        self._count_cache.pop((ctx.guild.id, member.id), None)
        current = await self.get_invite_count(ctx.guild.id, member.id)

        embed = discord.Embed(color=COLOR, timestamp=discord.utils.utcnow())
        embed.set_author(name="Invite adjustment", icon_url=member.display_avatar.url)
        embed.description = (
            f"{member.mention} → **{delta:+}**\n"
            f"New net invites: **{current}**\n"
            f"> {reason[:500]}"
        )
        embed.set_footer(
            text=f"By {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        await ctx.reply(embed=embed, mention_author=False)
        log.info("Manual invite adjustment | guild_id=%s | user_id=%s | delta=%s | actor_id=%s | reason=%s",
                 ctx.guild.id, member.id, delta, ctx.author.id, reason)

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #

    @commands.group(name="invite", aliases=["invites"], invoke_without_command=True)
    async def invite_group(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        if not ctx.guild:
            return await ctx.reply("Use this command inside a server.", mention_author=False)
        target = member or ctx.author
        if member and member.id != ctx.author.id:
            if not isinstance(ctx.author, discord.Member) or not await self._staff_allowed(ctx.author):
                return await ctx.reply("You can only view your own invite count.", mention_author=False)

        net = await self.get_invite_count(ctx.guild.id, target.id)
        live = await self._live_uses_for_user(ctx.guild.id, target.id)

        async with self.pool.acquire() as conn:
            joins = await conn.fetchval("""
                SELECT COUNT(*) FROM invite_tracker_joins
                WHERE guild_id=$1 AND inviter_id=$2
            """, ctx.guild.id, target.id)
            adjustments = await conn.fetchval("""
                SELECT COALESCE(SUM(delta), 0) FROM invite_tracker_adjustments
                WHERE guild_id=$1 AND user_id=$2
            """, ctx.guild.id, target.id)
            recent = await conn.fetch("""
                SELECT invitee_id, invite_code, joined_at FROM invite_tracker_joins
                WHERE guild_id=$1 AND inviter_id=$2 ORDER BY joined_at DESC LIMIT 5
            """, ctx.guild.id, target.id)

        live_display = f"{live}" if live is not None else "unavailable"
        embed = discord.Embed(color=COLOR, timestamp=discord.utils.utcnow())
        embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.description = (
            f"### {net} invites\n"
            f"`Live uses`  **{live_display}**\n"
            f"`Tracked`    **{int(joins or 0)}**\n"
            f"`Adjust`     **{int(adjustments or 0):+}**"
        )
        if recent:
            embed.add_field(
                name="Recent joins",
                value="\n".join(
                    f"<@{r['invitee_id']}> · {_fmt_ts(r['joined_at'])}"
                    for r in recent
                ),
                inline=False,
            )
        embed.set_footer(
            text=f"Requested by {ctx.author.display_name}",
            icon_url=ctx.author.display_avatar.url,
        )
        await ctx.reply(embed=embed, mention_author=False)

    @invite_group.command(name="add")
    async def invite_add(self, ctx: commands.Context, member: discord.Member, amount: int = 1, *, reason: str = "Manual invite credit"):
        if not ctx.guild or not await self._need_staff(ctx):
            return
        if amount < 1 or amount > 100000:
            return await ctx.reply("Amount must be between 1 and 100000.", mention_author=False)
        await self._adjust(ctx, member, amount, reason)

    @invite_group.command(name="remove")
    async def invite_remove(self, ctx: commands.Context, member: discord.Member, amount: int = 1, *, reason: str = "Manual invite removal"):
        if not ctx.guild or not await self._need_staff(ctx):
            return
        if amount < 1 or amount > 100000:
            return await ctx.reply("Amount must be between 1 and 100000.", mention_author=False)
        await self._adjust(ctx, member, -amount, reason)

    @invite_group.command(name="view")
    async def invite_view(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        await self.invite_group.callback(self, ctx, member)

    @invite_group.command(name="info")
    async def invite_info(self, ctx: commands.Context, *, link_or_code: str):
        if not ctx.guild:
            return await ctx.reply("Use this command inside a server.", mention_author=False)
        code = _extract_invite_code(link_or_code)
        if not code:
            return await ctx.reply("Provide a Discord invite link or a bare code.", mention_author=False)
        invite = await self._fetch_invite(code)
        if invite is None:
            return await ctx.reply("I couldn't fetch that invite. It may be invalid or expired.", mention_author=False)

        embed = discord.Embed(color=COLOR, timestamp=discord.utils.utcnow())
        embed.set_author(name=f"Invite · {invite.code}")
        embed.add_field(name="Server", value=invite.guild.name if invite.guild else "Unknown", inline=True)
        embed.add_field(name="Channel", value=invite.channel.mention if invite.channel else "Unknown", inline=True)
        embed.add_field(name="Inviter", value=invite.inviter.mention if invite.inviter else "Unknown", inline=True)
        embed.add_field(name="Uses", value=f"{invite.uses if invite.uses is not None else '—'}", inline=True)
        embed.add_field(name="Max uses", value=str(invite.max_uses) if invite.max_uses else "Unlimited", inline=True)
        embed.add_field(name="Expires", value=_fmt_ts(invite.expires_at), inline=True)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.reply(embed=embed, mention_author=False)

    @invite_group.command(name="sync")
    async def invite_sync(self, ctx: commands.Context):
        if not ctx.guild or not await self._need_staff(ctx):
            return
        await self.refresh_guild_invites(ctx.guild)
        await ctx.reply("Invite snapshot refreshed.", mention_author=False)

    @invite_group.command(name="leaderboard")
    async def invite_leaderboard(self, ctx: commands.Context):
        if not ctx.guild:
            return
        guild = ctx.guild
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            invites = []

        live_counts: dict[int, int] = {}
        for inv in invites:
            inviter = inv.inviter
            if inviter is None:
                fetched = await self._fetch_invite(inv.code)
                inviter = fetched.inviter if fetched else None
            if inviter:
                live_counts[inviter.id] = live_counts.get(inviter.id, 0) + int(inv.uses or 0)

        async with self.pool.acquire() as conn:
            adj_rows = await conn.fetch("""
                SELECT user_id, SUM(delta)::BIGINT AS adj
                FROM invite_tracker_adjustments WHERE guild_id=$1 GROUP BY user_id
            """, guild.id)
        for row in adj_rows:
            uid = int(row["user_id"])
            live_counts[uid] = live_counts.get(uid, 0) + int(row["adj"] or 0)

        leaders = sorted(live_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = [
            f"{medals.get(i, f'`#{i}`')} <@{uid}> — **{count}**"
            for i, (uid, count) in enumerate(leaders, 1)
        ] or "> No invites yet."

        embed = discord.Embed(
            title=f"{EMOJIS.get('trophy', '🏆')} Invite Leaderboard",
            description="\n".join(lines),
            color=COLOR,
            timestamp=discord.utils.utcnow(),
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        await ctx.reply(embed=embed, mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InviteTracker(bot))
