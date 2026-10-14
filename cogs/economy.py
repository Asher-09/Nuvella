import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncpg
import random
import time
import asyncio
import os
import json
import logging
import re
from typing import Optional
from collections import defaultdict
from dotenv import load_dotenv

from utils.emoji import EMOJIS
from utils.components import ComponentsV2View, prepare_v2_view, disable_all_components
from utils.database import run_transaction

log = logging.getLogger("nuvella.economy")

load_dotenv()

# ─── CONFIGURATION ─────────────────────────────────────────────
CONFIG_FILE = "config.json"


def load_bot_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


CONFIG = load_bot_config()
LOGGING_CONFIG = CONFIG.get("logging", {}) or {}
ECONOMY_CONFIG = CONFIG.get("economy", {}) or {}
ROLES_CONFIG = CONFIG.get("roles", {}) or {}
PREFIX = str(CONFIG.get("prefix", "$"))

DISCORD_LOG_CHANNEL_ID = int(CONFIG.get("log_channel_id") or 0) or None
ALLOWED_CHANNEL_ID = int(CONFIG.get("allowed_channel_id") or 0) or None

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise RuntimeError("DATABASE_URL not set in .env")

GAME_COOLDOWN = 10
LONG_COOLDOWN = 600
cooldowns = {}

BOOSTER_ROLE_ID = int(ROLES_CONFIG.get("booster_role_id") or 0)
TOP_ROLE_ID = int(ROLES_CONFIG.get("top_role_id") or 0)
ADMIN_ROLE_ID = int(ROLES_CONFIG.get("admin_role_id") or 0)

# ─── UNIFORM DISCORD GREY THEME ────────────────────────────────
EMBED_COLOR = 0x2F3136
MAIN_COLOR = EMBED_COLOR
WIN_COLOR = EMBED_COLOR
LOSS_COLOR = EMBED_COLOR
SPIN_COLOR = EMBED_COLOR
GOLD_COLOR = EMBED_COLOR

CURRENCY = "Moon Dust"
BASE_CHANCE_BOOST = 0.07

# ─── EMOJI VARIABLES ───────────────────────────────────────────
NUM_1 = EMOJIS["number_1"]
NUM_2 = EMOJIS["number_2"]
NUM_3 = EMOJIS["number_3"]
CARDS = EMOJIS["playing_cards"]
SLOT_SEVEN = EMOJIS["slot_seven"]
WHITE_SQUARE = EMOJIS["white_square"]

BREAKER_LINE = EMOJIS["breaker"] * 10

# Read-only commands: skip logging & broke-purge for these.
READ_ONLY_COMMANDS = frozenset({
    "balance", "bal",
    "profile", "prof",
    "leaderboard", "lb", "top",
    "inv", "shop",
    "registeredusers", "totalusers", "regcount",
    "interestrate", "ir",
    "economy",
})

GAMBLING_COMMANDS_LIST = (
    "**Earning**\n"
    f"{EMOJIS['gift']} `$daily` — claim a daily reward\n"
    f"{EMOJIS['pouch']} `$collect` — claim role-based daily reward\n"
    f"{EMOJIS['backpack']} `$work` — work for Moon Dust\n"
    f"{EMOJIS['beg']} `$beg` — beg for a small chance of Moon Dust\n"
    f"{EMOJIS['fishes']} `$fish <bet>` — go fishing\n\n"
    "**Gambling**\n"
    f"{EMOJIS['moon_dust']} `$coinflip <bet>` — 50/50 coin flip\n"
    f"{EMOJIS['roulette']} `$roulette <bet> [number]` — roulette table\n"
    f"{CARDS} `$blackjack <bet>` — play the dealer\n"
    f"{EMOJIS['slot_machine']} `$slots <bet>` — spin the slot machine\n"
    f"{EMOJIS['spin_wheel']} `$spin <bet>` — spin the wheel\n"
    f"{EMOJIS['dices']} `$dice <bet> <guess>` — guess the roll\n"
    f"{CARDS} `$highlow <bet>` — higher or lower\n\n"
    "**Other**\n"
    f"{EMOJIS['pouch']} `$balance` — check your wallet/bank\n"
    f"{EMOJIS['deposit']} `$deposit` / `$withdraw` — move Moon Dust to/from your bank\n"
    f"{EMOJIS['shopping_cart']} `$shop` — open the shop\n"
    f"{EMOJIS['detective']} `$rob` / `$heist` — try to steal Moon Dust\n"
    f"{EMOJIS['gift']} `$transfer` — send Moon Dust to another player"
)


class ErrorEmbed(discord.Embed):
    """Embed that the patched ctx.send auto-deletes after 5 seconds."""
    pass


# ─── UTILITY FUNCTIONS ─────────────────────────────────────────
def get_owner_id():
    if not os.path.exists(CONFIG_FILE):
        return None
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
    return config.get("owner_id")


async def log_to_discord(bot, message: str):
    if not DISCORD_LOG_CHANNEL_ID:
        return
    try:
        channel = bot.get_channel(DISCORD_LOG_CHANNEL_ID)
        if channel:
            if len(message) <= 2000:
                await channel.send(message)
            else:
                for i in range(0, len(message), 2000):
                    await channel.send(message[i:i + 2000])
    except Exception as e:
        log.exception("Failed to send economy log message to Discord")


async def log_embed_to_discord(bot, embed: discord.Embed):
    if not DISCORD_LOG_CHANNEL_ID:
        return
    try:
        channel = bot.get_channel(DISCORD_LOG_CHANNEL_ID)
        if channel:
            await channel.send(embed=embed)
    except Exception as e:
        log.exception("Failed to send economy log embed to Discord")


# ─── DEFAULT SHOP ITEMS ────────────────────────────────────────
DEFAULT_SHOP_ITEMS = {
    "watchdog": {"price": 10000, "type": "consumable",
                 "description": "Consumable: protects you from one robbery."},
    "custom_role": {"price": 10_000_000, "type": "consumable",
                    "description": "One‑time purchase: grants you the `Custom Role` role for 2 months."},
    "afk_permission": {"price": 100_000, "type": "permanent_role",
                       "description": "Gives you the AFK role permanently."},
    "ghost_ping_viewer": {"price": 500_000, "type": "permanent_role",
                          "description": "Gives you the Ghost Ping role permanently."},
    "tic_tac_toe": {"price": 75_000, "type": "permanent_role",
                    "description": "Gives you the Tic Tac Toe role permanently."},
    "rock_paper_scissors": {"price": 75_000, "type": "permanent_role",
                            "description": "Gives you the Rock Paper Scissors role permanently."},
    "eight_ball": {"price": 75_000, "type": "permanent_role",
                   "description": "Gives you the 8ball role permanently."},
    "mafia_ping": {"price": 75_000, "type": "permanent_role",
                   "description": "Gives you the Mafia Ping role permanently."},
    "roulette_ping": {"price": 75_000, "type": "permanent_role",
                      "description": "Gives you the Roulette Ping role permanently."},
}

SHOP_EMOJI_MAP = {
    "watchdog": "red_shield",
    "custom_role": "owner",
    "afk_permission": "sleeping",
    "ghost_ping_viewer": "ghost",
    "tic_tac_toe": "tic_tac_toe",
    "rock_paper_scissors": "scissors",
    "eight_ball": "black_8ball",
    "mafia_ping": "gun",
    "roulette_ping": "slot_machine",
}

SHOP_DISPLAY_NAMES = {
    "watchdog": "WatchDog",
    "custom_role": "Custom Role",
    "afk_permission": "AFK Permission",
    "ghost_ping_viewer": "Ghost Ping Viewer",
    "tic_tac_toe": "Tic Tac Toe",
    "rock_paper_scissors": "Rock Paper Scissors",
    "eight_ball": "Eight Ball",
    "mafia_ping": "Mafia Ping",
    "roulette_ping": "Roulette Ping",
}


def parse_amount_str(s: str) -> int:
    s = s.lower().replace(",", "").strip()
    if s == "all":
        return 0
    match = re.match(r"^([\d.]+)([kmb]?)$", s)
    if not match:
        raise ValueError("Invalid amount format")
    num = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    elif suffix == "b":
        num *= 1_000_000_000
    return int(num)


