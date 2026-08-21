import discord
import json
import math
import os
import sys
import time
import datetime
import aiohttp
import asyncpg
from collections import defaultdict, deque
from dotenv import load_dotenv
from discord.ext import commands
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv()

console = Console()

DEFAULT_CONFIG = {
    "prefix": "$",
    "owner_id": 1327528116079296594,
    "guild_id": 1535960575341756527,
    "allowed_channel_id": 1535962338048475267,
    "staff_role_ids": [],
    "activity": "Watching over Nuvella",
    "activity_url": None,
    "sync_commands": False,
    "application_id": 1537342404099571762,
}


def load_bot_config():
    config = DEFAULT_CONFIG.copy()

    file_config = {}
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r") as f:
                file_config = json.load(f)
                config.update(file_config)
        except Exception as e:
            console.print_exception(show_locals=True)
            console.print("[yellow]Warning: Failed to load config.json, using defaults/environment variables.[/yellow]")

    if os.getenv("PREFIX"):
        config["prefix"] = os.getenv("PREFIX")
    if os.getenv("OWNER_ID"):
        config["owner_id"] = int(os.getenv("OWNER_ID"))
    if os.getenv("GUILD_ID"):
        config["guild_id"] = int(os.getenv("GUILD_ID"))
    if os.getenv("ALLOWED_CHANNEL_ID"):
        config["allowed_channel_id"] = int(os.getenv("ALLOWED_CHANNEL_ID"))
    if os.getenv("SYNC_COMMANDS"):
        config["sync_commands"] = os.getenv("SYNC_COMMANDS").lower() == "true"
    if os.getenv("APPLICATION_ID"):
        config["application_id"] = int(os.getenv("APPLICATION_ID"))
    if os.getenv("ACTIVITY"):
        config["activity"] = os.getenv("ACTIVITY")
    if os.getenv("ACTIVITY_URL"):
        config["activity_url"] = os.getenv("ACTIVITY_URL")

    staff_env = os.getenv("STAFF_ROLE_IDS")
    if staff_env:
        config["staff_role_ids"] = [int(x.strip()) for x in staff_env.split(",") if x.strip()]
    else:
        roles = file_config.get("roles", {})
        staff_keys = [
            "admin_role_id",
            "senior_moderator_role_id",
            "moderator_role_id",
            "trial_moderator_role_id",
            "developer_role_id",
            "event_manager_role_id",
            "trial_event_manager_role_id",
        ]
        staff_ids = []
        for key in staff_keys:
            if key in roles:
                try:
                    staff_ids.append(int(roles[key]))
                except (ValueError, TypeError) as e:
                    console.print(f"[yellow]Invalid role ID for '{key}': {roles[key]} (skipped)[/yellow]")
        config["staff_role_ids"] = staff_ids

    return config


def get_effective_token():
    return os.getenv("TOKEN")


config = load_bot_config()

TOKEN = get_effective_token()
PREFIX = config.get("prefix", "$")
OWNER_ID = config.get("owner_id")
GUILD_ID = int(config.get("guild_id") or 0) or None

MAIN_COLOR = 0x6A0DAD
WIN_COLOR = 0x57F287
LOSS_COLOR = 0xED4245
BREAKER = "<:breaker:1537913026643238932>"
BREAKER_LINE = BREAKER * 10

ALLOWED_CHANNEL_ID = int(config.get("allowed_channel_id") or 0) or None
STAFF_ROLE_IDS = set(int(role_id) for role_id in config.get("staff_role_ids", []) if role_id)

STARTUP_ISSUES = []
if not OWNER_ID:
    STARTUP_ISSUES.append("owner_id is not set in config.json / OWNER_ID env")
if not GUILD_ID:
    STARTUP_ISSUES.append("guild_id is not set, slash command sync will be global if enabled")
if not ALLOWED_CHANNEL_ID:
    STARTUP_ISSUES.append("allowed_channel_id is not set, prefix commands are unrestricted")
