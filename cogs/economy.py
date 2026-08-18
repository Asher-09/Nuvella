import discord
from discord.ext import commands, tasks
import asyncpg
import random
import time
import asyncio
import os
import json
import re
from pathlib import Path
from typing import Optional
from collections import defaultdict
from dotenv import load_dotenv

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

DISCORD_LOG_CHANNEL_ID = int(CONFIG.get("log_channel_id") or 0) or None
ALLOWED_CHANNEL_ID = int(CONFIG.get("allowed_channel_id") or 0) or None

DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    raise RuntimeError("DATABASE_URL not set in .env")

GAME_COOLDOWN = 30          # seconds for quick games
LONG_COOLDOWN = 600         # seconds for robberies, work, etc.
cooldowns = {}

BOOSTER_ROLE_ID = int(ROLES_CONFIG.get("booster_role_id") or 0)
LUCK_BOOST_ROLE_ID = int(ROLES_CONFIG.get("luck_boost_role_id") or 0)
TOP_ROLE_ID = int(ROLES_CONFIG.get("top_role_id") or 0)

# Dark purple theme
MAIN_COLOR = 0x6A0DAD
WIN_COLOR = 0x57F287
LOSS_COLOR = 0xED4245
SPIN_COLOR = 0xF1C40F
GOLD_COLOR = 0xF5C518

CURRENCY = "Moon Dust"

BASE_CHANCE_BOOST = 0.07   # 7% extra

# ─── EMOJI VARIABLES ───────────────────────────────────────────
BREAKER = "<:breaker:1537913026643238932>"
WALLET = "<:pouch:1538037282437730367>"
BANK = "<:bank:1538037751684005948>"
DEPOSIT = "<:Deposit:1538038110057664633>"
WITHDRAW = "<:Withdraw:1538038107675557969>"
TOP_WALLET = "<:s_trophy:1538039103113662574>"
TOP_BANK = "<a:diamond:1538039491741229056>"
WIN_STAR_1 = "<a:aa_star_purple:1537921654435348681>"
WIN_STAR_2 = "<a:x_confete:1538039846164234250>"
WIN_STAR_3 = "<a:es_purple_star:1538040114104639508>"
LOSS_HEART = "<a:purple_broken_heart:1538040614300680234>"
COOLDOWN = "<a:cooldown:1537918560616185956>"
COIN = "<:moon_dust:1537920654349828267>"
ROULETTE = "<a:Roulette:1538041237263749140>"
HEIST = "<a:pep_rob:1538042321978003507>"
LOOP = "<a:discord_loop:1538044068062306344>"
SKULL = "<a:UC_Black_skull:1538053746087563355>"
BOOM = "<a:boom:1538053991064141845>"
GUN = "<:black_gun:1538054286955642920>"
DICES = "<a:dices:1538055128349806623>"
SPIN_WHEEL = "<a:spin_wheel:1538055351373529189>"
BEG = "<a:beg:1538055793599840366>"
GOLD_MEDAL = "<a:gold_medal:1538056224271114331>"
SILVER_MEDAL = "<a:medal_silver_gif:1538056219670093874>"
BRONZE_MEDAL = "<a:medal_bronze_gif:1538056217304371232>"
CROWN = "<a:owner:1537913450754473994>"
CALENDAR = "<a:calendar:1538057240836182036>"
GLOBAL = "<:Global:1538057448718475314>"
GIFT = "<:wumpus_Gift:1538057708807258142>"
RED_BAGPACK = "<:BS_Backpack:1538057850478264402>"
FISHING = "<a:blue_fishes:1538058439270342727>"
DETECTIVE = "<:pikachu_detective:1538058009769676840>"
SHOPPING_CART = "<:shopping_cart_matic:1538058176232951839>"
FISHING_ROD = "<:Fishing_Rod:1537918276326395984>"
WATCHDOG_SHIELD = "<:shield_1:1537917735122505778>"
LUCK_CLOVER = "<:Four_Leaf_Clover:1538058631591764018>"
PACKAGE = "<:package_1f4e6:1538058767256653984>"
USER_ID = "<:userid:1537914514736025740>"
ICE_COIN = "<:moon_dust:1537920654349828267>"
TICK = "<a:tick:1537915747454554222>"
WRONG = "<:cross:1537915734087303199>"
WARN = "<:purple_warning:1537916196601733241>"
SHIELD = "<:dragon_sheild:1537922312060276827>"
GEAR = "<a:gear:1537918036688773171>"
HORSESHOE = "🧲"
RED_CIRCLE = "🔴"
GREEN_CIRCLE = "🟢"
BLACK_CIRCLE = "⚫"
NUM_1 = "1️⃣"
NUM_2 = "2️⃣"
NUM_3 = "3️⃣"
CARDS = "🃏"
SLOT_MACHINE = "🎰"
SLOT_CHERRY = "🍒"
SLOT_LEMON = "🍋"
SLOT_GEM = "💎"
SLOT_GRAPE = "🍇"
SLOT_SEVEN = "7️⃣"
GREEN_SQUARE = "🟩"
WHITE_SQUARE = "⬜"
WAVE = "<a:panda_wave:1538059749239558235>"

BREAKER_LINE = BREAKER * 10

GAMBLING_COMMANDS_LIST = (
    "**Earning**\n"
    "`/daily` — claim a daily reward\n"
    "`/collect` — claim role-based daily reward\n"
    "`/work` — work for Moon Dust\n"
    "`/beg` — beg for a small chance of Moon Dust\n"
    "`/fish <bet>` — go fishing\n\n"
    "**Gambling**\n"
    "`/coinflip <bet>` — 50/50 coin flip\n"
    "`/roulette <bet> [number]` — roulette table\n"
    "`/blackjack <bet>` — play the dealer\n"
    "`/slots <bet>` — spin the slot machine\n"
    "`/spin <bet>` — spin the wheel\n"
    "`/dice <bet> <guess>` — guess the roll\n"
    "`/highlow <bet>` — higher or lower\n\n"
    "**Other**\n"
    "`/balance` — check your wallet/bank\n"
    "`/deposit` `/withdraw` — move Moon Dust to/from your bank\n"
    "`/shop` `/buy` — purchase items\n"
    "`/rob` `/heist` — try to steal Moon Dust\n"
    "`/transfer` — send Moon Dust to another player"
)

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
            for i in range(0, len(message), 2000):
                await channel.send(message[i:i+2000])
    except Exception as e:
        print(f"Failed to log to Discord: {e}")

ITEM_PRICES = {
    "rod": 500, "watchdog": 10000, "luck_potion": 1500,
    "horseshoe": 3000, "custom_role": 10_000_000,
}
ITEM_TYPE = {
    "rod": "passive", "watchdog": "consumable", "luck_potion": "consumable",
    "horseshoe": "passive", "custom_role": "consumable",
}
ITEM_DESCRIPTIONS = {
    "rod": "Passive: +5% win chance when fishing.",
    "watchdog": "Consumable: protects you from one robbery.",
    "luck_potion": "Consumable: greatly boosts your luck in the next game.",
    "horseshoe": "Passive: permanent +5% win chance in all games.",
    "custom_role": "One‑time purchase: grants you the `Custom Role` role for 2 months.",
}
SHOP_ITEMS = list(ITEM_PRICES.keys())

def get_item_by_index(index: int):
    return SHOP_ITEMS[index - 1] if 1 <= index <= len(SHOP_ITEMS) else None

def parse_amount_str(s: str) -> int:
    s = s.lower().replace(",", "").strip()
    if s == "all":
        return 0
    match = re.match(r"^([\d.]+)([kmb]?)$", s)
    if not match:
        raise ValueError("Invalid amount format")
    num = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k": num *= 1_000
    elif suffix == "m": num *= 1_000_000
    elif suffix == "b": num *= 1_000_000_000
    return int(num)

def format_coins(amount: int) -> str:
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if amount >= 1_000_000_000: return f"{sign}{amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000: return f"{sign}{amount / 1_000_000:.1f}M"
    if amount >= 1_000: return f"{sign}{amount / 1_000:.1f}k"
    return f"{sign}{amount}"

def base_embed(ctx, title: str, color=MAIN_COLOR, user: discord.User = None):
    embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
    if ctx.guild:
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
    if user:
        embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    embed.description = BREAKER_LINE
    return embed

def spin_embed(ctx, title: str, description: str, user=None):
    embed = base_embed(ctx, title, SPIN_COLOR, user=user or ctx.author)
    embed.description = f"{description}\n{BREAKER_LINE}"
    return embed

def result_embed(ctx, title, description, win=True, user=None):
    color = WIN_COLOR if win else LOSS_COLOR
    emoji = random.choice([WIN_STAR_1, WIN_STAR_2, WIN_STAR_3]) if win else LOSS_HEART
    embed = base_embed(ctx, f"{emoji} {title}", color, user=user or ctx.author)
    embed.description = f"{description}\n{BREAKER_LINE}"
    embed.set_footer(text=f"Outcome: {'Win' if win else 'Loss'} • {ctx.author}", icon_url=ctx.author.display_avatar.url)
    return embed

def cooldown_embed(ctx, remaining):
    embed = base_embed(ctx, f"{COOLDOWN} Cooldown", LOSS_COLOR, user=ctx.author)
    total_secs = max(1, int(remaining))
    if total_secs <= 86400:
        bar_len = 10
        filled = int(total_secs / 86400 * bar_len)
        bar = "█" * max(1, filled) + "░" * (bar_len - max(1, filled))
    else:
        bar = "░░░░░░░░░░"
    embed.add_field(name="Time left", value=f"`{total_secs}s`\n[{bar}]", inline=False)
    return embed

def cd(uid, cmd, t):
    key = f"{uid}:{cmd}"
    now = time.time()
    if key in cooldowns:
        if now - cooldowns[key] < t:
            return int(t - (now - cooldowns[key]))
    cooldowns[key] = now
    return None

# ─── PER-USER LOCKS ───────────────────────────────────────────
_user_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

def user_lock(uid: int) -> asyncio.Lock:
    return _user_locks[uid]

# ─── VIEWS ──────────────────────────────────────────────────────
class CoinFlipView(discord.ui.View):
    def __init__(self, ctx, bet, has_luck, cog):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.bet = bet
        self.has_luck = has_luck
        self.cog = cog
        self.finished = False

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        await self.cog.add_wallet(self.ctx.author.id, -self.bet)
        wallet = await self.cog.get_wallet(self.ctx.author.id)
        embed = result_embed(self.ctx, f"{COOLDOWN} Time's Up!", f"You took too long and lost **{self.bet:,}** Moon Dust.", win=False)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        for child in self.children:
            child.disabled = True
        if hasattr(self, 'message'):
            await self.message.edit(embed=embed, view=self)
        self.cog.active_games.discard(self.ctx.author.id)

    @discord.ui.button(label="Heads", style=discord.ButtonStyle.primary)
    async def heads(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.flip(interaction, "heads")

    @discord.ui.button(label="Tails", style=discord.ButtonStyle.primary)
    async def tails(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.flip(interaction, "tails")

    async def flip(self, interaction: discord.Interaction, choice: str):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        if self.finished:
            return
        self.finished = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        spin = spin_embed(self.ctx, f"{COIN} Coin Flip", "Flipping...", user=self.ctx.author)
        await interaction.edit_original_response(embed=spin)
        await asyncio.sleep(0.4)
        async with user_lock(self.ctx.author.id):
            p = 0.25 + BASE_CHANCE_BOOST
            if await self.cog.has_item_equipped(self.ctx.author.id, "horseshoe"):
                p += 0.05
            if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in self.ctx.author.roles):
                p += 0.03
            if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in self.ctx.author.roles):
                p += 0.20
            if self.has_luck:
                await self.cog.consume_item(self.ctx.author.id, "luck_potion")
                p = 0.70 + (0.03 if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in self.ctx.author.roles) else 0) + (0.20 if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in self.ctx.author.roles) else 0)
            p = min(p, 0.95)
            outcome = choice if random.random() < p else ("tails" if choice == "heads" else "heads")
            win = outcome == choice
            net = self.bet if win else -self.bet
            await self.cog.add_wallet(self.ctx.author.id, net)
            wallet = await self.cog.get_wallet(self.ctx.author.id)
        desc = f"You chose **{choice}**\nResult: **{outcome}**\n{'You doubled your Moon Dust!' if win else f'You lost {self.bet:,} Moon Dust'}"
        final_embed = result_embed(self.ctx, f"{COIN} Coin Flip", desc, win=win, user=self.ctx.author)
        final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await interaction.edit_original_response(embed=final_embed)
        self.cog.active_games.discard(self.ctx.author.id)
        await self.cog.check_broke(self.ctx.author.id)

