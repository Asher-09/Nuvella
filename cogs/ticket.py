from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from utils.components import ComponentsV2View
from utils.database import run_transaction

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Emoji loading — ./data/emoji.json
# ---------------------------------------------------------------------------

def _load_emojis() -> dict[str, str]:
    """Load the shared emoji map from data/emoji.json."""
    candidates = [
        Path("data/emoji.json"),
        Path(__file__).resolve().parent / "data" / "emoji.json",
        Path(__file__).resolve().parent.parent / "data" / "emoji.json",
    ]
    for path in candidates:
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if isinstance(payload, dict):
                return {str(k): str(v) for k, v in payload.items()}
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError):
            log.exception("Could not parse emoji file %s", path)
    log.warning("emoji.json not found in any of %s", [str(p) for p in candidates])
    return {}


EMOJIS: dict[str, str] = _load_emojis()


def em(name: str, fallback: str = "") -> str:
    """Return the configured custom emoji for *name*, else *fallback*."""
    value = EMOJIS.get(name)
    return value if value else fallback


# Transparent / borderless-looking embed colour (Discord dark-theme surface).
EMBED_COLOR = 0x2B2D31

# How many times to repeat the breaker emoji to build a horizontal rule.
_BREAKER_COUNT = 10

# Seconds to leave the closed ticket visible before deleting the channels.
_CLOSE_DELETE_DELAY = 3


def _breaker_line() -> str:
    """A visual divider built from the `breaker` emoji, or '' if unavailable."""
    br = em("breaker")
    return br * _BREAKER_COUNT if br else ""


def _with_breaker(text: str | None) -> str | None:
    """Append the breaker line to *text* if there is room in the embed."""
    if not text:
        return text
    line = _breaker_line()
    if not line:
        return text
    # Discord description limit is 4096 chars.
    if len(text) + len(line) + 3 > 4096:
        return text
    return f"{text}\n\n{line}"


def _transparent_embed(title: str | None, description: str | None, *,
                       footer: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=_with_breaker(description),
        color=EMBED_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    if footer:
        embed.set_footer(text=footer)
    return embed


def _component_v2(title: str, description: str, *,
                  footer: str | None = None) -> ComponentsV2View:
    return ComponentsV2View(
        timeout=180,
        embed=_transparent_embed(title, description, footer=footer),
    )


def _cfg_id(config: dict, *keys: str) -> int | None:
    for key in keys:
        try:
            value = int(config.get(key) or 0)
            if value:
                return value
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# User prompt views
# ---------------------------------------------------------------------------

class TicketStartView(discord.ui.View):
    def __init__(self, cog: "Ticket", user_id: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This ticket prompt belongs to another user.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Yes, create a ticket", style=discord.ButtonStyle.success,
                       emoji=em("tick", "🎫"))
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = TicketTypeView(self.cog, self.user_id)
        await interaction.response.edit_message(
            embed=_transparent_embed(
                f"{em('tick', '🎫')} Support Ticket",
                "Choose the team you need help from:"),
            content=None,
            view=view,
        )

    @discord.ui.button(label="Not now", style=discord.ButtonStyle.secondary,
                       emoji=em("x", "✖"))
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=_transparent_embed(
                f"{em('tick', '🎫')} Support Ticket",
                "No problem. Send me a DM any time to open a support ticket."),
            content=None,
            view=self,
        )
        self.stop()


