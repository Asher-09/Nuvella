from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands, tasks

from utils.components import ComponentsV2View

log = logging.getLogger(__name__)
BACKUP_ROOT = Path("data") / "backups"
THREE_DAYS = dt.timedelta(days=3)


def _json_overwrites(channel: discord.abc.GuildChannel) -> list[dict[str, Any]]:
    result = []
    for target, overwrite in channel.overwrites.items():
        target_type = "role" if isinstance(target, discord.Role) else "member"
        allow, deny = overwrite.pair()
        result.append({
            "target_type": target_type,
            "target_id": target.id,
            "target_name": getattr(target, "name", str(target)),
            "allow": allow.value,
            "deny": deny.value,
        })
    return result


def _channel_snapshot(channel: discord.abc.GuildChannel) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": channel.id,
        "name": channel.name,
        "type": str(channel.type),
        "position": getattr(channel, "position", 0),
        "category_id": getattr(channel, "category_id", None),
        "overwrites": _json_overwrites(channel),
    }
    if isinstance(channel, discord.CategoryChannel):
        pass
    elif isinstance(channel, discord.TextChannel):
        row.update(topic=channel.topic, nsfw=channel.nsfw,
                   slowmode_delay=channel.slowmode_delay,
                   default_auto_archive_duration=channel.default_auto_archive_duration)
    elif isinstance(channel, discord.VoiceChannel):
        row.update(bitrate=channel.bitrate, user_limit=channel.user_limit,
                   rtc_region=str(channel.rtc_region) if channel.rtc_region else None)
    elif isinstance(channel, discord.StageChannel):
        row.update(bitrate=channel.bitrate, user_limit=channel.user_limit)
    elif isinstance(channel, discord.ForumChannel):
        row.update(topic=channel.topic, nsfw=channel.nsfw,
                   slowmode_delay=channel.slowmode_delay,
                   default_auto_archive_duration=channel.default_auto_archive_duration)
    return row


