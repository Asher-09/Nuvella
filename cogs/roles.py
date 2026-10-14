import discord
from discord.ext import commands
import json
import os
import aiohttp
import asyncpg
from dotenv import load_dotenv
import asyncio
import time
import logging

# Load environment variables from .env
load_dotenv()

# ─── Theme colour (modern grey only) ─────────────────────
GREY_COLOR = 0x808080

# ─── File paths ──────────────────────────
CONFIG_FILE = "config.json"
EMOJI_FILE = "./data/emoji.json"

# ─── Loaders ─────────────────────────────
def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_emojis():
    try:
        with open(EMOJI_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


EMOJIS = load_emojis()
BREAKER_LINE = EMOJIS.get("breaker", "") * 10


def E(key: str) -> str:
    """Safely fetch an emoji by key."""
    return EMOJIS.get(key, "")


# ─── Database helpers (PostgreSQL) ────────
async def db_init(bot):
    if getattr(bot, "pool", None) is None:
        raise RuntimeError("The shared PostgreSQL pool was not initialized by main.py.")
    bot.db_pool = bot.pool

    async with bot.db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS boost_roles (
                user_id BIGINT PRIMARY KEY,
                role_id BIGINT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS rgb_roles (
                role_id BIGINT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                hue_index INTEGER NOT NULL DEFAULT 0,
                last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("ALTER TABLE rgb_roles ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE")
        await conn.execute("ALTER TABLE rgb_roles ADD COLUMN IF NOT EXISTS interval_seconds INTEGER NOT NULL DEFAULT 86400")


async def db_get_boost_role(bot, user_id: int):
    async with bot.db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT role_id FROM boost_roles WHERE user_id = $1", user_id
        )
    return row["role_id"] if row else None


async def db_set_boost_role(bot, user_id: int, role_id: int):
    async with bot.db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO boost_roles (user_id, role_id)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET role_id = $2
        """, user_id, role_id)


async def db_delete_boost_role(bot, user_id: int):
    async with bot.db_pool.acquire() as conn:
        await conn.execute("DELETE FROM boost_roles WHERE user_id = $1", user_id)


async def db_add_rgb_role(bot, role_id: int, guild_id: int):
    async with bot.db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO rgb_roles (role_id, guild_id, hue_index, last_updated)
            VALUES ($1, $2, 0, NOW())
            ON CONFLICT (role_id) DO NOTHING
        """, role_id, guild_id)


async def db_get_rgb_roles_due(bot):
    async with bot.db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT role_id, guild_id, hue_index FROM rgb_roles
            WHERE enabled = TRUE
              AND last_updated + (interval_seconds * INTERVAL '1 second') <= NOW()
        """)
    return [dict(row) for row in rows]


async def db_update_rgb_role(bot, role_id: int, hue_index: int):
    async with bot.db_pool.acquire() as conn:
        await conn.execute("""
            UPDATE rgb_roles SET hue_index = $2, last_updated = NOW()
            WHERE role_id = $1
        """, role_id, hue_index)


# ─── Staff / booster role helpers ────────
def get_staff_role_ids():
    """Admin + Senior Moderator role IDs from config.json."""
    config = load_config()
    roles = config.get("roles", {})
    ids = []
    admin_id = roles.get("admin_role_id")
    if admin_id:
        ids.append(int(admin_id))
    sr_mod_id = roles.get("senior_moderator_role_id")
    if sr_mod_id:
        ids.append(int(sr_mod_id))
    return ids


def get_booster_role_id():
    config = load_config()
    roles = config.get("roles", {})
    bid = roles.get("booster_role_id")
    return int(bid) if bid else None


def is_staff(member: discord.Member, bot: commands.Bot):
    if member.id == member.guild.owner_id:
        return True
    config = load_config()
    owner_id = config.get("owner_id")
    if owner_id and member.id == int(owner_id):
        return True
    for role in member.roles:
        if role.id in get_staff_role_ids():
            return True
    return False


def staff_only():
    async def predicate(ctx):
        if not is_staff(ctx.author, ctx.bot):
            raise commands.CheckFailure("You must be staff to use this command.")
        return True
    return commands.check(predicate)


# ─── Embed builders ───────────────────────
def make_embed(ctx, title, desc=None):
    user = ctx.author
    guild = ctx.guild
    embed = discord.Embed(
        title=title,
        description=f"{desc}\n{BREAKER_LINE}" if desc else None,
        color=GREY_COLOR,
        timestamp=discord.utils.utcnow()
    )
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed.set_footer(text=f"Action by {user}", icon_url=user.display_avatar.url)
    return embed


def success_embed(ctx, msg):
    return make_embed(ctx, f"{E('tick')} Success", msg)


class RolePropertyModal(discord.ui.Modal):
    def __init__(self, panel, property_name: str, current_value: str = ""):
        super().__init__(title={"name": "Rename Role", "color": "Set Role Colour", "icon": "Set Role Icon URL"}.get(property_name, "Edit Role"))
        self.panel = panel
        self.property_name = property_name
        label = {"name": "Role name", "color": "Hex colour (#RRGGBB)", "icon": "HTTPS image URL"}.get(property_name, "Value")
        placeholder = {"name": "My role", "color": "#8B5CF6", "icon": "https://example.com/icon.png"}.get(property_name, "Enter value")
        self.value_input = discord.ui.TextInput(
            label=label, default=current_value[:400] if current_value else None,
            placeholder=placeholder, max_length=100 if property_name == "name" else 500,
            required=True,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            role = await self.panel.get_editable_role(interaction)
            if role is None:
                return await interaction.response.send_message("No active role is linked to your account/selection.", ephemeral=True)
            value = self.value_input.value.strip()
            if self.property_name == "name":
                if not value:
                    return await interaction.response.send_message("Role name cannot be empty.", ephemeral=True)
                await role.edit(name=value, reason=f"Role panel edit by {interaction.user.id}")
            elif self.property_name == "color":
                raw = value.removeprefix("#")
                if len(raw) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in raw):
                    return await interaction.response.send_message("Use a six-digit hex colour such as `#8B5CF6`.", ephemeral=True)
                await role.edit(color=discord.Color(int(raw, 16)), reason=f"Role panel colour by {interaction.user.id}")
            elif self.property_name == "icon":
                parsed = __import__("urllib.parse", fromlist=["urlparse"]).urlparse(value)
                if parsed.scheme not in ("https", "http") or not parsed.netloc:
                    return await interaction.response.send_message("Icon must be a valid HTTP(S) URL.", ephemeral=True)
                session = getattr(self.panel.cog.bot, "http_session", None)
                if not session or session.closed:
                    return await interaction.response.send_message("The bot's HTTP session is unavailable.", ephemeral=True)
                async with session.get(value) as response:
                    if response.status != 200:
                        return await interaction.response.send_message("Could not download that icon URL.", ephemeral=True)
                    image_data = await response.read()
                if len(image_data) > 256 * 1024:
                    return await interaction.response.send_message("Role icons must be 256 KB or smaller.", ephemeral=True)
                await role.edit(display_icon=image_data, reason=f"Role panel icon by {interaction.user.id}")
            else:
                return await interaction.response.send_message("Unsupported role action.", ephemeral=True)
        except discord.Forbidden:
            return await interaction.response.send_message("I cannot edit that role. Check Manage Roles and role hierarchy.", ephemeral=True)
        except (discord.HTTPException, ValueError):
            return await interaction.response.send_message("Discord rejected that change. Check the value, permissions, and server boost requirements.", ephemeral=True)
        await interaction.response.send_message(f"✅ Updated **{role.name}**.", ephemeral=True)
        await self.panel.refresh_panel()


class GradientCreateModal(discord.ui.Modal, title="Create Gradient Role"):
    def __init__(self, panel):
        super().__init__(timeout=120)
        self.panel = panel
        self.name_input = discord.ui.TextInput(label="Role name", placeholder="Aurora", max_length=100, required=True)
        self.interval_input = discord.ui.TextInput(label="Colour change interval in seconds", default="86400", placeholder="86400 (daily)", max_length=5, required=True)
        self.add_item(self.name_input)
        self.add_item(self.interval_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            interval = int(self.interval_input.value.strip())
        except ValueError:
            return await interaction.response.send_message("Interval must be a whole number of seconds.", ephemeral=True)
        if not 60 <= interval <= 86400:
            return await interaction.response.send_message("Use an interval from 60 to 86400 seconds.", ephemeral=True)
        try:
            role = await interaction.guild.create_role(
                name=self.name_input.value.strip(), color=discord.Color.from_hsv(0.0, 1.0, 1.0),
                reason=f"Gradient role created by {interaction.user.id}",
            )
            async with self.panel.cog.bot.db_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO rgb_roles (role_id, guild_id, hue_index, last_updated, enabled, interval_seconds)
                       VALUES ($1,$2,0,NOW(),TRUE,$3) ON CONFLICT (role_id) DO UPDATE SET
                       enabled=TRUE, interval_seconds=EXCLUDED.interval_seconds, last_updated=NOW()""",
                    role.id, interaction.guild.id, interval,
                )
        except (discord.Forbidden, discord.HTTPException):
            return await interaction.response.send_message("Could not create the role. Check Manage Roles and role hierarchy.", ephemeral=True)
        except Exception:
            logging.getLogger("nuvella.database").exception("Gradient role create failed | guild_id=%s", interaction.guild.id)
            return await interaction.response.send_message("Could not save the gradient settings; see logs/errors.log.", ephemeral=True)
        self.panel.selected_role_id = role.id
        await interaction.response.send_message(f"✅ Gradient role {role.mention} created and enabled.", ephemeral=True)
        await self.panel.refresh_data()
        await self.panel.refresh_panel()


