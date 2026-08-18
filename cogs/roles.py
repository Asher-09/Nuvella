import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import aiohttp
import asyncpg
from dotenv import load_dotenv
from datetime import datetime, timedelta
import asyncio

# Load environment variables from .env
load_dotenv()

# ─── Theme colours ─────────────────────
MAIN_COLOR  = 0x6A0DAD   # Dark purple
WIN_COLOR   = 0x57F287
LOSS_COLOR  = 0xED4245

# ─── Emoji constants ────────────────────
EMOJI_SUCCESS = "<a:tick:1537915747454554222>"
EMOJI_WRONG   = "<:cross:1537915734087303199>"
EMOJI_DELETE  = "<:Deleted:1537919221969846413>"
EMOJI_COLOR   = "<:color:1537919527856111686>"
EMOJI_UP      = "<:chart:1537919996984959046>"
EMOJI_BREAKER = "<:breaker:1537913026643238932>"

BREAKER_LINE = EMOJI_BREAKER * 10

# ─── File paths ──────────────────────────
CONFIG_FILE = "config.json"
BOOSTER_ROLE_ID = 1508007653652041839  # This might be overridden by config later

# ─── Database helpers (PostgreSQL) ────────
async def db_init(bot):
    """Create database pool and ensure tables exist."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set in .env")
    bot.db_pool = await asyncpg.create_pool(dsn)

    async with bot.db_pool.acquire() as conn:
        # Boost roles table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS boost_roles (
                user_id BIGINT PRIMARY KEY,
                role_id BIGINT NOT NULL
            )
        """)
        # RGB roles table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS rgb_roles (
                role_id BIGINT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                hue_index INTEGER NOT NULL DEFAULT 0,
                last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

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
    """Return list of dicts with role_id, guild_id, hue_index for roles due for update."""
    async with bot.db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT role_id, guild_id, hue_index FROM rgb_roles
            WHERE last_updated + INTERVAL '24 hours' <= NOW()
        """)
    return [dict(row) for row in rows]

async def db_update_rgb_role(bot, role_id: int, hue_index: int):
    async with bot.db_pool.acquire() as conn:
        await conn.execute("""
            UPDATE rgb_roles SET hue_index = $2, last_updated = NOW()
            WHERE role_id = $1
        """, role_id, hue_index)

# ─── Config loading ──────────────────────
def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except:
        return {}

def get_staff_role_ids():
    """Return a list of Discord role IDs considered staff from config.json."""
    config = load_config()
    return list(config.get("staff_roles", {}).values())

# ─── Staff check ────────────────────────
def is_staff(member: discord.Member, bot: commands.Bot):
    # Guild owner
    if member.id == member.guild.owner_id:
        return True
    # Bot owner from config
    config = load_config()
    owner_id = config.get("owner_id")
    if owner_id and member.id == owner_id:
        return True
    # Staff role IDs
    staff_role_ids = get_staff_role_ids()
    for role in member.roles:
        if role.id in staff_role_ids:
            return True
    return False

def staff_only():
    async def predicate(ctx):
        if not is_staff(ctx.author, ctx.bot):
            raise commands.CheckFailure("You must be staff to use this command.")
        return True
    return commands.check(predicate)

# ─── Embed builders ───────────────────────
def make_embed(ctx, title, desc, color, success=False):
    user = ctx.author if isinstance(ctx, commands.Context) else ctx.user
    guild = ctx.guild
    embed = discord.Embed(
        title=title,
        description=f"{desc}\n{BREAKER_LINE}" if desc else None,
        color=color if not success else WIN_COLOR,
        timestamp=discord.utils.utcnow()
    )
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed.set_footer(text=f"Action by {user}", icon_url=user.display_avatar.url)
    return embed

def success_embed(ctx, msg):
    return make_embed(ctx, f"{EMOJI_SUCCESS} Success", msg, WIN_COLOR, success=True)

# ─── Cog ──────────────────────────────────
class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Start the RGB role color cycling loop
        self.bot.loop.create_task(self.rgb_loop())

    # =============================================================
    #  BOOST ROLE SYSTEM (PostgreSQL backend)
    # =============================================================
    boostrole_group = app_commands.Group(name="boostrole", description="Manage your personal booster role")

    async def _check_booster(self, ctx):
        if isinstance(ctx, discord.Interaction):
            user = ctx.user
        else:
            user = ctx.author
        if not user.get_role(BOOSTER_ROLE_ID):
            # Silent failure – no error message to Discord
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)
            return False
        return True

    @boostrole_group.command(name="create", description="Create your one‑time custom booster role")
    async def slash_boostrole_create(self, interaction: discord.Interaction, name: str):
        if not await self._check_booster(interaction):
            return
        await self._boostrole_create(interaction, name)

    @boostrole_group.command(name="color", description="Change the colour of your boost role")
    async def slash_boostrole_color(self, interaction: discord.Interaction, hex_color: str):
        if not await self._check_booster(interaction):
            return
        await self._boostrole_color(interaction, hex_color)

    @boostrole_group.command(name="icon", description="Set an icon for your boost role (URL)")
    async def slash_boostrole_icon(self, interaction: discord.Interaction, url: str = None):
        if not await self._check_booster(interaction):
            return
        await self._boostrole_icon(interaction, url)

    @boostrole_group.command(name="info", description="Show info about your boost role")
    async def slash_boostrole_info(self, interaction: discord.Interaction):
        if not await self._check_booster(interaction):
            return
        await self._boostrole_info(interaction)

    # =============================================================
    #  STAFF ROLE GROUP – all role management in one place
    # =============================================================
    @commands.hybrid_group(name="role", fallback="help")
    @staff_only()
    async def role(self, ctx):
        """Main role management command. Use subcommands like create, delete, etc."""
        if ctx.invoked_subcommand is None:
            cmds = ctx.command.commands
            desc = "\n".join(f"`{c.name}` – {c.short_doc}" for c in cmds)
            embed = make_embed(ctx, "Role Management", f"Available subcommands:\n{desc}", MAIN_COLOR)
            await ctx.send(embed=embed)

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
            return  # silent
        await role.delete()
        await ctx.send(embed=make_embed(ctx, f"{EMOJI_DELETE} Role Deleted", f"Deleted **{role.name}**.", LOSS_COLOR))

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
        await ctx.send(embed=make_embed(ctx, f"{EMOJI_DELETE} Role Removed", f"Removed **{role.name}** from {member.mention}.", LOSS_COLOR))

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
        """Change a role's colour (hex, e.g. #ff0000). Embed will match."""
        if role >= ctx.guild.me.top_role:
            return
        try:
            col = discord.Color(int(color.strip("#"), 16))
        except:
            return  # silent
        await role.edit(color=col)
        await ctx.send(embed=make_embed(ctx, f"{EMOJI_COLOR} Role Color", f"**{role.name}** → {color}.", col))

    @role.command(name="position", aliases=["rp", "rposition"])
    @staff_only()
    async def role_position(self, ctx, *, role: discord.Role):
        """Show the current position of a role."""
        embed = make_embed(
            ctx,
            f"{EMOJI_UP} Role Position",
            f"**{role.name}** is at position **{role.position}**.",
            MAIN_COLOR
        )
        await ctx.send(embed=embed)

    @role.command(name="setposition", aliases=["sp", "rsetpos"])
    @staff_only()
    async def role_setposition(self, ctx, role: discord.Role, position: int):
        """Set a role's position (1 = lowest, higher = higher up)."""
        if role >= ctx.guild.me.top_role:
            return
        try:
            await role.edit(position=position)
        except discord.HTTPException as e:
            return  # silent
        await ctx.send(embed=success_embed(ctx, f"**{role.name}** moved to position **{role.position}**."))

    @role.command(name="rgb", aliases=["creatergb"])
    @staff_only()
    async def role_rgb(self, ctx, *, name: str):
        """Create an RGB colour‑cycling role. Color changes every 24 hours through hue gradient (no white/black)."""
        # Check if there is already an RGB role in this guild (limit one per guild)
        async with self.bot.db_pool.acquire() as conn:
            existing = await conn.fetchrow("SELECT role_id FROM rgb_roles WHERE guild_id = $1", ctx.guild.id)
        if existing:
            # Silent – already exists
            return

        role = await ctx.guild.create_role(name=name, reason="RGB colour cycling role")
        # Initial colour: hue 0 (red)
        await role.edit(color=discord.Color.from_hsv(0.0, 1.0, 1.0))
        await db_add_rgb_role(self.bot, role.id, ctx.guild.id)
        await ctx.send(embed=success_embed(ctx, f"RGB role **{name}** created. It will change colour every 24 hours."))

    # ─── RGB role background loop ─────────
    async def rgb_loop(self):
        """Loop that checks for due RGB roles and updates their colour every 24 hours."""
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
                        # Role was deleted – remove from DB (optional, but we can ignore)
                        continue

                    # Compute next hue
                    new_hue = (hue_index + 1) % 360
                    color = discord.Color.from_hsv(new_hue / 360.0, 1.0, 1.0)
                    await role.edit(color=color)
                    await db_update_rgb_role(self.bot, role_id, new_hue)
            except Exception as e:
                # Log internally but do not crash the loop
                print(f"RGB loop error: {e}")
            await asyncio.sleep(60)  # Check every minute

    # ─── Internal boost role logic (PostgreSQL) ───
    async def _boostrole_create(self, ctx, name: str):
        user = ctx.user if isinstance(ctx, discord.Interaction) else ctx.author
        uid = user.id
        existing_role_id = await db_get_boost_role(self.bot, uid)
        if existing_role_id:
            role = ctx.guild.get_role(existing_role_id)
            if role:
                # Already has a role – silent
                if isinstance(ctx, discord.Interaction):
                    await ctx.response.defer(ephemeral=True)
                return
            # Stale DB entry – remove it
            await db_delete_boost_role(self.bot, uid)

        new_role = await ctx.guild.create_role(name=name, reason="Booster custom role")
        await user.add_roles(new_role)
        await db_set_boost_role(self.bot, uid, new_role.id)
        await self._respond(ctx, success_embed(ctx, f"Boost role **{name}** created and assigned to you!"))

    async def _boostrole_color(self, ctx, hex_color: str):
        user = ctx.user if isinstance(ctx, discord.Interaction) else ctx.author
        role_id = await db_get_boost_role(self.bot, user.id)
        if not role_id:
            return await self._silent(ctx)
        role = ctx.guild.get_role(role_id)
        if not role:
            await db_delete_boost_role(self.bot, user.id)
            return await self._silent(ctx)

        try:
            col = discord.Color(int(hex_color.strip("#"), 16))
        except:
            return await self._silent(ctx)

        await role.edit(color=col)
        await self._respond(ctx, make_embed(ctx, f"{EMOJI_COLOR} Boost Role Colour", f"Colour of {role.mention} set to `{hex_color}`.", col))

    async def _boostrole_icon(self, ctx, url: str = None):
        user = ctx.user if isinstance(ctx, discord.Interaction) else ctx.author
        role_id = await db_get_boost_role(self.bot, user.id)
        if not role_id:
            return await self._silent(ctx)
        role = ctx.guild.get_role(role_id)
        if not role:
            await db_delete_boost_role(self.bot, user.id)
            return await self._silent(ctx)

        if url is None and hasattr(ctx, 'message') and ctx.message.attachments:
            url = ctx.message.attachments[0].url
        if url is None:
            return await self._silent(ctx)

        try:
            async with self.bot.http_session.get(url) as resp:
                if resp.status != 200:
                    return await self._silent(ctx)
                data = await resp.read()
                await role.edit(icon=data)
        except Exception as e:
            print(f"Icon update failed: {e}")
            return await self._silent(ctx)

        await self._respond(ctx, success_embed(ctx, f"Icon for {role.mention} updated!"))

    async def _boostrole_info(self, ctx):
        user = ctx.user if isinstance(ctx, discord.Interaction) else ctx.author
        role_id = await db_get_boost_role(self.bot, user.id)
        if not role_id:
            return await self._silent(ctx)
        role = ctx.guild.get_role(role_id)
        if not role:
            await db_delete_boost_role(self.bot, user.id)
            return await self._silent(ctx)

        embed = make_embed(ctx, "Your Boost Role", f"{role.mention}\nName: {role.name}\nColour: {role.color}\nPosition: {role.position}", MAIN_COLOR)
        await self._respond(ctx, embed)

    async def _respond(self, ctx, embed):
        if isinstance(ctx, discord.Interaction):
            await ctx.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)

    async def _silent(self, ctx):
        """Handle a silent failure. For interactions, defer ephemeral to avoid error."""
        if isinstance(ctx, discord.Interaction):
            await ctx.response.defer(ephemeral=True)
        # For prefix commands, do nothing
        return

# ─── Global error handlers ────────────────
async def setup(bot):
    # Load config
    config = load_config()
    bot.owner_id = config.get("owner_id")
    bot.http_session = aiohttp.ClientSession()
    await db_init(bot)
    await bot.add_cog(Roles(bot))

    # Prefix command error handler – only syntax errors are sent
    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CheckFailure):
            return
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            # Send syntax message (usage)
            embed = discord.Embed(
                title="Incorrect Usage",
                description=f"Usage: `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`\nUse `{ctx.prefix}help {ctx.command.qualified_name}` for more info.",
                color=MAIN_COLOR
            )
            await ctx.send(embed=embed)
        else:
            # Log all other errors to console, but do not send to Discord
            print(f"Command error in {ctx.command}: {error}")

    # Slash command error handler – never send error messages to Discord
    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error):
        # Log internally
        print(f"App command error in {interaction.command.name}: {error}")
        # If interaction hasn't been responded to, defer ephemeral to avoid "Interaction failed"
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        # No message is sent to the user