class RouletteView(discord.ui.View):
    RED_NUMBERS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}

    def __init__(self, ctx, bet, has_luck, cog):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.bet = bet
        self.has_luck = has_luck
        self.cog = cog
        self.finished = False
        self.CHOICES = {
            "0": {"label": "0", "description": "3x payout", "emoji": GREEN_CIRCLE, "multiplier": 3},
            "1-12": {"label": "1-12", "description": "2x payout", "emoji": NUM_1, "multiplier": 2},
            "13-24": {"label": "13-24", "description": "2x payout", "emoji": NUM_2, "multiplier": 2},
            "25-36": {"label": "25-36", "description": "2x payout", "emoji": NUM_3, "multiplier": 2},
            "Red": {"label": "Red", "description": "2x payout", "emoji": RED_CIRCLE, "multiplier": 2},
            "Black": {"label": "Black", "description": "2x payout", "emoji": BLACK_CIRCLE, "multiplier": 2},
        }
        options = [discord.SelectOption(label=v["label"], description=v["description"], emoji=v["emoji"]) for v in self.CHOICES.values()]
        select = discord.ui.Select(placeholder="Choose your bet...", options=options)
        select.callback = self.spin_callback
        self.add_item(select)

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        await self.cog.add_wallet(self.ctx.author.id, -self.bet)
        wallet = await self.cog.get_wallet(self.ctx.author.id)
        embed = result_embed(self.ctx, f"{COOLDOWN} Time's Up!", f"You took too long and lost **{self.bet:,}** Moon Dust.", win=False)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        for child in self.children:
            child.disabled = True
        if hasattr(self, 'message'):
            await self.message.edit(embed=embed, view=self)
        self.cog.active_games.discard(self.ctx.author.id)

    async def spin_callback(self, interaction: discord.Interaction):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        if self.finished:
            return
        self.finished = True
        await interaction.response.defer()
        self.clear_items()
        await interaction.edit_original_response(view=self)
        choice = interaction.data["values"][0]
        multiplier = self.CHOICES[choice]["multiplier"]
        spinning = spin_embed(self.ctx, f"{ROULETTE} Roulette", "Spinning...", user=self.ctx.author)
        await interaction.edit_original_response(embed=spinning)
        await asyncio.sleep(1.5)
        async with user_lock(self.ctx.author.id):
            if self.has_luck:
                await self.cog.consume_item(self.ctx.author.id, "luck_potion")
                p = 0.20 if choice == "0" else 0.40
            else:
                p = (0.20 if choice == "0" else 0.30) + BASE_CHANCE_BOOST
            if await self.cog.has_item_equipped(self.ctx.author.id, "horseshoe"): p += 0.05
            if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in self.ctx.author.roles): p += 0.03
            if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in self.ctx.author.roles): p += 0.20
            p = min(p, 0.95)
            win = random.random() < p
            if win:
                if choice == "0": number = 0
                elif choice == "Red": number = random.choice(list(self.RED_NUMBERS))
                elif choice == "Black": number = random.choice([n for n in range(1,37) if n not in self.RED_NUMBERS])
                elif choice == "1-12": number = random.randint(1,12)
                elif choice == "13-24": number = random.randint(13,24)
                else: number = random.randint(25,36)
            else:
                if choice == "0": number = random.randint(1,36)
                elif choice == "Red": number = random.choice([n for n in range(1,37) if n not in self.RED_NUMBERS])
                elif choice == "Black": number = random.choice(list(self.RED_NUMBERS))
                elif choice == "1-12": number = random.randint(13,36)
                elif choice == "13-24":
                    low = random.randint(1,12)
                    high = random.randint(25,36)
                    number = random.choice([low, high])
                else: number = random.randint(1,24)
            net = self.bet * (multiplier - 1) if win else -self.bet
            await self.cog.add_wallet(self.ctx.author.id, net)
            wallet = await self.cog.get_wallet(self.ctx.author.id)
        colour = "green" if number == 0 else ("red" if number in self.RED_NUMBERS else "black")
        colour_emoji = {"green": GREEN_CIRCLE, "red": RED_CIRCLE, "black": BLACK_CIRCLE}[colour]
        outcome_text = f"Landed on **{number}** {colour_emoji}"
        desc = f"{outcome_text}\nYour bet: **{choice}**\n{'You won **' + format_coins(self.bet * multiplier) + '** Moon Dust!' if win else f'You lost **{format_coins(self.bet)}** Moon Dust'}"
        final_embed = result_embed(self.ctx, f"{ROULETTE} Roulette", desc, win=win, user=self.ctx.author)
        final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await interaction.edit_original_response(embed=final_embed)
        self.cog.active_games.discard(self.ctx.author.id)
        await self.cog.check_broke(self.ctx.author.id)

class BlackjackView(discord.ui.View):
    def __init__(self, ctx, bet, cog):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.bet = bet
        self.cog = cog
        self.deck = [2,3,4,5,6,7,8,9,10,10,10,10,11]*4
        random.shuffle(self.deck)
        while True:
            self.player_cards = [self.deck.pop(), self.deck.pop()]
            self.player_value = sum(self.player_cards)
            if 12 <= self.player_value <= 16:
                break
            self.deck.extend(self.player_cards)
            random.shuffle(self.deck)
        self.dealer_cards = [10, random.choice([2,3,4,5,6,7,8,9,10,10,10,11])]
        self.dealer_value = sum(self.dealer_cards)
        self.finished = False

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        await self.cog.add_wallet(self.ctx.author.id, -self.bet)
        wallet = await self.cog.get_wallet(self.ctx.author.id)
        embed = result_embed(self.ctx, f"{COOLDOWN} Time's Up!", f"You took too long and lost **{self.bet:,}** Moon Dust.", win=False)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        for child in self.children:
            child.disabled = True
        if hasattr(self, 'message'):
            await self.message.edit(embed=embed, view=self)
        self.cog.active_games.discard(self.ctx.author.id)

    def hand_str(self, cards):
        return " ".join(str(c) for c in cards)

    def build_embed(self, hide_dealer=True):
        dealer_show = self.dealer_cards[0] if hide_dealer else self.dealer_value
        embed = base_embed(self.ctx, f"{CARDS} Blackjack", SPIN_COLOR, user=self.ctx.author)
        embed.description = (
            f"**Dealer:** {dealer_show}{' ?' if hide_dealer else ''}\n"
            f"**You:** {self.hand_str(self.player_cards)} (={self.player_value})\n"
            f"{BREAKER_LINE}"
        )
        if self.finished:
            if self.player_value > 21:
                embed.description += "\n**Bust! You lost.**"
                embed.color = LOSS_COLOR
            elif self.dealer_value > 21 or self.player_value > self.dealer_value:
                embed.description += "\n**You win!**"
                embed.color = WIN_COLOR
            elif self.player_value == self.dealer_value:
                embed.description += "\n**Push!** Bet returned."
                embed.color = MAIN_COLOR
            else:
                embed.description += "\n**Dealer wins.**"
                embed.color = LOSS_COLOR
        return embed

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.green)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            return await interaction.response.send_message("Game over.", ephemeral=True)
        self.player_cards.append(self.deck.pop())
        self.player_value = sum(self.player_cards)
        if self.player_value > 21:
            self.finished = True
            await self.end_game(interaction)
        else:
            embed = self.build_embed()
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.red)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.finished:
            return await interaction.response.send_message("Game over.", ephemeral=True)
        while self.dealer_value < 17:
            self.dealer_cards.append(self.deck.pop())
            self.dealer_value = sum(self.dealer_cards)
        self.finished = True
        await self.end_game(interaction)

    async def end_game(self, interaction):
        for child in self.children:
            child.disabled = True
        embed = self.build_embed(hide_dealer=False)
        if self.player_value > 21:
            net = -self.bet
        elif self.dealer_value > 21 or self.player_value > self.dealer_value:
            net = self.bet
        elif self.player_value == self.dealer_value:
            net = 0
        else:
            net = -self.bet
        async with user_lock(self.ctx.author.id):
            if net != 0:
                await self.cog.add_wallet(self.ctx.author.id, net)
            wallet = await self.cog.get_wallet(self.ctx.author.id)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)
        self.cog.active_games.discard(self.ctx.author.id)
        await self.cog.check_broke(self.ctx.author.id)

class HighLowView(discord.ui.View):
    def __init__(self, ctx, bet, first_card, cog):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.bet = bet
        self.first_card = first_card
        self.cog = cog
        self.finished = False

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        await self.cog.add_wallet(self.ctx.author.id, -self.bet)
        wallet = await self.cog.get_wallet(self.ctx.author.id)
        embed = result_embed(self.ctx, f"{COOLDOWN} Time's Up!", f"You took too long and lost **{self.bet:,}** Moon Dust.", win=False)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        for child in self.children:
            child.disabled = True
        if hasattr(self, 'message'):
            await self.message.edit(embed=embed, view=self)
        self.cog.active_games.discard(self.ctx.author.id)

    @discord.ui.button(label="Higher", style=discord.ButtonStyle.primary)
    async def higher(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play(interaction, "higher")

    @discord.ui.button(label="Lower", style=discord.ButtonStyle.primary)
    async def lower(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play(interaction, "lower")

    async def play(self, interaction: discord.Interaction, choice: str):
        if interaction.user != self.ctx.author:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        if self.finished:
            return
        self.finished = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        async with user_lock(self.ctx.author.id):
            has_luck = await self.cog.has_item_equipped(self.ctx.author.id, "luck_potion")
            p = 0.25 + BASE_CHANCE_BOOST
            if await self.cog.has_item_equipped(self.ctx.author.id, "horseshoe"): p += 0.05
            if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in self.ctx.author.roles): p += 0.03
            if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in self.ctx.author.roles): p += 0.20
            if has_luck:
                await self.cog.consume_item(self.ctx.author.id, "luck_potion")
                p = 0.70 + (0.03 if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in self.ctx.author.roles) else 0) + (0.20 if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in self.ctx.author.roles) else 0)
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
            win = (choice == "higher" and next_card > self.first_card) or (choice == "lower" and next_card < self.first_card)
            net = self.bet if win else -self.bet
            await self.cog.add_wallet(self.ctx.author.id, net)
            wallet = await self.cog.get_wallet(self.ctx.author.id)
        desc = f"Card: **{next_card}**\n{'You won **' + str(net) + '** Moon Dust!' if win else 'You lost **' + str(self.bet) + '** Moon Dust'}"
        final_embed = result_embed(self.ctx, f"{CARDS} Higher or Lower", desc, win=win, user=self.ctx.author)
        final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await interaction.edit_original_response(embed=final_embed)
        self.cog.active_games.discard(self.ctx.author.id)
        await self.cog.check_broke(self.ctx.author.id)