if not STAFF_ROLE_IDS:
    STARTUP_ISSUES.append("no staff_role_ids configured")
if not config.get("sync_commands", False):
    STARTUP_ISSUES.append("sync_commands is disabled, slash commands will not be synced")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

_cooldowns = {}
COOLDOWN_TIME = 5
_channel_warned = set()

RATE_LIMIT_MAX_COMMANDS = 15
RATE_LIMIT_WINDOW = 60
_command_attempts = defaultdict(lambda: deque(maxlen=RATE_LIMIT_MAX_COMMANDS + 1))

DATABASE_URL = os.getenv("DATABASE_URL")


def is_staff(member: discord.Member) -> bool:
    if not member or not getattr(member, "guild", None):
        return False

    if member.id == member.guild.owner_id or member.id == OWNER_ID:
        return True

    if STAFF_ROLE_IDS and any(role.id in STAFF_ROLE_IDS for role in member.roles):
        return True

    staff_file = "data/staff.json"
    if not os.path.exists(staff_file):
        return False

    try:
        with open(staff_file) as handle:
            staff_data = json.load(handle)
    except Exception as e:
        console.print_exception(show_locals=True)
        console.print("[red]Failed to read data/staff.json[/red]")
        return False

    uid = str(member.id)
    for values in staff_data.values():
        if isinstance(values, list) and uid in values:
            return True
    return False


def is_owner(user: discord.abc.User, guild: discord.Guild = None) -> bool:
    if user.id == OWNER_ID:
        return True
    if guild and user.id == guild.owner_id:
        return True
    return False


def load_np_users():
    """Load users who can use commands without the prefix."""
    path = os.path.join("data", "np_users.json")
    os.makedirs("data", exist_ok=True)

    if not os.path.exists(path):
        with open(path, "w") as f:
            json.dump({"users": []}, f, indent=2)
        return set()

    try:
        with open(path, "r") as f:
            data = json.load(f)
        return {int(uid) for uid in data.get("users", [])}
    except Exception as e:
        console.print_exception(show_locals=True)
        console.print("[red]Failed to load np_users.json, returning empty set.[/red]")
        return set()


def save_np_users(users_set):
    """Save the set of no-prefix user IDs to file."""
    path = os.path.join("data", "np_users.json")
    os.makedirs("data", exist_ok=True)

    try:
        with open(path, "w") as f:
            json.dump({"users": sorted(list(users_set))}, f, indent=2)
        return True
    except Exception as e:
        console.print_exception(show_locals=True)
        console.print("[red]Failed to save np_users.json[/red]")
        return False