class TicketTypeView(discord.ui.View):
    def __init__(self, cog: "Ticket", user_id: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This ticket prompt belongs to another user.", ephemeral=True)
            return False
        return True

    async def _choose(self, interaction: discord.Interaction, kind: str):
        await interaction.response.defer()
        try:
            channel = await self.cog.create_ticket(interaction.user, kind)
        except Exception as exc:
            log.exception("Ticket creation failed for user %s", interaction.user.id)
            return await interaction.followup.send(
                f"I could not create the ticket (`{type(exc).__name__}`). "
                "Please contact a server admin.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(
                embed=_transparent_embed(
                    f"{em('tick', '✅')} Ticket created",
                    f"Your **{kind.title()}** ticket has been created. "
                    "A staff member will respond shortly; keep this DM open."),
                content=None,
                view=self,
            )
        except discord.HTTPException:
            pass
        try:
            await interaction.user.send(view=_component_v2(
                f"{em('tick', '✅')} Ticket opened",
                f"Your support ticket is open in **{channel.guild.name}**. "
                "A staff member will respond to you shortly; please wait."))
        except discord.HTTPException:
            pass
        self.stop()

    @discord.ui.button(label="Admin ticket", style=discord.ButtonStyle.danger,
                       emoji=em("shield", "🛡️"))
    async def admin_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._choose(interaction, "admin")

    @discord.ui.button(label="Moderator ticket", style=discord.ButtonStyle.primary,
                       emoji=em("mod", "🧰"))
    async def moderator_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._choose(interaction, "moderator")

    @discord.ui.button(label="Event ticket", style=discord.ButtonStyle.success,
                       emoji=em("confetti", "🎉"))
    async def event_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._choose(interaction, "event")


class TicketClaimView(ComponentsV2View):
    def __init__(self, cog: "Ticket", ticket_id: int, *, timeout: float | None = None):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ticket_id = ticket_id
        self.add_text(
            f"## {em('tick', '🎫')} Support request\n"
            "A member of the assigned ticket team can claim this request. "
            "Claiming opens a private handler workspace; customer messages are "
            "relayed by DM."
        )
        row = discord.ui.ActionRow()
        button = discord.ui.Button(
            label="Claim ticket",
            style=discord.ButtonStyle.success,
            emoji=em("handshake", "🙋"),
            custom_id=f"support:claim:{ticket_id}",
        )
        button.callback = self.claim_callback
        self.claim_button = button
        row.add_item(button)
        self.add_item(row)

    async def claim_callback(self, interaction: discord.Interaction):
        # A channel-create operation can exceed Discord's 3-second interaction
        # acknowledgement window. Defer first, then edit this message on success.
        await interaction.response.defer()
        await self.cog._claim_from_interaction(
            interaction, self.ticket_id, self, acknowledged=True)


class Ticket(commands.Cog):
    """DM-first support ticket workflow with staff-only handler workspaces and live transcripts."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pool = getattr(bot, "pool", None)
        self.config = getattr(bot, "config", {}) or {}
        self._webhooks: dict[int, discord.Webhook] = {}

    async def cog_load(self):
        self.pool = getattr(self.bot, "pool", None)
        if self.pool is None:
            raise RuntimeError("Ticket cog requires the shared PostgreSQL pool from main.py.")
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS support_tickets (
                    id BIGSERIAL PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    kind TEXT NOT NULL,
                    ticket_channel_id BIGINT,
                    handler_channel_id BIGINT,
                    ticket_role_id BIGINT,
                    ticket_message_id BIGINT,
                    claimed_by BIGINT,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    closed_at TIMESTAMPTZ,
                    transcript JSONB NOT NULL DEFAULT '[]'::jsonb
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS support_tickets_user_status_idx "
                "ON support_tickets(user_id, status)")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS support_tickets_channel_idx "
                "ON support_tickets(ticket_channel_id, handler_channel_id)")
        # Re-register active claim buttons after a restart.
        rows = await self.pool.fetch(
            "SELECT id, ticket_message_id FROM support_tickets "
            "WHERE status='open' AND ticket_message_id IS NOT NULL")
        for row in rows:
            try:
                self.bot.add_view(
                    TicketClaimView(self, int(row["id"])),
                    message_id=int(row["ticket_message_id"]),
                )
            except (ValueError, discord.HTTPException):
                log.exception("Could not restore persistent ticket claim view %s", row["id"])

    def _owner_id(self) -> int:
        return _cfg_id(self.config, "owner_id") or int(getattr(self.bot, "owner_id", 0) or 0)

    async def _is_bot_owner(self, user: discord.abc.User) -> bool:
        if user.id == self._owner_id():
            return True
        try:
            return await self.bot.is_owner(user)
        except (discord.HTTPException, AttributeError):
            return False

    def _role_id(self, kind: str) -> int | None:
        keys = {
            "admin": ("admin_ticket",),
            "moderator": ("moderator_ticket",),
            "event": ("event_ticket",),
            "inquiry": ("inquiry_ticket", "admin_ticket"),
        }
        return _cfg_id(self.config, *keys[kind])

    def _admin_role_ids(self) -> set[int]:
        roles = self.config.get("roles", {}) or {}
        result = set()
        for key in ("admin_role_id",):
            value = _cfg_id(roles, key)
            if value:
                result.add(value)
        return result

    async def _is_admin_or_owner(self, member: discord.abc.User,
                                 guild: discord.Guild | None) -> bool:
        if await self._is_bot_owner(member):
            return True
        if isinstance(member, discord.Member):
            if guild and member.id == guild.owner_id:
                return True
            return any(role.id in self._admin_role_ids() for role in member.roles)
        return False

    def _guild_for_tickets(self) -> discord.Guild | None:
        configured = _cfg_id(self.config, "guild_id")
        if configured:
            guild = self.bot.get_guild(configured)
            if guild:
                return guild
        return self.bot.guilds[0] if self.bot.guilds else None

    async def _ticket_for_channel(self, channel_id: int):
        return await self.pool.fetchrow(
            "SELECT * FROM support_tickets "
            "WHERE ticket_channel_id=$1 OR handler_channel_id=$1 "
            "ORDER BY id DESC LIMIT 1",
            channel_id,
        )

    async def _append_transcript(self, ticket_id: int, *, author_id: int,
                                 author_name: str, message_type: str,
                                 content: str,
                                 attachments: list[str] | None = None):
        record = {
            "at": discord.utils.utcnow().isoformat(),
            "author_id": int(author_id),
            "author": str(author_name),
            "type": message_type,
            "content": (content or "")[:8000],
            "attachments": attachments or [],
        }
        await self.pool.execute(
            "UPDATE support_tickets SET transcript=transcript || $1::jsonb WHERE id=$2",
            json.dumps([record], ensure_ascii=False), ticket_id,
        )

    async def _create_text_channel(self, guild: discord.Guild, name: str, *,
                                   overwrites, category=None):
        return await guild.create_text_channel(
            name=name[:90], overwrites=overwrites, category=category,
            reason="Support ticket system",
        )

    async def create_ticket(self, user: discord.User | discord.Member,
                            kind: str) -> discord.TextChannel:
        if kind not in {"admin", "moderator", "event", "inquiry"}:
            raise ValueError("Unknown ticket category")
        guild = self._guild_for_tickets()
        if guild is None:
            raise RuntimeError("The bot is not connected to the configured ticket server.")
        current = await self.pool.fetchrow(
            "SELECT id FROM support_tickets "
            "WHERE guild_id=$1 AND user_id=$2 AND status IN ('open','claimed') "
            "ORDER BY id DESC LIMIT 1",
            guild.id, user.id,
        )
        if current:
            raise RuntimeError(
                "You already have an open ticket. Continue using this DM conversation.")
        role_id = self._role_id(kind)
        ticket_role = guild.get_role(role_id) if role_id else None
        if not ticket_role:
            raise RuntimeError(
                f"The {kind} ticket role ID is missing or not found in config.json.")
        category_id = _cfg_id(self.config, "ticket_category")
        category = guild.get_channel(category_id) if category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = None
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            ticket_role: discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                read_message_history=True, attach_files=True, embed_links=True),
        }
        bot_member = guild.me or guild.get_member(self.bot.user.id)
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_channels=True, manage_messages=True, manage_webhooks=True,
                attach_files=True, embed_links=True)
        for rid in self._admin_role_ids():
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    read_message_history=True,
                    manage_channels=True, manage_messages=True)
        owner = guild.get_member(self._owner_id())
        if owner:
            overwrites[owner] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_channels=True, manage_messages=True)
        if guild.owner:
            overwrites[guild.owner] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_channels=True, manage_messages=True)
        base_name = re.sub(
            r"[^a-z0-9-]", "-", getattr(user, "name", "user").lower()
        )[:32].strip("-") or "user"
        channel = await self._create_text_channel(
            guild, f"{kind}-ticket-{base_name}",
            overwrites=overwrites, category=category,
        )
        row = await self.pool.fetchrow(
            "INSERT INTO support_tickets "
            "(guild_id,user_id,kind,ticket_channel_id,ticket_role_id,status) "
            "VALUES ($1,$2,$3,$4,$5,'open') RETURNING id",
            guild.id, user.id, kind, channel.id, ticket_role.id,
        )
        ticket_id = int(row["id"])
        view = TicketClaimView(self, ticket_id)
        view.clear_content()
        display = (user.mention if isinstance(user, discord.Member)
                   else discord.utils.escape_markdown(user.name))
        view.add_text(
            f"## {em('tick', '🎫')} {kind.title()} Support Ticket — #{ticket_id}\n"
            f"**Requester:** {display} (`{user.id}`)\n"
            f"**Status:** Waiting for a handler\n"
            "A team member can claim this ticket below. Once claimed, the handler "
            "receives a private workspace and replies by DM relay."
        )
        # The ticket role mention is intentionally inside the V2 text so it notifies its team.
        view.add_text(f"Assigned team: {ticket_role.mention}")
        msg = await channel.send(
            view=view,
            allowed_mentions=discord.AllowedMentions(roles=[ticket_role], users=[]),
        )
        await self.pool.execute(
            "UPDATE support_tickets SET ticket_message_id=$1 WHERE id=$2",
            msg.id, ticket_id,
        )
        queue_id = _cfg_id(self.config, "ticket_channel")
        queue_channel = self.bot.get_channel(queue_id) if queue_id else None
        if isinstance(queue_channel, discord.TextChannel) and queue_channel.id != channel.id:
            try:
                queue_embed = _transparent_embed(
                    f"{em('tick', '🎫')} New {kind.title()} Ticket #{ticket_id}",
                    f"**Requester:** "
                    f"{discord.utils.escape_markdown(getattr(user, 'name', str(user)))} "
                    f"(`{user.id}`)\n"
                    f"**Ticket:** {channel.mention}\n"
                    f"**Assigned team:** {ticket_role.mention}\n"
                    f"**Created:** <t:{int(discord.utils.utcnow().timestamp())}:R>",
                    footer="Open the ticket channel to claim it.",
                )
                await queue_channel.send(
                    view=ComponentsV2View(timeout=180, embed=queue_embed),
                    allowed_mentions=discord.AllowedMentions(roles=[ticket_role], users=[]),
                )
            except discord.HTTPException:
                log.warning("Could not post ticket #%s to configured queue channel", ticket_id)
        await self._append_transcript(
            ticket_id, author_id=user.id,
            author_name=getattr(user, "name", str(user)),
            message_type="system",
            content=f"Ticket #{ticket_id} created ({kind}).",
        )
        return channel

    async def _claim_from_interaction(self, interaction: discord.Interaction,
                                      ticket_id: int, view: TicketClaimView,
                                      *, acknowledged: bool = False):
        async def reject(message: str):
            if acknowledged:
                return await interaction.followup.send(message, ephemeral=True)
            return await interaction.response.send_message(message, ephemeral=True)

        row = await self.pool.fetchrow(
            "SELECT * FROM support_tickets WHERE id=$1", ticket_id)
        if not row or row["status"] != "open":
            return await reject("This ticket is no longer available to claim.")
        member = interaction.user
        role_id = int(row["ticket_role_id"] or 0)
        allowed_role = (isinstance(member, discord.Member)
                        and any(r.id == role_id for r in member.roles))
        privileged = await self._is_admin_or_owner(member, interaction.guild)
        if not allowed_role and not privileged:
            return await reject(
                "Only the ticket team, an admin, or the bot owner can claim this ticket.")
        async def claim_operation(conn):
            return await conn.fetchrow(
                "UPDATE support_tickets SET claimed_by=$1,status='claimed' "
                "WHERE id=$2 AND status='open' RETURNING *",
                member.id, ticket_id,
            )
        changed = await run_transaction(
            self.pool, claim_operation, label="ticket.claim")
        if not changed:
            return await reject("Someone else has already claimed this ticket.")
        guild = interaction.guild
        requester = guild.get_member(int(row["user_id"])) if guild else None
        user_obj = requester or self.bot.get_user(int(row["user_id"]))
        if not guild:
            await self.pool.execute(
                "UPDATE support_tickets SET status='open',claimed_by=NULL "
                "WHERE id=$1 AND status='claimed'", ticket_id)
            return await reject("Ticket guild was not found.")
        category_id = _cfg_id(self.config, "ticket_category")
        category = guild.get_channel(category_id) if category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = None
        overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
        handler_member = guild.get_member(member.id)
        if handler_member:
            overwrites[handler_member] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True, manage_messages=True)
        bot_member = guild.me or guild.get_member(self.bot.user.id)
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                manage_channels=True, manage_webhooks=True,
                attach_files=True, embed_links=True)
        for rid in self._admin_role_ids():
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    read_message_history=True, manage_messages=True)
        owner = guild.get_member(self._owner_id())
        if owner:
            overwrites[owner] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                read_message_history=True, manage_messages=True)
        if guild.owner:
            overwrites[guild.owner] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                read_message_history=True, manage_messages=True)
        try:
            handler_channel = await self._create_text_channel(
                guild, f"ticket-{ticket_id}-handler",
                overwrites=overwrites, category=category)
        except Exception as exc:
            await self.pool.execute(
                "UPDATE support_tickets SET status='open',claimed_by=NULL "
                "WHERE id=$1 AND status='claimed'", ticket_id)
            log.exception("Could not create handler workspace for ticket #%s", ticket_id)
            return await reject(
                f"I couldn't create a private handler workspace "
                f"(`{type(exc).__name__}`). Check the bot's Manage Channels "
                "permission and try again.")
        await self.pool.execute(
            "UPDATE support_tickets SET handler_channel_id=$1 WHERE id=$2",
            handler_channel.id, ticket_id)
        view.claim_button.disabled = True
        try:
            if acknowledged:
                await interaction.edit_original_response(view=view)
            else:
                await interaction.response.edit_message(view=view)
        except (discord.HTTPException, AttributeError):
            try:
                if acknowledged:
                    await interaction.followup.send("Ticket claimed.", ephemeral=True)
                else:
                    await interaction.response.send_message("Ticket claimed.", ephemeral=True)
            except (discord.HTTPException, AttributeError):
                pass
        await handler_channel.send(view=_component_v2(
            f"{em('lock', '🔒')} Ticket #{ticket_id} — Private Handler Workspace",
            f"**Requester ID:** `{row['user_id']}`\n"
            f"**Ticket type:** {row['kind'].title()}\n"
            f"**Handler:** {member.mention}\n\n"
            f"Use `{self.config.get('prefix', '$')}relay <message>` to reply to the "
            "customer by DM. Attach a file to `$relay` to forward it too. "
            "Incoming customer DMs (with attachments) are mirrored here under "
            "their display name. Use `$transcript` to export the live transcript, "
            "or `$close` to archive and remove the ticket.",
        ))
        await self._append_transcript(
            ticket_id, author_id=member.id, author_name=str(member),
            message_type="system",
            content=f"Claimed by {member} (`{member.id}`). "
                    f"Handler workspace: #{handler_channel.name}.")
        if user_obj:
            try:
                await user_obj.send(view=_component_v2(
                    f"{em('tick', '✅')} Ticket claimed",
                    f"Your support ticket **#{ticket_id}** has been claimed by a "
                    "staff member. They'll respond here in DMs shortly."))
            except discord.HTTPException:
                pass

    async def _ensure_webhook(self, channel: discord.TextChannel) -> discord.Webhook | None:
        if channel.id in self._webhooks:
            return self._webhooks[channel.id]
        try:
            hooks = await channel.webhooks()
            hook = discord.utils.get(hooks, name="Customer Message Relay")
            if hook is None:
                hook = await channel.create_webhook(
                    name="Customer Message Relay", reason="Ticket customer relay")
            self._webhooks[channel.id] = hook
            return hook
        except (discord.Forbidden, discord.HTTPException):
            return None

    @staticmethod
    async def _fetch_attachments_as_files(attachments) -> list[discord.File]:
        """Download attachments and return them as discord.File objects."""
        files: list[discord.File] = []
        for att in attachments:
            try:
                files.append(await att.to_file())
            except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                log.warning("Could not fetch attachment %s", getattr(att, "url", "?"))
        return files

    async def _relay_customer_message(self, message: discord.Message, row):
        target_id = row["handler_channel_id"] or row["ticket_channel_id"]
        channel = self.bot.get_channel(int(target_id)) if target_id else None
        if not isinstance(channel, discord.TextChannel):
            return

        attach_urls = [a.url for a in message.attachments]
        files = await self._fetch_attachments_as_files(message.attachments)
        text = (message.content or "").strip()

        if text:
            display = text
        elif files:
            display = ""  # files carry the message
        else:
            display = "*(empty message)*"

        hook = await self._ensure_webhook(channel)
        delivered = False
        author_name = discord.utils.escape_markdown(message.author.display_name)[:80]
        try:
            # Keep attachments clean: send files first with no caption or embed.
            # If the customer also wrote text, follow with a separate styled body.
            if files:
                if hook:
                    await hook.send(
                        files=files,
                        username=message.author.display_name[:80],
                        avatar_url=message.author.display_avatar.url,
                        allowed_mentions=discord.AllowedMentions.none(),
                        wait=True,
                    )
                else:
                    await channel.send(
                        files=files,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

            if text:
                body = discord.Embed(
                    description=text[:4000],
                    color=EMBED_COLOR,
                    timestamp=discord.utils.utcnow(),
                )
                body.set_author(name=message.author.display_name[:256],
                                icon_url=message.author.display_avatar.url)
                if hook:
                    await hook.send(
                        embed=body,
                        username=message.author.display_name[:80],
                        avatar_url=message.author.display_avatar.url,
                        allowed_mentions=discord.AllowedMentions.none(),
                        wait=True,
                    )
                else:
                    await channel.send(embed=body, allowed_mentions=discord.AllowedMentions.none())
            elif not files:
                # Empty DM text without attachment still gets an explicit event.
                if hook:
                    await hook.send(
                        content="*(empty message)*", username=message.author.display_name[:80],
                        avatar_url=message.author.display_avatar.url,
                        allowed_mentions=discord.AllowedMentions.none(), wait=True,
                    )
                else:
                    await channel.send(f"**{author_name}**: *(empty message)*",
                                       allowed_mentions=discord.AllowedMentions.none())

            delivered = True
            await self._append_transcript(
                int(row["id"]), author_id=message.author.id,
                author_name=message.author.display_name, message_type="customer",
                content=message.content or "", attachments=attach_urls)
        except discord.HTTPException:
            log.exception("Could not relay customer DM for ticket_id=%s user_id=%s", row["id"], message.author.id)
            try:
                await message.author.send(
                    "I couldn't relay your message because of a temporary server "
                    "permission or size issue. Please try again or contact staff.")
            except discord.HTTPException:
                pass
        # Tick the source DM as a delivery receipt once the relay succeeds.
        if delivered:
            try:
                await message.add_reaction(em("tick", "✅"))
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.guild is not None:
            row = await self._ticket_for_channel(message.channel.id)
            if row:
                attach_urls = [a.url for a in message.attachments]
                await self._append_transcript(
                    int(row["id"]), author_id=message.author.id,
                    author_name=str(message.author), message_type="staff-channel",
                    content=message.content or "", attachments=attach_urls)
            return
        # Only regular user DMs are ticket input. Handler replies are sent with $relay.
        try:
            row = await self.pool.fetchrow(
                "SELECT * FROM support_tickets "
                "WHERE user_id=$1 AND status IN ('open','claimed') "
                "ORDER BY id DESC LIMIT 1",
                message.author.id,
            )
        except Exception:
            log.exception("Could not look up support ticket for DM")
            return
        if row:
            await self._relay_customer_message(message, row)
            return
        try:
            await message.channel.send(
                embed=_transparent_embed(
                    f"{em('tick', '🎫')} Support Ticket",
                    "Hi! Would you like to create a support ticket? "
                    "Select **Yes** and choose the team you need."),
                view=TicketStartView(self, message.author.id),
            )
        except discord.HTTPException:
            pass

    async def _current_ticket_or_reply(self, ctx):
        if not ctx.guild:
            await ctx.reply(
                "Ticket staff commands must be used in the server ticket channel.")
            return None
        row = await self._ticket_for_channel(ctx.channel.id)
        if not row:
            await ctx.reply("This channel is not a support ticket workspace.", delete_after=8)
        return row

    async def _can_manage(self, ctx, row) -> bool:
        if await self._is_admin_or_owner(ctx.author, ctx.guild):
            return True
        return int(row["claimed_by"] or 0) == ctx.author.id

    async def _save_transcript_file(self, row) -> discord.File:
        records = row["transcript"] or []
        # asyncpg returns JSON/JSONB as a string unless a custom codec is set.
        # Decode the top-level JSON array before iterating individual records.
        if isinstance(records, str):
            try:
                records = json.loads(records)
            except (json.JSONDecodeError, TypeError):
                records = []
        if not isinstance(records, list):
            records = []
        lines = [
            f"Ticket #{row['id']} | guild={row['guild_id']} | "
            f"requester={row['user_id']} | type={row['kind']}",
            f"Status: {row['status']} | created: {row['created_at']}",
            "=" * 78,
        ]
        for item in records:
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except ValueError:
                    continue
            lines.append(
                f"[{item.get('at', '')}] {item.get('author', 'Unknown')} "
                f"({item.get('author_id', '?')}) [{item.get('type', 'message')}]")
            lines.append(item.get("content", "") or "*(no text content)*")
            for url in item.get("attachments", []) or []:
                lines.append(f"Attachment: {url}")
            lines.append("")
        data = "\n".join(lines).encode("utf-8", "replace")
        return discord.File(io.BytesIO(data), filename=f"ticket-{row['id']}-transcript.txt")

    async def _post_transcript_archive(self, row, *, closer_id: int) -> bool:
        """Post the transcript to the configured archive channel. Returns True on success."""
        archive_id = _cfg_id(self.config, "ticket_transcript")
        channel = self.bot.get_channel(archive_id) if archive_id else None
        if not isinstance(channel, discord.TextChannel):
            return False
        try:
            await channel.send(
                content=f"Transcript for ticket #{row['id']} "
                        f"(requester `{row['user_id']}`, closed by `{closer_id}`).",
                file=await self._save_transcript_file(row),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True
        except discord.HTTPException:
            log.exception("Could not archive ticket transcript %s", row["id"])
            return False

    # ---- Commands ---------------------------------------------------------

    @commands.command(name="claim")
    async def claim(self, ctx: commands.Context):
        row = await self._current_ticket_or_reply(ctx)
        if not row:
            return
        if ctx.channel.id != int(row["ticket_channel_id"] or 0):
            return await ctx.reply(
                "Use the Claim button in the ticket intake channel.", delete_after=8)
        try:
            ticket_view = TicketClaimView(self, int(row["id"]))
            await self._claim_from_interaction(
                _CommandInteraction(ctx), int(row["id"]), ticket_view)
        except Exception as exc:
            log.exception("Ticket claim command failed")
            await ctx.reply(f"Could not claim the ticket (`{type(exc).__name__}`).",
                            delete_after=8)

    @commands.command(name="inquire")
    async def inquire(self, ctx: commands.Context, user: discord.User, *,
                      reason: str = "a short inquiry"):
        """Open a short-inquiry ticket for *user* and DM them the details."""
        if not await self._is_admin_or_owner(ctx.author, ctx.guild):
            return await ctx.reply(
                "Only admins and the bot owner can use `$inquire`.", delete_after=8)
        if getattr(user, "bot", False):
            return await ctx.reply("You cannot open an inquiry for a bot.", delete_after=8)
        reason = (reason or "a short inquiry")[:400]
        try:
            channel = await self.create_ticket(user, "inquiry")
        except Exception as exc:
            log.exception("Inquiry ticket creation failed for %s", user.id)
            return await ctx.reply(
                f"Could not create an inquiry ticket for {user.mention} "
                f"(`{type(exc).__name__}`).", delete_after=8)
        dm_ok = True
        try:
            await user.send(view=_component_v2(
                f"{em('mail', '📬')} Support inquiry opened",
                "A support ticket has been created for a short inquiry.\n\n"
                f"**Reason:** {discord.utils.escape_markdown(reason)}\n\n"
                "Please reply to this DM to continue the conversation with our team."))
        except discord.HTTPException:
            dm_ok = False
        if dm_ok:
            await ctx.reply(
                f"{em('tick', '✅')} Inquiry ticket opened for {user.mention} "
                f"in {channel.mention}.", delete_after=8)
        else:
            await ctx.reply(
                f"{em('warning', '⚠️')} Inquiry ticket opened in {channel.mention}, "
                f"but I could not DM {user.mention} (their DMs may be closed).",
                delete_after=8)

    @commands.command(name="allow")
    async def allow_member(self, ctx: commands.Context, member: discord.Member):
        row = await self._current_ticket_or_reply(ctx)
        if not row:
            return
        if not await self._can_manage(ctx, row):
            return await ctx.reply(
                "Only the assigned handler, an admin, or the bot owner can change "
                "ticket access.", delete_after=8)
        if member.id == self._owner_id() or await self._is_admin_or_owner(member, ctx.guild):
            return await ctx.reply(
                "Admins and the bot owner already have access and cannot be "
                "removed or reconfigured here.")
        await self._set_member_access(row, member, True)
        await ctx.reply(f"{em('tick', '✅')} Added {member.mention} to ticket #{row['id']}.")

    @commands.command(name="remove", aliases=["deny"])
    async def remove_member(self, ctx: commands.Context, member: discord.Member):
        row = await self._current_ticket_or_reply(ctx)
        if not row:
            return
        if not await self._can_manage(ctx, row):
            return await ctx.reply(
                "Only the assigned handler, an admin, or the bot owner can change "
                "ticket access.", delete_after=8)
        if member.id == self._owner_id() or await self._is_admin_or_owner(member, ctx.guild):
            return await ctx.reply("Admins and the bot owner always retain access.")
        await self._set_member_access(row, member, False)
        await ctx.reply(
            f"{em('tick', '✅')} Removed {member.mention} from ticket #{row['id']}.")

    async def _set_member_access(self, row, member: discord.Member, allow: bool):
        for channel_id in (row["ticket_channel_id"], row["handler_channel_id"]):
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            if isinstance(channel, discord.TextChannel):
                if allow:
                    await channel.set_permissions(
                        member, view_channel=True, send_messages=True,
                        read_message_history=True)
                else:
                    await channel.set_permissions(member, overwrite=None)
        await self._append_transcript(
            int(row["id"]), author_id=member.id, author_name=str(member),
            message_type="access",
            content=f"Ticket access {'granted' if allow else 'removed'} by staff.")

    @commands.command(name="addrole", aliases=["allowrole"])
    async def add_role(self, ctx: commands.Context, role: discord.Role):
        row = await self._current_ticket_or_reply(ctx)
        if not row:
            return
        if not await self._can_manage(ctx, row):
            return await ctx.reply(
                "Only the assigned handler, an admin, or bot owner can manage "
                "ticket roles.", delete_after=8)
        if role.id == ctx.guild.default_role.id:
            return await ctx.reply(
                "The @everyone role cannot be added to a private ticket.")
        for channel_id in (row["ticket_channel_id"], row["handler_channel_id"]):
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            if isinstance(channel, discord.TextChannel):
                await channel.set_permissions(
                    role, view_channel=True, send_messages=True,
                    read_message_history=True)
        await self._append_transcript(
            int(row["id"]), author_id=ctx.author.id, author_name=str(ctx.author),
            message_type="access",
            content=f"Role {role.name} (`{role.id}`) added to ticket access.")
        await ctx.reply(f"{em('tick', '✅')} {role.mention} can now access this ticket.")

    @commands.command(name="deleterole", aliases=["removerole", "denyrole"])
    async def delete_role(self, ctx: commands.Context, role: discord.Role):
        row = await self._current_ticket_or_reply(ctx)
        if not row:
            return
        if not await self._can_manage(ctx, row):
            return await ctx.reply(
                "Only the assigned handler, an admin, or bot owner can manage "
                "ticket roles.", delete_after=8)
        if role.id in self._admin_role_ids():
            return await ctx.reply("Admin access cannot be removed from tickets.")
        for channel_id in (row["ticket_channel_id"], row["handler_channel_id"]):
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            if isinstance(channel, discord.TextChannel):
                await channel.set_permissions(role, overwrite=None)
        await self._append_transcript(
            int(row["id"]), author_id=ctx.author.id, author_name=str(ctx.author),
            message_type="access",
            content=f"Role {role.name} (`{role.id}`) removed from ticket access.")
        await ctx.reply(f"{em('tick', '✅')} Removed {role.mention} from ticket overwrites.")

    @commands.command(name="relay")
    async def relay(self, ctx: commands.Context, *, message: str = ""):
        row = await self._current_ticket_or_reply(ctx)
        if not row:
            return
        if not await self._can_manage(ctx, row):
            return await ctx.reply(
                "Only the assigned handler, an admin, or the bot owner can relay "
                "ticket replies.", delete_after=8)
        if not row["handler_channel_id"] or ctx.channel.id != int(row["handler_channel_id"]):
            return await ctx.reply(
                "Use `$relay` inside your private handler workspace after claiming "
                "the ticket.", delete_after=8)

        attachments = list(ctx.message.attachments)
        if not message and not attachments:
            return await ctx.reply(
                "Nothing to relay — include a message or attach a file.",
                delete_after=8)

        user = self.bot.get_user(int(row["user_id"]))
        if user is None:
            try:
                user = await self.bot.fetch_user(int(row["user_id"]))
            except discord.HTTPException:
                user = None
        if user is None:
            return await ctx.reply("Could not find the customer account.")

        # Re-upload attachments (Discord CDN URLs cannot be embedded in a DM
        # payload directly, so we download and re-send the bytes).
        files = await self._fetch_attachments_as_files(attachments)

        try:
            # 1) Send attachments first as a plain message so Discord actually
            #    delivers the bytes. Combining `files=` with a Components V2
            #    view drops the attachment from the payload entirely.
            if files:
                await user.send(files=files)
            # 2) Then send the styled text reply, if the handler wrote one.
            if message:
                await user.send(view=_component_v2(
                    f"{em('message', '💬')} Support reply — Ticket #{row['id']}",
                    message,
                ))
        except discord.HTTPException:
            return await ctx.reply(
                "The customer's DMs are unavailable. The reply was not sent.")

        await self._append_transcript(
            int(row["id"]), author_id=ctx.author.id, author_name=str(ctx.author),
            message_type="handler", content=message,
            attachments=[a.url for a in attachments])

        try:
            await ctx.message.add_reaction(em("tick", "✅"))
        except discord.HTTPException:
            pass
        await ctx.reply(
            f"{em('tick', '✅')} Reply delivered by DM"
            + (f" with {len(files)} attachment(s)." if files else "."),
            delete_after=5)

    @commands.command(name="transcript")
    async def transcript(self, ctx: commands.Context):
        row = await self._current_ticket_or_reply(ctx)
        if not row:
            return
        if not await self._can_manage(ctx, row):
            return await ctx.reply(
                "Only the assigned handler, an admin, or bot owner can export the "
                "transcript.", delete_after=8)
        await ctx.reply(file=await self._save_transcript_file(row), mention_author=False)

    @commands.command(name="close")
    async def close(self, ctx: commands.Context):
        row = await self._current_ticket_or_reply(ctx)
        if not row:
            return
        if not await self._can_manage(ctx, row):
            return await ctx.reply(
                "Only the assigned handler, an admin, or bot owner can close this "
                "ticket.", delete_after=8)
        if row["status"] == "closed":
            return await ctx.reply("This ticket is already closed.")

        ticket_id = int(row["id"])

        # 1) Record the closing event, then snapshot the freshest row for the archive.
        await self._append_transcript(
            ticket_id, author_id=ctx.author.id, author_name=str(ctx.author),
            message_type="system", content="Ticket closed.")
        latest = await self.pool.fetchrow(
            "SELECT * FROM support_tickets WHERE id=$1", ticket_id)

        # 2) Archive the transcript BEFORE deleting the channels so nothing is lost
        #    if the archive channel is misconfigured.
        archived = await self._post_transcript_archive(
            latest, closer_id=ctx.author.id)

        # 3) Mark the ticket closed in the DB.
        await self.pool.execute(
            "UPDATE support_tickets SET status='closed',closed_at=now() WHERE id=$1",
            ticket_id)

        # 4) Notify the requester by DM.
        user = self.bot.get_user(int(row["user_id"]))
        if user:
            try:
                await user.send(view=_component_v2(
                    f"{em('lock', '🔒')} Ticket closed",
                    f"Your support ticket #{ticket_id} was closed. You can DM the "
                    "bot again to open a new one."))
            except discord.HTTPException:
                pass

        # 5) Confirmation in-channel, then delete both channels after a short delay
        #    so the reply is visible to staff.
        archive_note = "transcript archived" if archived else (
            "transcript saved (archive channel not configured)")
        try:
            await ctx.reply(
                f"{em('lock', '🔒')} Ticket #{ticket_id} closed — {archive_note}. "
                f"Deleting this channel in {_CLOSE_DELETE_DELAY}s…")
        except discord.HTTPException:
            pass

        await asyncio.sleep(_CLOSE_DELETE_DELAY)

        for channel_id in (row["ticket_channel_id"], row["handler_channel_id"]):
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.delete(
                        reason=f"Ticket #{ticket_id} closed by {ctx.author}")
                except discord.HTTPException:
                    log.warning("Could not delete closed ticket channel %s", channel_id)

    @commands.command(name="unclaim")
    async def unclaim(self, ctx: commands.Context):
        row = await self._current_ticket_or_reply(ctx)
        if not row:
            return
        if (int(row["claimed_by"] or 0) != ctx.author.id
                and not await self._is_admin_or_owner(ctx.author, ctx.guild)):
            return await ctx.reply(
                "Only the current handler, an admin, or bot owner can unclaim.",
                delete_after=8)
        if row["status"] != "claimed":
            return await ctx.reply("This ticket is not currently claimed.")
        await self._append_transcript(
            int(row["id"]), author_id=ctx.author.id, author_name=str(ctx.author),
            message_type="system", content="Ticket unclaimed.")
        handler_channel = (self.bot.get_channel(int(row["handler_channel_id"]))
                           if row["handler_channel_id"] else None)
        if isinstance(handler_channel, discord.TextChannel):
            try:
                await handler_channel.delete(reason=f"Ticket #{row['id']} unclaimed")
            except discord.HTTPException:
                pass
        await self.pool.execute(
            "UPDATE support_tickets SET status='open',claimed_by=NULL,"
            "handler_channel_id=NULL WHERE id=$1", int(row["id"]))
        ticket_channel = (self.bot.get_channel(int(row["ticket_channel_id"]))
                          if row["ticket_channel_id"] else None)
        if isinstance(ticket_channel, discord.TextChannel) and row["ticket_message_id"]:
            try:
                await ticket_channel.get_partial_message(
                    int(row["ticket_message_id"])
                ).edit(view=TicketClaimView(self, int(row["id"])))
            except discord.HTTPException:
                pass
        await ctx.reply(f"Ticket #{row['id']} is available for another handler.")

    @commands.command(name="deleteticket", aliases=["delete-ticket", "ticketdelete"])
    async def delete_ticket(self, ctx: commands.Context):
        row = await self._current_ticket_or_reply(ctx)
        if not row:
            return
        if not await self._can_manage(ctx, row):
            return await ctx.reply(
                "Only the assigned handler, an admin, or bot owner can delete a "
                "ticket.", delete_after=8)
        await self._append_transcript(
            int(row["id"]), author_id=ctx.author.id, author_name=str(ctx.author),
            message_type="system", content="Ticket deleted.")
        latest = await self.pool.fetchrow(
            "SELECT * FROM support_tickets WHERE id=$1", int(row["id"]))
        await self._post_transcript_archive(latest, closer_id=ctx.author.id)
        await self.pool.execute(
            "UPDATE support_tickets SET status='deleted',closed_at=now() WHERE id=$1",
            int(row["id"]))
        channel_ids = [row["ticket_channel_id"], row["handler_channel_id"]]
        await ctx.reply(
            f"Ticket #{row['id']} is being deleted after its transcript is archived.")
        for channel_id in channel_ids:
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.delete(
                        reason=f"Ticket #{row['id']} deleted by {ctx.author}")
                except discord.HTTPException:
                    pass

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        row = await self._ticket_for_channel(message.channel.id)
        if row:
            urls = [a.url for a in message.attachments]
            await self._append_transcript(
                int(row["id"]), author_id=message.author.id,
                author_name=str(message.author),
                message_type="deleted-staff-message",
                content=message.content or "", attachments=urls)


# ---------------------------------------------------------------------------
# Adapter that lets `$claim` reuse the interaction-based claim flow.
# ---------------------------------------------------------------------------

class _CommandFollowup:
    def __init__(self, ctx: commands.Context):
        self.ctx = ctx

    async def send(self, content: str, ephemeral: bool = False, **kwargs):
        await self.ctx.message.reply(content, delete_after=8)


class _CommandInteraction:
    """Small adapter that exposes a minimal interaction-like API for `$claim`."""

    def __init__(self, ctx: commands.Context):
        self.ctx = ctx
        self.user = ctx.author
        self.guild = ctx.guild
        self.channel = ctx.channel
        self.message = ctx.message
        self.response = self
        self.followup = _CommandFollowup(ctx)

    async def send_message(self, content: str, ephemeral: bool = False):
        await self.message.reply(content, delete_after=8)

    async def edit_message(self, *, view=None, content=None, embed=None):
        try:
            ticket = await self.ctx.bot.pool.fetchrow(
                "SELECT ticket_message_id FROM support_tickets "
                "WHERE ticket_channel_id=$1", self.channel.id)
            if ticket and ticket["ticket_message_id"]:
                msg = await self.channel.fetch_message(int(ticket["ticket_message_id"]))
                kwargs: dict[str, Any] = {}
                if view is not None:
                    kwargs["view"] = view
                if content is not None:
                    kwargs["content"] = content
                if embed is not None:
                    kwargs["embed"] = embed
                if kwargs:
                    await msg.edit(**kwargs)
        except Exception:
            log.exception("Could not edit ticket claim message from command context")

    async def edit_original_response(self, *, view=None, content=None, embed=None):
        await self.edit_message(view=view, content=content, embed=embed)

    async def defer(self):
        return None

    def is_done(self):
        return False


async def setup(bot: commands.Bot):
    await bot.add_cog(Ticket(bot))