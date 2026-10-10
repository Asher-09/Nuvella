import json
import os
import time
import discord
from discord.ext import commands
import asyncpg
from dotenv import load_dotenv

from utils.emoji import EMOJIS
load_dotenv()

HI_COOLDOWN = 600
TRIGGER_COOLDOWN = 10
AFK_MENTION_COOLDOWN = 30

# ── Light Purple Theme ──
MAIN_COLOR = 0xb19cd9
WIN_COLOR  = 0x8e44ad
LOSS_COLOR = 0xe74c3c

DEFAULT_WELCOME_CHANNEL_ID = 1535962095663980546

# ── Custom Discord Emote Constants ──



def embed_base(title, description, color=MAIN_COLOR, ctx=None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow()
    )
    if ctx:
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url
        )
        if ctx.guild:
            embed.set_author(
                name=ctx.guild.name,
                icon_url=ctx.guild.icon.url if ctx.guild.icon else None
            )
    return embed


def elevated_perms_check():
    """
    Allows users who are:
    - Bot owner (via discord.py's is_owner)
    - In config's owner_id
    - Have one of the admin, mod, trial mod, event manager roles.
    """
    async def predicate(ctx):
        if await ctx.bot.is_owner(ctx.author):
            return True

        config = ctx.cog.config_data
        owner_id = config.get("owner_id")
        roles = config.get("roles", {})

        # Check owner_id (could be int or list)
        if isinstance(owner_id, int):
            if ctx.author.id == owner_id:
                return True
        elif isinstance(owner_id, list):
            if ctx.author.id in owner_id:
                return True

        if ctx.guild:
            author_role_ids = [role.id for role in ctx.author.roles]

            # Collect elevated role IDs from config
            elevated_role_ids = []
            for key in ("admin_role_id", "moderator_role_id", "trial_moderator_role_id", "event_manager_role_id"):
                rid = roles.get(key)
                if rid is not None:
                    elevated_role_ids.append(rid)

            # Check if author has any of these roles
            if any(rid in author_role_ids for rid in elevated_role_ids):
                return True

        return False
    return commands.check(predicate)