def error_embed(ctx, title, desc):
    author = ctx.user if isinstance(ctx, discord.Interaction) else ctx.author
    embed = discord.Embed(
        title=title,
        description=f"{desc}\n{BREAKER_LINE}",
        color=LOSS_COLOR,
        timestamp=discord.utils.utcnow()
    )
    if ctx.guild:
        embed.set_author(name=ctx.guild.name,
                         icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
    embed.set_footer(text=f"Requested by {author}", icon_url=author.display_avatar.url)
    return embed


def build_command_candidates(bot: commands.Bot):
    """Build a list of possible full command names (including aliases) for prefix commands."""
    candidates = set()

    for cmd in bot.walk_commands():
        if cmd.hidden:
            continue

        # Build full name chain with real names/aliases at each level
        parts = []
        parent = cmd
        while parent is not None:
            parts.append((parent.name.lower(), [a.lower() for a in parent.aliases]))
            parent = parent.parent
        parts.reverse()

        # Generate all possible command name combinations
        combos = [""]
        for name, aliases in parts:
            names = [name] + aliases
            combos = [
                f"{prefix} {n}".strip() if prefix else n
                for prefix in combos
                for n in names
            ]

        candidates.update(combos)

    return sorted(candidates, key=len, reverse=True)


class Nuvella(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=PREFIX,
            intents=intents,
            help_command=None,
            case_insensitive=True,
            application_id=config.get("application_id")
        )
        self.config = config
        self.start_time = datetime.datetime.now(datetime.timezone.utc)
        self.launch_time = time.perf_counter()
        self.http_session = None
        self.pool = None
        self.startup_printed = False
        self.synced_commands = 0

    async def setup_hook(self):
        self.owner_id = OWNER_ID
        self.http_session = aiohttp.ClientSession()

        self.pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=5)
        async with self.pool.acquire() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS blacklist (user_id BIGINT PRIMARY KEY, reason TEXT)"
            )

        for file in os.listdir("./cogs"):
            if file.endswith(".py"):
                await self.load_extension(f"cogs.{file[:-3]}")

        if config.get("sync_commands", False):
            if GUILD_ID:
                guild = discord.Object(id=GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
            else:
                synced = await self.tree.sync()
            self.synced_commands = len(synced)

    async def on_ready(self):
        self.uptime = time.time()
        activity_name = config.get("activity", "Nuvella")
        activity_url = config.get("activity_url")
        if activity_url:
            activity = discord.Streaming(name=activity_name, url=activity_url)
        else:
            activity = discord.Activity(type=discord.ActivityType.playing, name=activity_name)
        await self.change_presence(activity=activity)

        if not self.startup_printed:
            self.startup_printed = True
            elapsed = time.perf_counter() - self.launch_time
            latency_ms = 0 if math.isnan(self.latency) else round(self.latency * 1000)

            table = Table.grid(padding=(0, 2))
            table.add_column(style="bold cyan", justify="right")
            table.add_column()
            table.add_row("Bot", f"{self.user} ({self.user.id})")
            table.add_row("Cogs Loaded", str(len(self.cogs)))
            table.add_row("Commands Synced", str(self.synced_commands))
            table.add_row("Startup Time", f"{elapsed:.2f}s")
            table.add_row("Latency", f"{latency_ms}ms")
            table.add_row("Issues", "\n".join(STARTUP_ISSUES) if STARTUP_ISSUES else "None")

            console.print(Panel(table, title="Nuvella Online", border_style="magenta", expand=False))

    async def close(self):
        if self.http_session:
            await self.http_session.close()
        if self.pool:
            await self.pool.close()
        await super().close()

    async def is_blacklisted(self, uid: int) -> bool:
        async with self.pool.acquire() as conn:
            row = await conn.fetchval("SELECT 1 FROM blacklist WHERE user_id=$1", uid)
            return row is not None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return True

        user = interaction.user
        member = interaction.guild.get_member(user.id)
        if member and is_staff(member):
            return True

        now = time.time()
        dq = _command_attempts[user.id]
        dq.append(now)

        while dq and now - dq[0] > RATE_LIMIT_WINDOW:
            dq.popleft()

        if len(dq) > RATE_LIMIT_MAX_COMMANDS:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO blacklist (user_id, reason) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
                    user.id, "Auto-blacklisted for command spam"
                )
            dq.clear()
            await interaction.response.send_message(
                embed=error_embed(interaction, "Blacklisted",
                                  "You have been blacklisted for spamming commands."),
                ephemeral=True
            )
            return False

        if await self.is_blacklisted(user.id):
            await interaction.response.send_message(
                embed=error_embed(interaction, "Error",
                                  "You are blacklisted from using the bot."),
                ephemeral=True
            )
            return False

        if user.id in _cooldowns and now - _cooldowns[user.id] < COOLDOWN_TIME:
            remaining = COOLDOWN_TIME - (now - _cooldowns[user.id])
            await interaction.response.send_message(
                embed=error_embed(interaction, "Cooldown",
                                  f"Please wait `{int(remaining)}s` before using another command."),
                ephemeral=True
            )
            return False
        _cooldowns[user.id] = now
        return True

    async def on_message(self, message):
        if message.author.bot or not message.guild:
            await self.process_commands(message)
            return

        original = message.content
        np_users = load_np_users()

        # No-prefix handling: if user is in np_users and message starts with a command name,
        # add the prefix so it gets processed normally.
        if message.author.id in np_users:
            content_lower = original.lower().strip()
            candidates = build_command_candidates(self)

            for name in candidates:
                if content_lower == name or content_lower.startswith(name + " "):
                    # Only add prefix if not already present
                    if not original.lower().startswith(PREFIX.lower()):
                        message.content = PREFIX + original
                    break

        # If message still doesn't start with prefix, ignore it
        if not PREFIX or not message.content.lower().startswith(PREFIX.lower()):
            return

        # Staff bypass all restrictions
        if is_staff(message.author):
            await self.process_commands(message)
            return

        # Enforce allowed channel for non‑staff
        if ALLOWED_CHANNEL_ID and message.channel.id != ALLOWED_CHANNEL_ID:
            try:
                await message.delete()
            except Exception as e:
                console.print_exception(show_locals=True)
                console.print("[red]Failed to delete message in wrong channel.[/red]")
            if ALLOWED_CHANNEL_ID and message.author.id not in _channel_warned:
                _channel_warned.add(message.author.id)
                try:
                    embed = error_embed(message, "Wrong Channel",
                                        f"Commands can only be used in <#{ALLOWED_CHANNEL_ID}>.")
                    await message.channel.send(embed=embed, delete_after=10)
                except Exception as e:
                    console.print_exception(show_locals=True)
                    console.print("[red]Failed to send channel warning.[/red]")
            return

        await self.process_commands(message)