class GradientPanelView(discord.ui.View):
    def __init__(self, cog, ctx):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = ctx.guild.id
        self.author_id = ctx.author.id
        self.selected_role_id: int | None = None
        self.roles = []
        self.gradient_state = {}
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This role dashboard belongs to another staff member.", ephemeral=True)
            return False
        return True

    async def refresh_data(self):
        guild = self.cog.bot.get_guild(self.guild_id)
        self.roles = [r for r in guild.roles if r != guild.default_role and not r.managed][:25] if guild else []
        async with self.cog.bot.db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT role_id, enabled, interval_seconds, hue_index FROM rgb_roles WHERE guild_id=$1", self.guild_id)
        self.gradient_state = {int(r["role_id"]): dict(r) for r in rows}
        available = {r.id for r in self.roles}
        if self.selected_role_id not in available:
            self.selected_role_id = self.roles[-1].id if self.roles else None
        self._rebuild()

    def render_embed(self):
        role = next((r for r in self.roles if r.id == self.selected_role_id), None)
        if not role:
            description = "No manageable roles are available."
        else:
            state = self.gradient_state.get(role.id)
            description = (
                f"**Selected:** {role.mention}\n**Colour:** `{role.color}`\n"
                f"**Gradient:** {'Enabled' if state and state.get('enabled') else 'Disabled'}\n"
                f"**Interval:** `{state.get('interval_seconds', 86400) if state else 86400}s`\n"
                "Use the buttons to enable/disable automatic hue rotation, shift the colour now, or create a new gradient role."
            )
        return discord.Embed(title="Role Gradient Control", description=description, color=GREY_COLOR, timestamp=discord.utils.utcnow())

    def _rebuild(self):
        self.clear_items()
        if self.roles:
            select = discord.ui.Select(
                placeholder="Select a role…", min_values=1, max_values=1, row=0,
                options=[discord.SelectOption(label=role.name[:100], value=str(role.id), default=role.id == self.selected_role_id) for role in self.roles],
            )
            select.callback = self._select_role
            self.add_item(select)
        actions = [
            ("Create Gradient Role", discord.ButtonStyle.success, self._create_role),
            ("Enable Gradient", discord.ButtonStyle.primary, self._enable),
            ("Stop Gradient", discord.ButtonStyle.secondary, self._disable),
            ("Shift Colour Now", discord.ButtonStyle.secondary, self._shift),
            ("Set Interval", discord.ButtonStyle.secondary, self._set_interval),
            ("Refresh", discord.ButtonStyle.secondary, self._refresh),
            ("Exit", discord.ButtonStyle.danger, self._exit),
        ]
        for label, style, callback in actions:
            btn = discord.ui.Button(label=label, style=style, row=1 if label in ("Create Gradient Role", "Enable Gradient", "Stop Gradient") else 2)
            btn.callback = callback
            self.add_item(btn)

    async def get_editable_role(self, interaction):
        role_id = self.selected_role_id
        if role_id is None:
            return None
        role = interaction.guild.get_role(role_id)
        if role is None or role >= interaction.guild.me.top_role:
            return None
        return role

    async def _select_role(self, interaction):
        self.selected_role_id = int(interaction.data["values"][0])
        await self.refresh_data()
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    async def _create_role(self, interaction):
        await interaction.response.send_modal(GradientCreateModal(self))

    async def _enable(self, interaction):
        role = await self.get_editable_role(interaction)
        if not role:
            return await interaction.response.send_message("Select a role below the bot's top role.", ephemeral=True)
        state = self.gradient_state.get(role.id)
        async with self.cog.bot.db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO rgb_roles (role_id,guild_id,hue_index,last_updated,enabled,interval_seconds)
                   VALUES ($1,$2,0,NOW(),TRUE,86400) ON CONFLICT (role_id) DO UPDATE SET enabled=TRUE,last_updated=NOW()""",
                role.id, self.guild_id,
            )
        await self.refresh_data()
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    async def _disable(self, interaction):
        role = await self.get_editable_role(interaction)
        if not role:
            return await interaction.response.send_message("Select a role first.", ephemeral=True)
        async with self.cog.bot.db_pool.acquire() as conn:
            await conn.execute("UPDATE rgb_roles SET enabled=FALSE WHERE guild_id=$1 AND role_id=$2", self.guild_id, role.id)
        await self.refresh_data()
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    async def _shift(self, interaction):
        role = await self.get_editable_role(interaction)
        if not role:
            return await interaction.response.send_message("Select a role first.", ephemeral=True)
        state = self.gradient_state.get(role.id) or {"hue_index": 0}
        hue = (int(state.get("hue_index", 0)) + 30) % 360
        try:
            await role.edit(color=discord.Color.from_hsv(hue / 360.0, 1, 1), reason=f"Gradient panel shift by {interaction.user.id}")
            async with self.cog.bot.db_pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO rgb_roles (role_id,guild_id,hue_index,last_updated,enabled,interval_seconds)
                       VALUES ($1,$2,$3,NOW(),TRUE,86400) ON CONFLICT (role_id) DO UPDATE SET hue_index=$3,last_updated=NOW()""",
                    role.id, self.guild_id, hue,
                )
        except discord.HTTPException:
            return await interaction.response.send_message("Could not change the colour; check role hierarchy.", ephemeral=True)
        await self.refresh_data()
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    async def _set_interval(self, interaction):
        role = await self.get_editable_role(interaction)
        if not role:
            return await interaction.response.send_message("Select a role first.", ephemeral=True)
        state = self.gradient_state.get(role.id) or {}
        await interaction.response.send_modal(GradientIntervalModal(self, int(role.id), int(state.get("interval_seconds") or 86400)))

    async def _refresh(self, interaction):
        await self.refresh_data()
        await interaction.response.edit_message(embed=self.render_embed(), view=self)

    async def _exit(self, interaction):
        self.stop()
        await interaction.response.edit_message(content="Role gradient panel closed.", embed=None, view=None)

    async def refresh_panel(self):
        if self.message:
            try:
                await self.message.edit(embed=self.render_embed(), view=self)
            except discord.HTTPException:
                pass

    async def on_timeout(self):
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class GradientIntervalModal(discord.ui.Modal, title="Gradient Timing"):
    def __init__(self, panel, role_id: int, interval: int):
        super().__init__(timeout=120)
        self.panel = panel
        self.role_id = role_id
        self.input = discord.ui.TextInput(label="Seconds between colour changes (60–86400)", default=str(interval), max_length=5, required=True)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            interval = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message("Enter a whole number of seconds.", ephemeral=True)
        if not 60 <= interval <= 86400:
            return await interaction.response.send_message("Interval must be between 60 and 86400 seconds.", ephemeral=True)
        async with self.panel.cog.bot.db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO rgb_roles (role_id,guild_id,hue_index,last_updated,enabled,interval_seconds)
                   VALUES ($1,$2,0,NOW(),TRUE,$3) ON CONFLICT (role_id) DO UPDATE SET
                   interval_seconds=$3,last_updated=NOW(),enabled=TRUE""",
                self.role_id, self.panel.guild_id, interval,
            )
        await self.panel.refresh_data()
        await interaction.response.send_message(f"✅ Gradient interval set to {interval} seconds.", ephemeral=True)
        await self.panel.refresh_panel()


class PersonalRolePanel(discord.ui.View):
    def __init__(self, cog, ctx, kind: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner_id = ctx.author.id
        self.guild_id = ctx.guild.id
        self.kind = kind
        self.message = None
        for label, style, action in [
            ("Create", discord.ButtonStyle.success, self._create),
            ("Rename", discord.ButtonStyle.secondary, self._rename),
            ("Colour", discord.ButtonStyle.secondary, self._color),
            ("Icon", discord.ButtonStyle.secondary, self._icon),
            ("Info", discord.ButtonStyle.secondary, self._info),
            ("Exit", discord.ButtonStyle.danger, self._exit),
        ]:
            button = discord.ui.Button(label=label, style=style, row=0 if label in ("Create", "Rename", "Colour") else 1)
            button.callback = action
            self.add_item(button)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This personal role panel belongs to another user.", ephemeral=True)
            return False
        return True

    async def get_editable_role(self, interaction):
        role = None
        if self.kind == "booster":
            if not any(r.id == get_booster_role_id() for r in interaction.user.roles if get_booster_role_id()):
                return None
            role_id = await db_get_boost_role(self.cog.bot, self.owner_id)
        else:
            async with self.cog.bot.db_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT role_id, expiry FROM custom_roles WHERE user_id=$1 AND guild_id=$2", self.owner_id, self.guild_id)
            if not row or int(row["expiry"]) <= int(time.time()):
                return None
            role_id = row["role_id"]
            role = interaction.guild.get_role(role_id) if role_id else None
            if role and (role.name == "Custom Role"):
                # Migrate legacy purchases away from the shared Custom Role so
                # one buyer cannot edit everybody else's colour/name.
                try:
                    personal_role = await interaction.guild.create_role(
                        name=f"Custom · {interaction.user.display_name}"[:100],
                        color=role.color, hoist=role.hoist, mentionable=False,
                        reason=f"Convert legacy shared shop role for user {self.owner_id}",
                    )
                    await interaction.user.add_roles(personal_role, reason="Convert legacy custom role")
                    if role in interaction.user.roles:
                        await interaction.user.remove_roles(role, reason="Convert legacy custom role")
                    async with self.cog.bot.db_pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE custom_roles SET role_id=$1 WHERE user_id=$2 AND guild_id=$3",
                            personal_role.id, self.owner_id, self.guild_id,
                        )
                    role = personal_role
                except (discord.Forbidden, discord.HTTPException):
                    return None
        role = interaction.guild.get_role(role_id) if self.kind == "booster" and role_id else role
        if not role or role >= interaction.guild.me.top_role:
            return None
        return role

    async def _create(self, interaction):
        if self.kind != "booster":
            return await interaction.response.send_message("Custom shop roles are created by purchasing them in the shop.", ephemeral=True)
        booster_id = get_booster_role_id()
        if not booster_id or not any(r.id == booster_id for r in interaction.user.roles):
            return await interaction.response.send_message("You need the configured Booster role to use this panel.", ephemeral=True)
        existing = await db_get_boost_role(self.cog.bot, self.owner_id)
        if existing and interaction.guild.get_role(existing):
            return await interaction.response.send_message("You already have a boost role. Use Rename/Colour to edit it.", ephemeral=True)
        await interaction.response.send_modal(PersonalRoleCreateModal(self))

    async def _rename(self, interaction):
        role = await self.get_editable_role(interaction)
        if not role:
            return await interaction.response.send_message("No active role is linked to you yet. Use Create or buy a custom role first.", ephemeral=True)
        await interaction.response.send_modal(RolePropertyModal(self, "name", role.name))

    async def _color(self, interaction):
        role = await self.get_editable_role(interaction)
        if not role:
            return await interaction.response.send_message("No active role is linked to you yet. Use Create or buy a custom role first.", ephemeral=True)
        await interaction.response.send_modal(RolePropertyModal(self, "color", f"#{role.color.value:06X}"))

    async def _icon(self, interaction):
        role = await self.get_editable_role(interaction)
        if not role:
            return await interaction.response.send_message("No active role is linked to you yet. Use Create or buy a custom role first.", ephemeral=True)
        await interaction.response.send_modal(RolePropertyModal(self, "icon"))

    async def _info(self, interaction):
        role = await self.get_editable_role(interaction)
        if not role:
            return await interaction.response.send_message("No active role is linked to you yet.", ephemeral=True)
        await interaction.response.send_message(
            embed=discord.Embed(title="Your Personal Role", description=f"**Role:** {role.mention}\n**Name:** {role.name}\n**Colour:** `#{role.color.value:06X}`\n**Position:** {role.position}", color=GREY_COLOR),
            ephemeral=True,
        )

    async def _exit(self, interaction):
        self.stop()
        await interaction.response.edit_message(content="Role panel closed.", embed=None, view=None)

    async def refresh_panel(self):
        if self.message:
            try:
                embed = discord.Embed(title="Personal Role Panel", description="Use the buttons to edit the role linked to your account.", color=GREY_COLOR)
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    async def on_timeout(self):
        for item in self.children:
            if hasattr(item, "disabled"):
                item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class PersonalRoleCreateModal(discord.ui.Modal, title="Create Booster Role"):
    def __init__(self, panel):
        super().__init__(timeout=120)
        self.panel = panel
        self.name_input = discord.ui.TextInput(label="Role name", placeholder="My Moon Role", max_length=100, required=True)
        self.color_input = discord.ui.TextInput(label="Hex colour", default="#8B5CF6", max_length=7, required=True)
        self.add_item(self.name_input)
        self.add_item(self.color_input)

    async def on_submit(self, interaction):
        value = self.color_input.value.strip().removeprefix("#")
        if len(value) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
            return await interaction.response.send_message("Use a six-digit hex colour such as `#8B5CF6`.", ephemeral=True)
        if self.panel.kind != "booster":
            return await interaction.response.send_message("Only booster roles may be created here.", ephemeral=True)
        try:
            existing = await db_get_boost_role(self.panel.cog.bot, interaction.user.id)
            if existing and interaction.guild.get_role(existing):
                return await interaction.response.send_message("You already have a boost role.", ephemeral=True)
            role = await interaction.guild.create_role(
                name=self.name_input.value.strip(), color=discord.Color(int(value, 16)),
                reason=f"Booster role created by {interaction.user.id}",
            )
            await interaction.user.add_roles(role, reason="Personal booster role")
            await db_set_boost_role(self.panel.cog.bot, interaction.user.id, role.id)
        except discord.Forbidden:
            return await interaction.response.send_message("I cannot create/assign that role. Check permissions and role hierarchy.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.response.send_message("Discord rejected the role change. Try a shorter name.", ephemeral=True)
        await interaction.response.send_message(f"✅ Created {role.mention}.", ephemeral=True)
        await self.panel.refresh_panel()


# ─── Cog ──────────────────────────────────
class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._rgb_task: asyncio.Task | None = None

    async def cog_load(self):
        self._rgb_task = asyncio.create_task(self.rgb_loop())

    async def cog_unload(self):
        if self._rgb_task and not self._rgb_task.done():
            self._rgb_task.cancel()
            try:
                await self._rgb_task
            except asyncio.CancelledError:
                pass
        self._rgb_task = None

    # =============================================================
    #  BOOST ROLE SYSTEM (prefix only, PostgreSQL backend)
    # =============================================================
    @commands.group(name="boostrole", invoke_without_command=True)
    async def boostrole(self, ctx):
        """Open an interactive panel to create and edit your personal booster role."""
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return await ctx.reply("Use this command inside the server.", mention_author=False)
        view = PersonalRolePanel(self, ctx, "booster")
        embed = make_embed(ctx, "Booster Role Studio", "Create, rename, recolour, or inspect your personal booster role using the buttons below.")
        view.message = await ctx.send(embed=embed, view=view)

    async def _check_booster(self, ctx):
        booster_id = get_booster_role_id()
        if not booster_id:
            return False
        if not ctx.author.get_role(booster_id):
            return False
        return True

    @boostrole.command(name="create")
    async def boostrole_create(self, ctx, *, name: str):
        """Create your one-time custom booster role."""
        if not await self._check_booster(ctx):
            return
        await self._boostrole_create(ctx, name)

    @boostrole.command(name="color", aliases=["colour"])
    async def boostrole_color(self, ctx, hex_color: str):
        """Change the colour of your boost role."""
        if not await self._check_booster(ctx):
            return
        await self._boostrole_color(ctx, hex_color)

    @boostrole.command(name="icon")
    async def boostrole_icon(self, ctx, url: str = None):
        """Set an icon for your boost role (URL or attachment)."""
        if not await self._check_booster(ctx):
            return
        await self._boostrole_icon(ctx, url)

    @boostrole.command(name="info")
    async def boostrole_info(self, ctx):
        """Show info about your boost role."""
        if not await self._check_booster(ctx):
            return
        await self._boostrole_info(ctx)

    # =============================================================
    #  STAFF ROLE GROUP – prefix only
    # =============================================================
    @commands.group(name="role", invoke_without_command=True)
    @staff_only()
    async def role(self, ctx):
        """Role management. Use subcommands like create, delete, etc."""
        cmds = ctx.command.commands
        desc = "\n".join(f"`{c.name}` – {c.short_doc or ''}" for c in cmds)
        await ctx.send(embed=make_embed(ctx, "Role Management", f"Available subcommands:\n{desc}"))

    @role.command(name="gradient", aliases=["grident", "gradientpanel"])
    @staff_only()
    async def role_gradient(self, ctx):
        """Open the interactive gradient-role manager."""
        if not ctx.guild:
            return await ctx.reply("Use this command in a server.", mention_author=False)
        view = GradientPanelView(self, ctx)
        await view.refresh_data()
        view.message = await ctx.send(embed=view.render_embed(), view=view)

    @commands.command(name="customrole", aliases=["shoprole", "mycustomrole"])
    async def customrole_panel(self, ctx):
        """Open the editor for a custom role purchased from the shop."""
        if not ctx.guild or not isinstance(ctx.author, discord.Member):
            return await ctx.reply("Use this command inside the server.", mention_author=False)
        async with self.bot.db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT role_id, expiry FROM custom_roles WHERE user_id=$1 AND guild_id=$2", ctx.author.id, ctx.guild.id)
        if not row or int(row["expiry"]) <= int(time.time()):
            return await ctx.reply("You do not have an active shop custom role. Purchase **Custom Role** from the shop first.", mention_author=False)
        view = PersonalRolePanel(self, ctx, "shop")
        view.message = await ctx.send(embed=make_embed(ctx, "Shop Custom Role Studio", "Use the buttons to rename, recolour, set an icon, or inspect your purchased custom role."), view=view)

    @role.command(name="create", aliases=["cr"])
    @staff_only()
    async def role_create(self, ctx, *, name: str):
        """Create a new role."""
        await ctx.guild.create_role(name=name)
        await ctx.send(embed=success_embed(ctx, f"Role **{name}** created."))

    @role.command(name="delete", aliases=["dr"])
    @staff_only()
    async def role_delete(self, ctx, role: discord.Role):
        """Delete a role."""
        if role >= ctx.guild.me.top_role:
            return
        await role.delete()
        await ctx.send(embed=make_embed(ctx, f"{E('delete')} Role Deleted", f"Deleted **{role.name}**."))

    @role.command(name="give", aliases=["gr"])
    @staff_only()
    async def role_give(self, ctx, member: discord.Member, role: discord.Role):
        """Give a role to a member."""
        if role >= ctx.guild.me.top_role:
            return
        await member.add_roles(role)
        await ctx.send(embed=success_embed(ctx, f"**{role.name}** given to {member.mention}."))

    @role.command(name="remove", aliases=["rr", "rtake"])
    @staff_only()
    async def role_remove(self, ctx, member: discord.Member, role: discord.Role):
        """Remove a role from a member."""
        if role >= ctx.guild.me.top_role:
            return
        await member.remove_roles(role)
        await ctx.send(embed=make_embed(ctx, f"{E('delete')} Role Removed", f"Removed **{role.name}** from {member.mention}."))

    @role.command(name="rename", aliases=["rn"])
    @staff_only()
    async def role_rename(self, ctx, role: discord.Role, *, new_name: str):
        """Rename a role."""
        if role >= ctx.guild.me.top_role:
            return
        old = role.name
        await role.edit(name=new_name)
        await ctx.send(embed=success_embed(ctx, f"**{old}** → **{new_name}**"))

    @role.command(name="color", aliases=["rc", "colour"])
    @staff_only()
    async def role_color(self, ctx, role: discord.Role, *, color: str):
        """Change a role's colour (hex, e.g. #ff0000)."""
        if role >= ctx.guild.me.top_role:
            return
        try:
            col = discord.Color(int(color.strip("#"), 16))
        except Exception:
            return
        await role.edit(color=col)
        await ctx.send(embed=make_embed(ctx, f"{E('color')} Role Color", f"**{role.name}** → {color}."))

    @role.command(name="position", aliases=["rp", "rposition"])
    @staff_only()
    async def role_position(self, ctx, *, role: discord.Role):
        """Show the current position of a role."""
        embed = make_embed(
            ctx,
            f"{E('chart')} Role Position",
            f"**{role.name}** is at position **{role.position}**."
        )
        await ctx.send(embed=embed)

    @role.command(name="setposition", aliases=["sp", "rsetpos"])
    @staff_only()
    async def role_setposition(self, ctx, role: discord.Role, position: int):
        """Set a role's position (1 = lowest)."""
        if role >= ctx.guild.me.top_role:
            return
        try:
            await role.edit(position=position)
        except discord.HTTPException:
            return
        await ctx.send(embed=success_embed(ctx, f"**{role.name}** moved to position **{role.position}**."))

    @role.command(name="rgb", aliases=["creatergb"])
    @staff_only()
    async def role_rgb(self, ctx, *, name: str):
        """Create an RGB colour-cycling role (one per guild)."""
        async with self.bot.db_pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT role_id FROM rgb_roles WHERE guild_id = $1", ctx.guild.id
            )
        if existing:
            return

        role = await ctx.guild.create_role(name=name, reason="RGB colour cycling role")
        await role.edit(color=discord.Color.from_hsv(0.0, 1.0, 1.0))
        await db_add_rgb_role(self.bot, role.id, ctx.guild.id)
        await ctx.send(embed=success_embed(ctx, f"RGB role **{name}** created. It will change colour every 24 hours."))

    # ─── RGB role background loop ─────────
    async def rgb_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                due_roles = await db_get_rgb_roles_due(self.bot)
                for entry in due_roles:
                    role_id = entry["role_id"]
                    guild_id = entry["guild_id"]
                    hue_index = entry["hue_index"]

                    guild = self.bot.get_guild(guild_id)
                    if guild is None:
                        continue
                    role = guild.get_role(role_id)
                    if role is None:
                        continue

                    new_hue = (hue_index + 1) % 360
                    color = discord.Color.from_hsv(new_hue / 360.0, 1.0, 1.0)
                    await role.edit(color=color)
                    await db_update_rgb_role(self.bot, role_id, new_hue)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger("nuvella.roles").exception("Gradient role scheduler failed")
            await asyncio.sleep(60)

    # ─── Internal boost role logic ───
    async def _boostrole_create(self, ctx, name: str):
        uid = ctx.author.id
        existing_role_id = await db_get_boost_role(self.bot, uid)
        if existing_role_id:
            role = ctx.guild.get_role(existing_role_id)
            if role:
                return
            await db_delete_boost_role(self.bot, uid)

        new_role = await ctx.guild.create_role(name=name, reason="Booster custom role")
        await ctx.author.add_roles(new_role)
        await db_set_boost_role(self.bot, uid, new_role.id)
        await ctx.send(embed=success_embed(ctx, f"Boost role **{name}** created and assigned to you!"))

    async def _boostrole_color(self, ctx, hex_color: str):
        role_id = await db_get_boost_role(self.bot, ctx.author.id)
        if not role_id:
            return
        role = ctx.guild.get_role(role_id)
        if not role:
            await db_delete_boost_role(self.bot, ctx.author.id)
            return

        try:
            col = discord.Color(int(hex_color.strip("#"), 16))
        except Exception:
            return

        await role.edit(color=col)
        await ctx.send(embed=make_embed(
            ctx,
            f"{E('color')} Boost Role Colour",
            f"Colour of {role.mention} set to `{hex_color}`."
        ))

    async def _boostrole_icon(self, ctx, url: str = None):
        role_id = await db_get_boost_role(self.bot, ctx.author.id)
        if not role_id:
            return
        role = ctx.guild.get_role(role_id)
        if not role:
            await db_delete_boost_role(self.bot, ctx.author.id)
            return

        if url is None and ctx.message.attachments:
            url = ctx.message.attachments[0].url
        if url is None:
            return

        try:
            async with self.bot.http_session.get(url) as resp:
                if resp.status != 200:
                    return
                data = await resp.read()
                await role.edit(display_icon=data)
        except Exception:
            logging.getLogger("nuvella.roles").exception(
                "Boost-role icon update failed | user_id=%s | role_id=%s",
                ctx.author.id, role.id,
            )
            return

        await ctx.send(embed=success_embed(ctx, f"Icon for {role.mention} updated!"))

    async def _boostrole_info(self, ctx):
        role_id = await db_get_boost_role(self.bot, ctx.author.id)
        if not role_id:
            return
        role = ctx.guild.get_role(role_id)
        if not role:
            await db_delete_boost_role(self.bot, ctx.author.id)
            return

        embed = make_embed(
            ctx,
            "Your Boost Role",
            f"{role.mention}\nName: {role.name}\nColour: {role.color}\nPosition: {role.position}"
        )
        await ctx.send(embed=embed)


# ─── Setup ────────────────────────────────
async def setup(bot):
    config = load_config()
    bot.owner_id = config.get("owner_id")
    if not getattr(bot, "http_session", None) or bot.http_session.closed:
        bot.http_session = aiohttp.ClientSession()
    await db_init(bot)
    await bot.add_cog(Roles(bot))