class Extra(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pool = None
        self.config_data = {}
        self.hi_cooldowns = {}
        self.trigger_cooldowns = {}
        self.afk_mention_cooldowns = {}

    async def cog_load(self):
        with open("config.json", "r") as f:
            self.config_data = json.load(f)

        self.pool = getattr(self.bot, "pool", None)
        if self.pool is None:
            raise RuntimeError("The shared PostgreSQL pool was not initialized by main.py.")

        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS triggers (
                    guild_id BIGINT NOT NULL,
                    trigger TEXT NOT NULL,
                    type TEXT NOT NULL,
                    response TEXT NOT NULL,
                    PRIMARY KEY (guild_id, trigger)
                );
                CREATE TABLE IF NOT EXISTS afk (
                    user_id BIGINT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    timestamp BIGINT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS welcome_config (
                    guild_id BIGINT PRIMARY KEY,
                    enabled BOOLEAN NOT NULL DEFAULT TRUE,
                    channel_id BIGINT
                );
                CREATE TABLE IF NOT EXISTS customgreet_config (
                    guild_id BIGINT PRIMARY KEY,
                    enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    message TEXT NOT NULL DEFAULT 'I greet thee, the Lord and Master of the Northern Frost {user}.',
                    cooldown INTEGER NOT NULL DEFAULT 1200,
                    role_ids BIGINT[] NOT NULL DEFAULT '{}',
                    user_ids BIGINT[] NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS customgreet_last_times (
                    guild_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    last_time DOUBLE PRECISION NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
            """)

    async def cog_unload(self):
        # The PostgreSQL pool belongs to the bot and is shared by every cog.
        return

    # ─── Helper: Get logging channel ID from config ───
    def get_logging_channel_id(self) -> int | None:
        # First try top-level
        channel_id = self.config_data.get("logging_channel_id")
        if channel_id is not None:
            return int(channel_id)
        # Then try inside "roles"
        roles = self.config_data.get("roles", {})
        channel_id = roles.get("logging_channel_id")
        if channel_id is not None:
            return int(channel_id)
        return None

    # ─── Helper: Send command log to logging channel ──
    async def log_command_usage(self, ctx, success: bool, error: Exception = None):
        channel_id = self.get_logging_channel_id()
        if not channel_id:
            return  # No logging channel configured

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        # Build embed
        embed = discord.Embed(
            title=f"Command {'Succeeded' if success else 'Failed'}",
            color=WIN_COLOR if success else LOSS_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="User", value=f"{ctx.author} (ID: {ctx.author.id})", inline=False)
        embed.add_field(name="Command", value=f"`{ctx.message.content}`", inline=False)
        embed.add_field(name="Channel", value=ctx.channel.mention if ctx.guild else "DM", inline=True)
        embed.add_field(name="Guild", value=ctx.guild.name if ctx.guild else "N/A", inline=True)
        if not success and error:
            embed.add_field(name="Error", value=str(error), inline=False)

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass  # Bot lacks permission to send in logging channel
        except Exception:
            pass

    # ─── Listener: Successful command ────────────────
    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        await self.log_command_usage(ctx, success=True)

    # ─── Listener: Failed command (for logging only) ──
    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        # This will also be handled by cog_command_error for user feedback,
        # but we log it here as well.
        await self.log_command_usage(ctx, success=False, error=error)

    # ─── Welcome Event ─────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member):
        if not member.guild:
            return
        enabled = await self.get_welcome_enabled(member.guild.id)
        if not enabled:
            return
        channel_id = await self.get_welcome_channel(member.guild.id) or DEFAULT_WELCOME_CHANNEL_ID
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return
        embed = discord.Embed(
            title=f"{EMOJIS["moon"]} Welcome!",
            description=(
                f"Hey lunar traveller {member.mention}, enjoy your time here and if you want roles go to "
                f"<#1536190617359482990> and Check about the server in <#1535962074415505510>"
            ),
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(
            text=f"Joined {member.guild.name}",
            icon_url=member.guild.icon.url if member.guild.icon else None
        )
        try:
            await channel.send(embed=embed)
        except Exception:
            pass

    # ─── Welcome DB helpers ───────────────────────────
    async def get_welcome_enabled(self, guild_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT enabled FROM welcome_config WHERE guild_id = $1", guild_id)
            if row is None:
                await conn.execute("INSERT INTO welcome_config (guild_id, enabled) VALUES ($1, TRUE)", guild_id)
                return True
            return row["enabled"]

    async def set_welcome_enabled(self, guild_id: int, enabled: bool):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO welcome_config (guild_id, enabled)
                VALUES ($1, $2)
                ON CONFLICT (guild_id) DO UPDATE SET enabled = $2
            """, guild_id, enabled)

    async def get_welcome_channel(self, guild_id: int) -> int | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT channel_id FROM welcome_config WHERE guild_id = $1", guild_id)
            return row["channel_id"] if row else None

    # ─── Welcome Toggle Commands (elevated perms) ──────
    @commands.group(name="welcome", invoke_without_command=True)
    @elevated_perms_check()
    async def welcome_group(self, ctx):
        state = "ENABLED" if await self.get_welcome_enabled(ctx.guild.id) else "DISABLED"
        embed = embed_base(
            f"{EMOJIS["moon"]} Welcome System",
            f"The welcomer is currently **{state}**.\n"
            f"Use `{ctx.clean_prefix}welcome on` or `{ctx.clean_prefix}welcome off` to toggle.\n"
            f"*Requires Owner, Admin, Mod, Trial Mod, or Event Manager roles.*",
            MAIN_COLOR,
            ctx
        )
        await ctx.send(embed=embed)

    @welcome_group.command(name="on")
    @elevated_perms_check()
    async def welcome_on(self, ctx):
        await self.set_welcome_enabled(ctx.guild.id, True)
        embed = embed_base(f"{EMOJIS["tick"]} Welcome Enabled", "New members will now receive a welcome message.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @welcome_group.command(name="off")
    @elevated_perms_check()
    async def welcome_off(self, ctx):
        await self.set_welcome_enabled(ctx.guild.id, False)
        embed = embed_base(f"{EMOJIS["cross"]} Welcome Disabled", "The welcome message has been turned off.", LOSS_COLOR, ctx)
        await ctx.send(embed=embed)

    # ─── Custom Greet DB helpers ──────────────────────
    async def get_customgreet_config(self, guild_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM customgreet_config WHERE guild_id = $1", guild_id)
            if row is None:
                await conn.execute(
                    "INSERT INTO customgreet_config (guild_id) VALUES ($1) ON CONFLICT DO NOTHING",
                    guild_id
                )
                row = await conn.fetchrow("SELECT * FROM customgreet_config WHERE guild_id = $1", guild_id)
            return row

    async def update_customgreet_config(self, guild_id: int, **kwargs):
        columns = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(kwargs.keys()))
        values = list(kwargs.values())
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"INSERT INTO customgreet_config (guild_id, {', '.join(kwargs.keys())}) "
                f"VALUES ($1, {', '.join(f'${i+2}' for i in range(len(kwargs)))}) "
                f"ON CONFLICT (guild_id) DO UPDATE SET {columns}",
                guild_id, *values
            )

    async def get_customgreet_last_time(self, guild_id: int, user_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT last_time FROM customgreet_last_times WHERE guild_id = $1 AND user_id = $2",
                guild_id, user_id
            )
            return row["last_time"] if row else 0.0

    async def set_customgreet_last_time(self, guild_id: int, user_id: int, last_time: float):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO customgreet_last_times (guild_id, user_id, last_time) "
                "VALUES ($1, $2, $3) ON CONFLICT (guild_id, user_id) DO UPDATE SET last_time = $3",
                guild_id, user_id, last_time
            )

    async def reset_customgreet_times(self, guild_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM customgreet_last_times WHERE guild_id = $1", guild_id)

    # ─── Custom Greet Commands (elevated perms) ─────────
    @commands.group(name="customgreet", invoke_without_command=True)
    @elevated_perms_check()
    async def customgreet_group(self, ctx):
        cfg = await self.get_customgreet_config(ctx.guild.id)
        status = f"{EMOJIS["tick"]} Enabled" if cfg["enabled"] else f"{EMOJIS["cross"]} Disabled"
        cooldown_min = cfg["cooldown"] // 60
        roles_mentions = ", ".join(f"<@&{rid}>" for rid in cfg["role_ids"]) or "None"
        users_mentions = ", ".join(f"<@{uid}>" for uid in cfg["user_ids"]) or "None"
        embed = embed_base(
            f"{EMOJIS["greet"]} Custom Greet System",
            f"**Status:** {status}\n"
            f"**Message:** {cfg['message']}\n"
            f"**Inactivity cooldown:** {cooldown_min} minute(s)\n"
            f"**Greeted Roles:** {roles_mentions}\n"
            f"**Greeted Users:** {users_mentions}\n\n"
            f"Use subcommands to configure.\n"
            f"*Requires Owner, Admin, Mod, Trial Mod, or Event Manager roles.*",
            MAIN_COLOR,
            ctx
        )
        await ctx.send(embed=embed)

    @customgreet_group.command(name="on")
    @elevated_perms_check()
    async def customgreet_on(self, ctx):
        await self.update_customgreet_config(ctx.guild.id, enabled=True)
        embed = embed_base(f"{EMOJIS["tick"]} Custom Greet Enabled", "The bot will now greet returning users.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_group.command(name="off")
    @elevated_perms_check()
    async def customgreet_off(self, ctx):
        await self.update_customgreet_config(ctx.guild.id, enabled=False)
        embed = embed_base(f"{EMOJIS["cross"]} Custom Greet Disabled", "The bot will no longer greet returning users.", LOSS_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_group.command(name="message")
    @elevated_perms_check()
    async def customgreet_message(self, ctx, *, message: str):
        await self.update_customgreet_config(ctx.guild.id, message=message)
        embed = embed_base(f"{EMOJIS["notes"]} Greeting Message Set", f"New message: {message}", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_group.command(name="cooldown")
    @elevated_perms_check()
    async def customgreet_cooldown(self, ctx, minutes: int):
        if minutes < 1:
            return await ctx.send(embed=embed_base("Invalid Cooldown", "Must be at least 1 minute.", LOSS_COLOR, ctx))
        await self.update_customgreet_config(ctx.guild.id, cooldown=minutes * 60)
        embed = embed_base(f"{EMOJIS["cooldown"]} Cooldown Updated", f"Inactivity threshold set to **{minutes}** minute(s).", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_group.command(name="reset")
    @elevated_perms_check()
    async def customgreet_reset(self, ctx):
        await self.reset_customgreet_times(ctx.guild.id)
        embed = embed_base(f"{EMOJIS["refresh"]} Times Reset", "All last‑message times have been cleared.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_group.group(name="role")
    @elevated_perms_check()
    async def customgreet_role(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `customgreet role add <role>` or `customgreet role remove <role>`")

    @customgreet_role.command(name="add")
    @elevated_perms_check()
    async def customgreet_role_add(self, ctx, role: discord.Role):
        cfg = await self.get_customgreet_config(ctx.guild.id)
        if role.id in cfg["role_ids"]:
            return await ctx.send(embed=embed_base("Already Added", f"{role.mention} is already in the greet list.", LOSS_COLOR, ctx))
        new_roles = list(cfg["role_ids"]) + [role.id]
        await self.update_customgreet_config(ctx.guild.id, role_ids=new_roles)
        embed = embed_base(f"{EMOJIS["tick"]} Role Added", f"Members with {role.mention} will now be greeted upon return.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_role.command(name="remove")
    @elevated_perms_check()
    async def customgreet_role_remove(self, ctx, role: discord.Role):
        cfg = await self.get_customgreet_config(ctx.guild.id)
        if role.id not in cfg["role_ids"]:
            return await ctx.send(embed=embed_base("Not Found", f"{role.mention} is not in the greet list.", LOSS_COLOR, ctx))
        new_roles = [r for r in cfg["role_ids"] if r != role.id]
        await self.update_customgreet_config(ctx.guild.id, role_ids=new_roles)
        embed = embed_base(f"{EMOJIS["tick"]} Role Removed", f"Members with {role.mention} will no longer be greeted.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_group.group(name="user")
    @elevated_perms_check()
    async def customgreet_user(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `customgreet user add <member>` or `customgreet user remove <member>`")

    @customgreet_user.command(name="add")
    @elevated_perms_check()
    async def customgreet_user_add(self, ctx, member: discord.Member):
        cfg = await self.get_customgreet_config(ctx.guild.id)
        if member.id in cfg["user_ids"]:
            return await ctx.send(embed=embed_base("Already Added", f"{member.mention} is already in the greet list.", LOSS_COLOR, ctx))
        new_users = list(cfg["user_ids"]) + [member.id]
        await self.update_customgreet_config(ctx.guild.id, user_ids=new_users)
        embed = embed_base(f"{EMOJIS["tick"]} User Added", f"{member.mention} will now be greeted upon return.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    @customgreet_user.command(name="remove")
    @elevated_perms_check()
    async def customgreet_user_remove(self, ctx, member: discord.Member):
        cfg = await self.get_customgreet_config(ctx.guild.id)
        if member.id not in cfg["user_ids"]:
            return await ctx.send(embed=embed_base("Not Found", f"{member.mention} is not in the greet list.", LOSS_COLOR, ctx))
        new_users = [u for u in cfg["user_ids"] if u != member.id]
        await self.update_customgreet_config(ctx.guild.id, user_ids=new_users)
        embed = embed_base(f"{EMOJIS["tick"]} User Removed", f"{member.mention} will no longer be greeted.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    # ─── Message Listener ─────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        content = message.content.strip().lower()
        now = time.time()
        guild_id = message.guild.id
        user_id = message.author.id

        # Custom greet
        cfg = await self.get_customgreet_config(guild_id)
        if cfg["enabled"]:
            should_greet = False
            if cfg["role_ids"] or cfg["user_ids"]:
                if any(role.id in cfg["role_ids"] for role in message.author.roles):
                    should_greet = True
                if message.author.id in cfg["user_ids"]:
                    should_greet = True
            if should_greet:
                last_time = await self.get_customgreet_last_time(guild_id, user_id)
                if now - last_time >= cfg["cooldown"]:
                    await self.set_customgreet_last_time(guild_id, user_id, now)
                    greeting = cfg["message"].replace("{user}", message.author.mention)
                    try:
                        await message.reply(greeting, mention_author=False)
                    except Exception:
                        pass

        # Hi/Hello auto-reply
        if content in ("hi", "hello"):
            key = (guild_id, user_id)
            last = self.hi_cooldowns.get(key, 0)
            if now - last >= HI_COOLDOWN:
                self.hi_cooldowns[key] = now
                await message.reply("hi : D", mention_author=False)

        # Trigger system
        triggers = await self.get_triggers(guild_id)
        for trigger, data in triggers.items():
            if trigger in content:
                tkey = (guild_id, user_id)
                last_trigger = self.trigger_cooldowns.get(tkey, 0)
                if now - last_trigger >= TRIGGER_COOLDOWN:
                    self.trigger_cooldowns[tkey] = now
                    await self.send_trigger_response(message, data)
                break

        # AFK removal
        if await self.is_afk(user_id):
            prefixes = await self.bot.get_prefix(message)
            is_command = any(message.content.startswith(p) for p in prefixes)
            if not is_command:
                await self.remove_afk(user_id)
                embed = embed_base(
                    f"{EMOJIS["panda_wave"]} Welcome Back!",
                    "Your AFK status has been removed.",
                    WIN_COLOR
                )
                embed.set_footer(text=f"Welcome back, {message.author.display_name}!",
                                 icon_url=message.author.display_avatar.url)
                try:
                    await message.reply(embed=embed, mention_author=False)
                except Exception:
                    pass

        # AFK mention check
        for mentioned in message.mentions:
            if mentioned.id != user_id and await self.is_afk(mentioned.id):
                last_ping = self.afk_mention_cooldowns.get(mentioned.id, 0)
                if now - last_ping < AFK_MENTION_COOLDOWN:
                    continue
                self.afk_mention_cooldowns[mentioned.id] = now
                afk_data = await self.get_afk_data(mentioned.id)
                reason = afk_data["reason"]
                since = afk_data["timestamp"]
                embed = embed_base(
                    f"{EMOJIS["purple_zzz"]} User is AFK",
                    f"{mentioned.mention} is currently AFK.",
                    MAIN_COLOR
                )
                embed.add_field(name="Reason", value=reason, inline=False)
                if since:
                    embed.add_field(name="AFK Since", value=f"<t:{since}:R>", inline=True)
                embed.set_footer(text=f"Requested by {message.author}",
                                 icon_url=message.author.display_avatar.url)
                await message.reply(embed=embed, mention_author=False)
                break

    async def send_trigger_response(self, message, data):
        response_type = data["type"]
        response = data["response"]
        if response_type == "text":
            await message.reply(response, mention_author=False)
        elif response_type in ("image", "gif"):
            embed = discord.Embed().set_image(url=response)
            await message.reply(embed=embed, mention_author=False)

    # ─── Trigger DB helpers ───────────────────────────
    async def get_triggers(self, guild_id: int) -> dict:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT trigger, type, response FROM triggers WHERE guild_id = $1", guild_id)
            return {row["trigger"]: {"type": row["type"], "response": row["response"]} for row in rows}

    async def add_trigger_to_db(self, guild_id: int, trigger: str, type: str, response: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO triggers (guild_id, trigger, type, response) VALUES ($1, $2, $3, $4) "
                "ON CONFLICT (guild_id, trigger) DO UPDATE SET type = $3, response = $4",
                guild_id, trigger, type, response
            )

    async def remove_trigger_from_db(self, guild_id: int, trigger: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM triggers WHERE guild_id = $1 AND trigger = $2",
                guild_id, trigger
            )
            return result != "DELETE 0"

    async def clear_triggers_db(self, guild_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM triggers WHERE guild_id = $1", guild_id)

    # ─── AFK DB helpers ───────────────────────────────
    async def is_afk(self, user_id: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT 1 FROM afk WHERE user_id = $1", user_id)
            return row is not None

    async def get_afk_data(self, user_id: int) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT reason, timestamp FROM afk WHERE user_id = $1", user_id)
            return dict(row) if row else None

    async def set_afk(self, user_id: int, reason: str, timestamp: int):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO afk (user_id, reason, timestamp) VALUES ($1, $2, $3) "
                "ON CONFLICT (user_id) DO UPDATE SET reason = $2, timestamp = $3",
                user_id, reason, timestamp
            )

    async def remove_afk(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("DELETE FROM afk WHERE user_id = $1", user_id)

    # ─── AFK Command ──────────────────────────────────
    @commands.command(name="afk")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def afk(self, ctx, *, reason: str = "No reason provided."):
        now = int(time.time())
        await self.set_afk(ctx.author.id, reason, now)
        embed = embed_base(
            f"{EMOJIS["purple_zzz"]} AFK Set",
            f"{ctx.author.mention} is now AFK.",
            WIN_COLOR
        )
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Since", value=f"<t:{now}:R>", inline=True)
        await ctx.reply(embed=embed, mention_author=False)

    @afk.error
    async def afk_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            embed = embed_base(
                f"{EMOJIS["cooldown"]} Slow Down",
                f"Try again in {error.retry_after:.0f}s.",
                LOSS_COLOR
            )
            await ctx.send(embed=embed, delete_after=5)

    # ─── Catfish Command ──────────────────────────────
    @commands.command(name="catfish")
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def catfish(self, ctx, user: discord.Member = None):
        user = user or ctx.author
        avatar_url = user.display_avatar.replace(static_format="png", size=1024).url
        yandex_url = f"https://yandex.com/images/search?rpt=imageview&url={avatar_url}"
        google_url = f"https://images.google.com/searchbyimage?image_url={avatar_url}"
        tineye_url = f"https://tineye.com/search?url={avatar_url}"
        embed = discord.Embed(
            title=f"{EMOJIS["search"]} Catfish Check",
            description=(
                f"Reverse image search for **{user.display_name}**'s profile picture.\n\n"
                f"[ Yandex]({yandex_url})\n"
                f"[ Google]({google_url})\n"
                f"[ TinEye]({tineye_url})"
            ),
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_image(url=avatar_url)
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url
        )
        await ctx.reply(embed=embed, mention_author=False)

    @catfish.error
    async def catfish_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            embed = embed_base(
                f"{EMOJIS["cooldown"]} Slow Down",
                f"Try again in {error.retry_after:.0f}s.",
                LOSS_COLOR
            )
            await ctx.send(embed=embed, delete_after=5)
        elif isinstance(error, commands.BadArgument):
            embed = embed_base(f"{EMOJIS["cross"]} Error", "Member not found.", LOSS_COLOR)
            await ctx.send(embed=embed)

    # ─── Trigger Management Commands (elevated perms) ──
    @commands.group(name="triggers", invoke_without_command=True)
    @elevated_perms_check()
    async def triggers_group(self, ctx):
        prefix = ctx.clean_prefix
        embed = embed_base(f"{EMOJIS["lightning"]} Trigger System Help", "", MAIN_COLOR, ctx)
        embed.description = (
            "Manage auto‑response triggers that fire when a certain word or mention is detected.\n"
            f"All commands require **Owner, Admin, Mod, Trial Mod, or Event Manager** permissions.\n\n"
            f"**Prefix:** `{prefix}`"
        )
        embed.add_field(
            name=f"{EMOJIS["pin"]} Add a trigger",
            value=(
                f"`{prefix}triggers add <trigger> <type> <response>`\n"
                "**Types:** `text`, `image`, `gif`\n"
                "*Examples:*\n"
                f"`{prefix}triggers add hi text Hello there!`\n"
                f"`{prefix}triggers add @User image https://i.imgur.com/abc.png`\n"
                f"`{prefix}triggers add party gif https://media.giphy.com/xyz.gif`"
            ),
            inline=False
        )
        embed.add_field(
            name=f"{EMOJIS["cross"]} Remove a trigger",
            value=f"`{prefix}triggers remove <trigger>`",
            inline=False
        )
        embed.add_field(
            name=f"{EMOJIS["scroll"]} List all triggers",
            value=f"`{prefix}triggers list`",
            inline=False
        )
        embed.add_field(
            name=f"{EMOJIS["broom"]} Clear all triggers",
            value=f"`{prefix}triggers clear`",
            inline=False
        )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @triggers_group.command(name="add")
    @elevated_perms_check()
    async def add_trigger(self, ctx, trigger: str, response_type: str, *, response: str):
        valid_types = ("text", "image", "gif")
        response_type = response_type.lower()
        if response_type not in valid_types:
            embed = embed_base(
                f"{EMOJIS["cross"]} Invalid Type",
                f"Please use one of: {', '.join(valid_types)}",
                LOSS_COLOR, ctx
            )
            return await ctx.send(embed=embed)
        trigger = trigger.lower()
        await self.add_trigger_to_db(ctx.guild.id, trigger, response_type, response)
        embed = embed_base(
            f"{EMOJIS["tick"]} Trigger Added",
            f"**{trigger}** → *{response_type}*\nResponse: {response}",
            WIN_COLOR, ctx
        )
        await ctx.send(embed=embed)

    @triggers_group.command(name="remove")
    @elevated_perms_check()
    async def remove_trigger(self, ctx, *, trigger: str):
        trigger = trigger.lower()
        success = await self.remove_trigger_from_db(ctx.guild.id, trigger)
        if success:
            embed = embed_base(f"{EMOJIS["delete"]} Trigger Removed", f"`{trigger}` has been deleted.", WIN_COLOR, ctx)
        else:
            embed = embed_base(f"{EMOJIS["cross"]} Not Found", f"No trigger named `{trigger}`.", LOSS_COLOR, ctx)
        await ctx.send(embed=embed)

    @triggers_group.command(name="list")
    @elevated_perms_check()
    async def list_triggers(self, ctx):
        triggers = await self.get_triggers(ctx.guild.id)
        if not triggers:
            embed = embed_base(f"{EMOJIS["mail"]} No Triggers", "There are no triggers set up yet.", MAIN_COLOR, ctx)
            return await ctx.send(embed=embed)
        embed = embed_base(f"{EMOJIS["scroll"]} Active Triggers", "", MAIN_COLOR, ctx)
        for trigger, data in triggers.items():
            value = f"**Type:** {data['type']}\n**Response:** {data['response']}"
            embed.add_field(name=trigger, value=value, inline=False)
        await ctx.send(embed=embed)

    @triggers_group.command(name="clear")
    @elevated_perms_check()
    async def clear_triggers(self, ctx):
        await self.clear_triggers_db(ctx.guild.id)
        embed = embed_base(f"{EMOJIS["broom"]} Triggers Cleared", "All triggers have been removed.", WIN_COLOR, ctx)
        await ctx.send(embed=embed)

    # ─── Error handling ───────────────────────────────
    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            embed = embed_base(f"{EMOJIS["cross"]} Invalid Argument", "The argument you provided is not valid. Please check the command usage.", LOSS_COLOR, ctx)
            await ctx.send(embed=embed)
        elif isinstance(error, commands.MissingRequiredArgument):
            embed = embed_base(f"{EMOJIS["warning"]} Missing Argument", "You are missing a required argument. Check the command syntax.", LOSS_COLOR, ctx)
            await ctx.send(embed=embed)
        elif isinstance(error, commands.CheckFailure):
            embed = embed_base(f"{EMOJIS["cross"]} Permission Denied", "You do not have permission to use this command.", LOSS_COLOR, ctx)
            await ctx.send(embed=embed)
        elif isinstance(error, commands.CommandOnCooldown):
            retry_after = error.retry_after
            embed = embed_base(f"{EMOJIS["cooldown"]} Cooldown", f"Please wait {retry_after:.0f} seconds before using this command again.", LOSS_COLOR, ctx)
            await ctx.send(embed=embed, delete_after=5)
        else:
            print(f"Ignoring exception in command {ctx.command}: {error}", flush=True)

async def setup(bot):
    await bot.add_cog(Extra(bot))
