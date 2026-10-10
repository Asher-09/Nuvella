import discord
from discord.ext import commands
import json
import os
import aiohttp
import asyncpg
from dotenv import load_dotenv
import asyncio

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
            WHERE last_updated + INTERVAL '24 hours' <= NOW()
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
        """Manage your personal booster role."""
        cmds = ctx.command.commands
        desc = "\n".join(f"`{c.name}` – {c.short_doc or ''}" for c in cmds)
        await ctx.send(embed=make_embed(ctx, "Boost Role", f"Available subcommands:\n{desc}"))

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
            except Exception as e:
                print(f"RGB loop error: {e}")
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
                await role.edit(icon=data)
        except Exception as e:
            print(f"Icon update failed: {e}")
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

    @bot.event
    async def on_command_error(ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CheckFailure):
            return
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            embed = discord.Embed(
                title="Incorrect Usage",
                description=(
                    f"Usage: `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`\n"
                    f"Use `{ctx.prefix}help {ctx.command.qualified_name}` for more info."
                ),
                color=GREY_COLOR
            )
            await ctx.send(embed=embed)
        else:
            print(f"Command error in {ctx.command}: {error}")