class ConfirmResetView(discord.ui.View):
    def __init__(self, bot, author: discord.Member):
        super().__init__(timeout=30)
        self.value = None
        self.bot = bot
        self.author = author

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("Only the command author can confirm this.", ephemeral=True)
        self.value = True
        await interaction.response.send_message("Resetting economy...", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return await interaction.response.send_message("Only the command author can cancel this.", ephemeral=True)
        self.value = False
        await interaction.response.send_message("Cancelled.", ephemeral=True)
        self.stop()

class HeistJoinView(discord.ui.View):
    def __init__(self, cog, host, target, channel):
        super().__init__(timeout=120)
        self.cog = cog
        self.host = host
        self.target = target
        self.participants = set()
        self.locked = False
        self.channel = channel
        join_button = discord.ui.Button(label="Join Heist", style=discord.ButtonStyle.danger, emoji=HEIST)
        join_button.callback = self.join
        self.add_item(join_button)

    def build_lobby_embed(self):
        filled = len(self.participants)
        needed = 5 - filled
        bar = GREEN_SQUARE * filled + WHITE_SQUARE * (needed if needed > 0 else 0)
        desc = f"**Heist Target:** {self.target.mention}\n**Host:** {self.host.mention}\n**Participants:** {filled}/5 {bar}\n{'─'*30}\n"
        if self.participants:
            desc += "**Joining:**\n" + "\n".join(f"• <@{uid}>" for uid in self.participants)
        else:
            desc += "*Waiting for participants...*"
        embed = discord.Embed(title=f"{HEIST} Heist Lobby", description=desc, color=0x8B0000)
        embed.set_footer(text=f"Target's Bank: {self.target.display_name} | Need 5 to start")
        return embed

    async def join(self, interaction: discord.Interaction):
        if self.locked:
            return await interaction.response.send_message("Heist already starting...", ephemeral=True)
        if interaction.user.id in self.participants:
            return await interaction.response.send_message("You're already in!", ephemeral=True)
        if interaction.user.id == self.target.id:
            return await interaction.response.send_message("You can't heist yourself!", ephemeral=True)
        if len(self.participants) >= 5:
            return await interaction.response.send_message("The heist is full.", ephemeral=True)
        if not await self.cog.is_registered(interaction.user.id):
            return await interaction.response.send_message("You need to `/register` before joining a heist!", ephemeral=True)
        self.participants.add(interaction.user.id)
        await interaction.response.send_message(f"You joined! ({len(self.participants)}/5)", ephemeral=True)
        embed = self.build_lobby_embed()
        if len(self.participants) >= 5:
            self.locked = True
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(embed=embed, view=self)
            await self.start_heist()
        else:
            await interaction.message.edit(embed=embed, view=self)

    async def start_heist(self):
        if len(self.participants) < 5:
            return
        self.locked = True
        game = HeistGame(self.channel, self.host, self.target, list(self.participants), self.cog)
        await game.start()

class HeistGame:
    def __init__(self, channel, host, target, participants, cog):
        self.channel = channel
        self.host = host
        self.target = target
        self.participants = participants
        self.cog = cog

    async def start(self):
        target_bank = await self.cog.get_bank(self.target.id)
        if target_bank <= 0:
            embed = discord.Embed(title=f"{WRONG} Heist Failed", description=f"{self.target.mention} has no Moon Dust in their bank!", color=LOSS_COLOR)
            return await self.channel.send(embed=embed)
        embed = discord.Embed(
            title=f"{HEIST} Heist in Progress",
            description=f"**Team:** {', '.join(f'<@{uid}>' for uid in self.participants[:3])} +{len(self.participants)-3} more\n**Target:** {self.target.mention}\n**Bank:** {target_bank:,} Moon Dust\n\n{LOOP} Attempting heist...",
            color=SPIN_COLOR
        )
        msg = await self.channel.send(embed=embed)
        await asyncio.sleep(2)
        success = random.random() < 0.35
        if success:
            steal_amount = int(target_bank * 0.5)
            per_person = steal_amount // len(self.participants)
            await self.cog.add_bank(self.target.id, -steal_amount)
            for pid in self.participants:
                await self.cog.add_wallet(pid, per_person)
            result_desc = f"{WIN_STAR_2} **HEIST SUCCESSFUL!**\n\n"
            result_desc += f"**Stolen:** `{steal_amount:,}` Moon Dust from {self.target.mention}'s bank\n"
            result_desc += f"**Per Person:** `{per_person:,}` Moon Dust\n"
            result_desc += f"**Reward Distribution:**\n" + "\n".join(f"• <@{uid}>: `{per_person:,}` Moon Dust" for uid in self.participants[:5])
            if len(self.participants) > 5:
                result_desc += f"\n• +{len(self.participants)-5} more participants"
            result_embed = discord.Embed(title=f"{HEIST} Heist Complete", description=result_desc, color=WIN_COLOR)
            result_embed.add_field(name=f"{self.target.display_name}'s Remaining Bank", value=f"`{target_bank - steal_amount:,}` Moon Dust", inline=False)
        else:
            fail_desc = f"{LOSS_HEART} **HEIST FAILED!**\n\n"
            fail_desc += f"You got caught trying to rob {self.target.mention}'s bank!\n"
            fail_desc += f"**Fine Paid:** `100-300` Moon Dust per person"
            result_embed = discord.Embed(title=f"{HEIST} Heist Complete", description=fail_desc, color=LOSS_COLOR)
            for pid in self.participants:
                fine = random.randint(100, 300)
                await self.cog.add_wallet(pid, -fine)
        await msg.edit(embed=result_embed)
        for pid in self.participants:
            await self.cog.check_broke(pid)
        await self.cog.check_broke(self.target.id)

class RussianRouletteJoinView(discord.ui.View):
    def __init__(self, cog, host, entry_fee, max_players, channel):
        super().__init__(timeout=120)
        self.cog = cog
        self.host = host
        self.entry_fee = entry_fee
        self.max_players = max_players
        self.players = set()
        self.locked = False
        self.channel = channel
        join_button = discord.ui.Button(label="Join", style=discord.ButtonStyle.green, emoji=SKULL)
        join_button.callback = self.join
        self.add_item(join_button)

    def build_lobby_embed(self):
        filled = len(self.players)
        empty = self.max_players - filled
        bar = GREEN_SQUARE * filled + WHITE_SQUARE * empty
        desc = f"**Host:** {self.host.mention}\n**Entry fee:** {self.entry_fee} Moon Dust\n**Slots:** {filled}/{self.max_players} {bar}\n{'─'*20}\n"
        if self.players:
            desc += "**Current players:**\n" + "\n".join(f"• <@{uid}>" for uid in self.players)
        else:
            desc += "*No one has joined yet. Be the first!*"
        embed = discord.Embed(title=f"{SKULL} Russian Roulette – Lobby", description=desc, color=0x800080)
        embed.set_footer(text="Click Join to enter! Game starts when full.")
        return embed

    async def join(self, interaction: discord.Interaction):
        if self.locked:
            return await interaction.response.send_message("Game already starting...", ephemeral=True)
        if interaction.user.id in self.players:
            return await interaction.response.send_message("You're already in!", ephemeral=True)
        if len(self.players) >= self.max_players:
            return await interaction.response.send_message("The game is full.", ephemeral=True)
        if not await self.cog.is_registered(interaction.user.id):
            return await interaction.response.send_message("You need to `/register` before joining!", ephemeral=True)
        async with user_lock(interaction.user.id):
            wallet = await self.cog.get_wallet(interaction.user.id)
            if wallet < self.entry_fee:
                return await interaction.response.send_message(f"You need **{self.entry_fee}** Moon Dust in your wallet to join.", ephemeral=True)
            await self.cog.add_wallet(interaction.user.id, -self.entry_fee)
        self.players.add(interaction.user.id)
        await interaction.response.send_message(f"You're in! ({len(self.players)}/{self.max_players})", ephemeral=True)
        embed = self.build_lobby_embed()
        if len(self.players) >= self.max_players:
            self.locked = True
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(embed=embed, view=self)
            await self.start_game()
        else:
            await interaction.message.edit(embed=embed, view=self)

    async def start_game(self):
        if len(self.players) < 2:
            return
        self.locked = True
        game = RussianRouletteGame(self.channel, self.host, self.entry_fee, list(self.players), self.cog)
        self.cog.russian_games[self.host.guild.id] = game
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
            f"{BOOM} **BANG!** The bullet found its mark…",
            f"{GUN} **click…** wait, no — **BANG!**",
            f"The chamber spun, and fate was sealed. {SKULL}",
            "One loud bang, one less player.",
            f"Luck ran out. {BOOM}",
            "A sudden shot echoes. **{loser}** is gone.",
        ]

    async def start(self):
        embed = discord.Embed(
            title=f"{SKULL} Russian Roulette",
            description=f"**{len(self.alive)}** players, **{self.pot}** Moon Dust pot.\nThe chamber is loaded…",
            color=SPIN_COLOR
        )
        embed.add_field(name="Survivors", value="\n".join(f"• <@{uid}>" for uid in self.alive), inline=False)
        msg = await self.channel.send(embed=embed)
        while len(self.alive) > 1:
            spinning = discord.Embed(title=f"{SKULL} Russian Roulette", description=f"{LOOP} **Spinning the cylinder…**", color=SPIN_COLOR)
            await msg.edit(embed=spinning)
            await asyncio.sleep(1)
            spinning.description = "🌀 **Click...**"
            await msg.edit(embed=spinning)
            await asyncio.sleep(1)
            loser = random.choice(self.alive)
            self.alive.remove(loser)
            death_msg = random.choice(self._death_messages()).format(loser=f"<@{loser}>")
            elim_embed = discord.Embed(title=f"{SKULL} Russian Roulette", description=death_msg, color=LOSS_COLOR)
            elim_embed.add_field(name="Remaining", value="\n".join(f"• <@{uid}>" for uid in self.alive) if self.alive else "None", inline=False)
            elim_embed.set_footer(text=f"Pot: {self.pot} Moon Dust | {len(self.alive)}/{self.total} left")
            await msg.edit(embed=elim_embed)
            await asyncio.sleep(2)
        winner = self.alive[0]
        await self.cog.add_wallet(winner, self.pot)
        win_embed = discord.Embed(title=f"{WIN_STAR_2} Winner! Russian Roulette", description=f"<@{winner}> survived and takes home **{self.pot}** Moon Dust!", color=WIN_COLOR)
        win_embed.add_field(name="Champion", value=f"<@{winner}>", inline=False)
        win_embed.set_footer(text="Russian Roulette – Game Over")
        await msg.edit(embed=win_embed)
        self.cog.russian_games.pop(self.channel.id, None)