def format_coins(amount: int) -> str:
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1_000_000_000:
        return f"{sign}{amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"{sign}{amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"{sign}{amount / 1_000:.1f}k"
    return f"{sign}{amount}"


def base_embed(ctx, title: str, color=EMBED_COLOR, user: discord.User = None):
    embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
    if ctx.guild:
        embed.set_author(name=ctx.guild.name,
                         icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
    if user:
        embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    embed.description = BREAKER_LINE
    return embed


def spin_embed(ctx, title: str, description: str, user=None):
    embed = base_embed(ctx, title, EMBED_COLOR, user=user or ctx.author)
    embed.description = f"{description}\n{BREAKER_LINE}"
    return embed


def result_embed(ctx, title, description, win=True, user=None):
    emoji = random.choice([EMOJIS["purple_star"], EMOJIS["confetti"], EMOJIS["star"]]) if win else EMOJIS["broken_heart"]
    embed = base_embed(ctx, f"{emoji} {title}", EMBED_COLOR, user=user or ctx.author)
    embed.description = f"{description}\n{BREAKER_LINE}"
    embed.set_footer(text=f"Outcome: {'Win' if win else 'Loss'} • {ctx.author}",
                     icon_url=ctx.author.display_avatar.url)
    return embed


def error_embed(ctx, title, description, user: discord.User = None):
    embed = ErrorEmbed(title=f"{EMOJIS['cross']} {title}", color=EMBED_COLOR,
                       timestamp=discord.utils.utcnow())
    if ctx.guild:
        embed.set_author(name=ctx.guild.name,
                         icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
    if user:
        embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    embed.description = f"{description}\n{BREAKER_LINE}"
    return embed


def cooldown_embed(ctx, remaining):
    embed = ErrorEmbed(title=f"{EMOJIS['cooldown']} Cooldown", color=EMBED_COLOR,
                       timestamp=discord.utils.utcnow())
    if ctx.guild:
        embed.set_author(name=ctx.guild.name,
                         icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    total_secs = max(1, int(remaining))
    bar_len = 10
    filled = int(min(total_secs / 60, 1) * bar_len)
    bar = "█" * max(1, filled) + "░" * (bar_len - max(1, filled))
    embed.description = (
        f"You're on cooldown — please wait before using this command again.\n{BREAKER_LINE}"
    )
    embed.add_field(name="Time left", value=f"`{total_secs}s`\n[{bar}]", inline=False)
    return embed


def result_embed_interaction(interaction: discord.Interaction, title, description, win=True):
    emoji = random.choice([EMOJIS["purple_star"], EMOJIS["confetti"], EMOJIS["star"]]) if win else EMOJIS["broken_heart"]
    embed = discord.Embed(title=f"{emoji} {title}", color=EMBED_COLOR,
                          timestamp=discord.utils.utcnow())
    if interaction.guild:
        embed.set_author(name=interaction.guild.name,
                         icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.set_footer(text=f"Outcome: {'Win' if win else 'Loss'} • {interaction.user}",
                     icon_url=interaction.user.display_avatar.url)
    embed.description = f"{description}\n{BREAKER_LINE}"
    return embed


def error_embed_interaction(interaction: discord.Interaction, title, description):
    embed = ErrorEmbed(title=f"{EMOJIS['cross']} {title}", color=EMBED_COLOR,
                       timestamp=discord.utils.utcnow())
    if interaction.guild:
        embed.set_author(name=interaction.guild.name,
                         icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.set_footer(text=f"Requested by {interaction.user}",
                     icon_url=interaction.user.display_avatar.url)
    embed.description = f"{description}\n{BREAKER_LINE}"
    return embed


# ─── PER-USER LOCKS ───────────────────────────────────────────
_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


def user_lock(uid: int) -> asyncio.Lock:
    return _user_locks[uid]


# ─── VIEWS ──────────────────────────────────────────────────────
class CoinFlipView(ComponentsV2View):
    def __init__(self, ctx, bet, cog):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.bet = bet
        self.cog = cog
        self.finished = False
        self.controls = discord.ui.ActionRow()
        heads_button = discord.ui.Button(label="Heads", style=discord.ButtonStyle.secondary)
        tails_button = discord.ui.Button(label="Tails", style=discord.ButtonStyle.secondary)
        heads_button.callback = self.heads
        tails_button.callback = self.tails
        self.controls.add_item(heads_button)
        self.controls.add_item(tails_button)
        self.add_item(self.controls)

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        wallet = await self.cog.get_wallet(self.ctx.author.id)
        embed = result_embed(self.ctx, f"{EMOJIS['cooldown']} Time's Up!",
                             f"You took too long and lost **{self.bet:,}** Moon Dust.", win=False)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        disable_all_components(self)
        if hasattr(self, 'message'):
            self.set_embed(embed)
            await self.message.edit(view=self)
        self.cog.active_games.discard(self.ctx.author.id)

    async def heads(self, interaction: discord.Interaction):
        await self.flip(interaction, "heads")

    async def tails(self, interaction: discord.Interaction):
        await self.flip(interaction, "tails")

    async def flip(self, interaction: discord.Interaction, choice: str):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        if self.finished:
            return
        self.finished = True
        disable_all_components(self)
        await interaction.response.edit_message(view=self)
        spin = spin_embed(self.ctx, f"{EMOJIS['moon_dust']} Coin Flip", "Flipping...", user=self.ctx.author)
        self.set_embed(spin)
        await interaction.edit_original_response(view=self)
        await asyncio.sleep(0.4)
        async with user_lock(self.ctx.author.id):
            p = 0.25 + BASE_CHANCE_BOOST
            if await self.cog.has_item_equipped(self.ctx.author.id, "horseshoe"):
                p += 0.05
            if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in self.ctx.author.roles):
                p += 0.03
            p = min(p, 0.95)
            outcome = choice if random.random() < p else ("tails" if choice == "heads" else "heads")
            win = outcome == choice
            if win:
                await self.cog.add_wallet(self.ctx.author.id, self.bet * 2)
            wallet = await self.cog.get_wallet(self.ctx.author.id)
        desc = (f"You chose **{choice}**\nResult: **{outcome}**\n"
                f"{'You doubled your Moon Dust!' if win else f'You lost {self.bet:,} Moon Dust'}")
        final_embed = result_embed(self.ctx, f"{EMOJIS['moon_dust']} Coin Flip", desc, win=win, user=self.ctx.author)
        final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        self.set_embed(final_embed)
        await interaction.edit_original_response(view=self)
        self.cog.active_games.discard(self.ctx.author.id)
        await self.cog.check_broke(self.ctx.author.id)


class RouletteView(ComponentsV2View):
    RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

    def __init__(self, ctx, bet, cog):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.bet = bet
        self.cog = cog
        self.finished = False
        self.CHOICES = {
            "0": {"label": "0", "description": "3x payout", "emoji": EMOJIS["green_circle"], "multiplier": 3},
            "1-12": {"label": "1-12", "description": "2x payout", "emoji": NUM_1, "multiplier": 2},
            "13-24": {"label": "13-24", "description": "2x payout", "emoji": NUM_2, "multiplier": 2},
            "25-36": {"label": "25-36", "description": "2x payout", "emoji": NUM_3, "multiplier": 2},
            "Red": {"label": "Red", "description": "2x payout", "emoji": EMOJIS["red_circle"], "multiplier": 2},
            "Black": {"label": "Black", "description": "2x payout", "emoji": EMOJIS["black_circle"], "multiplier": 2},
            "Odd": {"label": "Odd", "description": "2x payout", "emoji": EMOJIS["blue_circle"], "multiplier": 2},
            "Even": {"label": "Even", "description": "2x payout", "emoji": EMOJIS["purple_circle"], "multiplier": 2},
        }
        options = [discord.SelectOption(label=v["label"], description=v["description"], emoji=v["emoji"])
                   for v in self.CHOICES.values()]
        select = discord.ui.Select(placeholder="Choose your bet...", options=options)
        select.callback = self.spin_callback
        self.controls = discord.ui.ActionRow()
        self.controls.add_item(select)
        self.add_item(self.controls)

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        wallet = await self.cog.get_wallet(self.ctx.author.id)
        embed = result_embed(self.ctx, f"{EMOJIS['cooldown']} Time's Up!",
                             f"You took too long and lost **{self.bet:,}** Moon Dust.", win=False)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        disable_all_components(self)
        if hasattr(self, 'message'):
            self.set_embed(embed)
            await self.message.edit(view=self)
        self.cog.active_games.discard(self.ctx.author.id)

    async def spin_callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        if self.finished:
            return
        self.finished = True
        await interaction.response.defer()
        disable_all_components(self)
        spinning = spin_embed(self.ctx, f"{EMOJIS['roulette']} Roulette", "Spinning...", user=self.ctx.author)
        self.set_embed(spinning)
        await interaction.edit_original_response(view=self)
        choice = interaction.data["values"][0]
        multiplier = self.CHOICES[choice]["multiplier"]
        await asyncio.sleep(1.5)
        async with user_lock(self.ctx.author.id):
            p = (0.20 if choice == "0" else 0.30) + BASE_CHANCE_BOOST
            if await self.cog.has_item_equipped(self.ctx.author.id, "horseshoe"):
                p += 0.05
            if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in self.ctx.author.roles):
                p += 0.03
            p = min(p, 0.95)
            win = random.random() < p
            if win:
                if choice == "0":
                    number = 0
                elif choice == "Red":
                    number = random.choice(list(self.RED_NUMBERS))
                elif choice == "Black":
                    number = random.choice([n for n in range(1, 37) if n not in self.RED_NUMBERS])
                elif choice == "1-12":
                    number = random.randint(1, 12)
                elif choice == "13-24":
                    number = random.randint(13, 24)
                elif choice == "25-36":
                    number = random.randint(25, 36)
                elif choice == "Odd":
                    number = random.choice([n for n in range(1, 37) if n % 2 == 1])
                elif choice == "Even":
                    number = random.choice([n for n in range(1, 37) if n % 2 == 0])
                else:
                    number = random.randint(0, 36)
            else:
                if choice == "0":
                    number = random.randint(1, 36)
                elif choice == "Red":
                    number = random.choice([n for n in range(1, 37) if n not in self.RED_NUMBERS])
                elif choice == "Black":
                    number = random.choice(list(self.RED_NUMBERS))
                elif choice == "1-12":
                    number = random.randint(13, 36)
                elif choice == "13-24":
                    number = random.choice([random.randint(1, 12), random.randint(25, 36)])
                elif choice == "25-36":
                    number = random.randint(1, 24)
                elif choice == "Odd":
                    number = random.choice([n for n in range(1, 37) if n % 2 == 0])
                elif choice == "Even":
                    number = random.choice([n for n in range(1, 37) if n % 2 == 1])
                else:
                    number = random.randint(0, 36)
            if win:
                await self.cog.add_wallet(self.ctx.author.id, self.bet * multiplier)
            wallet = await self.cog.get_wallet(self.ctx.author.id)
        colour = "green" if number == 0 else ("red" if number in self.RED_NUMBERS else "black")
        colour_emoji = {"green": EMOJIS["green_circle"], "red": EMOJIS["red_circle"],
                        "black": EMOJIS["black_circle"]}[colour]
        outcome_text = f"Landed on **{number}** {colour_emoji}"
        desc = (f"{outcome_text}\nYour bet: **{choice}**\n"
                f"{'You won **' + format_coins(self.bet * multiplier) + '** Moon Dust!' if win else f'You lost **{format_coins(self.bet)}** Moon Dust'}")
        final_embed = result_embed(self.ctx, f"{EMOJIS['roulette']} Roulette", desc, win=win, user=self.ctx.author)
        final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        self.set_embed(final_embed)
        await interaction.edit_original_response(view=self)
        self.cog.active_games.discard(self.ctx.author.id)
        await self.cog.check_broke(self.ctx.author.id)


class BlackjackView(ComponentsV2View):
    def __init__(self, ctx, bet, cog):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.bet = bet
        self.cog = cog
        self.deck = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11] * 4
        random.shuffle(self.deck)
        while True:
            self.player_cards = [self.deck.pop(), self.deck.pop()]
            self.player_value = sum(self.player_cards)
            if 12 <= self.player_value <= 16:
                break
            self.deck.extend(self.player_cards)
            random.shuffle(self.deck)
        self.dealer_cards = [10, random.choice([2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 11])]
        self.dealer_value = sum(self.dealer_cards)
        self.finished = False
        self.controls = discord.ui.ActionRow()
        hit_button = discord.ui.Button(label="Hit", style=discord.ButtonStyle.secondary)
        stand_button = discord.ui.Button(label="Stand", style=discord.ButtonStyle.secondary)
        hit_button.callback = self.hit
        stand_button.callback = self.stand
        self.controls.add_item(hit_button)
        self.controls.add_item(stand_button)
        self.add_item(self.controls)

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        wallet = await self.cog.get_wallet(self.ctx.author.id)
        embed = result_embed(self.ctx, f"{EMOJIS['cooldown']} Time's Up!",
                             f"You took too long and lost **{self.bet:,}** Moon Dust.", win=False)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        disable_all_components(self)
        if hasattr(self, 'message'):
            self.set_embed(embed)
            await self.message.edit(view=self)
        self.cog.active_games.discard(self.ctx.author.id)

    def hand_str(self, cards):
        return " ".join(str(c) for c in cards)

    def build_embed(self, hide_dealer=True):
        dealer_show = self.dealer_cards[0] if hide_dealer else self.dealer_value
        embed = base_embed(self.ctx, f"{CARDS} Blackjack", EMBED_COLOR, user=self.ctx.author)
        embed.description = (
            f"**Dealer:** {dealer_show}{' ?' if hide_dealer else ''}\n"
            f"**You:** {self.hand_str(self.player_cards)} (={self.player_value})\n"
            f"{BREAKER_LINE}"
        )
        if self.finished:
            if self.player_value > 21:
                embed.description += "\n**Bust! You lost.**"
            elif self.dealer_value > 21 or self.player_value > self.dealer_value:
                embed.description += "\n**You win!**"
            elif self.player_value == self.dealer_value:
                embed.description += "\n**Push!** Bet returned."
            else:
                embed.description += "\n**Dealer wins.**"
        return embed

    async def hit(self, interaction: discord.Interaction):
        if self.finished:
            return await interaction.response.send_message("Game over.", ephemeral=True)
        self.player_cards.append(self.deck.pop())
        self.player_value = sum(self.player_cards)
        if self.player_value > 21:
            self.finished = True
            await self.end_game(interaction)
        else:
            embed = self.build_embed()
            self.set_embed(embed)
            await interaction.response.edit_message(view=self)

    async def stand(self, interaction: discord.Interaction):
        if self.finished:
            return await interaction.response.send_message("Game over.", ephemeral=True)
        while self.dealer_value < 17:
            self.dealer_cards.append(self.deck.pop())
            self.dealer_value = sum(self.dealer_cards)
        self.finished = True
        await self.end_game(interaction)

    async def end_game(self, interaction):
        disable_all_components(self)
        embed = self.build_embed(hide_dealer=False)
        if self.player_value > 21:
            pass
        elif self.dealer_value > 21 or self.player_value > self.dealer_value:
            async with user_lock(self.ctx.author.id):
                await self.cog.add_wallet(self.ctx.author.id, self.bet * 2)
        elif self.player_value == self.dealer_value:
            async with user_lock(self.ctx.author.id):
                await self.cog.add_wallet(self.ctx.author.id, self.bet)
        else:
            pass
        async with user_lock(self.ctx.author.id):
            wallet = await self.cog.get_wallet(self.ctx.author.id)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        self.set_embed(embed)
        await interaction.response.edit_message(view=self)
        self.cog.active_games.discard(self.ctx.author.id)
        await self.cog.check_broke(self.ctx.author.id)


class HighLowView(ComponentsV2View):
    def __init__(self, ctx, bet, first_card, cog):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.bet = bet
        self.first_card = first_card
        self.cog = cog
        self.finished = False
        self.controls = discord.ui.ActionRow()
        higher_button = discord.ui.Button(label="Higher", style=discord.ButtonStyle.secondary)
        lower_button = discord.ui.Button(label="Lower", style=discord.ButtonStyle.secondary)
        higher_button.callback = self.higher
        lower_button.callback = self.lower
        self.controls.add_item(higher_button)
        self.controls.add_item(lower_button)
        self.add_item(self.controls)

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        wallet = await self.cog.get_wallet(self.ctx.author.id)
        embed = result_embed(self.ctx, f"{EMOJIS['cooldown']} Time's Up!",
                             f"You took too long and lost **{self.bet:,}** Moon Dust.", win=False)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        disable_all_components(self)
        if hasattr(self, 'message'):
            self.set_embed(embed)
            await self.message.edit(view=self)
        self.cog.active_games.discard(self.ctx.author.id)

    async def higher(self, interaction: discord.Interaction):
        await self.play(interaction, "higher")

    async def lower(self, interaction: discord.Interaction):
        await self.play(interaction, "lower")

    async def play(self, interaction: discord.Interaction, choice: str):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        if self.finished:
            return
        self.finished = True
        disable_all_components(self)
        await interaction.response.edit_message(view=self)
        async with user_lock(self.ctx.author.id):
            p = 0.25 + BASE_CHANCE_BOOST
            if await self.cog.has_item_equipped(self.ctx.author.id, "horseshoe"):
                p += 0.05
            if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in self.ctx.author.roles):
                p += 0.03
            p = min(p, 0.95)
            if random.random() < p:
                if choice == "higher":
                    next_card = self.first_card + 1 if self.first_card < 14 else self.first_card - 1
                else:
                    next_card = self.first_card - 1 if self.first_card > 2 else self.first_card + 1
            else:
                if choice == "higher":
                    next_card = self.first_card - 1 if self.first_card > 2 else self.first_card + 1
                else:
                    next_card = self.first_card + 1 if self.first_card < 14 else self.first_card - 1
            if next_card == self.first_card:
                next_card = self.first_card + 1 if self.first_card < 14 else self.first_card - 1
            win = (choice == "higher" and next_card > self.first_card) or (
                    choice == "lower" and next_card < self.first_card)
            if win:
                await self.cog.add_wallet(self.ctx.author.id, self.bet * 2)
            wallet = await self.cog.get_wallet(self.ctx.author.id)
        desc = (f"Card: **{next_card}**\n"
                f"{'You won **' + str(self.bet) + '** Moon Dust!' if win else 'You lost **' + str(self.bet) + '** Moon Dust'}")
        final_embed = result_embed(self.ctx, f"{CARDS} Higher or Lower", desc, win=win, user=self.ctx.author)
        final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        self.set_embed(final_embed)
        await interaction.edit_original_response(view=self)
        self.cog.active_games.discard(self.ctx.author.id)
        await self.cog.check_broke(self.ctx.author.id)


class ConfirmResetView(ComponentsV2View):
    def __init__(self, bot, author: discord.Member):
        super().__init__(timeout=30)
        self.value = None
        self.bot = bot
        self.author = author
        self.controls = discord.ui.ActionRow()
        confirm_button = discord.ui.Button(label="Confirm Reset", style=discord.ButtonStyle.danger)
        cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        confirm_button.callback = self.confirm
        cancel_button.callback = self.cancel
        self.controls.add_item(confirm_button)
        self.controls.add_item(cancel_button)
        self.add_item(self.controls)

    async def confirm(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("Only the command author can confirm this.", ephemeral=True)
        self.value = True
        disable_all_components(self)
        await interaction.response.edit_message(view=self)
        self.stop()

    async def cancel(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("Only the command author can cancel this.", ephemeral=True)
        self.value = False
        disable_all_components(self)
        await interaction.response.edit_message(view=self)
        self.stop()


class HeistJoinView(ComponentsV2View):
    def __init__(self, cog, host, target, channel):
        super().__init__(timeout=120)
        self.cog = cog
        self.host = host
        self.target = target
        self.participants = {host.id}
        self.locked = False
        self._join_lock = asyncio.Lock()
        self.channel = channel
        join_button = discord.ui.Button(label="Join Heist", style=discord.ButtonStyle.secondary, emoji=EMOJIS["heist"])
        join_button.callback = self.join
        self.controls = discord.ui.ActionRow()
        self.controls.add_item(join_button)
        self.add_item(self.controls)

    def build_lobby_embed(self):
        filled = len(self.participants)
        needed = 5 - filled
        bar = EMOJIS["green_square"] * filled + WHITE_SQUARE * (needed if needed > 0 else 0)
        desc = (f"**Heist Target:** {self.target.mention}\n"
                f"**Host:** {self.host.mention}\n"
                f"**Participants:** {filled}/5 {bar}\n{'─' * 30}\n")
        if self.participants:
            desc += "**Joining:**\n" + "\n".join(f"• <@{uid}>" for uid in self.participants)
        else:
            desc += "*Waiting for participants...*"
        embed = discord.Embed(title=f"{EMOJIS['heist']} Heist Lobby", description=desc, color=EMBED_COLOR)
        embed.set_footer(text=f"Target's Bank: {self.target.display_name} | Need 5 to start")
        return embed

    async def join(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            log.warning("Russian Roulette join interaction expired | user_id=%s", interaction.user.id)
            return

        notice = None
        should_start = False
        async with self._join_lock:
            user_id = interaction.user.id
            if self.locked:
                notice = "Game already starting..."
            elif user_id in self.players:
                notice = "You're already in!"
            elif len(self.players) >= self.max_players:
                notice = "The game is full."
            elif not await self.cog.is_registered(user_id):
                notice = "You need to `+register` before joining!"
            else:
                wallet = await self.cog.add_wallet(user_id, -self.entry_fee)
                if wallet is None:
                    notice = f"You need **{self.entry_fee}** Moon Dust in your wallet to join."
                else:
                    self.players.add(user_id)
                    notice = f"You're in! ({len(self.players)}/{self.max_players})"
                    embed = self.build_lobby_embed()
                    should_start = len(self.players) >= self.max_players
                    if should_start:
                        self.locked = True
                        disable_all_components(self)
                    self.set_embed(embed)
                    try:
                        await interaction.message.edit(view=self)
                    except discord.NotFound:
                        log.warning("Russian Roulette lobby message expired while updating | channel_id=%s", self.channel.id)
                    except discord.HTTPException:
                        log.exception("Could not update Russian Roulette lobby | channel_id=%s", self.channel.id)

        if notice:
            try:
                await interaction.followup.send(notice, ephemeral=True)
            except discord.HTTPException:
                pass
        if should_start:
            await self.start_game()

    async def start_game(self):
        if len(self.players) < 2:
            return
        self.locked = True
        game = RussianRouletteGame(self.channel, self.host, self.entry_fee, list(self.players), self.cog)
        self.cog.russian_games[self.channel.id] = game
        await game.start()


class RussianRouletteGame:
    def __init__(self, channel, host, entry_fee, players, cog):
        self.channel = channel
        self.host = host
        self.entry_fee = entry_fee
        self.alive = list(players)
        self.cog = cog
        self.pot = entry_fee * len(players)
        self.total = len(players)

    def _death_messages(self):
        return [
            f"{EMOJIS['boom']} **BANG!** The bullet found its mark…",
            f"{EMOJIS['gun']} **click…** wait, no — **BANG!**",
            f"The chamber spun, and fate was sealed. {EMOJIS['skull']}",
            "One loud bang, one less player.",
            f"Luck ran out. {EMOJIS['boom']}",
            "A sudden shot echoes. **{loser}** is gone.",
        ]

    async def start(self):
        embed = discord.Embed(
            title=f"{EMOJIS['skull']} Russian Roulette",
            description=f"**{len(self.alive)}** players, **{self.pot}** Moon Dust pot.\nThe chamber is loaded…",
            color=EMBED_COLOR
        )
        embed.add_field(name="Survivors", value="\n".join(f"• <@{uid}>" for uid in self.alive), inline=False)
        msg = await self.channel.send(embed=embed)
        while len(self.alive) > 1:
            spinning = discord.Embed(title=f"{EMOJIS['skull']} Russian Roulette",
                                     description=f"{EMOJIS['loop']} **Spinning the cylinder…**",
                                     color=EMBED_COLOR)
            await msg.edit(embed=spinning)
            await asyncio.sleep(1)
            spinning.description = f"{EMOJIS['spinning']} **Click...**"
            await msg.edit(embed=spinning)
            await asyncio.sleep(1)
            loser = random.choice(self.alive)
            self.alive.remove(loser)
            death_msg = random.choice(self._death_messages()).format(loser=f"<@{loser}>")
            elim_embed = discord.Embed(title=f"{EMOJIS['skull']} Russian Roulette",
                                       description=death_msg, color=EMBED_COLOR)
            elim_embed.add_field(name="Remaining",
                                 value="\n".join(f"• <@{uid}>" for uid in self.alive) if self.alive else "None",
                                 inline=False)
            elim_embed.set_footer(text=f"Pot: {self.pot} Moon Dust | {len(self.alive)}/{self.total} left")
            await msg.edit(embed=elim_embed)
            await asyncio.sleep(2)
        winner = self.alive[0]
        await self.cog.add_wallet(winner, self.pot)
        win_embed = discord.Embed(title=f"{EMOJIS['confetti']} Winner! Russian Roulette",
                                  description=f"<@{winner}> survived and takes home **{self.pot}** Moon Dust!",
                                  color=EMBED_COLOR)
        win_embed.add_field(name="Champion", value=f"<@{winner}>", inline=False)
        win_embed.set_footer(text="Russian Roulette – Game Over")
        await msg.edit(embed=win_embed)
        self.cog.russian_games.pop(self.channel.id, None)


class LeaderboardView(ComponentsV2View):
    def __init__(self, cog, ctx):
        super().__init__(timeout=120)
        self.cog = cog
        self.ctx = ctx
        self.current = "wallet"
        self.MEDALS = [EMOJIS["gold_medal"], EMOJIS["silver_medal"], EMOJIS["bronze_medal"]] + [
            f"{EMOJIS['small_blue_diamond']}"] * 7
        select = discord.ui.Select(
            placeholder="Choose a leaderboard...",
            options=[
                discord.SelectOption(label="All-Time Wallet", value="wallet", emoji=EMOJIS["pouch"], default=True),
                discord.SelectOption(label="All-Time Bank", value="bank", emoji=EMOJIS["bank"]),
                discord.SelectOption(label="All-Time Net Worth", value="net", emoji=EMOJIS["owner"]),
                discord.SelectOption(label="Weekly Gains", value="weekly", emoji=EMOJIS["owner"]),
                discord.SelectOption(label="Monthly Gains", value="monthly", emoji=EMOJIS["calendar"]),
            ]
        )
        select.callback = self.on_select
        self.controls = discord.ui.ActionRow()
        self.controls.add_item(select)
        self.add_item(self.controls)

    async def on_timeout(self):
        disable_all_components(self)
        if getattr(self, "message", None):
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.exception("Failed to disable expired shop controls | user_id=%s", self.ctx.author.id)

    async def on_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        self.current = value
        for item in self.children:
            if isinstance(item, discord.ui.Select):
                for opt in item.options:
                    opt.default = (opt.value == value)
        embed = await self.build_embed()
        self.set_embed(embed)
        await interaction.response.edit_message(view=self)

    async def _resolve_name(self, uid):
        now = time.time()
        cached = self.cog.name_cache.get(uid)
        if cached and now - cached[1] < 300:
            return cached[0]
        member = self.ctx.guild.get_member(uid) if self.ctx.guild else None
        if member:
            name = member.display_name
        else:
            try:
                user = await self.cog.bot.fetch_user(uid)
                name = user.display_name
            except (discord.NotFound, discord.HTTPException):
                name = f"Unknown ({uid})"
        self.cog.name_cache[uid] = (name, now)
        return name

    async def build_embed(self):
        if self.current == "wallet":
            rows = await self.cog._get_top_10("wallet", self.ctx)
            title = f"{EMOJIS['trophy']} All-Time Wallet Leaderboard"
            label, suffix = "Wallet", "Moon Dust"
        elif self.current == "bank":
            rows = await self.cog._get_top_10("bank", self.ctx)
            title = f"{EMOJIS['diamond']} All-Time Bank Leaderboard"
            label, suffix = "Bank", "Moon Dust"
        elif self.current == "net":
            rows = await self.cog._get_top_10_net(self.ctx)
            title = f"{EMOJIS['owner']} All-Time Net Worth Leaderboard"
            label, suffix = "Net Worth", "Moon Dust"
        elif self.current == "weekly":
            rows = await self.cog._get_top_gains(self.ctx, days=7)
            title = f"{EMOJIS['owner']} Weekly Top Earners (last 7 days)"
            label, suffix = "Gained", "Moon Dust"
        else:
            rows = await self.cog._get_top_gains(self.ctx, days=30)
            title = f"{EMOJIS['calendar']} Monthly Top Earners (last 30 days)"
            label, suffix = "Gained", "Moon Dust"
        embed = discord.Embed(title=title, color=EMBED_COLOR, timestamp=discord.utils.utcnow())
        if self.ctx.guild:
            embed.set_author(name=self.ctx.guild.name,
                             icon_url=self.ctx.guild.icon.url if self.ctx.guild.icon else None)
        if not rows:
            embed.description = "*No one is on this leaderboard yet.*"
        else:
            lines = []
            for idx, (uid, value) in enumerate(rows):
                medal = self.MEDALS[idx] if idx < len(self.MEDALS) else f"`#{idx + 1}`"
                name = await self._resolve_name(uid)
                sign = "+" if self.current in ("weekly", "monthly") and value > 0 else ""
                lines.append(f"{medal} **{name}** — `{sign}{format_coins(value)}` {suffix}")
            embed.description = BREAKER_LINE + "\n" + "\n".join(lines) + "\n" + BREAKER_LINE
        embed.set_footer(
            text=f" {label} leaderboard \n• Use the dropdown to switch views \n• Requested by {self.ctx.author}",
            icon_url=self.ctx.author.display_avatar.url)
        return embed


class BuySelectView(discord.ui.View):
    def __init__(self, cog, user: discord.User, quantity: int, options: list):
        super().__init__(timeout=120)
        self.cog = cog
        self.user = user
        self.quantity = quantity
        self.select = discord.ui.Select(placeholder="Select an item to buy...", options=options)
        self.select.callback = self.on_select
        self.message: discord.Message | None = None
        self.processing = False
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This isn't your menu.", ephemeral=True)
            return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        # Prevent multiple purchases if the user double-clicks before the UI refreshes.
        if self.processing:
            try:
                await interaction.response.send_message("A purchase is already being processed.", ephemeral=True)
            except discord.HTTPException:
                pass
            return
        self.processing = True
        self.select.disabled = True
        # Acknowledge before any database or Discord role work.
        try:
            await interaction.response.defer()
        except discord.NotFound:
            log.warning("Shop selection arrived after its interaction expired | user_id=%s", interaction.user.id)
            self.processing = False
            return
        item_name = interaction.data["values"][0]
        try:
            embed = await self.cog._process_purchase_interaction(interaction, item_name, self.quantity)
        except Exception:
            log.exception(
                "Shop purchase callback failed | user_id=%s | item=%s",
                interaction.user.id, item_name,
            )
            embed = error_embed_interaction(
                interaction, "Purchase Failed", "The purchase could not be completed. Please try again later.")
        try:
            await interaction.edit_original_response(embed=embed, view=self)
        except discord.NotFound:
            log.warning(
                "Shop purchase response expired | user_id=%s | item=%s",
                interaction.user.id, item_name,
            )
        except discord.HTTPException:
            log.exception("Could not update shop purchase result | user_id=%s | item=%s", interaction.user.id, item_name)

    async def on_timeout(self):
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass
            except discord.HTTPException:
                log.exception("Failed to disable expired buy dropdown")


class ShopView(ComponentsV2View):
    """Button-driven shop: Buy, View More, Exit."""

    def __init__(self, cog, ctx):
        super().__init__(timeout=180)
        self.cog = cog
        self.ctx = ctx
        self._purchase_in_progress = False
        self._build_main_buttons(clear=False)

    def _clear_controls(self):
        """Remove buttons/select rows without detaching the V2 content container."""
        for child in list(self.children):
            if child is not self.content_container:
                self.remove_item(child)

    def _all_items(self):
        items = {}
        for name, item in DEFAULT_SHOP_ITEMS.items():
            items[name] = {
                "price": item["price"],
                "description": item["description"],
                "type": item.get("type", "consumable"),
                "custom": False,
            }
        for name, item in self.cog.custom_shop_items.items():
            items[name] = {
                "price": item["price"],
                "description": item["description"] or f"Custom {item['type'].capitalize()}",
                "type": item["type"],
                "custom": True,
            }
        return items

    def _item_emoji(self, name):
        key = SHOP_EMOJI_MAP.get(name, "package")
        return EMOJIS.get(key, EMOJIS.get("package", "📦"))

    def _display_name(self, name, is_custom=False):
        if not is_custom and name in SHOP_DISPLAY_NAMES:
            return SHOP_DISPLAY_NAMES[name]
        return name.replace("_", " ").title()

    async def _shop_embed(self):
        guild = self.ctx.guild

        embed = discord.Embed(
            title=f"{EMOJIS.get('shopping_cart', '🛒')} Nuvella Shop",
            color=EMBED_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        if guild:
            embed.set_author(
                name=guild.name,
                icon_url=guild.icon.url if guild.icon else None,
            )

        items = self._all_items()
        item_lines = []
        for idx, (name, item) in enumerate(items.items(), start=1):
            item_lines.append(f"{idx}) {self._display_name(name, item.get('custom', False))}")
        item_block = "\n".join(item_lines) if item_lines else "*No items available right now.*"

        wallet = await self.cog.get_wallet(self.ctx.author.id)
        bank = await self.cog.get_bank(self.ctx.author.id)

        embed.description = (
            f"Hey there {self.ctx.author.mention}. You have reached the **Nuvella Shop** "
            f"where you can use your Moon Dust to buy various items or perks. Feel free to "
            f"click on **View More** for more information of any item and **Buy** to buy any "
            f"item or perk you like.\n\n"
            f"{BREAKER_LINE}\n"
            f"**Please Note** — Once you buy an item, it cannot and will not be refunded back "
            f"to you unless there happens to be a technical difficulty.\n"
            f"{BREAKER_LINE}\n"
            f"**Item List** —\n"
            f"{item_block}\n"
            f"{BREAKER_LINE}\n"
            f"**Your Balance** —\n"
            f"{EMOJIS['pouch']} Wallet: `{wallet:,}` Moon Dust\n"
            f"{EMOJIS['bank']} Bank: `{bank:,}` Moon Dust\n"
            f"{BREAKER_LINE}"
        )

        embed.set_footer(
            text=f"Requested by {self.ctx.author}",
            icon_url=self.ctx.author.display_avatar.url,
        )
        return embed

    def _make_select(self, placeholder, callback):
        items = self._all_items()
        options = []
        for name, item in items.items():
            if len(options) >= 25:
                break
            label = self._display_name(name, item.get("custom", False))[:100]
            try:
                options.append(discord.SelectOption(
                    label=label,
                    description=f"{format_coins(item['price'])} Moon Dust",
                    value=name,
                    emoji=self._item_emoji(name),
                ))
            except Exception:
                options.append(discord.SelectOption(
                    label=label,
                    description=f"{format_coins(item['price'])} Moon Dust",
                    value=name,
                ))
        select = discord.ui.Select(placeholder=placeholder, options=options)
        select.callback = callback
        row = discord.ui.ActionRow()
        row.add_item(select)
        return row

    def _back_row(self):
        row = discord.ui.ActionRow()
        back = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary,
                                 emoji=EMOJIS.get("arrow_left", "◀"))
        back.callback = self._back_to_main
        row.add_item(back)
        return row

    def _build_main_buttons(self, clear: bool = True):
        if clear:
            self._clear_controls()
        row = discord.ui.ActionRow()

        buy_btn = discord.ui.Button(
            label="Buy", style=discord.ButtonStyle.success,
            emoji=EMOJIS.get("shopping_cart"))
        buy_btn.callback = self._open_buy

        view_btn = discord.ui.Button(
            label="View More", style=discord.ButtonStyle.primary,
            emoji=EMOJIS.get("package"))
        view_btn.callback = self._open_info

        exit_btn = discord.ui.Button(
            label="Exit", style=discord.ButtonStyle.danger,
            emoji=EMOJIS.get("cross"))
        exit_btn.callback = self._exit

        row.add_item(buy_btn)
        row.add_item(view_btn)
        row.add_item(exit_btn)
        self.add_item(row)

    def _build_buy_menu(self):
        self._clear_controls()
        self.add_item(self._make_select("Select an item to buy (1×)...", self._buy_select))
        self.add_item(self._back_row())

    def _build_info_menu(self):
        self._clear_controls()
        self.add_item(self._make_select("Select an item to view details...", self._info_select))
        self.add_item(self._back_row())

    async def _back_to_main(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Not your shop!", ephemeral=True)

        await interaction.response.defer()
        self._clear_controls()
        self._build_main_buttons(clear=False)
        embed = await self._shop_embed()
        self.set_embed(embed)
        prepare_v2_view(self, embed)
        try:
            await interaction.edit_original_response(view=self)
        except discord.NotFound:
            logging.getLogger("nuvella.economy").warning(
                "Shop Back interaction expired | user_id=%s", interaction.user.id)

    async def _open_buy(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Not your shop!", ephemeral=True)
        await interaction.response.defer()
        if not await self.cog.is_registered(self.ctx.author.id):
            await interaction.followup.send(
                "You need to register first! Use `+register`.", ephemeral=True)
            return

        self._build_buy_menu()

        embed = discord.Embed(
            title=f"{EMOJIS.get('shopping_cart', '🛒')} Buy an Item",
            color=EMBED_COLOR,
        )
        if self.ctx.guild:
            embed.set_author(name=self.ctx.guild.name,
                             icon_url=self.ctx.guild.icon.url if self.ctx.guild.icon else None)
        embed.description = (
            f"{BREAKER_LINE}\n"
            f"Pick an item from the dropdown to purchase **1×**.\n"
            f"Custom shop items can only be bought one at a time.\n"
            f"{BREAKER_LINE}"
        )
        embed.set_footer(text=f"Requested by {self.ctx.author}",
                         icon_url=self.ctx.author.display_avatar.url)

        self.set_embed(embed)
        prepare_v2_view(self, embed)
        await interaction.edit_original_response(view=self)

    async def _open_info(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Not your shop!", ephemeral=True)

        self._build_info_menu()

        embed = discord.Embed(
            title=f"{EMOJIS.get('package', '📦')} Item Information",
            color=EMBED_COLOR,
        )
        if self.ctx.guild:
            embed.set_author(name=self.ctx.guild.name,
                             icon_url=self.ctx.guild.icon.url if self.ctx.guild.icon else None)
        embed.description = (
            f"{BREAKER_LINE}\n"
            f"Pick an item from the dropdown to see its details.\n"
            f"{BREAKER_LINE}"
        )
        embed.set_footer(text=f"Requested by {self.ctx.author}",
                         icon_url=self.ctx.author.display_avatar.url)

        self.set_embed(embed)
        prepare_v2_view(self, embed)
        await interaction.response.edit_message(view=self)

    async def _buy_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Not your shop!", ephemeral=True)
        if self._purchase_in_progress:
            return await interaction.response.send_message("A purchase is already being processed.", ephemeral=True)
        self._purchase_in_progress = True
        item_name = interaction.data["values"][0]
        try:
            await interaction.response.defer()
        except discord.NotFound:
            log.warning("Shop buy selection interaction expired | user_id=%s", interaction.user.id)
            self._purchase_in_progress = False
            return
        try:
            result_embed_msg = await self.cog._process_purchase_interaction(interaction, item_name, 1)
        except Exception:
            logging.getLogger("nuvella.economy").exception(
                "Shop purchase callback failed | user_id=%s | item=%s",
                interaction.user.id, item_name,
            )
            result_embed_msg = error_embed_interaction(
                interaction, "Purchase Failed", "The purchase could not be completed. Please try again later.")
        self._clear_controls()
        self.add_item(self._back_row())
        self.set_embed(result_embed_msg)
        prepare_v2_view(self, result_embed_msg)
        self._purchase_in_progress = False
        try:
            await interaction.edit_original_response(view=self)
        except discord.NotFound:
            log.warning(
                "Shop purchase response expired | user_id=%s | item=%s",
                interaction.user.id, item_name,
            )
        except discord.HTTPException:
            log.exception("Could not update shop purchase result | user_id=%s | item=%s", interaction.user.id, item_name)

    async def _info_select(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Not your shop!", ephemeral=True)
        item_name = interaction.data["values"][0]
        items = self._all_items()
        if item_name not in items:
            return await interaction.response.send_message("Unknown item.", ephemeral=True)
        item = items[item_name]
        embed = discord.Embed(
            title=f"{self._item_emoji(item_name)} {self._display_name(item_name, item.get('custom', False))}",
            color=EMBED_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        if self.ctx.guild:
            embed.set_author(name=self.ctx.guild.name,
                             icon_url=self.ctx.guild.icon.url if self.ctx.guild.icon else None)
        embed.description = (
            f"{BREAKER_LINE}\n"
            f"{item['description']}\n"
            f"{BREAKER_LINE}"
        )
        embed.add_field(name="Price",
                        value=f"`{format_coins(item['price'])}` Moon Dust", inline=True)
        embed.add_field(name="Type",
                        value=item.get("type", "consumable"), inline=True)
        embed.add_field(name="Custom",
                        value="Yes" if item.get("custom") else "No", inline=True)
        embed.set_footer(text=f"Requested by {self.ctx.author}",
                         icon_url=self.ctx.author.display_avatar.url)
        self._clear_controls()
        self.add_item(self._back_row())
        self.set_embed(embed)
        prepare_v2_view(self, embed)
        await interaction.response.edit_message(view=self)

    async def _exit(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            return await interaction.response.send_message("Not your shop!", ephemeral=True)
        disable_all_components(self)
        self.stop()
        exit_embed = discord.Embed(
            title=f"{EMOJIS.get('cross', '❌')} Shop Closed",
            description="Thanks for visiting! Type `$shop` again to reopen.",
            color=EMBED_COLOR,
        )
        self.set_embed(exit_embed)
        await interaction.response.edit_message(view=self)

    async def on_timeout(self):
        disable_all_components(self)
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass


# ─── MAIN COG ────────────────────────────────────────────────
class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pool = None
        self.active_games = set()
        self.russian_games = {}
        self.current_top_user_id = None
        self.collect_roles = {}
        self.staff_role_ids = set()
        self.custom_shop_items = {}
        self.shop_role_mapping = {}
        self.name_cache: dict[int, tuple[str, float]] = {}

    async def cog_load(self):
        self.pool = getattr(self.bot, "pool", None)
        if self.pool is None:
            raise RuntimeError("The shared PostgreSQL pool was not initialized by main.py.")
        await self._init_db()
        self._load_collect_config()
        self._load_shop_roles()
        await self._load_custom_shop()
        self.hourly_status.start()
        self.top_role_update.start()
        self.custom_role_check.start()
        self.purge_left_users.start()
        self.purge_broke_users.start()
        self.bank_interest.start()
        self.snapshot_balances.start()

    async def cog_unload(self):
        self.hourly_status.cancel()
        self.top_role_update.cancel()
        self.custom_role_check.cancel()
        self.purge_left_users.cancel()
        self.purge_broke_users.cancel()
        self.bank_interest.cancel()
        self.snapshot_balances.cancel()

    def is_owner(self, user: discord.User) -> bool:
        return user.id == get_owner_id()

    def is_admin(self, user: discord.User) -> bool:
        if self.is_owner(user):
            return True
        if not isinstance(user, discord.Member):
            return False
        if ADMIN_ROLE_ID and any(role.id == ADMIN_ROLE_ID for role in user.roles):
            return True
        return False

    def is_staff(self, user: discord.Member) -> bool:
        if not isinstance(user, discord.Member):
            return False
        return any(role.id in self.staff_role_ids for role in user.roles)

    async def _require_admin_silent(self, ctx) -> bool:
        if self.is_admin(ctx.author):
            return True
        try:
            await ctx.message.delete()
        except (discord.HTTPException, discord.Forbidden):
            pass
        return False

    def _load_collect_config(self):
        DEFAULT_AMOUNTS = {
            "admin": 16000, "senior_moderator": 15000, "moderator": 14000,
            "trial_moderator": 12000, "developer": 13000, "event_manager": 14000,
            "event_trial": 12000, "level_100": 12000, "level_90": 10000,
            "level_80": 8000, "level_70": 6000, "level_60": 4000,
            "level_50": 2000, "level_40": 1000, "level_30": 800,
            "level_20": 600, "level_10": 400, "level_1": 200,
        }
        collect_cfg = ECONOMY_CONFIG.get("collect_roles", {})
        amounts_cfg = ECONOMY_CONFIG.get("collect_amounts", {})
        for key, default_amt in DEFAULT_AMOUNTS.items():
            role_id = None
            amount = default_amt
            entry = collect_cfg.get(key)
            if entry:
                if isinstance(entry, dict):
                    role_id = entry.get("role_id", ROLES_CONFIG.get(f"{key}_role_id"))
                    amount = entry.get("amount", amounts_cfg.get(key, default_amt))
                else:
                    role_id = entry
                    amount = amounts_cfg.get(key, default_amt)
            else:
                role_id = ROLES_CONFIG.get(f"{key}_role_id")
                amount = amounts_cfg.get(key, default_amt)
            if role_id:
                self.collect_roles[int(role_id)] = int(amount)

        staff_keys = [
            "admin_role_id", "moderator_role_id", "trial_moderator_role_id",
            "event_manager_role_id", "trial_event_manager_role_id",
            "developer_role_id", "senior_moderator_role_id"
        ]
        for key in staff_keys:
            rid = ROLES_CONFIG.get(key)
            if rid:
                self.staff_role_ids.add(int(rid))

        if 1535961511275536477 not in self.collect_roles:
            self.collect_roles[1535961511275536477] = 10000
        if 1536182809830625292 not in self.collect_roles:
            self.collect_roles[1536182809830625292] = 5000

    def _load_shop_roles(self):
        mapping = {
            "afk_permission": ("afk_role_id", "afk_perm"),
            "ghost_ping_viewer": ("ghost_ping_role_id", "ghost_ping_perm"),
            "tic_tac_toe": ("ttt_role_id", "ttt"),
            "rock_paper_scissors": ("rps_role_id", "rps"),
            "eight_ball": ("8ball_role_id", "8ball"),
            "mafia_ping": ("mafia_ping_role_id", "mafia_ping"),
            "roulette_ping": ("roulette_ping_role_id", "russian_roulette_ping"),
        }
        for item_name, (config_key, legacy_key) in mapping.items():
            role_id = ROLES_CONFIG.get(config_key) or CONFIG.get(legacy_key)
            if role_id:
                try:
                    self.shop_role_mapping[item_name] = int(role_id)
                except (TypeError, ValueError):
                    pass

    def get_collect_amount(self, member: discord.Member) -> int:
        best = 0
        for role in member.roles:
            amt = self.collect_roles.get(role.id, 0)
            if amt > best:
                best = amt
        return best

    async def _lock_economy_users(self, conn, *user_ids: int) -> None:
        """Acquire per-user transaction locks in a stable order across economy mutations."""
        for user_id in sorted({int(uid) for uid in user_ids if uid is not None}):
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", user_id)

    async def _fetchval(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def _fetchrow(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def _fetch(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def _execute(self, query, *args):
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def _init_db(self):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id BIGINT PRIMARY KEY,
                        wallet BIGINT NOT NULL DEFAULT 0,
                        bank BIGINT NOT NULL DEFAULT 0,
                        xp BIGINT NOT NULL DEFAULT 0
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS registered_users (
                        user_id BIGINT PRIMARY KEY,
                        registered_at BIGINT NOT NULL
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS inventory (
                        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        item TEXT NOT NULL,
                        qty INTEGER NOT NULL DEFAULT 1,
                        PRIMARY KEY (user_id, item)
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS equipped (
                        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        item TEXT NOT NULL,
                        PRIMARY KEY (user_id, item)
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS custom_roles (
                        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        guild_id BIGINT NOT NULL,
                        expiry BIGINT NOT NULL,
                        role_id BIGINT NOT NULL,
                        PRIMARY KEY (user_id, guild_id)
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS left_users (
                        user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                        left_at BIGINT NOT NULL,
                        guild_id BIGINT NOT NULL
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS rob_immunity (
                        user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                        guild_id BIGINT NOT NULL
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS balance_snapshots (
                        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        taken_at BIGINT NOT NULL,
                        wallet BIGINT NOT NULL,
                        bank BIGINT NOT NULL
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS purged_accounts (
                        user_id BIGINT PRIMARY KEY,
                        last_wallet BIGINT NOT NULL,
                        last_bank BIGINT NOT NULL,
                        reason TEXT NOT NULL,
                        purged_at BIGINT NOT NULL
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS economy_channel_bypass (
                        user_id BIGINT NOT NULL,
                        channel_id BIGINT NOT NULL,
                        PRIMARY KEY (user_id, channel_id)
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS collect_cooldowns (
                        user_id BIGINT PRIMARY KEY,
                        last_collected BIGINT NOT NULL
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS command_cooldowns (
                        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        command TEXT NOT NULL,
                        last_used BIGINT NOT NULL,
                        PRIMARY KEY (user_id, command)
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS custom_shop_items (
                        id SERIAL PRIMARY KEY,
                        name TEXT UNIQUE NOT NULL,
                        price BIGINT NOT NULL,
                        item_type TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        description TEXT,
                        active BOOLEAN DEFAULT TRUE
                    )
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_custom_items (
                        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        shop_item_id INTEGER NOT NULL REFERENCES custom_shop_items(id) ON DELETE CASCADE,
                        purchased_at BIGINT NOT NULL,
                        PRIMARY KEY (user_id, shop_item_id)
                    )
                """)
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_user_time ON balance_snapshots (user_id, taken_at)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_taken_at ON balance_snapshots (taken_at)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_wallet ON users (wallet DESC)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_bank ON users (bank DESC)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_net ON users ((wallet + bank) DESC)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_custom_shop_active ON custom_shop_items (active)")
                await conn.execute("ANALYZE")

    async def _load_custom_shop(self):
        rows = await self._fetch(
            "SELECT id, name, price, item_type, payload, description FROM custom_shop_items WHERE active = TRUE")
        self.custom_shop_items = {}
        for r in rows:
            self.custom_shop_items[r['name']] = {
                'id': r['id'], 'price': r['price'], 'type': r['item_type'],
                'payload': r['payload'], 'description': r['description'] or "",
            }

    async def _add_custom_shop_item(self, name, price, item_type, payload, description=""):
        async def operation(conn):
            await conn.execute(
                "INSERT INTO custom_shop_items (name, price, item_type, payload, description) VALUES ($1,$2,$3,$4,$5)",
                name, price, item_type, payload, description,
            )

        await run_transaction(self.pool, operation, label="economy.add_custom_shop_item")
        await self._load_custom_shop()

    async def _remove_custom_shop_item(self, name):
        async def operation(conn):
            await conn.execute("UPDATE custom_shop_items SET active = FALSE WHERE name = $1", name)

        await run_transaction(self.pool, operation, label="economy.remove_custom_shop_item")
        await self._load_custom_shop()

    async def _grant_custom_item(self, user_id, item_name):
        item = self.custom_shop_items.get(item_name)
        if not item:
            for key in self.custom_shop_items:
                if key.lower() == item_name.lower():
                    item = self.custom_shop_items[key]
                    item_name = key
                    break
            if not item:
                return None
        guild = self.bot.guilds[0] if self.bot.guilds else None
        if not guild:
            return False
        member = guild.get_member(user_id)
        if not member:
            return False
        payload = item['payload']
        item_type = item['type'].lower()
        granted_role = None
        role_was_already_present = False
        if item_type == "role":
            role_id = int(payload)
            role = guild.get_role(role_id)
            if role:
                role_was_already_present = role in member.roles
                try:
                    await member.add_roles(role, reason=f"Purchased from shop: {item_name}")
                    if not role_was_already_present:
                        granted_role = role
                except discord.Forbidden:
                    return False
            else:
                return False
        elif item_type in ("perk", "title"):
            pass
        else:
            return False
        async def persist_purchase(conn):
            inserted = await conn.fetchval(
                """INSERT INTO user_custom_items (user_id, shop_item_id, purchased_at)
                   SELECT $1, id, $2 FROM custom_shop_items WHERE name = $3 AND active = TRUE
                   ON CONFLICT (user_id, shop_item_id) DO NOTHING
                   RETURNING user_id""",
                user_id, int(time.time()), item_name,
            )
            return inserted is not None

        try:
            saved = await run_transaction(self.pool, persist_purchase, label="economy.persist_custom_item_purchase")
        except Exception:
            if granted_role is not None:
                try:
                    await member.remove_roles(granted_role, reason="Custom item purchase persistence failed")
                except (discord.Forbidden, discord.HTTPException):
                    log.exception("Could not roll back unrecorded custom item role | user_id=%s", user_id)
            raise
        if not saved:
            if granted_role is not None:
                try:
                    await member.remove_roles(granted_role, reason="Custom item purchase persistence failed")
                except (discord.Forbidden, discord.HTTPException):
                    log.exception("Could not roll back unrecorded custom item role | user_id=%s", user_id)
            return False
        return True

    async def get_wallet(self, uid):
        row = await self._fetchrow("SELECT wallet FROM users WHERE id=$1", uid)
        return row['wallet'] if row else 0

    async def get_bank(self, uid):
        row = await self._fetchrow("SELECT bank FROM users WHERE id=$1", uid)
        return row['bank'] if row else 0

    async def is_registered(self, uid) -> bool:
        return await self._fetchval("SELECT 1 FROM registered_users WHERE user_id=$1", uid) is not None

    async def register_user(self, uid, starting_wallet=1000) -> bool:
        async def operation(conn):
            await self._lock_economy_users(conn, uid)
            inserted = await conn.fetchval(
                "INSERT INTO registered_users (user_id, registered_at) VALUES ($1, $2) "
                "ON CONFLICT (user_id) DO NOTHING RETURNING user_id",
                uid, int(time.time()),
            )
            if inserted is None:
                return False
            await conn.execute(
                "INSERT INTO users (id, wallet, bank) VALUES ($1, $2, 0) ON CONFLICT (id) DO NOTHING",
                uid, starting_wallet,
            )
            return True

        return await run_transaction(self.pool, operation, label="economy.register_user")

    async def add_wallet(self, uid, amount):
        async def operation(conn):
            await self._lock_economy_users(conn, uid)
            if amount >= 0:
                await conn.execute(
                    "INSERT INTO users (id, wallet) VALUES ($1, 0) ON CONFLICT (id) DO NOTHING", uid
                )
                return await conn.fetchval(
                    "UPDATE users SET wallet = wallet + $1 WHERE id = $2 RETURNING wallet", amount, uid
                )
            return await conn.fetchval(
                "UPDATE users SET wallet = wallet - $1 WHERE id = $2 AND wallet >= $1 RETURNING wallet",
                -amount, uid,
            )

        return await run_transaction(self.pool, operation, label="economy.add_wallet")

    async def add_bank(self, uid, amount):
        async def operation(conn):
            await self._lock_economy_users(conn, uid)
            if amount >= 0:
                await conn.execute(
                    "INSERT INTO users (id, bank) VALUES ($1, 0) ON CONFLICT (id) DO NOTHING", uid
                )
                return await conn.fetchval(
                    "UPDATE users SET bank = bank + $1 WHERE id = $2 RETURNING bank", amount, uid
                )
            return await conn.fetchval(
                "UPDATE users SET bank = bank - $1 WHERE id = $2 AND bank >= $1 RETURNING bank",
                -amount, uid,
            )

        return await run_transaction(self.pool, operation, label="economy.add_bank")

    async def move_wallet_to_bank(self, uid, amount):
        async def operation(conn):
            await self._lock_economy_users(conn, uid)
            await conn.execute(
                "INSERT INTO users (id, wallet, bank) VALUES ($1, 0, 0) ON CONFLICT (id) DO NOTHING", uid
            )
            return await conn.fetchrow(
                """UPDATE users SET wallet = wallet - $1, bank = bank + $1
                   WHERE id = $2 AND wallet >= $1 RETURNING wallet, bank""",
                amount, uid,
            )

        return await run_transaction(self.pool, operation, label="economy.move_wallet_to_bank")

    async def move_bank_to_wallet(self, uid, amount):
        async def operation(conn):
            await self._lock_economy_users(conn, uid)
            await conn.execute(
                "INSERT INTO users (id, wallet, bank) VALUES ($1, 0, 0) ON CONFLICT (id) DO NOTHING", uid
            )
            return await conn.fetchrow(
                """UPDATE users SET bank = bank - $1, wallet = wallet + $1
                   WHERE id = $2 AND bank >= $1 RETURNING wallet, bank""",
                amount, uid,
            )

        return await run_transaction(self.pool, operation, label="economy.move_bank_to_wallet")

    async def transfer_wallet(self, from_id, to_id, amount):
        if from_id == to_id or amount <= 0:
            return None

        async def operation(conn):
            await self._lock_economy_users(conn, from_id, to_id)
            locked = set()
            for user_id in sorted((from_id, to_id)):
                row = await conn.fetchrow("SELECT id FROM users WHERE id = $1 FOR UPDATE", user_id)
                if row:
                    locked.add(user_id)
            if locked != {from_id, to_id}:
                return None
            row = await conn.fetchrow(
                "UPDATE users SET wallet = wallet - $1 WHERE id = $2 AND wallet >= $1 RETURNING wallet",
                amount, from_id,
            )
            if row is None:
                return None
            credited = await conn.fetchval(
                "UPDATE users SET wallet = wallet + $1 WHERE id = $2 RETURNING wallet", amount, to_id
            )
            if credited is None:
                # Defensive guard; transaction rollback prevents money disappearing.
                raise RuntimeError("Recipient account disappeared during a locked wallet transfer")
            return row["wallet"]

        return await run_transaction(self.pool, operation, label="economy.transfer_wallet")

    async def _atomic_wallet_transfer(self, from_id, to_id, amount):
        """Atomically move currency between users in a deterministic lock order."""
        if from_id == to_id or amount <= 0:
            return None

        async def operation(conn):
            await self._lock_economy_users(conn, from_id, to_id)
            locked = set()
            for user_id in sorted((from_id, to_id)):
                row = await conn.fetchrow("SELECT id FROM users WHERE id = $1 FOR UPDATE", user_id)
                if row:
                    locked.add(user_id)
            if locked != {from_id, to_id}:
                return None
            src = await conn.fetchrow(
                "UPDATE users SET wallet = wallet - $1 WHERE id = $2 AND wallet >= $1 RETURNING wallet",
                amount, from_id,
            )
            if src is None:
                return None
            dst = await conn.fetchrow(
                "UPDATE users SET wallet = wallet + $1 WHERE id = $2 RETURNING wallet", amount, to_id
            )
            if dst is None:
                raise RuntimeError("Destination account disappeared during a locked transfer")
            return src["wallet"], dst["wallet"]

        return await run_transaction(self.pool, operation, label="economy.atomic_wallet_transfer")

    async def set_wallet(self, uid, amount):
        amount = max(0, int(amount))

        async def operation(conn):
            await self._lock_economy_users(conn, uid)
            await conn.execute("INSERT INTO users (id, wallet) VALUES ($1, 0) ON CONFLICT (id) DO NOTHING", uid)
            await conn.execute("UPDATE users SET wallet = $1 WHERE id=$2", amount, uid)

        await run_transaction(self.pool, operation, label="economy.set_wallet")

    async def set_bank(self, uid, amount):
        amount = max(0, int(amount))

        async def operation(conn):
            await self._lock_economy_users(conn, uid)
            await conn.execute("INSERT INTO users (id, bank) VALUES ($1, 0) ON CONFLICT (id) DO NOTHING", uid)
            await conn.execute("UPDATE users SET bank = $1 WHERE id=$2", amount, uid)

        await run_transaction(self.pool, operation, label="economy.set_bank")

    async def has_item_equipped(self, user_id, item_name):
        return await self._fetchval("SELECT 1 FROM equipped WHERE user_id=$1 AND item=$2",
                                    user_id, item_name) is not None

    async def consume_item(self, user_id, item_name):
        async def operation(conn):
            await self._lock_economy_users(conn, user_id)
            # Lock/update inventory deterministically; do not decrement below zero.
            row = await conn.fetchrow(
                "UPDATE inventory SET qty = qty - 1 WHERE user_id=$1 AND item=$2 AND qty > 0 RETURNING qty",
                user_id, item_name,
            )
            if row is None:
                return False
            await conn.execute("DELETE FROM equipped WHERE user_id=$1 AND item=$2", user_id, item_name)
            await conn.execute("DELETE FROM inventory WHERE user_id=$1 AND item=$2 AND qty <= 0", user_id, item_name)
            return True

        return await run_transaction(self.pool, operation, label="economy.consume_item")

    async def add_item(self, user_id, item_name, quantity):
        async def operation(conn):
            await self._lock_economy_users(conn, user_id)
            await conn.execute(
                """INSERT INTO inventory (user_id, item, qty) VALUES ($1, $2, $3)
                   ON CONFLICT (user_id, item) DO UPDATE SET qty = inventory.qty + EXCLUDED.qty""",
                user_id, item_name, quantity,
            )

        await run_transaction(self.pool, operation, label="economy.add_item")

    async def require_registered(self, ctx) -> bool:
        if not await self.is_registered(ctx.author.id):
            embed = error_embed(ctx, "Not Registered",
                                "You need to open an account first! Use `$register` to get started.")
            await ctx.send(embed=embed)
            return False
        await self.add_wallet(ctx.author.id, 0)
        return True

    # ─── LOGGING ────────────────────────────────────────────────
    def _is_read_only(self, ctx) -> bool:
        """True if the invoked command (or its root group) is read-only."""
        name = ctx.command.qualified_name or ""
        root = name.split()[0]
        return root in READ_ONLY_COMMANDS

    async def log_command_use(self, ctx, start_wallet=None, start_bank=None):
        if not LOGGING_CONFIG.get("command_logging", True):
            return

        # Single atomic read of wallet + bank. Previously two separate
        # connections could observe a torn state if a concurrent transaction
        # was moving money between wallet and bank.
        row = await self._fetchrow(
            "SELECT COALESCE(wallet, 0) AS wallet, COALESCE(bank, 0) AS bank "
            "FROM users WHERE id = $1",
            ctx.author.id,
        )
        now_wallet = row["wallet"] if row else 0
        now_bank = row["bank"] if row else 0

        if start_wallet is None:
            start_wallet = now_wallet
        if start_bank is None:
            start_bank = now_bank

        prev_total = start_wallet + start_bank
        curr_total = now_wallet + now_bank

        embed = discord.Embed(
            title=f"{EMOJIS['chart']} Economy Log",
            color=EMBED_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)

        channel_value = ctx.channel.mention if ctx.guild else f"DM ({ctx.channel.id})"
        guild_value = ctx.guild.name if ctx.guild else "DM"

        embed.add_field(
            name="User",
            value=f"{ctx.author.display_name} (ID: {ctx.author.id})",
            inline=False,
        )
        embed.add_field(
            name="Command",
            value=f"`{ctx.command.qualified_name}`",
            inline=False,
        )
        embed.add_field(
            name="Channel",
            value=channel_value,
            inline=False,
        )
        embed.add_field(
            name="Previous Balance",
            value=f"`{prev_total:,}` Moon Dust",
            inline=False,
        )
        embed.add_field(
            name="Current Balance",
            value=f"`{curr_total:,}` Moon Dust",
            inline=False,
        )
        embed.add_field(
            name="Guild",
            value=guild_value,
            inline=False,
        )

        await log_embed_to_discord(self.bot, embed)

    # ─── BROKE-USER PURGE ──────────────────────────────────────
    async def check_broke(self, user_id: int):
        # Mid-game accounts can temporarily be zeroed while winnings are pending.
        if user_id in self.active_games:
            return

        async def operation(conn):
            await self._lock_economy_users(conn, user_id)
            row = await conn.fetchrow(
                "SELECT wallet, bank FROM users WHERE id = $1 FOR UPDATE", user_id
            )
            if row is None or row["wallet"] != 0 or row["bank"] != 0:
                return False
            await conn.execute(
                """INSERT INTO purged_accounts (user_id, last_wallet, last_bank, reason, purged_at)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (user_id) DO UPDATE SET
                       last_wallet = EXCLUDED.last_wallet,
                       last_bank = EXCLUDED.last_bank,
                       reason = EXCLUDED.reason,
                       purged_at = EXCLUDED.purged_at""",
                user_id, 0, 0, "broke (wallet & bank both 0)", int(time.time()),
            )
            await conn.execute("DELETE FROM users WHERE id = $1", user_id)
            return True

        await run_transaction(self.pool, operation, label="economy.check_broke")

    @tasks.loop(hours=1)
    async def purge_broke_users(self):
        await self.bot.wait_until_ready()
        active = list(self.active_games) or [0]
        total_purged = 0

        async def purge_batch(conn):
            candidates = await conn.fetch(
                """SELECT id FROM users
                   WHERE wallet = 0 AND bank = 0 AND id != ALL($1::bigint[])
                   ORDER BY id LIMIT 500""",
                active,
            )
            candidate_ids = [row["id"] for row in candidates]
            if not candidate_ids:
                return 0
            await self._lock_economy_users(conn, *candidate_ids)
            rows = await conn.fetch(
                """SELECT id, wallet, bank FROM users
                   WHERE id = ANY($1::bigint[]) AND wallet = 0 AND bank = 0
                   ORDER BY id FOR UPDATE""",
                candidate_ids,
            )
            ids = [row["id"] for row in rows]
            if not ids:
                # A balance changed while locks were being acquired; rescan rather than
                # leaving later eligible accounts behind for the next scheduled sweep.
                return -1
            await conn.execute(
                """INSERT INTO purged_accounts (user_id, last_wallet, last_bank, reason, purged_at)
                   SELECT id, wallet, bank, 'broke (wallet & bank both 0) — periodic sweep', $1
                   FROM users WHERE id = ANY($2::bigint[])
                   ON CONFLICT (user_id) DO UPDATE SET
                       last_wallet = EXCLUDED.last_wallet,
                       last_bank = EXCLUDED.last_bank,
                       reason = EXCLUDED.reason,
                       purged_at = EXCLUDED.purged_at""",
                int(time.time()), ids,
            )
            # Delete dependent rows in a stable order before deleting their users.
            for table in (
                "inventory", "equipped", "custom_roles", "left_users",
                "balance_snapshots", "user_custom_items", "command_cooldowns",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE user_id = ANY($1::bigint[])", ids)
            await conn.execute("DELETE FROM users WHERE id = ANY($1::bigint[])", ids)
            return len(ids)

        while True:
            count = await run_transaction(self.pool, purge_batch, label="economy.purge_broke_users")
            if count < 0:
                continue
            total_purged += count
            if count == 0:
                break
        if total_purged:
            await log_to_discord(self.bot, f"Periodic sweep purged {total_purged} broke account(s).")

    @purge_broke_users.before_loop
    async def before_purge_broke_users(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=6)
    async def snapshot_balances(self):
        await self.bot.wait_until_ready()
        now = int(time.time())

        async def operation(conn):
            await conn.execute(
                "INSERT INTO balance_snapshots (user_id, taken_at, wallet, bank) SELECT id, $1, wallet, bank FROM users",
                now,
            )
            await conn.execute("DELETE FROM balance_snapshots WHERE taken_at < $1", now - (35 * 86400))

        await run_transaction(self.pool, operation, label="economy.snapshot_balances")

    @snapshot_balances.before_loop
    async def before_snapshot_balances(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def bank_interest(self):
        await self.bot.wait_until_ready()

        async def operation(conn):
            rows = await conn.fetch("SELECT id FROM users WHERE bank > 0 ORDER BY id")
            user_ids = [row["id"] for row in rows]
            if not user_ids:
                return 0
            await self._lock_economy_users(conn, *user_ids)
            updated = await conn.fetchval(
                """WITH changed AS (
                       UPDATE users SET bank = bank + LEAST(bank / 50, 10000) WHERE bank > 0
                       RETURNING id
                   ) SELECT COUNT(*) FROM changed"""
            )
            return int(updated or 0)

        count = await run_transaction(self.pool, operation, label="economy.bank_interest")
        if count:
            await log_to_discord(self.bot, f"Bank interest applied to {count} accounts.")

    @bank_interest.before_loop
    async def before_bank_interest(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=15)
    async def custom_role_check(self):
        await self.bot.wait_until_ready()
        now = int(time.time())
        rows = await self._fetch("SELECT user_id, guild_id, role_id FROM custom_roles WHERE expiry < $1", now)
        for row in rows:
            user_id, guild_id, role_id = row['user_id'], row['guild_id'], row['role_id']
            guild = self.bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(user_id)
                role = guild.get_role(role_id)
                if member and role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="Shop custom role expired")
                    except (discord.Forbidden, discord.HTTPException):
                        logging.getLogger("nuvella.roles").exception(
                            "Could not remove expired custom role | guild_id=%s | user_id=%s | role_id=%s",
                            guild_id, user_id, role_id,
                        )
                if role and role.name.startswith("Custom · "):
                    try:
                        await role.delete(reason="Shop custom role expired")
                    except (discord.Forbidden, discord.HTTPException):
                        logging.getLogger("nuvella.roles").exception(
                            "Could not delete expired custom role | guild_id=%s | role_id=%s", guild_id, role_id
                        )
            await self._execute("DELETE FROM custom_roles WHERE user_id=$1 AND guild_id=$2", user_id, guild_id)

    @custom_role_check.before_loop
    async def before_custom_role_check(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def purge_left_users(self):
        await self.bot.wait_until_ready()
        now = int(time.time())

        async def operation(conn):
            candidates = await conn.fetch(
                "SELECT user_id FROM left_users WHERE left_at < $1 ORDER BY user_id LIMIT 500",
                now - 86400,
            )
            candidate_ids = [row["user_id"] for row in candidates]
            if not candidate_ids:
                return 0
            await self._lock_economy_users(conn, *candidate_ids)
            rows = await conn.fetch(
                """SELECT user_id FROM left_users
                   WHERE user_id = ANY($1::bigint[]) AND left_at < $2
                   ORDER BY user_id FOR UPDATE""",
                candidate_ids, now - 86400,
            )
            ids = [row["user_id"] for row in rows]
            if not ids:
                return -1
            accounts = await conn.fetch(
                "SELECT id, wallet, bank FROM users WHERE id = ANY($1::bigint[]) ORDER BY id FOR UPDATE",
                ids,
            )
            balances = {row["id"]: row for row in accounts}
            for user_id in ids:
                row = balances.get(user_id)
                if row:
                    await conn.execute(
                        """INSERT INTO purged_accounts (user_id, last_wallet, last_bank, reason, purged_at)
                           VALUES ($1, $2, $3, $4, $5)
                           ON CONFLICT (user_id) DO UPDATE SET
                               last_wallet = EXCLUDED.last_wallet,
                               last_bank = EXCLUDED.last_bank,
                               reason = EXCLUDED.reason,
                               purged_at = EXCLUDED.purged_at""",
                        user_id, row["wallet"], row["bank"],
                        "left server (24h grace period expired)", now,
                    )
            for table in (
                "inventory", "equipped", "custom_roles", "left_users",
                "balance_snapshots", "user_custom_items", "command_cooldowns",
            ):
                await conn.execute(f"DELETE FROM {table} WHERE user_id = ANY($1::bigint[])", ids)
            await conn.execute("DELETE FROM users WHERE id = ANY($1::bigint[])", ids)
            return len(ids)

        total = 0
        while True:
            count = await run_transaction(self.pool, operation, label="economy.purge_left_users")
            if count < 0:
                continue
            total += count
            if count == 0:
                break
        if total:
            await log_to_discord(
                self.bot,
                f"Purged economy data for {total} user(s) who left and didn't return within 24h.",
            )

    @purge_left_users.before_loop
    async def before_purge_left_users(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=30)
    async def top_role_update(self):
        await self.bot.wait_until_ready()
        log_channel = self.bot.get_channel(DISCORD_LOG_CHANNEL_ID)
        if not log_channel or not log_channel.guild:
            return
        guild = log_channel.guild
        role = guild.get_role(TOP_ROLE_ID)
        if not role:
            return
        row = await self._fetchrow("SELECT id, wallet FROM users WHERE wallet > 0 ORDER BY wallet DESC LIMIT 1")
        if not row:
            return
        top_user_id = row['id']
        if top_user_id == self.current_top_user_id:
            return
        for member in role.members:
            if member.id != top_user_id:
                try:
                    await member.remove_roles(role, reason="No longer the top economy player")
                except discord.Forbidden:
                    pass
        member = guild.get_member(top_user_id)
        if member and role not in member.roles:
            try:
                await member.add_roles(role, reason="Top economy player")
            except discord.Forbidden:
                pass
        self.current_top_user_id = top_user_id

    @top_role_update.before_loop
    async def before_top_role_update(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def hourly_status(self):
        try:
            user_count = await self._fetchval("SELECT COUNT(*) FROM registered_users")
            total_wallet = await self._fetchval("SELECT COALESCE(SUM(wallet),0) FROM users")
            total_bank = await self._fetchval("SELECT COALESCE(SUM(bank),0) FROM users")
            inventory_entries = await self._fetchval("SELECT COUNT(*) FROM inventory")
            await log_to_discord(
                self.bot,
                f"{EMOJIS['chart']} Hourly Status: Registered users: {user_count} | "
                f"Wallet total: {total_wallet} | Bank total: {total_bank} | Inventory entries: {inventory_entries}")
        except Exception as e:
            await log_to_discord(self.bot, f"{EMOJIS['warning']} Hourly status failed: {e}")

    @hourly_status.before_loop
    async def before_hourly(self):
        await self.bot.wait_until_ready()

    # ─── EVENT LISTENERS ────────────────────────────────────────
    async def cog_before_invoke(self, ctx):
        if ctx.cog is self and self.pool is not None:
            ctx._starting_wallet = await self.get_wallet(ctx.author.id)
            ctx._starting_bank = await self.get_bank(ctx.author.id)

        if not getattr(ctx, "_send_patched", False):
            ctx._send_patched = True
            _original_send = ctx.send

            async def _patched_send(*args, **kwargs):
                embed = kwargs.get("embed")
                if embed is None:
                    for a in args:
                        if isinstance(a, discord.Embed):
                            embed = a
                            break
                if isinstance(embed, ErrorEmbed) and "delete_after" not in kwargs:
                    kwargs["delete_after"] = 5
                return await _original_send(*args, **kwargs)

            ctx.send = _patched_send

    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        if ctx.cog is not self or self.pool is None:
            return
        if self._is_read_only(ctx):
            return
        start_wallet = getattr(ctx, '_starting_wallet', None)
        start_bank = getattr(ctx, '_starting_bank', None)
        await self.log_command_use(ctx, start_wallet, start_bank)
        if ctx.author.id not in self.active_games:
            await self.check_broke(ctx.author.id)
        if LOGGING_CONFIG.get("reply_on_command", False):
            now_wallet = await self.get_wallet(ctx.author.id)
            now_bank = await self.get_bank(ctx.author.id)
            embed = discord.Embed(
                title=f"{EMOJIS['tick']} Command Executed",
                description=f"`{ctx.command.qualified_name}` completed.",
                color=EMBED_COLOR)
            embed.add_field(name=f"{EMOJIS['pouch']} Starting Wallet",
                            value=f"`{start_wallet if start_wallet is not None else 'N/A'}` Moon Dust", inline=True)
            embed.add_field(name=f"{EMOJIS['pouch']} Ending Wallet",
                            value=f"`{now_wallet}` Moon Dust", inline=True)
            embed.add_field(name=f"{EMOJIS['bank']} Starting Bank",
                            value=f"`{start_bank if start_bank is not None else 'N/A'}` Moon Dust", inline=True)
            embed.add_field(name=f"{EMOJIS['bank']} Ending Bank",
                            value=f"`{now_bank}` Moon Dust", inline=True)
            embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
            try:
                await ctx.send(embed=embed)
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        log_channel = self.bot.get_channel(DISCORD_LOG_CHANNEL_ID)
        if not log_channel or member.guild.id != log_channel.guild.id:
            return
        if not await self.is_registered(member.id):
            return
        wallet = await self.get_wallet(member.id)
        bank = await self.get_bank(member.id)
        inventory = await self._fetch("SELECT item, qty FROM inventory WHERE user_id=$1", member.id)
        equipped = await self._fetch("SELECT item FROM equipped WHERE user_id=$1", member.id)
        inv_str = "\n".join(f"{r['item']} x{r['qty']}" for r in inventory) or "Empty"
        equip_str = ", ".join(r['item'] for r in equipped) or "None"
        embed = discord.Embed(
            title=f"{EMOJIS['panda_wave']} Member Left – Data Snapshot",
            color=EMBED_COLOR,
            timestamp=discord.utils.utcnow())
        embed.add_field(name="User", value=f"{member} ({member.id})", inline=False)
        embed.add_field(name=f"{EMOJIS['pouch']} Wallet", value=f"{wallet:,} Moon Dust", inline=True)
        embed.add_field(name=f"{EMOJIS['bank']} Bank", value=f"{bank:,} Moon Dust", inline=True)
        embed.add_field(name="Inventory", value=inv_str, inline=False)
        embed.add_field(name="Equipped", value=equip_str, inline=False)
        embed.set_footer(text="Data will be permanently deleted after 24 hours unless they rejoin.")
        await log_channel.send(embed=embed)
        await self._execute(
            "INSERT INTO left_users (user_id, left_at, guild_id) VALUES ($1,$2,$3) ON CONFLICT (user_id) DO UPDATE SET left_at = EXCLUDED.left_at, guild_id = EXCLUDED.guild_id",
            member.id, int(time.time()), member.guild.id)

    # ─── COG CHECK ─────────────────────────────────────────────
    async def cog_check(self, ctx):
        if ctx.command.qualified_name == "economy":
            return self.is_owner(ctx.author) or self.is_admin(ctx.author)
        if self.is_owner(ctx.author) or self.is_admin(ctx.author) or self.is_staff(ctx.author):
            return True
        bypass = await self._fetchval(
            "SELECT 1 FROM economy_channel_bypass WHERE user_id=$1 AND channel_id=$2",
            ctx.author.id, ctx.channel.id)
        if bypass:
            return True
        if not ALLOWED_CHANNEL_ID or ctx.channel.id == ALLOWED_CHANNEL_ID:
            return True
        if ctx.message:
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
        return False

    # ─── PERSISTENT COOLDOWNS ─────────────────────────────────
    async def get_db_cooldown(self, user_id: int, command: str, seconds: int):
        """Atomically reserve a persistent cooldown while coordinating with account purges."""
        async def operation(conn):
            await self._lock_economy_users(conn, user_id)
            await conn.execute(
                "INSERT INTO users (id, wallet, bank) VALUES ($1, 0, 0) ON CONFLICT (id) DO NOTHING",
                user_id,
            )
            parent = await conn.fetchval("SELECT id FROM users WHERE id = $1 FOR UPDATE", user_id)
            if parent is None:
                raise RuntimeError("Could not ensure economy account for cooldown")
            row = await conn.fetchrow(
                "SELECT last_used FROM command_cooldowns WHERE user_id = $1 AND command = $2 FOR UPDATE",
                user_id, command,
            )
            now = int(time.time())
            if row and now - row["last_used"] < seconds:
                return seconds - (now - row["last_used"])
            await conn.execute(
                """INSERT INTO command_cooldowns (user_id, command, last_used)
                   VALUES ($1, $2, $3) ON CONFLICT (user_id, command)
                   DO UPDATE SET last_used = EXCLUDED.last_used""",
                user_id, command, now,
            )
            return None

        return await run_transaction(self.pool, operation, label="economy.get_db_cooldown")

    async def try_start_game(self, user_id: int) -> bool:
        async with user_lock(user_id):
            if user_id in self.active_games:
                return False
            self.active_games.add(user_id)
            return True

    # ─── REGISTRATION ───────────────────────────────────────────
    @commands.command(name="register", aliases=["start", "openaccount"])
    async def register(self, ctx: commands.Context):
        """Open your Moon Dust economy account to start earning and gambling."""
        new_user = await self.register_user(ctx.author.id, starting_wallet=1000)
        if not new_user:
            return await ctx.send(embed=error_embed(
                ctx, "Already Registered",
                "You already have an account! Use `$balance` to check your Moon Dust."))
        await self._execute(
            "INSERT INTO balance_snapshots (user_id, taken_at, wallet, bank) VALUES ($1,$2,$3,$4)",
            ctx.author.id, int(time.time()), 1000, 0)
        embed = result_embed(
            ctx, f"{EMOJIS['tick']} Account Created",
            f"Welcome! Your account has been opened with a starting balance of **1,000** {CURRENCY}.",
            win=True)
        await ctx.send(embed=embed)
        info_embed = base_embed(ctx, f"{EMOJIS['pouch']} Available Commands", user=ctx.author)
        info_embed.description = f"Here's everything you can do now that you're registered:\n{BREAKER_LINE}"
        info_embed.add_field(name="Commands", value=GAMBLING_COMMANDS_LIST, inline=False)
        try:
            await ctx.author.send(embed=info_embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.command(name="registeredusers", aliases=["totalusers", "regcount"])
    async def registered_users(self, ctx: commands.Context):
        """View the total number of registered economy users. Admin only."""
        if not await self._require_admin_silent(ctx):
            return
        count = await self._fetchval("SELECT COUNT(*) FROM registered_users")
        embed = base_embed(ctx, f"{EMOJIS['global']} Registered Users")
        embed.add_field(name="Total Registered", value=f"`{count:,}`", inline=False)
        await ctx.send(embed=embed)

    # ─── COLLECT COMMAND ────────────────────────────────────────
    @commands.command(name="collect")
    async def collect(self, ctx: commands.Context):
        if not await self.require_registered(ctx):
            return
        user_id = ctx.author.id
        amount = self.get_collect_amount(ctx.author)
        if amount <= 0:
            return await ctx.send(embed=error_embed(
                ctx, "No Collect Role", "You don't have a role eligible for collect."))

        async def operation(conn):
            await self._lock_economy_users(conn, user_id)
            await conn.execute(
                "INSERT INTO users (id, wallet, bank) VALUES ($1, 0, 0) ON CONFLICT (id) DO NOTHING",
                user_id,
            )
            await conn.fetchval("SELECT id FROM users WHERE id=$1 FOR UPDATE", user_id)
            row = await conn.fetchrow(
                "SELECT last_collected FROM collect_cooldowns WHERE user_id=$1 FOR UPDATE", user_id
            )
            now = int(time.time())
            if row and now - row["last_collected"] < 86400:
                return ("cooldown", 86400 - (now - row["last_collected"]))
            await conn.execute("UPDATE users SET wallet = wallet + $1 WHERE id=$2", amount, user_id)
            await conn.execute(
                """INSERT INTO collect_cooldowns (user_id, last_collected) VALUES ($1,$2)
                   ON CONFLICT (user_id) DO UPDATE SET last_collected = EXCLUDED.last_collected""",
                user_id, now,
            )
            wallet = await conn.fetchval("SELECT wallet FROM users WHERE id=$1", user_id)
            return ("success", wallet)

        status, value = await run_transaction(self.pool, operation, label="economy.collect")
        if status == "cooldown":
            return await ctx.send(embed=cooldown_embed(ctx, value))
        embed = result_embed(
            ctx, f"{EMOJIS['gift']} Collect", f"You collected **{amount:,}** Moon Dust!", win=True
        )
        embed.add_field(name="Wallet", value=f"`{value:,}` Moon Dust", inline=False)
        await ctx.send(embed=embed)

    # ─── ECONOMY COMMANDS ───────────────────────────────────────
    @commands.command(name="balance", aliases=["bal"])
    async def balance(self, ctx: commands.Context):
        """Check your wallet and bank balance."""
        if not await self.require_registered(ctx):
            return
        wallet = await self.get_wallet(ctx.author.id)
        bank = await self.get_bank(ctx.author.id)
        embed = base_embed(ctx, f"{EMOJIS['pouch']} Balance", user=ctx.author)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=True)
        embed.add_field(name="Bank", value=f"{EMOJIS['bank']} `{bank:,}` Moon Dust", inline=True)
        embed.add_field(name="Total", value=f"`{wallet + bank:,}` Moon Dust", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="profile", aliases=["prof"])
    async def profile(self, ctx: commands.Context, user: discord.Member = None):
        user = user or ctx.author
        if not await self.is_registered(user.id):
            return await ctx.send(embed=error_embed(
                ctx, "Not Registered", f"{user.mention} hasn't registered an account yet."))
        wallet = await self.get_wallet(user.id)
        bank = await self.get_bank(user.id)
        embed = base_embed(ctx, f"{EMOJIS['user_id']} {user.display_name}", user=user)
        embed.add_field(name="Wallet", value=f"{wallet:,} Moon Dust", inline=True)
        embed.add_field(name="Bank", value=f"{bank:,} Moon Dust", inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="daily", aliases=["dly"])
    async def daily(self, ctx: commands.Context):
        if not await self.require_registered(ctx):
            return
        rem = await self.get_db_cooldown(ctx.author.id, "daily", 86400)
        if rem:
            return await ctx.send(embed=cooldown_embed(ctx, rem))
        base_reward = random.randint(800, 1500)
        if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles):
            base_reward = int(base_reward * 1.05)
        await self.add_wallet(ctx.author.id, base_reward)
        wallet = await self.get_wallet(ctx.author.id)
        embed = result_embed(ctx, f"{EMOJIS['gift']} Daily Reward",
                             f"You received **{base_reward:,}** Moon Dust!", win=True)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="fish")
    async def fish(self, ctx: commands.Context, bet: str):
        if not await self.require_registered(ctx):
            return
        if not await self.try_start_game(ctx.author.id):
            return await ctx.send(embed=error_embed(
                ctx, "Game in Progress", "You're already playing a game! Finish it first."))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            self.active_games.discard(ctx.author.id)
            return
        if not self.is_owner(ctx.author):
            rem = await self.get_db_cooldown(ctx.author.id, "fish", GAME_COOLDOWN)
            if rem:
                self.active_games.discard(ctx.author.id)
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        try:
            async with user_lock(ctx.author.id):
                wallet = await self.get_wallet(ctx.author.id)
                if wallet < amount:
                    self.active_games.discard(ctx.author.id)
                    return await ctx.send(embed=error_embed(
                        ctx, "Insufficient Funds",
                        "You don't have enough Moon Dust in your wallet."))
                p = 0.25 + BASE_CHANCE_BOOST
                if await self.has_item_equipped(ctx.author.id, "rod"):
                    p += 0.05
                if await self.has_item_equipped(ctx.author.id, "horseshoe"):
                    p += 0.05
                if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles):
                    p += 0.03
                p = min(p, 0.95)
                win = random.random() < p
                has_rod = await self.has_item_equipped(ctx.author.id, "rod")
                if win:
                    gain = amount * 2 if has_rod else amount
                    await self.add_wallet(ctx.author.id, gain)
                    desc = f"You caught a big one! **+{gain:,}** Moon Dust"
                else:
                    await self.add_wallet(ctx.author.id, -amount)
                    desc = f"The line snapped... **-{amount:,}** Moon Dust"
                wallet = await self.get_wallet(ctx.author.id)
            embed = result_embed(ctx, f"{EMOJIS['fishes']} Fishing", desc, win=win)
            embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
            await ctx.send(embed=embed)
        finally:
            self.active_games.discard(ctx.author.id)
            await self.check_broke(ctx.author.id)

    @commands.command(name="work", aliases=["wk"])
    async def work(self, ctx: commands.Context):
        if not await self.require_registered(ctx):
            return
        if not self.is_owner(ctx.author):
            rem = await self.get_db_cooldown(ctx.author.id, "work", LONG_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        gain = random.randint(300, 800)
        await self.add_wallet(ctx.author.id, gain)
        wallet = await self.get_wallet(ctx.author.id)
        embed = result_embed(ctx, f"{EMOJIS['backpack']} Work", f"You earned **{gain:,}** Moon Dust!", win=True)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="deposit", aliases=["dep"])
    async def deposit(self, ctx: commands.Context, amount: str):
        if not await self.require_registered(ctx):
            return
        amt_str = amount.lower().strip()
        async with user_lock(ctx.author.id):
            if amt_str == "all":
                wallet = await self.get_wallet(ctx.author.id)
                amt = wallet
                if amt <= 0:
                    return await ctx.send(embed=error_embed(
                        ctx, "Nothing to Deposit", "Your wallet is empty."))
            else:
                amt = await self._parse_amount(ctx, amount)
                if amt is None:
                    return
            result = await self.move_wallet_to_bank(ctx.author.id, amt)
            if result is None:
                return await ctx.send(embed=error_embed(
                    ctx, "Insufficient Funds", "Not enough Moon Dust in your wallet."))
            wallet, bank = result
        embed = result_embed(ctx, f"{EMOJIS['deposit']} Deposit",
                             f"Deposited **{amt:,}** Moon Dust into your bank.", win=True)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=True)
        embed.add_field(name="Bank", value=f"{EMOJIS['bank']} `{bank:,}` Moon Dust", inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="withdraw", aliases=["wd"])
    async def withdraw(self, ctx: commands.Context, amount: str):
        if not await self.require_registered(ctx):
            return
        amt_str = amount.lower().strip()
        async with user_lock(ctx.author.id):
            if amt_str == "all":
                bank = await self.get_bank(ctx.author.id)
                amt = bank
                if amt <= 0:
                    return await ctx.send(embed=error_embed(
                        ctx, "Nothing to Withdraw", "Your bank is empty."))
            else:
                amt = await self._parse_amount(ctx, amount)
                if amt is None:
                    return
            result = await self.move_bank_to_wallet(ctx.author.id, amt)
            if result is None:
                return await ctx.send(embed=error_embed(
                    ctx, "Insufficient Funds", "Not enough Moon Dust in your bank."))
            wallet, bank = result
        embed = result_embed(ctx, f"{EMOJIS['withdraw']} Withdraw",
                             f"Withdrew **{amt:,}** Moon Dust to your wallet.", win=True)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=True)
        embed.add_field(name="Bank", value=f"{EMOJIS['bank']} `{bank:,}` Moon Dust", inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="interestrate", aliases=["ir"])
    async def interest_rate(self, ctx: commands.Context):
        """View the bank interest rate. Admin only."""
        if not await self._require_admin_silent(ctx):
            return
        embed = base_embed(ctx, f"{EMOJIS['bank']} Bank Interest Rate")
        embed.add_field(name="Current Rate", value="**2%** per hour", inline=True)
        embed.add_field(name="Max Interest", value="**10,000 Moon Dust** per cycle", inline=True)
        embed.set_footer(text="Interest is added automatically every hour.")
        await ctx.send(embed=embed)

    @commands.command(name="rob", aliases=["steal", "take"])
    async def rob(self, ctx: commands.Context, user: discord.Member):
        if not await self.require_registered(ctx):
            return
        if user.bot:
            return await ctx.send(embed=error_embed(ctx, "Robbery", "You can't rob bots."))
        if user == ctx.author:
            return await ctx.send(embed=error_embed(ctx, "Robbery", "You can't rob yourself."))
        if not await self.is_registered(user.id):
            return await ctx.send(embed=error_embed(
                ctx, "Robbery", f"{user.mention} hasn't registered an account yet."))
        immunity = await self._fetchval(
            "SELECT 1 FROM rob_immunity WHERE user_id=$1 AND guild_id=$2", user.id, ctx.guild.id)
        if immunity:
            return await ctx.send(embed=error_embed(
                ctx, "Robbery Protected",
                f"{user.mention} has immunity from robberies and heists!"))
        if not self.is_owner(ctx.author):
            rem = await self.get_db_cooldown(ctx.author.id, "rob", LONG_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        if await self.has_item_equipped(user.id, "watchdog"):
            await self.consume_item(user.id, "watchdog")
            return await ctx.send(embed=error_embed(
                ctx, "Robbery", f"{user.mention}'s Watchdog scared you away! Robbery failed."))
        first_id, second_id = sorted((ctx.author.id, user.id))
        async with user_lock(first_id):
            async with user_lock(second_id):
                target_wallet = await self.get_wallet(user.id)
                if target_wallet <= 0:
                    return await ctx.send(embed=error_embed(
                        ctx, "Robbery", f"{user.mention} has nothing in their wallet to steal."))
                success = random.random() < 0.50
                if success:
                    stolen = min(random.randint(200, 600), target_wallet)
                    # The target is the source; the robber receives the stolen amount.
                    result = await self._atomic_wallet_transfer(user.id, ctx.author.id, stolen)
                    if result is None:
                        return await ctx.send(embed=error_embed(
                            ctx, "Robbery Failed", "Insufficient funds on target."))
                    _, robber_wallet = result
                    desc = (f"You robbed {user.mention} and got **{stolen:,}** Moon Dust!\n"
                            f"Your wallet: `{robber_wallet:,}` Moon Dust")
                else:
                    penalty = random.randint(100, 300)
                    await self.add_wallet(ctx.author.id, -penalty)
                    robber_wallet = await self.get_wallet(ctx.author.id)
                    desc = (f"You got caught! Lost **{penalty:,}** Moon Dust.\n"
                            f"Your wallet: `{robber_wallet:,}` Moon Dust")
        embed = result_embed(ctx, f"{EMOJIS['detective']} Robbery", desc, win=success)
        await ctx.send(embed=embed)
        await self.check_broke(ctx.author.id)
        await self.check_broke(user.id)

    @commands.command(name="shop")
    @commands.guild_only()
    async def shop(self, ctx: commands.Context):
        """Open the interactive shop — buy, view more, and exit."""
        view = ShopView(self, ctx)
        embed = await view._shop_embed()
        view.set_embed(embed)
        prepare_v2_view(view, embed)
        msg = await ctx.send(view=view)
        view.message = msg
        try:
            view.set_embed(embed)
            await msg.edit(view=view)
        except (discord.NotFound, discord.HTTPException):
            pass

    @commands.command(name="inv")
    async def inv(self, ctx: commands.Context):
        if not await self.require_registered(ctx):
            return
        rows = await self._fetch("SELECT item, qty FROM inventory WHERE user_id=$1", ctx.author.id)
        embed = discord.Embed(title=f"{EMOJIS['backpack']} Frosty Inventory", color=EMBED_COLOR)
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        embed.description = BREAKER_LINE
        if rows:
            for r in rows:
                item, qty = r['item'], r['qty']
                type_tag = "passive" if item in ("rod", "horseshoe") else "consumable"
                embed.add_field(name=f"{item.replace('_', ' ').title()} ×{qty}",
                                value=f"Type: {type_tag}", inline=False)
        else:
            embed.description += "\n*Empty*"
        embed.add_field(name="\u200b", value=BREAKER_LINE, inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx: commands.Context):
        """View the interactive leaderboard — wallet, bank, net worth, weekly & monthly gains."""
        view = LeaderboardView(self, ctx)
        embed = await view.build_embed()
        view.set_embed(embed)
        prepare_v2_view(view, embed)
        msg = await ctx.send(view=view)
        view.message = msg
        try:
            view.set_embed(embed)
            await msg.edit(view=view)
        except (discord.NotFound, discord.HTTPException):
            pass

    async def _get_top_10(self, category: str, ctx):
        if category == "wallet":
            query = "SELECT id, wallet FROM users WHERE wallet > 0 ORDER BY wallet DESC LIMIT 10"
        elif category == "bank":
            query = "SELECT id, bank FROM users WHERE bank > 0 ORDER BY bank DESC LIMIT 10"
        else:
            return []
        rows = await self._fetch(query)
        return [(row['id'], row[category]) for row in rows]

    async def _get_top_10_net(self, ctx):
        rows = await self._fetch(
            "SELECT id, (wallet + bank) AS net FROM users WHERE (wallet + bank) > 0 ORDER BY net DESC LIMIT 10")
        return [(row['id'], row['net']) for row in rows]

    async def _get_top_gains(self, ctx, days: int = 7):
        now = int(time.time())
        start = now - days * 86400
        query = """
            WITH period AS (
                SELECT user_id, taken_at, wallet + bank AS total
                FROM balance_snapshots
                WHERE taken_at > $1 AND taken_at <= $2
            ),
            firsts AS (
                SELECT DISTINCT ON (user_id) user_id, total AS first_total
                FROM period ORDER BY user_id, taken_at ASC
            ),
            lasts AS (
                SELECT DISTINCT ON (user_id) user_id, total AS last_total
                FROM period ORDER BY user_id, taken_at DESC
            )
            SELECT u.id,
                   COALESCE(l.last_total, u.wallet + u.bank) - COALESCE(f.first_total, u.wallet + u.bank) AS gained
            FROM users u
            LEFT JOIN firsts f ON f.user_id = u.id
            LEFT JOIN lasts l ON l.user_id = u.id
            WHERE COALESCE(l.last_total, u.wallet + u.bank) - COALESCE(f.first_total, u.wallet + u.bank) > 0
            ORDER BY gained DESC
            LIMIT 10
        """
        rows = await self._fetch(query, start, now)
        return [(row['id'], row['gained']) for row in rows]

    @commands.command(name="mgive")
    async def mgive(self, ctx: commands.Context, user: discord.Member, amount: str):
        """Give Moon Dust to a user. Bot owner or admin role only."""
        if not self.is_admin(ctx.author):
            return await ctx.send(embed=error_embed(
                ctx, "Permission Denied",
                "Only the bot owner or an admin can use this command."))
        parsed = await self._parse_amount(ctx, amount)
        if parsed is None:
            return
        if not await self.is_registered(user.id):
            await self.register_user(user.id)
        await self.add_wallet(user.id, parsed)
        embed = result_embed(ctx, f"{EMOJIS['gift']} Moon Dust Given",
                             f"Gave **{parsed:,}** Moon Dust to {user.mention}.", win=True)
        await ctx.send(embed=embed)

    @commands.command(name="mtake")
    async def mtake(self, ctx: commands.Context, user: discord.Member, amount: str):
        """Remove Moon Dust from a user. Bot owner or admin role only."""
        if not self.is_admin(ctx.author):
            return await ctx.send(embed=error_embed(
                ctx, "Permission Denied",
                "Only the bot owner or an admin can use this command."))
        parsed = await self._parse_amount(ctx, amount)
        if parsed is None:
            return
        new_wallet = await self.add_wallet(user.id, -parsed)
        if new_wallet is None:
            await self.set_wallet(user.id, 0)
        embed = result_embed(ctx, f"{EMOJIS['moon_dust']} Moon Dust Removed",
                             f"Removed **{parsed:,}** Moon Dust from {user.mention}.", win=False)
        await ctx.send(embed=embed)
        await self.check_broke(user.id)

    @commands.command(name="cleareconomy")
    async def clear_economy(self, ctx: commands.Context):
        if not self.is_admin(ctx.author):
            return await ctx.send(embed=error_embed(
                ctx, "Permission Denied",
                "Only the bot owner or an admin can reset the economy."))
        confirm_view = ConfirmResetView(self.bot, ctx.author)
        embed = discord.Embed(title=f"{EMOJIS['warning']} Reset Economy",
                              description="Are you sure you want to **delete all economy data**? This cannot be undone.",
                              color=EMBED_COLOR)
        prepare_v2_view(confirm_view, embed)
        msg = await ctx.send(view=confirm_view)
        await confirm_view.wait()
        if confirm_view.value:
            async def clear_operation(conn):
                # Take per-user locks in a stable order before the destructive reset.
                user_rows = await conn.fetch("SELECT id FROM users ORDER BY id")
                user_ids = [row["id"] for row in user_rows]
                if user_ids:
                    await self._lock_economy_users(conn, *user_ids)
                # Tables are fixed constants, never user input.
                for tbl in (
                    "inventory", "equipped", "custom_roles", "left_users", "balance_snapshots",
                    "economy_channel_bypass", "collect_cooldowns", "command_cooldowns", "rob_immunity",
                    "user_custom_items", "custom_shop_items", "registered_users", "purged_accounts", "users",
                ):
                    await conn.execute(f"DELETE FROM {tbl}")

            await run_transaction(self.pool, clear_operation, label="economy.clear_all")
            await self._load_custom_shop()
            result = discord.Embed(title=f"{EMOJIS['tick']} Economy Reset",
                                   description="All balances, inventories, and equipment have been cleared.",
                                   color=EMBED_COLOR)
            self_v2 = ComponentsV2View(timeout=30, embed=result)
            await msg.edit(view=self_v2)
        else:
            result = discord.Embed(title=f"{EMOJIS['cross']} Cancelled",
                                   description="Economy reset cancelled.", color=EMBED_COLOR)
            self_v2 = ComponentsV2View(timeout=30, embed=result)
            await msg.edit(view=self_v2)

    @commands.group(name="economy", invoke_without_command=True)
    async def economy_group(self, ctx: commands.Context):
        """Economy admin commands (owner or admin role only)."""
        if not self.is_admin(ctx.author):
            return
        embed = base_embed(ctx, f"{EMOJIS['gear']} Economy Admin")
        embed.add_field(
            name="Subcommands",
            value=("`economy shop add <name> <price> <type> <payload> [description]`\n"
                   "`economy shop remove <name>`\n"
                   "`economy shop list`\n"
                   "`economy channelbypass <user> <channel>`"),
            inline=False)
        await ctx.send(embed=embed)

    @economy_group.group(name="shop", invoke_without_command=True)
    async def economy_shop_group(self, ctx: commands.Context):
        if not self.is_admin(ctx.author):
            return
        await ctx.send_help(ctx.command)

    @economy_shop_group.command(name="add")
    async def economy_shop_add(self, ctx: commands.Context, name: str, price: int, item_type: str, payload: str, *,
                               description: str = ""):
        if not self.is_admin(ctx.author):
            return
        item_type = item_type.lower()
        if item_type not in ("role", "perk", "title"):
            return await ctx.send(embed=error_embed(
                ctx, "Invalid Type", "Use `role`, `perk`, or `title`."))
        if price <= 0:
            return await ctx.send(embed=error_embed(ctx, "Invalid Price", "Price must be positive."))
        await self._add_custom_shop_item(name, price, item_type, payload, description)
        embed = result_embed(ctx, f"{EMOJIS['tick']} Shop Item Added",
                             f"Added **{name}** to the shop.", win=True)
        await ctx.send(embed=embed)

    @economy_shop_group.command(name="remove")
    async def economy_shop_remove(self, ctx: commands.Context, name: str):
        if not self.is_admin(ctx.author):
            return
        await self._remove_custom_shop_item(name)
        embed = result_embed(ctx, f"{EMOJIS['tick']} Shop Item Removed",
                             f"Removed **{name}** from the shop.", win=True)
        await ctx.send(embed=embed)

    @economy_shop_group.command(name="list")
    async def economy_shop_list(self, ctx: commands.Context):
        if not self.is_admin(ctx.author):
            return
        embed = base_embed(ctx, f"{EMOJIS['shopping_cart']} Custom Shop Items")
        if not self.custom_shop_items:
            embed.description = "No custom items."
        else:
            lines = [f"**{n}** — `{format_coins(i['price'])}` Moon Dust | Type: {i['type']}"
                     for n, i in self.custom_shop_items.items()]
            embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @economy_group.command(name="channelbypass")
    async def economy_channelbypass(self, ctx: commands.Context, user: discord.Member,
                                     channel: discord.TextChannel):
        if not self.is_admin(ctx.author):
            return
        await self._execute(
            "INSERT INTO economy_channel_bypass (user_id, channel_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            user.id, channel.id)
        embed = result_embed(ctx, f"{EMOJIS['tick']} Bypass Added",
                             f"{user.mention} can now use economy commands in {channel.mention}.", win=True)
        await ctx.send(embed=embed)

    @commands.command(name="coinflip", aliases=["cf"])
    async def coinflip(self, ctx: commands.Context, bet: str):
        if not await self.require_registered(ctx):
            return
        if not self.is_owner(ctx.author):
            rem = await self.get_db_cooldown(ctx.author.id, "coinflip", GAME_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        if not await self.try_start_game(ctx.author.id):
            return await ctx.send(embed=error_embed(
                ctx, "Game in Progress", "You're already playing a game! Finish it first."))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            self.active_games.discard(ctx.author.id)
            return
        wallet = await self.add_wallet(ctx.author.id, -amount)
        if wallet is None:
            self.active_games.discard(ctx.author.id)
            return await ctx.send(embed=error_embed(
                ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet."))
        embed = base_embed(ctx, f"{EMOJIS['moon_dust']} Coin Flip", user=ctx.author)
        embed.description = "Choose **Heads** or **Tails**!\n" + BREAKER_LINE
        view = CoinFlipView(ctx, amount, self)
        prepare_v2_view(view, embed)
        msg = await ctx.send(view=view)
        view.message = msg

    @commands.command(name="roulette", aliases=["rl"])
    async def roulette(self, ctx: commands.Context, bet: str, number: Optional[int] = None):
        if not await self.require_registered(ctx):
            return
        if not self.is_owner(ctx.author):
            rem = await self.get_db_cooldown(ctx.author.id, "roulette", GAME_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        if not await self.try_start_game(ctx.author.id):
            return await ctx.send(embed=error_embed(
                ctx, "Game in Progress", "You're already playing a game! Finish it first."))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            self.active_games.discard(ctx.author.id)
            return
        wallet = await self.add_wallet(ctx.author.id, -amount)
        if wallet is None:
            self.active_games.discard(ctx.author.id)
            return await ctx.send(embed=error_embed(
                ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet."))
        if number is not None:
            if not 1 <= number <= 36:
                self.active_games.discard(ctx.author.id)
                return await ctx.send(embed=error_embed(
                    ctx, "Invalid Number", "Number must be between 1 and 36."))
            spin = random.randint(0, 36)
            win = (spin == number)
            multiplier = 2.5 if win else 0
            if win:
                await self.add_wallet(ctx.author.id, int(amount * multiplier))
            wallet = await self.get_wallet(ctx.author.id)
            colour = "green" if spin == 0 else ("red" if spin in RouletteView.RED_NUMBERS else "black")
            colour_emoji = {"green": EMOJIS["green_circle"], "red": EMOJIS["red_circle"],
                            "black": EMOJIS["black_circle"]}[colour]
            desc = (f"Landed on **{spin}** {colour_emoji} — "
                    f"{'**WIN** ' + str(int(amount * multiplier)) + ' Moon Dust!' if win else '**LOSE** ' + str(amount) + ' Moon Dust'}")
            final_embed = result_embed(ctx, f"{EMOJIS['roulette']} Roulette", desc, win=win, user=ctx.author)
            final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
            await ctx.send(embed=final_embed)
            self.active_games.discard(ctx.author.id)
            await self.check_broke(ctx.author.id)
            return
        embed = base_embed(ctx, f"{EMOJIS['roulette']} Roulette", user=ctx.author)
        embed.description = f"Bet: **{amount:,}** Moon Dust\nPick a bet type from the dropdown!\n{BREAKER_LINE}"
        view = RouletteView(ctx, amount, self)
        prepare_v2_view(view, embed)
        msg = await ctx.send(view=view)
        view.message = msg

    @commands.command(name="blackjack", aliases=["bj"])
    async def blackjack(self, ctx: commands.Context, bet: str):
        if not await self.require_registered(ctx):
            return
        if not self.is_owner(ctx.author):
            rem = await self.get_db_cooldown(ctx.author.id, "blackjack", GAME_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        if not await self.try_start_game(ctx.author.id):
            return await ctx.send(embed=error_embed(
                ctx, "Game in Progress", "You're already playing a game! Finish it first."))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            self.active_games.discard(ctx.author.id)
            return
        wallet = await self.add_wallet(ctx.author.id, -amount)
        if wallet is None:
            self.active_games.discard(ctx.author.id)
            return await ctx.send(embed=error_embed(
                ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet."))
        view = BlackjackView(ctx, amount, self)
        embed = view.build_embed()
        prepare_v2_view(view, embed)
        msg = await ctx.send(view=view)
        view.message = msg

    @commands.command(name="slots", aliases=["sl"])
    async def slots(self, ctx: commands.Context, bet: str):
        if not await self.require_registered(ctx):
            return
        if not self.is_owner(ctx.author):
            rem = await self.get_db_cooldown(ctx.author.id, "slots", GAME_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        if not await self.try_start_game(ctx.author.id):
            return await ctx.send(embed=error_embed(
                ctx, "Game in Progress", "You're already playing a game! Finish it first."))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            self.active_games.discard(ctx.author.id)
            return
        async with user_lock(ctx.author.id):
            wallet = await self.get_wallet(ctx.author.id)
            if wallet < amount:
                self.active_games.discard(ctx.author.id)
                return await ctx.send(embed=error_embed(
                    ctx, "Insufficient Funds",
                    "You don't have enough Moon Dust in your wallet."))
            p = 0.25 + BASE_CHANCE_BOOST
            if await self.has_item_equipped(ctx.author.id, "horseshoe"):
                p += 0.05
            if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles):
                p += 0.03
            p = min(p, 0.95)
            win = random.random() < p
            emojis = [EMOJIS["cherry"], EMOJIS["lemon"], EMOJIS["gem"], EMOJIS["grape"], SLOT_SEVEN]
            if win:
                final = [random.choice(emojis)] * 3
            else:
                a, b = random.choices(emojis, k=2)
                others = [e for e in emojis if e != a] if a == b else emojis
                c = random.choice(others)
                final = [a, b, c]
            net = amount if win else -amount
            await self.add_wallet(ctx.author.id, net)
            wallet = await self.get_wallet(ctx.author.id)
        desc = f"`| {final[0]} | {final[1]} | {final[2]} |`\n{'**JACKPOT!**' if win else 'Better luck next time.'}"
        final_embed = result_embed(ctx, f"{EMOJIS['slot_machine']} Slots", desc, win=win, user=ctx.author)
        final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await ctx.send(embed=final_embed)
        self.active_games.discard(ctx.author.id)
        await self.check_broke(ctx.author.id)

    @commands.command(name="spin")
    async def spin_wheel(self, ctx: commands.Context, bet: str):
        if not await self.require_registered(ctx):
            return
        if not self.is_owner(ctx.author):
            rem = await self.get_db_cooldown(ctx.author.id, "spin", GAME_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        if not await self.try_start_game(ctx.author.id):
            return await ctx.send(embed=error_embed(
                ctx, "Game in Progress", "You're already playing a game! Finish it first."))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            self.active_games.discard(ctx.author.id)
            return
        async with user_lock(ctx.author.id):
            wallet = await self.get_wallet(ctx.author.id)
            if wallet < amount:
                self.active_games.discard(ctx.author.id)
                return await ctx.send(embed=error_embed(
                    ctx, "Insufficient Funds",
                    "You don't have enough Moon Dust in your wallet."))
            p_win = 0.25 + BASE_CHANCE_BOOST
            if await self.has_item_equipped(ctx.author.id, "horseshoe"):
                p_win += 0.05
            if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles):
                p_win += 0.03
            p_win = min(p_win, 0.95)
            if random.random() < p_win:
                win_multipliers = [0.5, 1, 1.5, 2, 5, 10]
                weights = [0.2, 0.3, 0.2, 0.15, 0.1, 0.05]
                multiplier = random.choices(win_multipliers, weights=weights, k=1)[0]
                total_win = int(amount * multiplier)
                net = total_win - amount
                win = True
            else:
                multiplier = 0
                total_win = 0
                net = -amount
                win = False
            await self.add_wallet(ctx.author.id, net)
            wallet = await self.get_wallet(ctx.author.id)
        if win:
            desc = f"Multiplier: **{multiplier}x**\nYou won **{total_win}** Moon Dust!"
        else:
            desc = f"Multiplier: **0x**\nYou lost **{amount}** Moon Dust."
        final_embed = result_embed(ctx, f"{EMOJIS['spin_wheel']} Spin the Wheel", desc, win=win, user=ctx.author)
        final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await ctx.send(embed=final_embed)
        self.active_games.discard(ctx.author.id)
        await self.check_broke(ctx.author.id)

    @commands.command(name="dice")
    async def dice(self, ctx: commands.Context, bet: str, guess: int):
        if not await self.require_registered(ctx):
            return
        if not self.is_owner(ctx.author):
            rem = await self.get_db_cooldown(ctx.author.id, "dice", GAME_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        if not await self.try_start_game(ctx.author.id):
            return await ctx.send(embed=error_embed(
                ctx, "Game in Progress", "You're already playing a game! Finish it first."))
        if not 1 <= guess <= 6:
            self.active_games.discard(ctx.author.id)
            return await ctx.send(embed=error_embed(
                ctx, "Invalid Guess", "Guess a number from 1 to 6."))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            self.active_games.discard(ctx.author.id)
            return
        async with user_lock(ctx.author.id):
            wallet = await self.get_wallet(ctx.author.id)
            if wallet < amount:
                self.active_games.discard(ctx.author.id)
                return await ctx.send(embed=error_embed(
                    ctx, "Insufficient Funds",
                    "You don't have enough Moon Dust in your wallet."))
            p = 0.25 + BASE_CHANCE_BOOST
            if await self.has_item_equipped(ctx.author.id, "horseshoe"):
                p += 0.05
            if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles):
                p += 0.03
            p = min(p, 0.95)
            if random.random() < p:
                roll = guess
            else:
                possible = [n for n in range(1, 7) if n != guess]
                roll = random.choice(possible)
            win = (roll == guess)
            net = amount * 4 if win else -amount
            await self.add_wallet(ctx.author.id, net)
            wallet = await self.get_wallet(ctx.author.id)
        if win:
            total_win = amount * 5
            desc = f"You guessed **{guess}**, rolled **{roll}**\nYou won **{total_win}** Moon Dust!"
        else:
            desc = f"You guessed **{guess}**, rolled **{roll}**\nYou lost **{amount}** Moon Dust."
        final_embed = result_embed(ctx, f"{EMOJIS['dices']} Dice", desc, win=win, user=ctx.author)
        final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await ctx.send(embed=final_embed)
        self.active_games.discard(ctx.author.id)
        await self.check_broke(ctx.author.id)

    @commands.command(name="highlow", aliases=["hl"])
    async def highlow(self, ctx: commands.Context, bet: str):
        if not await self.require_registered(ctx):
            return
        if not self.is_owner(ctx.author):
            rem = await self.get_db_cooldown(ctx.author.id, "highlow", GAME_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        if not await self.try_start_game(ctx.author.id):
            return await ctx.send(embed=error_embed(
                ctx, "Game in Progress", "You're already playing a game! Finish it first."))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            self.active_games.discard(ctx.author.id)
            return
        wallet = await self.add_wallet(ctx.author.id, -amount)
        if wallet is None:
            self.active_games.discard(ctx.author.id)
            return await ctx.send(embed=error_embed(
                ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet."))
        card = random.randint(2, 14)
        embed = base_embed(ctx, f"{CARDS} Higher or Lower", EMBED_COLOR, user=ctx.author)
        embed.description = f"Current card: **{card}**\n{BREAKER_LINE}"
        view = HighLowView(ctx, amount, card, self)
        prepare_v2_view(view, embed)
        msg = await ctx.send(view=view)
        view.message = msg

    @commands.command(name="beg")
    async def beg(self, ctx: commands.Context):
        if not await self.require_registered(ctx):
            return
        rem = await self.get_db_cooldown(ctx.author.id, "beg", LONG_COOLDOWN)
        if rem:
            return await ctx.send(embed=cooldown_embed(ctx, rem))
        if random.random() < 0.3:
            amount = random.randint(10, 50)
            await self.add_wallet(ctx.author.id, amount)
            wallet = await self.get_wallet(ctx.author.id)
            desc = f"You begged and received **{amount}** Moon Dust!"
            win = True
        else:
            desc = "No one gave you anything... try again later."
            win = False
        embed = result_embed(ctx, f"{EMOJIS['beg']} Begging", desc, win=win)
        if win:
            embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="russian", aliases=["rroulette"])
    async def russian_roulette(self, ctx: commands.Context, entry_fee: str, max_players: Optional[int] = 15):
        if not self.is_admin(ctx.author):
            return await ctx.send(embed=error_embed(
                ctx, "Permission Denied",
                "Only the bot owner or an admin can start a Russian Roulette."))
        fee = await self._parse_amount(ctx, entry_fee)
        if fee is None:
            return
        if max_players < 2 or max_players > 30:
            return await ctx.send(embed=error_embed(
                ctx, "Invalid Player Count", "Max players must be between 2 and 30."))
        if ctx.channel.id in self.russian_games:
            return await ctx.send(embed=error_embed(
                ctx, "Game Already Running",
                "A Russian Roulette is already in progress here."))
        view = RussianRouletteJoinView(self, ctx.author, fee, max_players, ctx.channel)
        embed = view.build_lobby_embed()
        prepare_v2_view(view, embed)
        join_msg = await ctx.send(view=view)
        await view.wait()
        disable_all_components(view)
        try:
            await join_msg.edit(view=view)
        except discord.HTTPException:
            pass
        if not view.locked:
            for pid in view.players:
                await self.add_wallet(pid, fee)
            await ctx.send("Game cancelled. Entry fees refunded.")

    @commands.command(name="transfer", aliases=["gift"])
    async def transfer(self, ctx: commands.Context, user: discord.Member, amount: str):
        if not await self.require_registered(ctx):
            return
        if user == ctx.author:
            return await ctx.send(embed=error_embed(
                ctx, "Transfer Failed", "You can't send Moon Dust to yourself."))
        if not await self.is_registered(user.id):
            return await ctx.send(embed=error_embed(
                ctx, "Transfer Failed",
                f"{user.mention} hasn't registered an account yet."))
        parsed = await self._parse_amount(ctx, amount)
        if parsed is None:
            return
        sender_wallet = await self.transfer_wallet(ctx.author.id, user.id, parsed)
        if sender_wallet is None:
            return await ctx.send(embed=error_embed(
                ctx, "Insufficient Funds",
                "You don't have enough Moon Dust in your wallet."))
        embed = result_embed(
            ctx, f"{EMOJIS['gift']} Transfer Complete",
            f"Sent **{parsed:,}** Moon Dust to {user.mention}.\n"
            f"Your wallet: **{format_coins(sender_wallet)}** Moon Dust.", win=True)
        await ctx.send(embed=embed)
        await self.check_broke(ctx.author.id)

    @commands.command(name="heist")
    async def heist(self, ctx: commands.Context, target: discord.Member):
        if not await self.require_registered(ctx):
            return
        if target.bot:
            return await ctx.send(embed=error_embed(ctx, "Heist", "You can't heist bots."))
        if target == ctx.author:
            return await ctx.send(embed=error_embed(ctx, "Heist", "You can't heist yourself."))
        if not await self.is_registered(target.id):
            return await ctx.send(embed=error_embed(
                ctx, "Heist", f"{target.mention} hasn't registered an account yet."))
        immunity = await self._fetchval(
            "SELECT 1 FROM rob_immunity WHERE user_id=$1 AND guild_id=$2", target.id, ctx.guild.id)
        if immunity:
            return await ctx.send(embed=error_embed(
                ctx, "Heist Protected",
                f"{target.mention} has immunity from heists and robberies!"))
        target_bank = await self.get_bank(target.id)
        if target_bank <= 0:
            return await ctx.send(embed=error_embed(
                ctx, "Heist", f"{target.mention} has no Moon Dust in their bank!"))
        view = HeistJoinView(self, ctx.author, target, ctx.channel)
        embed = view.build_lobby_embed()
        embed.description += f"\n\nTarget's Bank: `{target_bank:,}` Moon Dust"
        prepare_v2_view(view, embed)
        await ctx.send(view=view)

    @commands.command(name="buy")
    @commands.guild_only()
    async def buy(self, ctx: commands.Context, quantity: int = 1):
        if quantity < 1 or quantity > 10:
            return await ctx.send(embed=error_embed(
                ctx, "Invalid Quantity", "Quantity must be between 1 and 10."))
        if not await self.is_registered(ctx.author.id):
            return await ctx.send(embed=error_embed(
                ctx, "Not Registered", f"Use `{PREFIX}register` first."))

        options = []
        for name, item in DEFAULT_SHOP_ITEMS.items():
            emoji_key = SHOP_EMOJI_MAP.get(name, "package")
            emoji_value = EMOJIS.get(emoji_key, EMOJIS.get("package"))
            try:
                options.append(discord.SelectOption(
                    label=name.replace('_', ' ').title()[:100],
                    description=f"{format_coins(item['price'])} Moon Dust",
                    value=name,
                    emoji=emoji_value,
                ))
            except Exception:
                options.append(discord.SelectOption(
                    label=name.replace('_', ' ').title()[:100],
                    description=f"{format_coins(item['price'])} Moon Dust",
                    value=name,
                ))
        for name, item in self.custom_shop_items.items():
            if len(options) >= 25:
                break
            options.append(discord.SelectOption(
                label=name[:100],
                description=f"{format_coins(item['price'])} Moon Dust",
                value=name,
            ))

        if not options:
            return await ctx.send(embed=error_embed(ctx, "Shop Empty", "No items available."))

        view = BuySelectView(self, ctx.author, quantity, options)
        view.message = await ctx.send(content=f"Select an item to buy — quantity: **{quantity}**", view=view)

    async def _process_purchase_interaction(self, interaction: discord.Interaction,
                                             item_name: str, quantity: int):
        user = interaction.user
        guild = interaction.guild

        if item_name in DEFAULT_SHOP_ITEMS:
            price = DEFAULT_SHOP_ITEMS[item_name]['price'] * quantity
            is_default = True
        elif item_name in self.custom_shop_items:
            price = self.custom_shop_items[item_name]['price'] * quantity
            is_default = False
        else:
            return error_embed_interaction(interaction, "Unknown Item", f"Item `{item_name}` not found.")

        if quantity < 1:
            return error_embed_interaction(interaction, "Invalid Quantity", "Quantity must be at least 1.")
        if guild is None:
            return error_embed_interaction(interaction, "Server Only", "Shop purchases must be made inside the server.")
        if not is_default and quantity != 1:
            return error_embed_interaction(
                interaction, "Invalid Quantity", "Custom items can only be bought one at a time."
            )

        # These catalogue entries are explicitly role perks. A missing role ID must
        # never silently turn one into an inventory item (or charge the buyer).
        role_required_items = {
            "afk_permission", "ghost_ping_viewer", "tic_tac_toe",
            "rock_paper_scissors", "eight_ball", "mafia_ping", "roulette_ping",
        }
        if is_default and item_name in role_required_items and item_name not in self.shop_role_mapping:
            log.error(
                "Shop role is not configured | item=%s | guild_id=%s",
                item_name, guild.id,
            )
            return error_embed_interaction(
                interaction, "Role Not Configured",
                f"The role for **{item_name.replace('_', ' ').title()}** has not been configured. "
                "No Moon Dust was deducted. Please contact an administrator."
            )

        async with user_lock(user.id):
            # Consumables have no Discord API side effects; charge, inventory, and equipment
            # are committed together in one retryable database transaction.
            if is_default and item_name not in self.shop_role_mapping and item_name != "custom_role":
                async def buy_consumable(conn):
                    await self._lock_economy_users(conn, user.id)
                    row = await conn.fetchrow("SELECT wallet FROM users WHERE id=$1 FOR UPDATE", user.id)
                    if row is None or row["wallet"] < price:
                        return None
                    await conn.execute(
                        "UPDATE users SET wallet = wallet - $1 WHERE id=$2 AND wallet >= $1",
                        price, user.id,
                    )
                    await conn.execute(
                        """INSERT INTO inventory (user_id, item, qty) VALUES ($1,$2,$3)
                           ON CONFLICT (user_id, item)
                           DO UPDATE SET qty = inventory.qty + EXCLUDED.qty""",
                        user.id, item_name, quantity,
                    )
                    await conn.execute(
                        "INSERT INTO equipped (user_id, item) VALUES ($1,$2) ON CONFLICT (user_id,item) DO NOTHING",
                        user.id, item_name,
                    )
                    return await conn.fetchval("SELECT wallet FROM users WHERE id=$1", user.id)

                wallet_after = await run_transaction(
                    self.pool, buy_consumable, label="economy.shop_buy_consumable"
                )
                if wallet_after is None:
                    return error_embed_interaction(
                        interaction, "Insufficient Funds",
                        f"You need **{format_coins(price)}** Moon Dust in your wallet.")
                return result_embed_interaction(
                    interaction, f"{EMOJIS['shopping_cart']} Purchase",
                    f"You bought **{quantity}× {item_name.replace('_', ' ').title()}** for "
                    f"**{format_coins(price)}** Moon Dust.\nIt has been equipped automatically.")

            wallet = await self.add_wallet(user.id, -price)
            if wallet is None:
                return error_embed_interaction(
                    interaction, "Insufficient Funds",
                    f"You need **{format_coins(price)}** Moon Dust in your wallet.")

            if not is_default:
                try:
                    success = await self._grant_custom_item(user.id, item_name)
                except Exception:
                    log.exception("Custom shop item grant failed | user_id=%s | item=%s", user.id, item_name)
                    success = False
                if not success:
                    await self.add_wallet(user.id, price)
                    return error_embed_interaction(
                        interaction, "Purchase Failed",
                        "Could not apply the item. Your payment was refunded.")
                return result_embed_interaction(
                    interaction, f"{EMOJIS['shopping_cart']} Purchase",
                    f"You bought **{item_name}** for **{format_coins(price)}** Moon Dust.")

            if item_name in self.shop_role_mapping:
                role_id = self.shop_role_mapping[item_name]
                role = guild.get_role(role_id)
                if not role:
                    await self.add_wallet(user.id, price)
                    return error_embed_interaction(
                        interaction, "Role Not Found",
                        "The required role does not exist. Contact an administrator.")
                if role in user.roles:
                    await self.add_wallet(user.id, price)
                    return error_embed_interaction(
                        interaction, "Already Purchased",
                        f"You already have the {role.name} role.")
                try:
                    await user.add_roles(role, reason=f"Purchased from shop: {item_name}")
                except discord.Forbidden:
                    await self.add_wallet(user.id, price)
                    return error_embed_interaction(
                        interaction, "Permission Error", "I cannot assign that role.")
                return result_embed_interaction(
                    interaction, f"{EMOJIS['tick']} Purchase Complete",
                    f"You bought **{item_name.replace('_', ' ').title()}** and received the {role.name} role.")

            if item_name == "custom_role":
                existing = await self._fetchrow(
                    "SELECT expiry, role_id FROM custom_roles WHERE user_id=$1 AND guild_id=$2",
                    user.id, guild.id)
                if existing and time.time() < existing['expiry']:
                    await self.add_wallet(user.id, price)
                    days_left = int((existing['expiry'] - time.time()) / 86400)
                    return error_embed_interaction(
                        interaction, "Already Purchased",
                        f"You already have a custom role for another **{days_left}** days.")

                old_role = guild.get_role(existing['role_id']) if existing else None
                if old_role and old_role.name.startswith("Custom · "):
                    try:
                        if old_role in user.roles:
                            await user.remove_roles(old_role, reason="Replacing expired custom role")
                        await old_role.delete(reason="Replacing expired custom role")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                role_name = f"Custom · {user.display_name}"[:100]
                try:
                    role = await guild.create_role(
                        name=role_name, color=discord.Color(0x8B5CF6),
                        reason=f"Shop custom role purchased by {user.id}",
                    )
                    await user.add_roles(role, reason="Purchased shop custom role")
                except discord.Forbidden:
                    await self.add_wallet(user.id, price)
                    return error_embed_interaction(
                        interaction, "Permission Error", "I cannot create or assign your custom role. Check Manage Roles and role hierarchy.")
                except discord.HTTPException:
                    await self.add_wallet(user.id, price)
                    return error_embed_interaction(
                        interaction, "Purchase Failed", "Discord rejected the custom role creation. Your payment was refunded.")

                expiry = int(time.time()) + 60 * 60 * 24 * 60
                try:
                    await self._execute(
                        "INSERT INTO custom_roles (user_id, guild_id, expiry, role_id) VALUES ($1,$2,$3,$4) ON CONFLICT (user_id, guild_id) DO UPDATE SET expiry = EXCLUDED.expiry, role_id = EXCLUDED.role_id",
                        user.id, guild.id, expiry, role.id)
                except Exception:
                    logging.getLogger("nuvella.database").exception(
                        "Failed to save purchased custom role | guild_id=%s | user_id=%s", guild.id, user.id
                    )
                    try:
                        await user.remove_roles(role, reason="Custom role purchase persistence failed")
                        await role.delete(reason="Custom role purchase persistence failed")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    await self.add_wallet(user.id, price)
                    return error_embed_interaction(
                        interaction, "Purchase Failed", "Could not save the role ownership. Your payment was refunded.")
                return result_embed_interaction(
                    interaction, f"{EMOJIS['owner']} Custom Role Purchased",
                    f"You bought your own **{role.name}** for **{format_coins(price)}** Moon Dust.\nIt will expire in 2 months. Use the `customrole` command to edit it.")

            await self.add_item(user.id, item_name, quantity)
            await self._execute(
                "INSERT INTO equipped (user_id, item) VALUES ($1,$2) ON CONFLICT (user_id, item) DO NOTHING",
                user.id, item_name)
            return result_embed_interaction(
                interaction, f"{EMOJIS['shopping_cart']} Purchase",
                f"You bought **{quantity}× {item_name.replace('_', ' ').title()}** for "
                f"**{format_coins(price)}** Moon Dust.\nIt has been equipped automatically.")

    async def _parse_amount(self, ctx, amount_str: str) -> Optional[int]:
        """Parse a bet/amount. Rejects 'all' and any amount <= 0."""
        s = amount_str.lower().replace(",", "").strip()
        if s == "all":
            await ctx.send(embed=error_embed(
                ctx, "Invalid Amount",
                "`all` isn't a valid bet. Use a positive number like `100`, `1k`, or `1.5m`."))
            return None
        try:
            value = parse_amount_str(amount_str)
        except ValueError:
            await ctx.send(embed=error_embed(
                ctx, "Invalid Amount", "Use format: `100`, `1k`, `1.5m`, `1b`"))
            return None
        if value <= 0:
            await ctx.send(embed=error_embed(
                ctx, "Invalid Bet", "Bet amount must be greater than **0** Moon Dust."))
            return None
        return value


async def setup(bot):
    await bot.add_cog(Economy(bot))