bot = Nuvella()


@bot.check
async def global_prefix_check(ctx):
    if not ctx.guild:
        return True

    if is_staff(ctx.author):
        return True

    now = time.time()
    dq = _command_attempts[ctx.author.id]
    dq.append(now)

    while dq and now - dq[0] > RATE_LIMIT_WINDOW:
        dq.popleft()

    if len(dq) > RATE_LIMIT_MAX_COMMANDS:
        async with bot.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO blacklist (user_id, reason) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
                ctx.author.id, "Auto-blacklisted for command spam"
            )
        dq.clear()
        raise commands.CheckFailure("You have been blacklisted for spamming commands.")

    if await bot.is_blacklisted(ctx.author.id):
        raise commands.CheckFailure("You are blacklisted from using the bot.")

    if ALLOWED_CHANNEL_ID and ctx.channel.id != ALLOWED_CHANNEL_ID:
        raise commands.CheckFailure(f"Commands can only be used in <#{ALLOWED_CHANNEL_ID}>.")

    if ctx.author.id in _cooldowns and now - _cooldowns[ctx.author.id] < COOLDOWN_TIME:
        remaining = COOLDOWN_TIME - (now - _cooldowns[ctx.author.id])
        raise commands.CommandOnCooldown(
            commands.Cooldown(1, COOLDOWN_TIME), remaining, commands.BucketType.user
        )
    _cooldowns[ctx.author.id] = now
    return True


@bot.command(name="amiblacklisted")
async def amiblacklisted(ctx):
    blacklisted = await bot.is_blacklisted(ctx.author.id)
    if blacklisted:
        await ctx.send("🔴 You **ARE** blacklisted. (But if you're staff, you're exempt.)")
    else:
        await ctx.send("🟢 You are **NOT** blacklisted.")


@bot.group(name="np", invoke_without_command=True)
async def np_group(ctx):
    """Manage no-prefix users."""
    if not is_staff(ctx.author):
        embed = error_embed(ctx, "Denied", "Only staff members can manage no-prefix users.")
        await ctx.send(embed=embed)
        return

    users = load_np_users()
    if users:
        user_list = "\n".join([f"• <@{uid}> (`{uid}`)" for uid in sorted(users)])
    else:
        user_list = "No users have no-prefix access."

    embed = discord.Embed(
        title="No-Prefix Users",
        description=f"{user_list}\n{BREAKER_LINE}",
        color=MAIN_COLOR,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)