class LeaderboardView(discord.ui.View):
    def __init__(self, cog, ctx):
        super().__init__(timeout=120)
        self.cog = cog
        self.ctx = ctx
        self.current = "wallet"
        self.MEDALS = [GOLD_MEDAL, SILVER_MEDAL, BRONZE_MEDAL] + ["🔹"] * 7
        select = discord.ui.Select(
            placeholder="Choose a leaderboard...",
            options=[
                discord.SelectOption(label="All-Time Wallet", value="wallet", emoji=WALLET, default=True),
                discord.SelectOption(label="All-Time Bank", value="bank", emoji=BANK),
                discord.SelectOption(label="All-Time Net Worth", value="net", emoji=CROWN),
                discord.SelectOption(label="Weekly Gains", value="weekly", emoji=CROWN),
                discord.SelectOption(label="Monthly Gains", value="monthly", emoji=CALENDAR),
            ]
        )
        select.callback = self.on_select
        self.add_item(select)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass

    async def on_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        self.current = value
        for item in self.children:
            if isinstance(item, discord.ui.Select):
                for opt in item.options:
                    opt.default = (opt.value == value)
        embed = await self.build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _resolve_name(self, uid):
        member = self.ctx.guild.get_member(uid) if self.ctx.guild else None
        if member:
            return member.display_name
        try:
            user = await self.cog.bot.fetch_user(uid)
            return user.display_name
        except (discord.NotFound, discord.HTTPException):
            return f"Unknown ({uid})"

    async def build_embed(self):
        if self.current == "wallet":
            rows = await self.cog._get_top_10("wallet", self.ctx)
            title = f"{TOP_WALLET} All-Time Wallet Leaderboard"
            label, suffix = "Wallet", "Moon Dust"
        elif self.current == "bank":
            rows = await self.cog._get_top_10("bank", self.ctx)
            title = f"{TOP_BANK} All-Time Bank Leaderboard"
            label, suffix = "Bank", "Moon Dust"
        elif self.current == "net":
            rows = await self.cog._get_top_10_net(self.ctx)
            title = f"{CROWN} All-Time Net Worth Leaderboard"
            label, suffix = "Net Worth", "Moon Dust"
        elif self.current == "weekly":
            rows = await self.cog._get_top_gains(self.ctx, days=7)
            title = f"{CROWN} Weekly Top Earners (last 7 days)"
            label, suffix = "Gained", "Moon Dust"
        else:
            rows = await self.cog._get_top_gains(self.ctx, days=30)
            title = f"{CALENDAR} Monthly Top Earners (last 30 days)"
            label, suffix = "Gained", "Moon Dust"
        embed = discord.Embed(title=title, color=GOLD_COLOR, timestamp=discord.utils.utcnow())
        if self.ctx.guild:
            embed.set_author(name=self.ctx.guild.name, icon_url=self.ctx.guild.icon.url if self.ctx.guild.icon else None)
        if not rows:
            embed.description = "*Not enough data yet for this leaderboard.*"
        else:
            lines = []
            for idx, (uid, value) in enumerate(rows):
                medal = self.MEDALS[idx] if idx < len(self.MEDALS) else f"`#{idx + 1}`"
                name = await self._resolve_name(uid)
                sign = "+" if self.current in ("weekly", "monthly") and value > 0 else ""
                lines.append(f"{medal} **{name}** — `{sign}{format_coins(value)}` {suffix}")
            embed.description = BREAKER_LINE + "\n" + "\n".join(lines) + "\n" + BREAKER_LINE
        embed.set_footer(text=f" {label} leaderboard \n• Use the dropdown to switch views \n• Requested by {self.ctx.author}",
                         icon_url=self.ctx.author.display_avatar.url)
        return embed

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

    async def cog_load(self):
        self.pool = await asyncpg.create_pool(DB_URL, min_size=5, max_size=20)
        await self._init_db()
        self._load_collect_config()

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
        if self.pool:
            await self.pool.close()

    def is_owner(self, user: discord.User) -> bool:
        return user.id == get_owner_id()

    def is_staff(self, user: discord.Member) -> bool:
        if not isinstance(user, discord.Member):
            return False
        return any(role.id in self.staff_role_ids for role in user.roles)

    def _load_collect_config(self):
        DEFAULT_AMOUNTS = {
            "admin": 16000,
            "senior_moderator": 15000,
            "moderator": 14000,
            "trial_moderator": 12000,
            "developer": 13000,
            "event_manager": 14000,
            "event_trial": 12000,
            "level_100": 12000,
            "level_90": 10000,
            "level_80": 8000,
            "level_70": 6000,
            "level_60": 4000,
            "level_50": 2000,
            "level_40": 1000,
            "level_30": 800,
            "level_20": 600,
            "level_10": 400,
            "level_1": 200,
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

    def get_collect_amount(self, member: discord.Member) -> int:
        best = 0
        for role in member.roles:
            amt = self.collect_roles.get(role.id, 0)
            if amt > best:
                best = amt
        return best

    # ─── DB Helper Methods ──────────────────────────────────────
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
                        wallet BIGINT NOT NULL DEFAULT 1000,
                        bank BIGINT NOT NULL DEFAULT 0,
                        xp BIGINT DEFAULT 0
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
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_user_time ON balance_snapshots (user_id, taken_at)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_wallet ON users (wallet DESC)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_bank ON users (bank DESC)")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_net ON users ((wallet + bank) DESC)")
                await conn.execute("ANALYZE")

    # ─── Economy DB helpers ──────────────────────────────────────
    async def get_wallet(self, uid):
        row = await self._fetchrow("SELECT wallet FROM users WHERE id=$1", uid)
        return row['wallet'] if row else 0

    async def get_bank(self, uid):
        row = await self._fetchrow("SELECT bank FROM users WHERE id=$1", uid)
        return row['bank'] if row else 0

    async def is_registered(self, uid) -> bool:
        return await self._fetchval("SELECT 1 FROM users WHERE id=$1", uid) is not None

    async def register_user(self, uid, starting_wallet=1000):
        await self._execute(
            "INSERT INTO users (id, wallet) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
            uid, starting_wallet
        )

    async def add_wallet(self, uid, amount):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", uid)
                await conn.execute("UPDATE users SET wallet = wallet + $1 WHERE id=$2", amount, uid)

    async def add_bank(self, uid, amount):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", uid)
                await conn.execute("UPDATE users SET bank = bank + $1 WHERE id=$2", amount, uid)

    async def move_wallet_to_bank(self, uid, amount):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", uid)
                await conn.execute("UPDATE users SET wallet = wallet - $1 WHERE id=$2", amount, uid)
                await conn.execute("UPDATE users SET bank = bank + $1 WHERE id=$2", amount, uid)

    async def move_bank_to_wallet(self, uid, amount):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", uid)
                await conn.execute("UPDATE users SET bank = bank - $1 WHERE id=$2", amount, uid)
                await conn.execute("UPDATE users SET wallet = wallet + $1 WHERE id=$2", amount, uid)

    async def set_wallet(self, uid, amount):
        if amount < 0:
            amount = 0
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", uid)
                await conn.execute("UPDATE users SET wallet = $1 WHERE id=$2", amount, uid)

    async def set_bank(self, uid, amount):
        if amount < 0:
            amount = 0
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", uid)
                await conn.execute("UPDATE users SET bank = $1 WHERE id=$2", amount, uid)

    async def has_item_equipped(self, user_id, item_name):
        return await self._fetchval("SELECT 1 FROM equipped WHERE user_id=$1 AND item=$2", user_id, item_name) is not None

    async def consume_item(self, user_id, item_name):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM equipped WHERE user_id=$1 AND item=$2", user_id, item_name)
                await conn.execute("UPDATE inventory SET qty = qty - 1 WHERE user_id=$1 AND item=$2", user_id, item_name)
                await conn.execute("DELETE FROM inventory WHERE user_id=$1 AND item=$2 AND qty <= 0", user_id, item_name)

    async def add_item(self, user_id, item_name, quantity):
        await self._execute(
            """INSERT INTO inventory (user_id, item, qty) VALUES ($1, $2, $3)
               ON CONFLICT (user_id, item) DO UPDATE SET qty = inventory.qty + EXCLUDED.qty""",
            user_id, item_name, quantity
        )

    async def require_registered(self, ctx) -> bool:
        if await self.is_registered(ctx.author.id):
            return True
        embed = result_embed(
            ctx, "Not Registered",
            "You need to open an account first! Use `/register` to get started.",
            win=False
        )
        await ctx.send(embed=embed)
        return False

    async def log_command_use(self, ctx):
        wallet = await self.get_wallet(ctx.author.id)
        await log_to_discord(
            self.bot,
            f"**{ctx.author}** ({ctx.author.id}) used `{ctx.command.qualified_name}` "
            f"in #{ctx.channel} — balance before command: `{wallet:,}` Moon Dust"
        )

    # ─── BROKE-USER PURGE ────────────────────────────────────────
    async def check_broke(self, user_id: int):
        wallet = await self.get_wallet(user_id)
        bank = await self.get_bank(user_id)
        if wallet != 0 or bank != 0:
            return
        if not await self.is_registered(user_id):
            return
        await self._archive_and_purge(user_id, wallet, bank, reason="broke (wallet & bank both 0)")

    async def _archive_and_purge(self, user_id: int, last_wallet: int, last_bank: int, reason: str):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO purged_accounts (user_id, last_wallet, last_bank, reason, purged_at)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (user_id) DO UPDATE SET
                           last_wallet = EXCLUDED.last_wallet,
                           last_bank = EXCLUDED.last_bank,
                           reason = EXCLUDED.reason,
                           purged_at = EXCLUDED.purged_at""",
                    user_id, last_wallet, last_bank, reason, int(time.time())
                )
                await conn.execute("DELETE FROM users WHERE id = $1", user_id)
                await conn.execute("DELETE FROM inventory WHERE user_id = $1", user_id)
                await conn.execute("DELETE FROM equipped WHERE user_id = $1", user_id)
                await conn.execute("DELETE FROM custom_roles WHERE user_id = $1", user_id)
                await conn.execute("DELETE FROM left_users WHERE user_id = $1", user_id)
                await conn.execute("DELETE FROM balance_snapshots WHERE user_id = $1", user_id)
        try:
            user = await self.bot.fetch_user(user_id)
            label = f"{user} ({user_id})"
        except (discord.NotFound, discord.HTTPException):
            label = f"Unknown user ({user_id})"
        await log_to_discord(
            self.bot,
            f"<:Deleted:1537919221969846413> **Account purged** — {label}\n"
            f"Reason: {reason}\n"
            f"Final balance — Wallet: `{last_wallet:,}` Moon Dust | Bank: `{last_bank:,}` Moon Dust\n"
            f"Account has been removed from the database and all leaderboards."
        )

    # ─── TASKS ───────────────────────────────────────────────────
    @tasks.loop(hours=1)
    async def purge_broke_users(self):
        await self.bot.wait_until_ready()
        async with self.pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM users WHERE wallet = 0 AND bank = 0")
            if count == 0:
                return
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO purged_accounts (user_id, last_wallet, last_bank, reason, purged_at)
                       SELECT id, wallet, bank, 'broke (wallet & bank both 0) — periodic sweep', $1
                       FROM users WHERE wallet = 0 AND bank = 0
                       ON CONFLICT (user_id) DO UPDATE SET
                           last_wallet = EXCLUDED.last_wallet,
                           last_bank = EXCLUDED.last_bank,
                           reason = EXCLUDED.reason,
                           purged_at = EXCLUDED.purged_at""",
                    int(time.time())
                )
                await conn.execute("DELETE FROM inventory WHERE user_id IN (SELECT id FROM users WHERE wallet = 0 AND bank = 0)")
                await conn.execute("DELETE FROM equipped WHERE user_id IN (SELECT id FROM users WHERE wallet = 0 AND bank = 0)")
                await conn.execute("DELETE FROM custom_roles WHERE user_id IN (SELECT id FROM users WHERE wallet = 0 AND bank = 0)")
                await conn.execute("DELETE FROM left_users WHERE user_id IN (SELECT id FROM users WHERE wallet = 0 AND bank = 0)")
                await conn.execute("DELETE FROM balance_snapshots WHERE user_id IN (SELECT id FROM users WHERE wallet = 0 AND bank = 0)")
                await conn.execute("DELETE FROM users WHERE wallet = 0 AND bank = 0")
        await log_to_discord(self.bot, f"Periodic sweep purged {count} broke account(s).")

    @purge_broke_users.before_loop
    async def before_purge_broke_users(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=6)
    async def snapshot_balances(self):
        await self.bot.wait_until_ready()
        now = int(time.time())
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO balance_snapshots (user_id, taken_at, wallet, bank) SELECT id, $1, wallet, bank FROM users",
                    now
                )
                cutoff = now - (35 * 86400)
                await conn.execute("DELETE FROM balance_snapshots WHERE taken_at < $1", cutoff)

    @snapshot_balances.before_loop
    async def before_snapshot_balances(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def bank_interest(self):
        await self.bot.wait_until_ready()
        count = await self._fetchval("SELECT COUNT(*) FROM users WHERE bank > 0")
        if count:
            await self._execute("UPDATE users SET bank = bank + LEAST(bank / 50, 10000) WHERE bank > 0")
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
                if member and role:
                    try:
                        await member.remove_roles(role, reason="Custom role expired")
                    except (discord.Forbidden, discord.HTTPException):
                        pass
            await self._execute("DELETE FROM custom_roles WHERE user_id=$1 AND guild_id=$2", user_id, guild_id)

    @custom_role_check.before_loop
    async def before_custom_role_check(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def purge_left_users(self):
        await self.bot.wait_until_ready()
        now = int(time.time())
        rows = await self._fetch("SELECT user_id FROM left_users WHERE left_at < $1", now - 86400)
        for (user_id,) in rows:
            wallet = await self.get_wallet(user_id)
            bank = await self.get_bank(user_id)
            await self._archive_and_purge(user_id, wallet, bank, reason="left server (24h grace period expired)")
        if rows:
            await log_to_discord(self.bot, f"Purged economy data for {len(rows)} user(s) who left and didn't return within 24h.")

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
        row = await self._fetchrow("SELECT id, wallet FROM users ORDER BY wallet DESC LIMIT 1")
        if not row:
            return
        top_user_id = row['id']
        if top_user_id == self.current_top_user_id:
            return
        for member in guild.members:
            if role in member.roles and member.id != top_user_id:
                try:
                    await member.remove_roles(role, reason="No longer the top economy player")
                except discord.Forbidden:
                    pass
        member = guild.get_member(top_user_id)
        if member:
            if role not in member.roles:
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
            user_count = await self._fetchval("SELECT COUNT(*) FROM users")
            total_wallet = await self._fetchval("SELECT COALESCE(SUM(wallet),0) FROM users")
            total_bank = await self._fetchval("SELECT COALESCE(SUM(bank),0) FROM users")
            inventory_entries = await self._fetchval("SELECT COUNT(*) FROM inventory")
            await log_to_discord(
                self.bot,
                f"Registered users: {user_count} | Wallet total: {total_wallet} | Bank total: {total_bank} | Inventory entries: {inventory_entries}"
            )
        except Exception as e:
            await log_to_discord(self.bot, f"Hourly status failed: {e}")

    @hourly_status.before_loop
    async def before_hourly(self):
        await self.bot.wait_until_ready()

    # ─── EVENT LISTENERS ────────────────────────────────────────
    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        if ctx.cog is self and self.pool is not None:
            await self.log_command_use(ctx)
            await self.check_broke(ctx.author.id)

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
            title=f"{WAVE} Member Left – Data Snapshot",
            color=0xe74c3c,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="User", value=f"{member} ({member.id})", inline=False)
        embed.add_field(name="Wallet", value=f"{wallet:,} Moon Dust", inline=True)
        embed.add_field(name="Bank", value=f"{bank:,} Moon Dust", inline=True)
        embed.add_field(name="Inventory", value=inv_str, inline=False)
        embed.add_field(name="Equipped", value=equip_str, inline=False)
        embed.set_footer(text="Data will be permanently deleted after 24 hours unless they rejoin.")
        await log_channel.send(embed=embed)
        await self._execute(
            "INSERT INTO left_users (user_id, left_at, guild_id) VALUES ($1,$2,$3) ON CONFLICT (user_id) DO UPDATE SET left_at = EXCLUDED.left_at, guild_id = EXCLUDED.guild_id",
            member.id, int(time.time()), member.guild.id
        )

    # ─── COG CHECK (channel restrictions) ───────────────────────
    async def cog_check(self, ctx):
        if ctx.command.qualified_name == "economy":
            if self.is_owner(ctx.author) or self.is_staff(ctx.author):
                return True
            return False
        if self.is_owner(ctx.author) or self.is_staff(ctx.author):
            return True
        bypass = await self._fetchval(
            "SELECT 1 FROM economy_channel_bypass WHERE user_id=$1 AND channel_id=$2",
            ctx.author.id, ctx.channel.id
        )
        if bypass:
            return True
        if ALLOWED_CHANNEL_ID and ctx.channel.id == ALLOWED_CHANNEL_ID:
            return True
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        return False

    # ─── HELPER FOR LEADERBOARDS ────────────────────────────────
    async def _get_top_10(self, column, ctx):
        rows = await self._fetch(f"SELECT id, {column} FROM users ORDER BY {column} DESC LIMIT 30")
        filtered = []
        for r in rows:
            member = ctx.guild.get_member(r['id']) if ctx.guild else None
            if member and member.bot:
                continue
            filtered.append((r['id'], r[column]))
            if len(filtered) == 10:
                break
        return filtered

    async def _get_top_10_net(self, ctx):
        rows = await self._fetch("SELECT id, wallet, bank FROM users ORDER BY (wallet + bank) DESC LIMIT 30")
        filtered = []
        for r in rows:
            member = ctx.guild.get_member(r['id']) if ctx.guild else None
            if member and member.bot:
                continue
            filtered.append((r['id'], r['wallet'] + r['bank']))
            if len(filtered) == 10:
                break
        return filtered

    async def _get_top_gains(self, ctx, days: int):
        cutoff = int(time.time()) - (days * 86400)
        query = """
            WITH oldest_snap AS (
                SELECT user_id, wallet AS old_wallet, bank AS old_bank,
                       ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY taken_at ASC) AS rn
                FROM balance_snapshots
                WHERE taken_at >= $1
            )
            SELECT u.id, (u.wallet + u.bank) - (o.old_wallet + o.old_bank) AS gain
            FROM users u
            JOIN oldest_snap o ON o.user_id = u.id AND o.rn = 1
            WHERE (u.wallet + u.bank) - (o.old_wallet + o.old_bank) > 0
            ORDER BY gain DESC
            LIMIT 50
        """
        rows = await self._fetch(query, cutoff)
        gains = []
        for r in rows:
            member = ctx.guild.get_member(r['id']) if ctx.guild else None
            if member and member.bot:
                continue
            gains.append((r['id'], r['gain']))
            if len(gains) == 10:
                break
        return gains

    # ─── REGISTRATION ───────────────────────────────────────────
    @commands.hybrid_command(name="register", aliases=["start", "openaccount"])
    async def register(self, ctx: commands.Context):
        """Open your Moon Dust economy account to start earning and gambling."""
        if await self.is_registered(ctx.author.id):
            return await ctx.send(embed=result_embed(ctx, "Already Registered", "You already have an account! Use `/balance` to check your Moon Dust.", win=False))
        await self.register_user(ctx.author.id, starting_wallet=1000)
        await self._execute(
            "INSERT INTO balance_snapshots (user_id, taken_at, wallet, bank) VALUES ($1,$2,$3,$4)",
            ctx.author.id, int(time.time()), 1000, 0
        )
        embed = result_embed(
            ctx, f"{TICK} Account Created",
            f"Welcome! Your account has been opened with a starting balance of **1,000** {CURRENCY}.",
            win=True
        )
        await ctx.send(embed=embed)
        info_embed = base_embed(ctx, f"{WALLET} Available Commands", user=ctx.author)
        info_embed.description = f"Here's everything you can do now that you're registered:\n{BREAKER_LINE}"
        info_embed.add_field(name="Commands", value=GAMBLING_COMMANDS_LIST, inline=False)
        try:
            if ctx.interaction:
                await ctx.interaction.followup.send(embed=info_embed, ephemeral=True)
            else:
                await ctx.author.send(embed=info_embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.hybrid_command(name="registeredusers", aliases=["totalusers", "regcount"])
    async def registered_users(self, ctx: commands.Context):
        """View the total number of registered economy users."""
        count = await self._fetchval("SELECT COUNT(*) FROM users")
        embed = base_embed(ctx, f"{GLOBAL} Registered Users")
        embed.add_field(name="Total Registered", value=f"`{count:,}`", inline=False)
        await ctx.send(embed=embed)

    # ─── COLLECT COMMAND ────────────────────────────────────────
    @commands.hybrid_command(name="collect")
    async def collect(self, ctx: commands.Context):
        """Collect your role-based daily Moon Dust reward."""
        if not await self.require_registered(ctx):
            return
        user_id = ctx.author.id
        amount = self.get_collect_amount(ctx.author)
        if amount <= 0:
            return await ctx.send(embed=result_embed(ctx, "No Collect Role", "You don't have a role eligible for collect.", win=False))
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT last_collected FROM collect_cooldowns WHERE user_id=$1 FOR UPDATE", user_id)
                now = int(time.time())
                if row and now - row['last_collected'] < 86400:
                    remaining = 86400 - (now - row['last_collected'])
                    await ctx.send(embed=cooldown_embed(ctx, remaining))
                    return
                await conn.execute("INSERT INTO users (id) VALUES ($1) ON CONFLICT (id) DO NOTHING", user_id)
                await conn.execute("UPDATE users SET wallet = wallet + $1 WHERE id=$2", amount, user_id)
                await conn.execute(
                    "INSERT INTO collect_cooldowns (user_id, last_collected) VALUES ($1,$2) ON CONFLICT (user_id) DO UPDATE SET last_collected = EXCLUDED.last_collected",
                    user_id, now
                )
                wallet = await conn.fetchval("SELECT wallet FROM users WHERE id=$1", user_id)
        embed = result_embed(ctx, f"{GIFT} Collect", f"You collected **{amount:,}** Moon Dust!", win=True)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await ctx.send(embed=embed)

    # ─── ECONOMY COMMANDS ───────────────────────────────────────
    @commands.hybrid_command(name="balance", aliases=["bal"])
    async def balance(self, ctx: commands.Context):
        """Check your wallet and bank balance."""
        wallet = await self.get_wallet(ctx.author.id)
        bank = await self.get_bank(ctx.author.id)
        embed = base_embed(ctx, f"{WALLET} Balance", user=ctx.author)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=True)
        embed.add_field(name="Bank", value=f"{BANK} `{bank:,}` Moon Dust", inline=True)
        embed.add_field(name="Total", value=f"`{wallet + bank:,}` Moon Dust", inline=False)
        if not await self.is_registered(ctx.author.id):
            embed.set_footer(text="You haven't registered yet — use /register to start earning!")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="profile", aliases=["prof"])
    async def profile(self, ctx: commands.Context, user: discord.Member = None):
        """View someone's Moon Dust profile and balance."""
        user = user or ctx.author
        wallet = await self.get_wallet(user.id)
        bank = await self.get_bank(user.id)
        embed = base_embed(ctx, f"{USER_ID} {user.display_name}", user=user)
        embed.add_field(name="Wallet", value=f"{wallet:,} Moon Dust", inline=True)
        embed.add_field(name="Bank", value=f"{bank:,} Moon Dust", inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="daily", aliases=["dly"])
    async def daily(self, ctx: commands.Context):
        """Claim your daily Moon Dust reward (once per 24 hours)."""
        if not await self.require_registered(ctx):
            return
        rem = cd(ctx.author.id, "daily", 86400)
        if rem:
            return await ctx.send(embed=cooldown_embed(ctx, rem))
        base_reward = random.randint(800, 1500)
        if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles):
            base_reward = int(base_reward * 1.05)
        await self.add_wallet(ctx.author.id, base_reward)
        wallet = await self.get_wallet(ctx.author.id)
        embed = result_embed(ctx, f"{GIFT} Daily Reward", f"You received **{base_reward:,}** Moon Dust!", win=True)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="fish")
    async def fish(self, ctx: commands.Context, bet: str):
        """Go fishing for Moon Dust! 25% base chance to win."""
        if not await self.require_registered(ctx):
            return
        if ctx.author.id in self.active_games:
            return await ctx.send(embed=result_embed(ctx, "Game in Progress", "You're already playing a game! Finish it first.", win=False))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            return
        if not self.is_owner(ctx.author):
            rem = cd(ctx.author.id, "fish", GAME_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        self.active_games.add(ctx.author.id)
        try:
            async with user_lock(ctx.author.id):
                wallet = await self.get_wallet(ctx.author.id)
                if wallet < amount:
                    self.active_games.discard(ctx.author.id)
                    return await ctx.send(embed=result_embed(ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet.", win=False))
                p = 0.25 + BASE_CHANCE_BOOST
                if await self.has_item_equipped(ctx.author.id, "rod"): p += 0.05
                if await self.has_item_equipped(ctx.author.id, "horseshoe"): p += 0.05
                if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles): p += 0.03
                if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in ctx.author.roles): p += 0.20
                if await self.has_item_equipped(ctx.author.id, "luck_potion"):
                    await self.consume_item(ctx.author.id, "luck_potion")
                    p = 0.70 + (0.03 if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles) else 0) + (0.20 if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in ctx.author.roles) else 0)
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
            embed = result_embed(ctx, f"{FISHING} Fishing", desc, win=win)
            embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
            await ctx.send(embed=embed)
        finally:
            self.active_games.discard(ctx.author.id)
            await self.check_broke(ctx.author.id)

    @commands.hybrid_command(name="work", aliases=["wk"])
    async def work(self, ctx: commands.Context):
        """Work for Moon Dust and get random rewards."""
        if not await self.require_registered(ctx):
            return
        if not self.is_owner(ctx.author):
            rem = cd(ctx.author.id, "work", LONG_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        gain = random.randint(300, 800)
        await self.add_wallet(ctx.author.id, gain)
        wallet = await self.get_wallet(ctx.author.id)
        embed = result_embed(ctx, f"{RED_BAGPACK} Work", f"You earned **{gain:,}** Moon Dust!", win=True)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="deposit", aliases=["dep"])
    async def deposit(self, ctx: commands.Context, amount: str):
        """Deposit Moon Dust from wallet to bank for safekeeping."""
        if not await self.require_registered(ctx):
            return
        amt_str = amount.lower().strip()
        async with user_lock(ctx.author.id):
            if amt_str == "all":
                wallet = await self.get_wallet(ctx.author.id)
                amt = wallet
                if amt <= 0:
                    return await ctx.send(embed=result_embed(ctx, "Nothing to Deposit", "Your wallet is empty.", win=False))
            else:
                amt = await self._parse_amount(ctx, amount)
                if amt is None:
                    return
                wallet = await self.get_wallet(ctx.author.id)
                if wallet < amt:
                    return await ctx.send(embed=result_embed(ctx, "Insufficient Funds", "Not enough Moon Dust in your wallet.", win=False))
            await self.move_wallet_to_bank(ctx.author.id, amt)
            wallet = await self.get_wallet(ctx.author.id)
            bank = await self.get_bank(ctx.author.id)
        embed = result_embed(ctx, f"{DEPOSIT} Deposit", f"Deposited **{amt:,}** Moon Dust into your bank.", win=True)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=True)
        embed.add_field(name="Bank", value=f"{BANK} `{bank:,}` Moon Dust", inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="withdraw", aliases=["wd"])
    async def withdraw(self, ctx: commands.Context, amount: str):
        """Withdraw Moon Dust from your bank to wallet."""
        if not await self.require_registered(ctx):
            return
        amt_str = amount.lower().strip()
        async with user_lock(ctx.author.id):
            if amt_str == "all":
                bank = await self.get_bank(ctx.author.id)
                amt = bank
                if amt <= 0:
                    return await ctx.send(embed=result_embed(ctx, "Nothing to Withdraw", "Your bank is empty.", win=False))
            else:
                amt = await self._parse_amount(ctx, amount)
                if amt is None:
                    return
                bank = await self.get_bank(ctx.author.id)
                if bank < amt:
                    return await ctx.send(embed=result_embed(ctx, "Insufficient Funds", "Not enough Moon Dust in your bank.", win=False))
            await self.move_bank_to_wallet(ctx.author.id, amt)
            wallet = await self.get_wallet(ctx.author.id)
            bank = await self.get_bank(ctx.author.id)
        embed = result_embed(ctx, f"{WITHDRAW} Withdraw", f"Withdrew **{amt:,}** Moon Dust to your wallet.", win=True)
        embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=True)
        embed.add_field(name="Bank", value=f"{BANK} `{bank:,}` Moon Dust", inline=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="interestrate", aliases=["ir"])
    async def interest_rate(self, ctx: commands.Context):
        """View your bank interest rate and earn passive Moon Dust."""
        embed = base_embed(ctx, f"{BANK} Bank Interest Rate")
        embed.add_field(name="Current Rate", value="**2%** per hour", inline=True)
        embed.add_field(name="Max Interest", value="**10,000 Moon Dust** per cycle", inline=True)
        embed.set_footer(text="Interest is added automatically every hour.")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="rob", aliases=["steal","take"])
    async def rob(self, ctx: commands.Context, user: discord.Member):
        """Rob someone's wallet (50% success chance). They can have immunity!"""
        if not await self.require_registered(ctx):
            return
        if user.bot:
            return await ctx.send(embed=result_embed(ctx, f"{DETECTIVE} Robbery", "You can't rob bots.", win=False))
        if user == ctx.author:
            return await ctx.send(embed=result_embed(ctx, f"{DETECTIVE} Robbery", "You can't rob yourself.", win=False))
        if not await self.is_registered(user.id):
            return await ctx.send(embed=result_embed(ctx, f"{DETECTIVE} Robbery", f"{user.mention} hasn't registered an account yet.", win=False))
        immunity = await self._fetchval("SELECT 1 FROM rob_immunity WHERE user_id=$1 AND guild_id=$2", user.id, ctx.guild.id)
        if immunity:
            return await ctx.send(embed=result_embed(ctx, f"{DETECTIVE} Robbery Protected", f"{user.mention} has immunity from robberies and heists!", win=False))
        if not self.is_owner(ctx.author):
            rem = cd(ctx.author.id, "rob", LONG_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        if await self.has_item_equipped(user.id, "watchdog"):
            await self.consume_item(user.id, "watchdog")
            return await ctx.send(embed=result_embed(ctx, f"{DETECTIVE} Robbery", f"{user.mention}'s Watchdog scared you away! Robbery failed.", win=False))
        first_id, second_id = sorted((ctx.author.id, user.id))
        async with user_lock(first_id):
            async with user_lock(second_id):
                target_wallet = await self.get_wallet(user.id)
                if target_wallet <= 0:
                    return await ctx.send(embed=result_embed(ctx, f"{DETECTIVE} Robbery", f"{user.mention} has nothing in their wallet to steal.", win=False))
                success = random.random() < 0.50
                if success:
                    stolen = min(random.randint(200, 600), target_wallet)
                    await self.add_wallet(ctx.author.id, stolen)
                    await self.add_wallet(user.id, -stolen)
                    robber_wallet = await self.get_wallet(ctx.author.id)
                    desc = f"You robbed {user.mention} and got **{stolen:,}** Moon Dust!\nYour wallet: `{robber_wallet:,}` Moon Dust"
                else:
                    penalty = random.randint(100, 300)
                    await self.add_wallet(ctx.author.id, -penalty)
                    robber_wallet = await self.get_wallet(ctx.author.id)
                    desc = f"You got caught! Lost **{penalty:,}** Moon Dust.\nYour wallet: `{robber_wallet:,}` Moon Dust"
        embed = result_embed(ctx, f"{DETECTIVE} Robbery", desc, win=success)
        await ctx.send(embed=embed)
        await self.check_broke(ctx.author.id)
        await self.check_broke(user.id)

    @commands.hybrid_command(name="shop")
    async def shop(self, ctx: commands.Context):
        """Browse and purchase special items to boost your economy."""
        embed = discord.Embed(title=f"{SHOPPING_CART} Frosty Shop", color=MAIN_COLOR)
        if ctx.guild:
            embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
        embed.description = f"{BREAKER_LINE}\n*Chill items to boost your luck!*\n{BREAKER_LINE}"
        emoji_map = {
            "rod": FISHING_ROD, "watchdog": WATCHDOG_SHIELD,
            "luck_potion": LUCK_CLOVER, "horseshoe": HORSESHOE,
            "custom_role": CROWN
        }
        for idx, name in enumerate(SHOP_ITEMS, 1):
            price = ITEM_PRICES[name]
            emoji = emoji_map.get(name, PACKAGE)
            desc = ITEM_DESCRIPTIONS.get(name, "")
            embed.add_field(
                name=f"`{idx}` {emoji} {name.replace('_', ' ').title()} — `{format_coins(price)}` Moon Dust",
                value=desc, inline=False
            )
        embed.add_field(name="\u200b", value=BREAKER_LINE, inline=False)
        embed.set_footer(text="Use /buy <item_name or index> [quantity] to purchase")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="buy")
    async def buy(self, ctx: commands.Context, item: str, quantity: Optional[int] = 1):
        """Purchase items from the shop to boost your game odds."""
        if not await self.require_registered(ctx):
            return
        if quantity < 1 or quantity > 10:
            return await ctx.send(embed=result_embed(ctx, "Invalid Quantity", "Quantity must be between 1 and 10.", win=False))
        item_name = None
        if item.isdigit():
            item_name = get_item_by_index(int(item))
        else:
            item_lower = item.lower()
            if item_lower in ITEM_PRICES:
                item_name = item_lower
        if item_name is None:
            return await ctx.send(embed=result_embed(ctx, "Unknown Item", f"Item `{item}` not found. Check `/shop` for available items.", win=False))
        price = ITEM_PRICES[item_name] * quantity
        async with user_lock(ctx.author.id):
            wallet = await self.get_wallet(ctx.author.id)
            if wallet < price:
                return await ctx.send(embed=result_embed(ctx, "Insufficient Funds", f"You need **{format_coins(price)}** Moon Dust in your wallet but only have **{format_coins(wallet)}**.", win=False))
            if item_name == "custom_role":
                existing = await self._fetchrow("SELECT expiry FROM custom_roles WHERE user_id=$1 AND guild_id=$2", ctx.author.id, ctx.guild.id)
                if existing and time.time() < existing['expiry']:
                    days_left = int((existing['expiry'] - time.time()) / 86400)
                    return await ctx.send(embed=result_embed(ctx, "Already Purchased", f"You already have the Custom Role for another **{days_left}** days.", win=False))
                role = discord.utils.get(ctx.guild.roles, name="Custom Role")
                if not role:
                    return await ctx.send(embed=result_embed(ctx, "Role Not Found", "The 'Custom Role' does not exist. Contact an administrator.", win=False))
                await self.add_wallet(ctx.author.id, -price)
                try:
                    await ctx.author.add_roles(role)
                except discord.Forbidden:
                    return await ctx.send(embed=result_embed(ctx, "Permission Error", "I cannot assign that role.", win=False))
                expiry = int(time.time()) + 60 * 60 * 24 * 60
                await self._execute(
                    "INSERT INTO custom_roles (user_id, guild_id, expiry, role_id) VALUES ($1,$2,$3,$4) ON CONFLICT (user_id, guild_id) DO UPDATE SET expiry = EXCLUDED.expiry, role_id = EXCLUDED.role_id",
                    ctx.author.id, ctx.guild.id, expiry, role.id
                )
                embed = result_embed(ctx, f"{CROWN} Custom Role Purchased", f"You bought the **Custom Role** for **{format_coins(price)}** Moon Dust.\nIt will expire in 2 months.", win=True)
                return await ctx.send(embed=embed)
            await self.add_wallet(ctx.author.id, -price)
            await self.add_item(ctx.author.id, item_name, quantity)
            await self._execute("INSERT INTO equipped (user_id, item) VALUES ($1,$2) ON CONFLICT (user_id, item) DO NOTHING", ctx.author.id, item_name)
        embed = result_embed(ctx, f"{SHOPPING_CART} Purchase", f"You bought **{quantity}× {item_name.replace('_', ' ').title()}** for **{format_coins(price)}** Moon Dust.\nIt has been equipped automatically.")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="inv")
    async def inv(self, ctx: commands.Context):
        """View your inventory of purchased items."""
        rows = await self._fetch("SELECT item, qty FROM inventory WHERE user_id=$1", ctx.author.id)
        embed = discord.Embed(title=f"{RED_BAGPACK} Frosty Inventory", color=MAIN_COLOR)
        embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
        embed.description = BREAKER_LINE
        if rows:
            for r in rows:
                item, qty = r['item'], r['qty']
                type_tag = ITEM_TYPE.get(item, "consumable")
                embed.add_field(name=f"{item.replace('_', ' ').title()} ×{qty}", value=f"Type: {type_tag}", inline=False)
        else:
            embed.description += "\n*Empty*"
        embed.add_field(name="\u200b", value=BREAKER_LINE, inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    # ─── LEADERBOARD COMMANDS ───────────────────────────────────
    @commands.hybrid_command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx: commands.Context):
        """View the fancy interactive leaderboard — wallet, bank, net worth, weekly & monthly gains."""
        view = LeaderboardView(self, ctx)
        embed = await view.build_embed()
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.hybrid_command(name="topwallet", aliases=["tw"])
    async def topwallet(self, ctx: commands.Context):
        """See the top 10 wealthiest wallets currently."""
        filtered = await self._get_top_10("wallet", ctx)
        embed = base_embed(ctx, f"{TOP_WALLET} Top Wallets")
        medals = [GOLD_MEDAL, SILVER_MEDAL, BRONZE_MEDAL] + ["🔹"]*7
        lines = []
        for idx, (uid, bal) in enumerate(filtered):
            medal = medals[idx] if idx < len(medals) else f"`{idx+1}`"
            try:
                user = await self.bot.fetch_user(uid)
                name = user.display_name
            except:
                name = f"Unknown ({uid})"
            lines.append(f"{medal} **{name}** — `{bal:,}` Moon Dust")
        embed.add_field(name="Richest Wallets", value="\n".join(lines) if lines else "No players yet.", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="topbank", aliases=["tb"])
    async def topbank(self, ctx: commands.Context):
        """View the top 10 largest bank savings."""
        filtered = await self._get_top_10("bank", ctx)
        embed = base_embed(ctx, f"{TOP_BANK} Top Banks")
        medals = [GOLD_MEDAL, SILVER_MEDAL, BRONZE_MEDAL] + ["🔹"]*7
        lines = []
        for idx, (uid, bal) in enumerate(filtered):
            medal = medals[idx] if idx < len(medals) else f"`{idx+1}`"
            try:
                user = await self.bot.fetch_user(uid)
                name = user.display_name
            except:
                name = f"Unknown ({uid})"
            lines.append(f"{medal} **{name}** — `{bal:,}` Moon Dust")
        embed.add_field(name="Largest Savings", value="\n".join(lines) if lines else "No players yet.", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="topnet", aliases=["tn"])
    async def topnet(self, ctx: commands.Context):
        """View the top 10 net worth (wallet + bank) players."""
        filtered = await self._get_top_10_net(ctx)
        embed = base_embed(ctx, f"{GLOBAL} Top Net Worth")
        medals = [GOLD_MEDAL, SILVER_MEDAL, BRONZE_MEDAL] + ["🔹"]*7
        lines = []
        for idx, (uid, bal) in enumerate(filtered):
            medal = medals[idx] if idx < len(medals) else f"`{idx+1}`"
            try:
                user = await self.bot.fetch_user(uid)
                name = user.display_name
            except:
                name = f"Unknown ({uid})"
            lines.append(f"{medal} **{name}** — `{bal:,}` Moon Dust")
        embed.add_field(name="Richest Overall", value="\n".join(lines) if lines else "No players yet.", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="topweekly", aliases=["weeklytop", "tw7"])
    async def topweekly(self, ctx: commands.Context):
        """View the top 10 Moon Dust earners over the last 7 days."""
        rows = await self._get_top_gains(ctx, days=7)
        embed = base_embed(ctx, f"{CROWN} Weekly Top Earners")
        if not rows:
            embed.add_field(name="Last 7 Days", value="Not enough data yet for this leaderboard.", inline=False)
        else:
            medals = [GOLD_MEDAL, SILVER_MEDAL, BRONZE_MEDAL] + ["🔹"]*7
            lines = []
            for idx, (uid, gain) in enumerate(rows):
                medal = medals[idx] if idx < len(medals) else f"`{idx+1}`"
                try:
                    user = await self.bot.fetch_user(uid)
                    name = user.display_name
                except:
                    name = f"Unknown ({uid})"
                lines.append(f"{medal} **{name}** — `+{format_coins(gain)}` Moon Dust")
            embed.add_field(name="Last 7 Days", value="\n".join(lines), inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="topmonthly", aliases=["monthlytop", "tw30"])
    async def topmonthly(self, ctx: commands.Context):
        """View the top 10 Moon Dust earners over the last 30 days."""
        rows = await self._get_top_gains(ctx, days=30)
        embed = base_embed(ctx, f"{CALENDAR} Monthly Top Earners")
        if not rows:
            embed.add_field(name="Last 30 Days", value="Not enough data yet for this leaderboard.", inline=False)
        else:
            medals = [GOLD_MEDAL, SILVER_MEDAL, BRONZE_MEDAL] + ["🔹"]*7
            lines = []
            for idx, (uid, gain) in enumerate(rows):
                medal = medals[idx] if idx < len(medals) else f"`{idx+1}`"
                try:
                    user = await self.bot.fetch_user(uid)
                    name = user.display_name
                except:
                    name = f"Unknown ({uid})"
                lines.append(f"{medal} **{name}** — `+{format_coins(gain)}` Moon Dust")
            embed.add_field(name="Last 30 Days", value="\n".join(lines), inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="icegive")
    async def mgive(self, ctx: commands.Context, user: discord.Member, amount: str):
        """(Owner only) Give Moon Dust to a player."""
        if not self.is_owner(ctx.author):
            return await ctx.send(f"{WRONG} Only the bot owner can use this command.")
        parsed = await self._parse_amount(ctx, amount)
        if parsed is None:
            return
        if not await self.is_registered(user.id):
            await self.register_user(user.id)
        await self.add_wallet(user.id, parsed)
        wallet = await self.get_wallet(user.id)
        embed = result_embed(ctx, f"{GIFT} Moon Dust Given", f"Gave **{parsed:,}** Moon Dust to {user.mention}.", win=True)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="mtake")
    async def mtake(self, ctx: commands.Context, user: discord.Member, amount: str):
        """(Owner only) Take Moon Dust from a player."""
        if not self.is_owner(ctx.author):
            return await ctx.send(f"{WRONG} Only the bot owner can use this command.")
        parsed = await self._parse_amount(ctx, amount)
        if parsed is None:
            return
        wallet = await self.get_wallet(user.id)
        new_wallet = max(0, wallet - parsed)
        removed = wallet - new_wallet
        await self.set_wallet(user.id, new_wallet)
        embed = result_embed(ctx, f"{ICE_COIN} Moon Dust Removed", f"Removed **{removed:,}** Moon Dust from {user.mention}.", win=False)
        await ctx.send(embed=embed)
        await self.check_broke(user.id)

    @commands.hybrid_command(name="cleareconomy")
    async def clear_economy(self, ctx: commands.Context):
        """(Owner only) Reset all economy data on the server."""
        if not self.is_owner(ctx.author):
            return await ctx.send(f"{WRONG} Only the bot owner can reset the economy.")
        confirm_view = ConfirmResetView(self.bot, ctx.author)
        embed = discord.Embed(title=f"{WARN} Reset Economy", description="Are you sure you want to **delete all economy data**? This cannot be undone.", color=LOSS_COLOR)
        msg = await ctx.send(embed=embed, view=confirm_view)
        await confirm_view.wait()
        if confirm_view.value:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("DELETE FROM users")
                    await conn.execute("DELETE FROM inventory")
                    await conn.execute("DELETE FROM equipped")
                    await conn.execute("DELETE FROM custom_roles")
                    await conn.execute("DELETE FROM left_users")
                    await conn.execute("DELETE FROM balance_snapshots")
                    await conn.execute("DELETE FROM economy_channel_bypass")
                    await conn.execute("DELETE FROM collect_cooldowns")
            await msg.edit(embed=discord.Embed(title=f"{TICK} Economy Reset", description="All balances, inventories, and equipment have been cleared.", color=WIN_COLOR), view=None)
        else:
            await msg.edit(embed=discord.Embed(title=f"{WRONG} Cancelled", description="Economy reset cancelled.", color=MAIN_COLOR), view=None)

    # ─── META COMMAND: economy channelbypass ────────────────────
    @commands.group(name="economy", invoke_without_command=True)
    async def economy_group(self, ctx: commands.Context):
        """Economy admin commands."""
        if not self.is_owner(ctx.author) and not self.is_staff(ctx.author):
            return
        embed = base_embed(ctx, f"{GEAR} Economy Admin")
        embed.add_field(name="Subcommands", value="`economy channelbypass <user> <channel>` — Allow user to use economy commands in a specific channel.", inline=False)
        await ctx.send(embed=embed)

    @economy_group.command(name="channelbypass")
    async def economy_channelbypass(self, ctx: commands.Context, user: discord.Member, channel: discord.TextChannel):
        """Allow a user to use economy commands in a specific channel."""
        if not self.is_owner(ctx.author) and not self.is_staff(ctx.author):
            return
        await self._execute(
            "INSERT INTO economy_channel_bypass (user_id, channel_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            user.id, channel.id
        )
        embed = result_embed(ctx, f"{TICK} Bypass Added", f"{user.mention} can now use economy commands in {channel.mention}.", win=True)
        await ctx.send(embed=embed)

    # ─── GAME COMMANDS ──────────────────────────────────────────
    @commands.hybrid_command(name="coinflip", aliases=["cf"])
    async def coinflip(self, ctx: commands.Context, bet: str):
        """Classic coin flip! 50% win chance."""
        if not await self.require_registered(ctx):
            return
        if ctx.author.id in self.active_games:
            return await ctx.send(embed=result_embed(ctx, "Game in Progress", "You're already playing a game! Finish it first.", win=False))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            return
        wallet = await self.get_wallet(ctx.author.id)
        if wallet < amount:
            return await ctx.send(embed=result_embed(ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet.", win=False))
        has_luck = await self.has_item_equipped(ctx.author.id, "luck_potion")
        self.active_games.add(ctx.author.id)
        embed = base_embed(ctx, f"{COIN} Coin Flip", user=ctx.author)
        embed.description = "Choose **Heads** or **Tails**!\n" + BREAKER_LINE
        view = CoinFlipView(ctx, amount, has_luck, self)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.hybrid_command(name="roulette", aliases=["rl"])
    async def roulette(self, ctx: commands.Context, bet: str, number: Optional[int] = None):
        """Spin the roulette wheel and pick a lucky number!"""
        if not await self.require_registered(ctx):
            return
        if ctx.author.id in self.active_games:
            return await ctx.send(embed=result_embed(ctx, "Game in Progress", "You're already playing a game! Finish it first.", win=False))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            return
        wallet = await self.get_wallet(ctx.author.id)
        if wallet < amount:
            return await ctx.send(embed=result_embed(ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet.", win=False))
        if number is not None:
            if not 1 <= number <= 36:
                return await ctx.send("Number must be between 1 and 36.")
            self.active_games.add(ctx.author.id)
            spin = random.randint(0, 36)
            win = (spin == number)
            multiplier = 2.5 if win else 0
            net = int(amount * (multiplier - 1)) if win else -amount
            spinning = spin_embed(ctx, f"{ROULETTE} Roulette", f"Betting on **{number}**\nSpinning...", user=ctx.author)
            msg = await ctx.send(embed=spinning)
            await asyncio.sleep(1.5)
            async with user_lock(ctx.author.id):
                if net != 0:
                    await self.add_wallet(ctx.author.id, net)
                wallet = await self.get_wallet(ctx.author.id)
            colour = "green" if spin == 0 else ("red" if spin in RouletteView.RED_NUMBERS else "black")
            colour_emoji = {"green": GREEN_CIRCLE, "red": RED_CIRCLE, "black": BLACK_CIRCLE}[colour]
            if win:
                desc = f"Landed on **{spin}** {colour_emoji} — **WIN** {int(amount * multiplier)} Moon Dust!"
            else:
                desc = f"Landed on **{spin}** {colour_emoji} — **LOSE** {amount} Moon Dust"
            final_embed = result_embed(ctx, f"{ROULETTE} Roulette", desc, win=win, user=ctx.author)
            final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
            await msg.edit(embed=final_embed)
            self.active_games.discard(ctx.author.id)
            await self.check_broke(ctx.author.id)
            return
        has_luck = await self.has_item_equipped(ctx.author.id, "luck_potion")
        self.active_games.add(ctx.author.id)
        embed = base_embed(ctx, f"{ROULETTE} Roulette", user=ctx.author)
        embed.description = f"Bet: **{amount:,}** Moon Dust\nPick a bet type from the dropdown!\n{BREAKER_LINE}"
        view = RouletteView(ctx, amount, has_luck, self)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.hybrid_command(name="blackjack", aliases=["bj"])
    async def blackjack(self, ctx: commands.Context, bet: str):
        """Play blackjack against the dealer. Hit or stand?"""
        if not await self.require_registered(ctx):
            return
        if ctx.author.id in self.active_games:
            return await ctx.send(embed=result_embed(ctx, "Game in Progress", "You're already playing a game! Finish it first.", win=False))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            return
        wallet = await self.get_wallet(ctx.author.id)
        if wallet < amount:
            return await ctx.send(embed=result_embed(ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet.", win=False))
        self.active_games.add(ctx.author.id)
        view = BlackjackView(ctx, amount, self)
        embed = view.build_embed()
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.hybrid_command(name="slots", aliases=["sl"])
    async def slots(self, ctx: commands.Context, bet: str):
        """Play the slot machine for big wins!"""
        if not await self.require_registered(ctx):
            return
        if ctx.author.id in self.active_games:
            return await ctx.send(embed=result_embed(ctx, "Game in Progress", "You're already playing a game! Finish it first.", win=False))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            return
        wallet = await self.get_wallet(ctx.author.id)
        if wallet < amount:
            return await ctx.send(embed=result_embed(ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet.", win=False))
        has_luck = await self.has_item_equipped(ctx.author.id, "luck_potion")
        p = 0.25 + BASE_CHANCE_BOOST
        if await self.has_item_equipped(ctx.author.id, "horseshoe"): p += 0.05
        if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles): p += 0.03
        if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in ctx.author.roles): p += 0.20
        if has_luck:
            await self.consume_item(ctx.author.id, "luck_potion")
            p = 0.80 + (0.03 if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles) else 0) + (0.20 if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in ctx.author.roles) else 0)
        p = min(p, 0.95)
        win = random.random() < p
        emojis = [SLOT_CHERRY, SLOT_LEMON, SLOT_GEM, SLOT_GRAPE, SLOT_SEVEN]
        if win:
            final = [random.choice(emojis)] * 3
        else:
            a, b = random.choices(emojis, k=2)
            others = [e for e in emojis if e != a] if a == b else emojis
            c = random.choice(others)
            final = [a, b, c]
        self.active_games.add(ctx.author.id)
        spinning = spin_embed(ctx, f"{SLOT_MACHINE} Slots", "Spinning the reels...", user=ctx.author)
        msg = await ctx.send(embed=spinning)
        await asyncio.sleep(2)
        async with user_lock(ctx.author.id):
            net = amount if win else -amount
            await self.add_wallet(ctx.author.id, net)
            wallet = await self.get_wallet(ctx.author.id)
        desc = f"`| {final[0]} | {final[1]} | {final[2]} |`\n{'**JACKPOT!**' if win else 'Better luck next time.'}"
        final_embed = result_embed(ctx, f"{SLOT_MACHINE} Slots", desc, win=win, user=ctx.author)
        final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await msg.edit(embed=final_embed)
        self.active_games.discard(ctx.author.id)
        await self.check_broke(ctx.author.id)

    @commands.hybrid_command(name="spin")
    async def spin_wheel(self, ctx: commands.Context, bet: str):
        """Spin the wheel for random multiplied payouts."""
        if not await self.require_registered(ctx):
            return
        if ctx.author.id in self.active_games:
            return await ctx.send(embed=result_embed(ctx, "Game in Progress", "You're already playing a game! Finish it first.", win=False))
        if not self.is_owner(ctx.author):
            rem = cd(ctx.author.id, "spin", GAME_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            return
        wallet = await self.get_wallet(ctx.author.id)
        if wallet < amount:
            return await ctx.send(embed=result_embed(ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet.", win=False))
        has_luck = await self.has_item_equipped(ctx.author.id, "luck_potion")
        p_win = 0.25 + BASE_CHANCE_BOOST
        if await self.has_item_equipped(ctx.author.id, "horseshoe"): p_win += 0.05
        if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles): p_win += 0.03
        if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in ctx.author.roles): p_win += 0.20
        if has_luck:
            await self.consume_item(ctx.author.id, "luck_potion")
            p_win = 0.70 + (0.03 if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles) else 0) + (0.20 if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in ctx.author.roles) else 0)
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
        self.active_games.add(ctx.author.id)
        spinning = spin_embed(ctx, f"{SPIN_WHEEL} Spin the Wheel", "Spinning...", user=ctx.author)
        msg = await ctx.send(embed=spinning)
        await asyncio.sleep(2.5)
        async with user_lock(ctx.author.id):
            await self.add_wallet(ctx.author.id, net)
            wallet = await self.get_wallet(ctx.author.id)
        if win:
            desc = f"Multiplier: **{multiplier}x**\nYou won **{total_win}** Moon Dust!"
            final_embed = result_embed(ctx, f"{SPIN_WHEEL} Spin the Wheel", desc, win=True, user=ctx.author)
        else:
            desc = f"Multiplier: **0x**\nYou lost **{amount}** Moon Dust."
            final_embed = result_embed(ctx, f"{SPIN_WHEEL} Spin the Wheel", desc, win=False, user=ctx.author)
        final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await msg.edit(embed=final_embed)
        self.active_games.discard(ctx.author.id)
        await self.check_broke(ctx.author.id)

    @commands.hybrid_command(name="dice")
    async def dice(self, ctx: commands.Context, bet: str, guess: int):
        """Roll dice and guess the number! Pick 1-6."""
        if not await self.require_registered(ctx):
            return
        if ctx.author.id in self.active_games:
            return await ctx.send(embed=result_embed(ctx, "Game in Progress", "You're already playing a game! Finish it first.", win=False))
        if not self.is_owner(ctx.author):
            rem = cd(ctx.author.id, "dice", GAME_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        if not 1 <= guess <= 6:
            return await ctx.send("Guess a number from 1 to 6.")
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            return
        wallet = await self.get_wallet(ctx.author.id)
        if wallet < amount:
            return await ctx.send(embed=result_embed(ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet.", win=False))
        has_luck = await self.has_item_equipped(ctx.author.id, "luck_potion")
        p = 0.25 + BASE_CHANCE_BOOST
        if await self.has_item_equipped(ctx.author.id, "horseshoe"): p += 0.05
        if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles): p += 0.03
        if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in ctx.author.roles): p += 0.20
        if has_luck:
            await self.consume_item(ctx.author.id, "luck_potion")
            p = 0.70 + (0.03 if BOOSTER_ROLE_ID and any(role.id == BOOSTER_ROLE_ID for role in ctx.author.roles) else 0) + (0.20 if LUCK_BOOST_ROLE_ID and any(role.id == LUCK_BOOST_ROLE_ID for role in ctx.author.roles) else 0)
        p = min(p, 0.95)
        if random.random() < p:
            roll = guess
        else:
            possible = [n for n in range(1,7) if n != guess]
            roll = random.choice(possible)
        self.active_games.add(ctx.author.id)
        spinning = spin_embed(ctx, f"{DICES} Dice", "Rolling...", user=ctx.author)
        msg = await ctx.send(embed=spinning)
        await asyncio.sleep(1)
        spinning.description = f"{DICES} → **{roll}**\n{BREAKER_LINE}"
        await msg.edit(embed=spinning)
        async with user_lock(ctx.author.id):
            win = (roll == guess)
            net = amount * 4 if win else -amount
            await self.add_wallet(ctx.author.id, net)
            wallet = await self.get_wallet(ctx.author.id)
        if win:
            total_win = amount * 5
            desc = f"You guessed **{guess}**, rolled **{roll}**\nYou won **{total_win}** Moon Dust!"
        else:
            desc = f"You guessed **{guess}**, rolled **{roll}**\nYou lost **{amount}** Moon Dust."
        final_embed = result_embed(ctx, f"{DICES} Dice", desc, win=win, user=ctx.author)
        final_embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await msg.edit(embed=final_embed)
        self.active_games.discard(ctx.author.id)
        await self.check_broke(ctx.author.id)

    @commands.hybrid_command(name="highlow", aliases=["hl"])
    async def highlow(self, ctx: commands.Context, bet: str):
        """Guess if the next card will be high or low."""
        if not await self.require_registered(ctx):
            return
        if ctx.author.id in self.active_games:
            return await ctx.send(embed=result_embed(ctx, "Game in Progress", "You're already playing a game! Finish it first.", win=False))
        if not self.is_owner(ctx.author):
            rem = cd(ctx.author.id, "highlow", GAME_COOLDOWN)
            if rem:
                return await ctx.send(embed=cooldown_embed(ctx, rem))
        amount = await self._parse_amount(ctx, bet)
        if amount is None:
            return
        wallet = await self.get_wallet(ctx.author.id)
        if wallet < amount:
            return await ctx.send(embed=result_embed(ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet.", win=False))
        card = random.randint(2,14)
        self.active_games.add(ctx.author.id)
        embed = base_embed(ctx, f"{CARDS} Higher or Lower", SPIN_COLOR, user=ctx.author)
        embed.description = f"Current card: **{card}**\n{BREAKER_LINE}"
        view = HighLowView(ctx, amount, card, self)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @commands.hybrid_command(name="beg")
    async def beg(self, ctx: commands.Context):
        """Beg for Moon Dust and get a random small reward."""
        if not await self.require_registered(ctx):
            return
        rem = cd(ctx.author.id, "beg", LONG_COOLDOWN)
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
        embed = result_embed(ctx, f"{BEG} Begging", desc, win=win)
        if win:
            embed.add_field(name="Wallet", value=f"`{wallet:,}` Moon Dust", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="russian", aliases=["rroulette"])
    async def russian_roulette(self, ctx: commands.Context, entry_fee: str, max_players: Optional[int] = 15):
        """(Owner only) Start a Russian Roulette game."""
        if not self.is_owner(ctx.author):
            return await ctx.send(f"{WRONG} Only the bot owner can use this command.")
        fee = await self._parse_amount(ctx, entry_fee)
        if fee is None:
            return
        if fee < 1:
            return await ctx.send("Entry fee must be at least 1 Moon Dust.")
        if max_players < 2 or max_players > 30:
            return await ctx.send("Max players must be between 2 and 30.")
        if ctx.channel.id in self.russian_games:
            return await ctx.send("A Russian Roulette is already in progress here.")
        view = RussianRouletteJoinView(self, ctx.author, fee, max_players, ctx.channel)
        embed = view.build_lobby_embed()
        join_msg = await ctx.send(embed=embed, view=view)
        await view.wait()
        for child in view.children:
            child.disabled = True
        await join_msg.edit(view=view)
        if not view.locked and len(view.players) < 2:
            for pid in view.players:
                await self.add_wallet(pid, fee)
            await ctx.send("Not enough players. Entry fees refunded.")

    @commands.hybrid_command(name="transfer", aliases=["gift"])
    async def transfer(self, ctx: commands.Context, user: discord.Member, amount: str):
        """Send Moon Dust to another player."""
        if not await self.require_registered(ctx):
            return
        if user == ctx.author:
            return await ctx.send(embed=result_embed(ctx, "Transfer Failed", "You can't send Moon Dust to yourself.", win=False))
        if not await self.is_registered(user.id):
            return await ctx.send(embed=result_embed(ctx, "Transfer Failed", f"{user.mention} hasn't registered an account yet.", win=False))
        parsed = await self._parse_amount(ctx, amount)
        if parsed is None:
            return
        if parsed <= 0:
            return await ctx.send(embed=result_embed(ctx, "Invalid Amount", "Amount must be positive.", win=False))
        first_id, second_id = sorted((ctx.author.id, user.id))
        async with user_lock(first_id):
            async with user_lock(second_id):
                sender_wallet = await self.get_wallet(ctx.author.id)
                if sender_wallet < parsed:
                    return await ctx.send(embed=result_embed(ctx, "Insufficient Funds", "You don't have enough Moon Dust in your wallet.", win=False))
                await self.add_wallet(ctx.author.id, -parsed)
                await self.add_wallet(user.id, parsed)
                sender_wallet = await self.get_wallet(ctx.author.id)
        embed = result_embed(ctx, f"{GIFT} Transfer Complete", f"Sent **{parsed:,}** Moon Dust to {user.mention}.\nYour wallet: **{format_coins(sender_wallet)}** Moon Dust.", win=True)
        await ctx.send(embed=embed)
        await self.check_broke(ctx.author.id)

    # ─── HEIST SYSTEM ───────────────────────────────────────────
    @commands.hybrid_command(name="heist")
    async def heist(self, ctx: commands.Context, target: discord.Member):
        """Start a heist! Needs 5+ people to steal 50% of target's bank (35% success)."""
        if not await self.require_registered(ctx):
            return
        if target.bot:
            return await ctx.send(embed=result_embed(ctx, f"{DETECTIVE} Heist", "You can't heist bots.", win=False))
        if target == ctx.author:
            return await ctx.send(embed=result_embed(ctx, f"{DETECTIVE} Heist", "You can't heist yourself.", win=False))
        if not await self.is_registered(target.id):
            return await ctx.send(embed=result_embed(ctx, f"{DETECTIVE} Heist", f"{target.mention} hasn't registered an account yet.", win=False))
        immunity = await self._fetchval("SELECT 1 FROM rob_immunity WHERE user_id=$1 AND guild_id=$2", target.id, ctx.guild.id)
        if immunity:
            return await ctx.send(embed=result_embed(ctx, f"{DETECTIVE} Heist Protected", f"{target.mention} has immunity from heists and robberies!", win=False))
        target_bank = await self.get_bank(target.id)
        if target_bank <= 0:
            return await ctx.send(embed=result_embed(ctx, f"{DETECTIVE} Heist", f"{target.mention} has no Moon Dust in their bank!", win=False))
        view = HeistJoinView(self, ctx.author, target, ctx.channel)
        embed = view.build_lobby_embed()
        embed.description += f"\n\nTarget's Bank: `{target_bank:,}` Moon Dust"
        await ctx.send(embed=embed, view=view)

    async def _parse_amount(self, ctx, amount_str: str) -> Optional[int]:
        try:
            return parse_amount_str(amount_str)
        except ValueError:
            await ctx.send(embed=result_embed(ctx, "Invalid Amount", "Use format: 100, 1k, 1.5m, 1b", win=False))
            return None

    # ─── ERROR HANDLING ─────────────────────────────────────────
    async def cog_command_error(self, ctx, error):
        print(f"[Economy] Command {ctx.command} raised: {error}")


async def setup(bot):
    await bot.add_cog(Economy(bot))