class Backup(commands.Cog):
    """Owner-only non-destructive JSON snapshots of guild configuration."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._guild_locks: dict[int, asyncio.Lock] = {}

    async def cog_load(self):
        self.auto_backup.start()

    async def cog_unload(self):
        self.auto_backup.cancel()

    def _owner_id(self) -> int:
        cfg = getattr(self.bot, "config", {}) or {}
        try:
            return int(cfg.get("owner_id") or getattr(self.bot, "owner_id", 0) or 0)
        except (TypeError, ValueError):
            return 0

    async def _owner_only(self, ctx: commands.Context) -> bool:
        if ctx.author.id == self._owner_id():
            return True
        try:
            return await self.bot.is_owner(ctx.author)
        except (discord.HTTPException, AttributeError):
            return False

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self._guild_locks.setdefault(guild_id, asyncio.Lock())

    def _folder(self, guild_id: int) -> Path:
        folder = BACKUP_ROOT / str(guild_id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    async def _snapshot(self, guild: discord.Guild, *, automatic: bool = False) -> Path:
        async with self._lock(guild.id):
            now = discord.utils.utcnow()
            state = {
                "schema_version": 1,
                "guild_id": guild.id,
                "guild_name": guild.name,
                "created_at": now.isoformat(),
                "automatic": automatic,
                "server": {
                    "name": guild.name,
                    "description": guild.description,
                    "preferred_locale": str(guild.preferred_locale),
                    "verification_level": int(guild.verification_level.value),
                    "default_notifications": int(guild.default_notifications.value),
                    "explicit_content_filter": int(guild.explicit_content_filter.value),
                    "afk_timeout": guild.afk_timeout,
                    "afk_channel_id": guild.afk_channel.id if guild.afk_channel else None,
                    "system_channel_id": guild.system_channel.id if guild.system_channel else None,
                    "rules_channel_id": guild.rules_channel.id if guild.rules_channel else None,
                    "public_updates_channel_id": guild.public_updates_channel.id if guild.public_updates_channel else None,
                    "system_channel_flags": int(guild.system_channel_flags.value),
                    "icon_url": guild.icon.url if guild.icon else None,
                },
                "roles": [
                    {"id": role.id, "name": role.name, "permissions": role.permissions.value,
                     "color": role.color.value, "hoist": role.hoist,
                     "mentionable": role.mentionable, "position": role.position,
                     "managed": role.managed}
                    for role in guild.roles if not role.is_default() and not role.managed
                ],
                "everyone_permissions": guild.default_role.permissions.value,
                "channels": [_channel_snapshot(channel) for channel in sorted(
                    guild.channels, key=lambda c: (getattr(c, "position", 0), c.id))],
                "emojis": [{"id": e.id, "name": e.name, "animated": e.animated,
                            "available": e.available} for e in guild.emojis],
            }
            stamp = now.strftime("%Y%m%d_%H%M%S_utc")
            target = self._folder(guild.id) / f"backup_{stamp}.json"
            temp = target.with_suffix(".json.tmp")
            await asyncio.to_thread(temp.write_text, json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
            await asyncio.to_thread(os.replace, temp, target)
            return target

    @tasks.loop(hours=12)
    async def auto_backup(self):
        await self.bot.wait_until_ready()
        for guild in list(self.bot.guilds):
            try:
                folder = self._folder(guild.id)
                backups = []
                for path in folder.glob("backup_*.json"):
                    try:
                        data = json.loads(path.read_text("utf-8"))
                        if data.get("automatic"):
                            created = dt.datetime.fromisoformat(data["created_at"])
                            if created.tzinfo is None:
                                created = created.replace(tzinfo=dt.timezone.utc)
                            backups.append((created, path))
                    except (OSError, ValueError, KeyError, TypeError):
                        continue
                latest = max((created for created, _ in backups), default=None)
                if latest is None or discord.utils.utcnow() - latest >= THREE_DAYS:
                    path = await self._snapshot(guild, automatic=True)
                    log.info("Automatic guild backup written: %s", path)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Automatic backup failed for guild %s", guild.id)

    @auto_backup.before_loop
    async def before_auto_backup(self):
        await self.bot.wait_until_ready()

    async def _resolve_path(self, guild_id: int, filename: str) -> Path | None:
        # Filenames only; prohibit traversal and only allow backups for this guild.
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}\.json", filename):
            return None
        path = self._folder(guild_id) / filename
        return path if path.is_file() else None

    @commands.group(name="backup", invoke_without_command=True)
    async def backup_group(self, ctx: commands.Context):
        if not await self._owner_only(ctx):
            return await ctx.reply("Only the bot owner can manage backups.", delete_after=8)
        embed = discord.Embed(
            title="Guild Backup Manager",
            description=("Create and manage JSON snapshots of server settings, roles/permissions, channels, "
                         "channel overwrites and emoji metadata. Automatic backups run every 3 days.\n\n"
                         f"`{ctx.clean_prefix}backup now` — create a snapshot\n"
                         f"`{ctx.clean_prefix}backup list` — list snapshots\n"
                         f"`{ctx.clean_prefix}backup restore <filename>` — restore settings\n"
                         f"`{ctx.clean_prefix}backup delete <filename>` — delete a snapshot"),
            color=0x2F3136, timestamp=discord.utils.utcnow())
        await ctx.send(view=ComponentsV2View(timeout=120, embed=embed))

    @backup_group.command(name="now")
    async def backup_now(self, ctx: commands.Context):
        if not await self._owner_only(ctx):
            return await ctx.reply("Only the bot owner can create backups.", delete_after=8)
        if not ctx.guild:
            return await ctx.reply("This command must be run in a server.")
        try:
            path = await self._snapshot(ctx.guild)
            await ctx.reply(f"✅ Backup created: `{path.name}`")
        except Exception as exc:
            log.exception("Manual backup failed")
            await ctx.reply(f"Backup failed: `{type(exc).__name__}`. Check bot logs.", delete_after=10)

    @backup_group.command(name="list")
    async def backup_list(self, ctx: commands.Context):
        if not await self._owner_only(ctx):
            return await ctx.reply("Only the bot owner can list backups.", delete_after=8)
        if not ctx.guild:
            return await ctx.reply("This command must be run in a server.")
        files = sorted(self._folder(ctx.guild.id).glob("backup_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        if not files:
            return await ctx.reply("No backups exist yet. Use `backup now`.")
        lines = [f"• `{p.name}` — {dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc):%Y-%m-%d %H:%M UTC}" for p in files[:20]]
        embed = discord.Embed(title="Available Backups", description="\n".join(lines), color=0x2F3136)
        await ctx.send(view=ComponentsV2View(timeout=120, embed=embed))

    @backup_group.command(name="delete")
    async def backup_delete(self, ctx: commands.Context, filename: str):
        if not await self._owner_only(ctx):
            return await ctx.reply("Only the bot owner can delete backups.", delete_after=8)
        if not ctx.guild:
            return await ctx.reply("This command must be run in a server.")
        path = await self._resolve_path(ctx.guild.id, filename)
        if path is None:
            return await ctx.reply("Backup not found. Use `backup list` for valid filenames.")
        try:
            await asyncio.to_thread(path.unlink)
            await ctx.reply(f"🗑️ Deleted backup `{filename}`.")
        except OSError as exc:
            await ctx.reply(f"Could not delete backup: `{type(exc).__name__}`.")

    @backup_group.command(name="restore")
    async def backup_restore(self, ctx: commands.Context, filename: str):
        if not await self._owner_only(ctx):
            return await ctx.reply("Only the bot owner can restore backups.", delete_after=8)
        if not ctx.guild:
            return await ctx.reply("This command must be run in a server.")
        path = await self._resolve_path(ctx.guild.id, filename)
        if path is None:
            return await ctx.reply("Backup not found. Use `backup list` for valid filenames.")
        try:
            state = json.loads(await asyncio.to_thread(path.read_text, "utf-8"))
            if int(state.get("guild_id", 0)) != ctx.guild.id:
                return await ctx.reply("That snapshot belongs to a different server; refusing to restore it.")
        except (OSError, ValueError, TypeError) as exc:
            return await ctx.reply(f"The JSON backup could not be read: `{type(exc).__name__}`.")

        # Restore is intentionally additive/non-destructive: it updates matching
        # entities and recreates missing ones, but never deletes current channels or roles.
        status = await ctx.reply(f"Restoring `{filename}`. Existing channels/roles will not be deleted.")
        guild = ctx.guild
        role_map: dict[int, discord.Role] = {guild.id: guild.default_role}
        if "everyone_permissions" in state:
            try:
                await guild.default_role.edit(
                    permissions=discord.Permissions(int(state["everyone_permissions"])),
                    reason=f"JSON backup restore by {ctx.author}",
                )
            except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
                log.warning("Could not restore @everyone permissions: %s", exc)
        for saved in sorted(state.get("roles", []), key=lambda r: int(r.get("position", 0))):
            try:
                role = guild.get_role(int(saved["id"])) or discord.utils.get(guild.roles, name=saved["name"])
                kwargs = {"name": str(saved["name"])[:100],
                          "permissions": discord.Permissions(int(saved.get("permissions", 0))),
                          "colour": discord.Colour(int(saved.get("color", 0))),
                          "hoist": bool(saved.get("hoist", False)),
                          "mentionable": bool(saved.get("mentionable", False))}
                if role is None:
                    role = await guild.create_role(**kwargs, reason=f"JSON backup restore by {ctx.author}")
                else:
                    await role.edit(**kwargs, reason=f"JSON backup restore by {ctx.author}")
                role_map[int(saved["id"])] = role
            except (discord.Forbidden, discord.HTTPException, KeyError, ValueError) as exc:
                log.warning("Could not restore role %s: %s", saved.get("name"), exc)
        if role_map:
            try:
                position_map = {role_map[int(row["id"])]: int(row.get("position", 0))
                                for row in state.get("roles", []) if int(row.get("id", 0)) in role_map}
                if position_map:
                    await guild.edit_role_positions(positions=position_map, reason="JSON backup restore")
            except (discord.Forbidden, discord.HTTPException, TypeError):
                pass

        channel_map: dict[int, discord.abc.GuildChannel] = {guild.id: guild.default_role}  # type: ignore[dict-item]
        saved_channels = state.get("channels", [])
        # Categories first, then channels, so category references can be remapped.
        def ctype(row):
            return row.get("type", "")
        ordered = sorted(saved_channels, key=lambda row: (0 if ctype(row) == "category" else 1, int(row.get("position", 0))))
        for saved in ordered:
            try:
                old_id = int(saved["id"])
                channel = guild.get_channel(old_id)
                if channel is None:
                    channel = discord.utils.get(guild.channels, name=saved["name"], type=self._discord_channel_type(ctype(saved)))
                category = channel_map.get(int(saved.get("category_id") or 0)) if saved.get("category_id") else None
                overwrites = {}
                for item in saved.get("overwrites", []):
                    target = None
                    if item.get("target_type") == "role":
                        target = role_map.get(int(item.get("target_id", 0))) or discord.utils.get(guild.roles, name=item.get("target_name"))
                    else:
                        target = guild.get_member(int(item.get("target_id", 0)))
                    if target is not None:
                        allow = discord.Permissions(int(item.get("allow", 0)))
                        deny = discord.Permissions(int(item.get("deny", 0)))
                        overwrites[target] = discord.PermissionOverwrite.from_pair(allow, deny)
                kind = ctype(saved)
                common = {"name": str(saved["name"])[:100], "overwrites": overwrites}
                if kind == "category":
                    channel = await guild.create_category(**common, reason="JSON backup restore") if channel is None else await channel.edit(**common, reason="JSON backup restore")
                elif kind == "text":
                    extra = {k: saved[k] for k in ("topic", "nsfw", "slowmode_delay", "default_auto_archive_duration") if k in saved}
                    if channel is None:
                        channel = await guild.create_text_channel(**common, category=category if isinstance(category, discord.CategoryChannel) else None, **extra, reason="JSON backup restore")
                    else:
                        channel = await channel.edit(**common, category=category if isinstance(category, discord.CategoryChannel) else None, **extra, reason="JSON backup restore")
                elif kind in ("voice", "stage voice"):
                    extra = {k: saved[k] for k in ("bitrate", "user_limit") if k in saved}
                    creator = guild.create_stage_channel if kind == "stage voice" else guild.create_voice_channel
                    if channel is None:
                        channel = await creator(**common, category=category if isinstance(category, discord.CategoryChannel) else None, **extra, reason="JSON backup restore")
                    else:
                        channel = await channel.edit(**common, category=category if isinstance(category, discord.CategoryChannel) else None, **extra, reason="JSON backup restore")
                elif kind == "forum":
                    extra = {k: saved[k] for k in ("topic", "nsfw", "slowmode_delay", "default_auto_archive_duration") if k in saved}
                    if channel is None:
                        channel = await guild.create_forum(**common, category=category if isinstance(category, discord.CategoryChannel) else None, **extra, reason="JSON backup restore")
                    else:
                        channel = await channel.edit(**common, category=category if isinstance(category, discord.CategoryChannel) else None, **extra, reason="JSON backup restore")
                else:
                    continue
                channel_map[old_id] = channel
            except (discord.Forbidden, discord.HTTPException, KeyError, ValueError, TypeError) as exc:
                log.warning("Could not restore channel %s: %s", saved.get("name"), exc)

        server = state.get("server", {})
        try:
            settings = {}
            for field, enum_type in (("verification_level", discord.VerificationLevel),
                                     ("default_notifications", discord.NotificationLevel),
                                     ("explicit_content_filter", discord.ContentFilter)):
                if field in server:
                    settings[field] = enum_type(int(server[field]))
            if "afk_timeout" in server:
                settings["afk_timeout"] = int(server["afk_timeout"])
            if server.get("name"):
                settings["name"] = str(server["name"])[:100]
            if server.get("description") is not None:
                settings["description"] = server["description"]
            for field in ("afk_channel_id", "system_channel_id", "rules_channel_id", "public_updates_channel_id"):
                saved_id = server.get(field)
                argname = field.removesuffix("_id")
                target = channel_map.get(int(saved_id)) if saved_id else None
                if saved_id and isinstance(target, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
                    settings[argname] = target
                elif not saved_id:
                    settings[argname] = None
            await guild.edit(reason=f"JSON backup restore by {ctx.author}", **settings)
        except (discord.Forbidden, discord.HTTPException, ValueError, TypeError) as exc:
            log.warning("Could not restore all guild settings: %s", exc)

        await status.edit(content=f"✅ Restore finished for `{filename}`. Missing roles/channels were recreated where possible; permission/API-limited items are written to the bot log. Current-only channels and roles were preserved.")

    @staticmethod
    def _discord_channel_type(label: str):
        return {"text": discord.ChannelType.text, "voice": discord.ChannelType.voice,
                "category": discord.ChannelType.category, "forum": discord.ChannelType.forum,
                "stage voice": discord.ChannelType.stage_voice}.get(label)


async def setup(bot: commands.Bot):
    await bot.add_cog(Backup(bot))