@np_group.command(name="add")
async def np_add(ctx, user: discord.User = None):
    """Add a user to the no-prefix list."""
    if not is_staff(ctx.author):
        embed = error_embed(ctx, "Denied", "Only staff members can manage no-prefix users.")
        await ctx.send(embed=embed)
        return

    if user is None:
        embed = error_embed(ctx, "Error", "Please specify a user to add. Usage: `$np add @user`")
        await ctx.send(embed=embed)
        return

    users = load_np_users()
    if user.id in users:
        embed = discord.Embed(
            title="Already Added",
            description=f"{user.mention} already has no-prefix access.\n{BREAKER_LINE}",
            color=WIN_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)
        return

    users.add(user.id)
    if save_np_users(users):
        embed = discord.Embed(
            title="No-Prefix Access Granted",
            description=f"{user.mention} can now use commands without the prefix.\n{BREAKER_LINE}",
            color=WIN_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)
    else:
        embed = error_embed(ctx, "Error", "Failed to save no-prefix users.")
        await ctx.send(embed=embed)


@np_group.command(name="remove")
async def np_remove(ctx, user: discord.User = None):
    """Remove a user from the no-prefix list."""
    if not is_staff(ctx.author):
        embed = error_embed(ctx, "Denied", "Only staff members can manage no-prefix users.")
        await ctx.send(embed=embed)
        return

    if user is None:
        embed = error_embed(ctx, "Error", "Please specify a user to remove. Usage: `$np remove @user`")
        await ctx.send(embed=embed)
        return

    users = load_np_users()
    if user.id not in users:
        embed = discord.Embed(
            title="Not Found",
            description=f"{user.mention} does not have no-prefix access.\n{BREAKER_LINE}",
            color=LOSS_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)
        return

    users.remove(user.id)
    if save_np_users(users):
        embed = discord.Embed(
            title="No-Prefix Access Revoked",
            description=f"{user.mention} can no longer use commands without the prefix.\n{BREAKER_LINE}",
            color=WIN_COLOR,
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)
    else:
        embed = error_embed(ctx, "Error", "Failed to save no-prefix users.")
        await ctx.send(embed=embed)


@bot.tree.command(name="shutdown", description="Shut down the bot.")
async def shutdown_command(interaction: discord.Interaction):
    if not is_owner(interaction.user, interaction.guild):
        await interaction.response.send_message(
            embed=error_embed(interaction, "Denied", "Only the bot owner can use this command."),
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="Shutting Down",
        description=f"Nuvella is shutting down.\n{BREAKER_LINE}",
        color=MAIN_COLOR,
        timestamp=discord.utils.utcnow()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    console.print(Panel(f"Shutdown requested by {interaction.user} ({interaction.user.id})", border_style="red", expand=False))
    await bot.close()
    sys.exit(0)


@bot.tree.command(name="restart", description="Restart the bot.")
async def restart_command(interaction: discord.Interaction):
    if not is_owner(interaction.user, interaction.guild):
        await interaction.response.send_message(
            embed=error_embed(interaction, "Denied", "Only the bot owner can use this command."),
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="Restarting",
        description=f"Nuvella is restarting.\n{BREAKER_LINE}",
        color=MAIN_COLOR,
        timestamp=discord.utils.utcnow()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    console.print(Panel(f"Restart requested by {interaction.user} ({interaction.user.id})", border_style="yellow", expand=False))
    await bot.close()
    os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("No bot token found. Set TOKEN in your environment or .env.")
    if not DATABASE_URL:
        raise SystemExit("DATABASE_URL is not set in .env")
    bot.run(TOKEN)