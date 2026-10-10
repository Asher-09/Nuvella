import discord
from discord.ext import commands
from discord import app_commands
import random
import asyncio
import logging
import time
from typing import Optional, Dict
import aiohttp

from utils.emoji import EMOJIS
from utils.components import ComponentsV2View, prepare_v2_view, disable_all_components
log = logging.getLogger(__name__)

# ── theme ──────────────────────────────────────────────────────────────────────
MAIN_COLOR   = 0x3498DB
WIN_COLOR    = 0x57F287
LOSS_COLOR   = 0xED4245

# ── emoji constants ──────────────────────────────────────────────────────────
# custom Discord emojis (server-specific)

# Unicode emojis

# derived
BREAKER_LINE = EMOJIS["breaker"] * 10

GAME_EMOJI = {
    "rps":      EMOJIS["rps_challenge"],
    "ttt":      EMOJIS["game_controller"],
    "hangman":  EMOJIS["hangman"],
    "trivia":   EMOJIS["brain"],
}

# ── config keys for staff roles ──────────────────────────────────────────────
STAFF_ROLE_KEYS = [
    "admin_role_id",
    "senior_moderator_role_id",
    "moderator_role_id",
    "trial_moderator_role_id",
    "developer_role_id",
    "event_manager_role_id",
    "trial_event_manager_role_id",
]

def get_staff_role_ids(bot) -> set:
    """Extract staff role IDs from bot.config['roles']."""
    config = getattr(bot, "config", {})
    roles_config = config.get("roles", {})
    return {
        roles_config[key]
        for key in STAFF_ROLE_KEYS
        if roles_config.get(key) is not None
    }

def get_booster_role_id(bot):
    config = getattr(bot, "config", {})
    return config.get("roles", {}).get("booster_role_id")

# ── embed helpers ──────────────────────────────────────────────────────────────
def make_embed(ctx, title: str, desc: str, color: int, success: bool = False) -> discord.Embed:
    if isinstance(ctx, discord.Interaction):
        author = ctx.user
        guild = ctx.guild
    else:
        author = ctx.author
        guild = ctx.guild

    embed = discord.Embed(
        title=title,
        description=f"{desc}\n{BREAKER_LINE}",
        color=WIN_COLOR if success else color,
        timestamp=discord.utils.utcnow(),
    )
    if guild:
        embed.set_author(
            name=guild.name,
            icon_url=guild.icon.url if guild.icon else None,
        )
    embed.set_footer(
        text=f"Action by {author}",
        icon_url=author.display_avatar.url,
    )
    return embed

def error_embed(ctx, title: str, desc: str) -> discord.Embed:
    return make_embed(ctx, f"{EMOJIS["cross"]} {title}", desc, LOSS_COLOR)

def success_embed(ctx, title: str, desc: str) -> discord.Embed:
    return make_embed(ctx, f"{EMOJIS["tick"]} {title}", desc, MAIN_COLOR, success=True)

# ── permission helpers ─────────────────────────────────────────────────────────
def is_staff(member: discord.Member, bot) -> bool:
    if member.id == bot.owner_id:
        return True
    if member.id == member.guild.owner_id:
        return True
    staff_role_ids = get_staff_role_ids(bot)
    return any(role.id in staff_role_ids for role in member.roles)

def is_booster(member: discord.Member, bot) -> bool:
    booster_role_id = get_booster_role_id(bot)
    if booster_role_id is None:
        # Fallback to Discord premium_since if config is missing booster_role_id
        return member.premium_since is not None
    return any(role.id == booster_role_id for role in member.roles)

def booster_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            raise commands.CheckFailure("Server only.")
        if is_staff(ctx.author, ctx.bot):
            return True
        if not is_booster(ctx.author, ctx.bot):
            raise commands.CheckFailure("Only server boosters and staff can use this command.")
        return True
    return commands.check(predicate)

def app_booster_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            raise app_commands.CheckFailure("Server only.")
        member = interaction.guild.get_member(interaction.user.id)
        if not member:
            raise app_commands.CheckFailure("Could not resolve member.")
        if is_staff(member, interaction.client):
            return True
        if not is_booster(member, interaction.client):
            raise app_commands.CheckFailure("Only server boosters and staff can use this command.")
        return True
    return app_commands.check(predicate)

def can_force_end(member: discord.Member, starter_id: int, bot) -> bool:
    if is_staff(member, bot):
        return True
    return member.id == starter_id and is_booster(member, bot)

# ══════════════════════════════════════════════════════════════════════════════
#  ACCEPT / DECLINE VIEW
# ══════════════════════════════════════════════════════════════════════════════
class DuelInviteView(ComponentsV2View):
    def __init__(self, challenger: discord.User, opponent: discord.User,
                 game_name: str, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.challenger = challenger
        self.opponent = opponent
        self.game_name = game_name
        self.accepted: Optional[bool] = None
        self.message: Optional[discord.Message] = None
        self.controls = discord.ui.ActionRow()
        accept_button = discord.ui.Button(label="Accept", style=discord.ButtonStyle.success, emoji=EMOJIS["sword"])
        decline_button = discord.ui.Button(label="Decline", style=discord.ButtonStyle.danger, emoji=EMOJIS["no_entry"])
        accept_button.callback = self.accept
        decline_button.callback = self.decline
        self.controls.add_item(accept_button)
        self.controls.add_item(decline_button)
        self.add_item(self.controls)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                f"This duel isn't addressed to you {EMOJIS['eyes']}", ephemeral=True)
            return False
        return True

    async def accept(self, interaction: discord.Interaction):
        self.accepted = True
        disable_all_components(self)
        embed = discord.Embed(
            description=f"{EMOJIS["tick"]} **{self.opponent.display_name}** accepted the challenge! Let's go.",
            color=WIN_COLOR,
        )
        self.set_embed(embed)
        await interaction.response.edit_message(view=self)
        self.stop()

    async def decline(self, interaction: discord.Interaction):
        self.accepted = False
        disable_all_components(self)
        embed = discord.Embed(
            description=f"{EMOJIS["cross"]} **{self.opponent.display_name}** chickened out and declined.",
            color=LOSS_COLOR,
        )
        self.set_embed(embed)
        await interaction.response.edit_message(view=self)
        self.stop()

    async def on_timeout(self):
        if self.accepted is None:
            disable_all_components(self)
            if self.message:
                embed = discord.Embed(
                    description=f"{EMOJIS["cooldown"]} **{self.opponent.display_name}** didn't respond in time. Challenge expired.",
                    color=LOSS_COLOR,
                )
                try:
                    self.set_embed(embed)
                    await self.message.edit(view=self)
                except Exception:
                    pass

def invite_embed(challenger: discord.User, opponent: discord.User,
                  game_label: str, emoji: str, extra: str = "") -> discord.Embed:
    desc = (
        f"{challenger.mention} is challenging {opponent.mention} to **{game_label}**!\n"
        f"{extra}\n"
        f"{opponent.mention}, do you accept?"
    )
    embed = discord.Embed(
        title=f"{emoji} Duel Request",
        description=f"{desc}\n{BREAKER_LINE}",
        color=MAIN_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="60 seconds to respond")
    return embed

async def send_duel_invite(channel, challenger: discord.User,
                            opponent: discord.User, game_label: str, emoji: str,
                            extra: str = "") -> DuelInviteView:
    view = DuelInviteView(challenger, opponent, game_label)
    embed = invite_embed(challenger, opponent, game_label, emoji, extra)
    prepare_v2_view(view, embed)
    view.add_text(opponent.mention)
    msg = await channel.send(view=view)
    view.message = msg
    await view.wait()
    return view

# ══════════════════════════════════════════════════════════════════════════════
#  HANGMAN DATA CLASS
# ══════════════════════════════════════════════════════════════════════════════
class HangmanChallenge:
    MAX_MISTAKES = 6

    HANGMAN_STAGES = [
        "```\n +---+\n |   |\n     |\n     |\n     |\n     |\n=========```",
        "```\n +---+\n |   |\n O   |\n     |\n     |\n     |\n=========```",
        "```\n +---+\n |   |\n O   |\n |   |\n     |\n     |\n=========```",
        "```\n +---+\n |   |\n O   |\n/|   |\n     |\n     |\n=========```",
        "```\n +---+\n |   |\n O   |\n/|\\  |\n     |\n     |\n=========```",
        "```\n +---+\n |   |\n O   |\n/|\\  |\n/    |\n     |\n=========```",
        "```\n +---+\n |   |\n O   |\n/|\\  |\n/ \\  |\n     |\n=========```",
    ]

    def __init__(self, word: str, challenger_id: int, opponent_id: int,
                 channel_id: int, message_id: int):
        self.word = word.lower()
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.channel_id = channel_id
        self.message_id = message_id
        self.guessed: set = set()
        self.mistakes = 0
        self.ended = False
        self.expires = time.time() + 600

    @property
    def display_word(self) -> str:
        return " ".join(c if c in self.guessed else "_" for c in self.word)

    @property
    def won(self) -> bool:
        return all(c in self.guessed for c in self.word)

    def guess(self, letter: str) -> bool:
        if self.ended:
            return False
        letter = letter.lower()
        self.guessed.add(letter)
        correct = letter in self.word
        if not correct:
            self.mistakes += 1
        if self.won or self.mistakes >= self.MAX_MISTAKES:
            self.ended = True
        return correct

    def get_embed(self, challenger: discord.User, opponent: discord.User) -> discord.Embed:
        stage = self.HANGMAN_STAGES[min(self.mistakes, self.MAX_MISTAKES)]
        if self.ended:
            if self.won:
                title = f"{EMOJIS["confetti"]} Hangman — Solved!"
                color = WIN_COLOR
                desc = (
                    f"{opponent.mention} cracked it! The word was **`{self.word}`**\n"
                    f"{stage}"
                )
            else:
                title = f"{EMOJIS["hangman"]} Hangman — Game Over"
                color = LOSS_COLOR
                desc = (
                    f"{EMOJIS['skull_unicode']} {opponent.mention} got hanged! The word was **`{self.word}`**\n"
                    f"{stage}"
                )
        else:
            title = f"{EMOJIS["hangman"]} Hangman"
            color = MAIN_COLOR
            desc = (
                f"{stage}\n"
                f"## `{self.display_word}`\n"
                f"**Guessed:** {', '.join(sorted(self.guessed)) or '*none yet*'}\n"
                f"**Mistakes:** {self.mistakes}/{self.MAX_MISTAKES}\n\n"
                f"{opponent.mention}, reply in this channel with **one letter** to guess."
            )
        embed = discord.Embed(title=title, description=desc + f"\n{BREAKER_LINE}", color=color)
        embed.set_footer(
            text=f"{challenger.display_name} (setter) vs {opponent.display_name} (guesser)",
            icon_url=opponent.display_avatar.url,
        )
        return embed

# ══════════════════════════════════════════════════════════════════════════════
#  TRIVIA VIEW
# ══════════════════════════════════════════════════════════════════════════════
class TriviaView(ComponentsV2View):
    def __init__(self, question: str, answer: str,
                 challenger: discord.User, opponent: discord.User,
                 channel: discord.TextChannel):
        super().__init__(timeout=300)
        self.question = question
        self.answer = answer.strip().lower()
        self.challenger = challenger
        self.opponent = opponent
        self.channel = channel
        self.answered = False
        self.message: Optional[discord.Message] = None
        self.controls = discord.ui.ActionRow()
        question_button = discord.ui.Button(label="Show Question", style=discord.ButtonStyle.blurple, emoji=EMOJIS["question"])
        answer_button = discord.ui.Button(label="Answer", style=discord.ButtonStyle.green, emoji=EMOJIS["notes"])
        question_button.callback = self.show_question
        answer_button.callback = self.answer_button
        self.controls.add_item(question_button)
        self.controls.add_item(answer_button)
        self.add_item(self.controls)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("This challenge isn't for you.", ephemeral=True)
            return False
        return True

    async def show_question(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(description=f"**{EMOJIS["question"]} Question:** {self.question}", color=MAIN_COLOR),
            ephemeral=True,
        )

    async def answer_button(self, interaction: discord.Interaction):
        if self.answered:
            await interaction.response.send_message("You already answered.", ephemeral=True)
            return

        modal = discord.ui.Modal(title="Your Answer")
        answer_input = discord.ui.TextInput(
            label="Answer", placeholder="Type your answer here…", max_length=100
        )
        modal.add_item(answer_input)

        async def on_submit(modal_interaction: discord.Interaction):
            text = answer_input.value.strip().lower()
            correct = text == self.answer
            self.answered = True

            disable_all_components(self)
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass

            result = discord.Embed(
                title=f"{EMOJIS["brain"]} Trivia Result",
                description=(
                    f"{self.opponent.mention} answered: **{text}**\n"
                    + (f"{EMOJIS["tick"]} Correct! Big brain confirmed. {EMOJIS["brain"]}" if correct
                       else f"{EMOJIS["cross"]} Wrong! The answer was **{self.answer}**")
                    + f"\n{BREAKER_LINE}"
                ),
                color=WIN_COLOR if correct else LOSS_COLOR,
                timestamp=discord.utils.utcnow(),
            )
            await self.channel.send(content=f"{self.opponent.mention} {self.challenger.mention}", embed=result)
            await modal_interaction.response.defer()

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    async def on_timeout(self):
        if self.answered:
            return
        disable_all_components(self)
        if self.message:
            try:
                timeout_embed = discord.Embed(
                    description=f"{EMOJIS["cooldown"]} {self.opponent.mention} ran out of time! Answer was **{self.answer}**\n{BREAKER_LINE}",
                    color=LOSS_COLOR,
                )
                self.set_embed(timeout_embed)
                await self.message.edit(view=self)
            except Exception:
                pass

# ══════════════════════════════════════════════════════════════════════════════
#  ROCK‑PAPER‑SCISSORS VIEW
# ══════════════════════════════════════════════════════════════════════════════
class RPSView(ComponentsV2View):
    WINS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    MOVE_EMOJI = {"rock": EMOJIS["rock"], "paper": EMOJIS["paper"], "scissors": EMOJIS["scissors"]}

    def __init__(self, p1: discord.User, p2: discord.User):
        super().__init__(timeout=60)
        self.p1 = p1
        self.p2 = p2
        self.p1_move = None
        self.p2_move = None
        self.controls = discord.ui.ActionRow()
        for label, move, emoji_key in (("Rock", "rock", "rock"), ("Paper", "paper", "paper"), ("Scissors", "scissors", "scissors")):
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, emoji=EMOJIS[emoji_key])
            button.callback = getattr(self, move)
            self.controls.add_item(button)
        self.add_item(self.controls)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user not in (self.p1, self.p2):
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return False
        return True

    async def rock(self, interaction: discord.Interaction):
        await self._move(interaction, "rock")

    async def paper(self, interaction: discord.Interaction):
        await self._move(interaction, "paper")

    async def scissors(self, interaction: discord.Interaction):
        await self._move(interaction, "scissors")

    async def _move(self, interaction: discord.Interaction, move: str):
        if interaction.user == self.p1:
            if self.p1_move is not None:
                await interaction.response.send_message(f"Already chose! No backsies. {EMOJIS['wink']}", ephemeral=True)
                return
            self.p1_move = move
        else:
            if self.p2_move is not None:
                await interaction.response.send_message(f"Already chose! No backsies. {EMOJIS['wink']}", ephemeral=True)
                return
            self.p2_move = move

        await interaction.response.send_message(
            f"Locked in **{move}** {self.MOVE_EMOJI[move]} — waiting for opponent…", ephemeral=True
        )

        if self.p1_move is not None and self.p2_move is not None:
            disable_all_components(self)
            w = self._winner()
            desc = (
                f"{self.p1.mention} chose **{self.p1_move}** {self.MOVE_EMOJI[self.p1_move]}\n"
                f"{self.p2.mention} chose **{self.p2_move}** {self.MOVE_EMOJI[self.p2_move]}\n\n"
                + (f"{EMOJIS['handshake']} It's a tie! Great minds think alike." if w == 0
                   else f"{EMOJIS["trophy"]} **{(self.p1 if w == 1 else self.p2).display_name}** wins! {EMOJIS["confetti"]}")
            )
            embed = discord.Embed(
                title=f"{EMOJIS["rps_challenge"]} Rock Paper Scissors — Results",
                description=desc + f"\n{BREAKER_LINE}",
                color=MAIN_COLOR if w == 0 else WIN_COLOR,
                timestamp=discord.utils.utcnow(),
            )
            try:
                self.set_embed(embed)
                await interaction.message.edit(view=self)
            except discord.NotFound:
                await interaction.channel.send(embed=embed)

    def _winner(self) -> int:
        if self.p1_move == self.p2_move:
            return 0
        return 1 if self.WINS[self.p1_move] == self.p2_move else 2

# ══════════════════════════════════════════════════════════════════════════════
#  TIC‑TAC‑TOE VIEW
# ══════════════════════════════════════════════════════════════════════════════
class TicTacToeView(ComponentsV2View):
    WINS = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]

    def __init__(self, p1: discord.User, p2: discord.User):
        super().__init__(timeout=120)
        self.p1 = p1
        self.p2 = p2
        self.current = p1
        self.board = [None] * 9
        self.winner = None
        self.symbols = {p1.id: EMOJIS["x"], p2.id: EMOJIS["o"]}
        self.board_buttons: list[discord.ui.Button] = []
        rows = [discord.ui.ActionRow() for _ in range(3)]
        for row in rows:
            self.add_item(row)
        for i in range(9):
            btn = discord.ui.Button(label="\u200b", style=discord.ButtonStyle.secondary)
            btn.callback = self._make_callback(i)
            self.board_buttons.append(btn)
            rows[i // 3].add_item(btn)

    def _make_callback(self, idx: int):
        async def callback(interaction: discord.Interaction):
            await self._click(interaction, idx)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user not in (self.p1, self.p2):
            await interaction.response.send_message("Not your game!", ephemeral=True)
            return False
        if interaction.user != self.current:
            await interaction.response.send_message(f"Not your turn — patience. {EMOJIS['cooldown']}", ephemeral=True)
            return False
        return True

    async def _click(self, interaction: discord.Interaction, idx: int):
        if self.board[idx] is not None or self.winner:
            return
        self.board[idx] = interaction.user.id
        btn = self.board_buttons[idx]
        btn.label = self.symbols[interaction.user.id]
        btn.style = (discord.ButtonStyle.danger
                     if btn.label == EMOJIS["x"] else discord.ButtonStyle.success)
        btn.disabled = True
        self.winner = self._check_win()

        if self.winner:
            desc = (f"{self.p1.mention} {EMOJIS["x"]} vs {EMOJIS["o"]} {self.p2.mention}\n\n"
                    f"{EMOJIS["trophy"]} **{interaction.user.display_name} wins!** {EMOJIS["confetti"]}")
            disable_all_components(self)
        elif None not in self.board:
            desc = f"{self.p1.mention} {EMOJIS["x"]} vs {EMOJIS["o"]} {self.p2.mention}\n\n{EMOJIS["handshake"]} It's a draw!"
            self.winner = "draw"
            disable_all_components(self)
        else:
            self.current = self.p2 if self.current == self.p1 else self.p1
            desc = f"{self.p1.mention} {EMOJIS["x"]} vs {EMOJIS["o"]} {self.p2.mention}\n\n{EMOJIS["bullseye"]} {self.current.mention}'s turn"

        embed = make_embed(
            interaction,
            f"{EMOJIS["game_controller"]} Tic Tac Toe",
            desc, MAIN_COLOR,
            success=(bool(self.winner) and self.winner != "draw"),
        )
        self.set_embed(embed)
        await interaction.response.edit_message(view=self)

    def _check_win(self):
        for combo in self.WINS:
            a, b, c = combo
            if self.board[a] and self.board[a] == self.board[b] == self.board[c]:
                return self.board[a]
        return None

# ══════════════════════════════════════════════════════════════════════════════
#  FUN COG
# ══════════════════════════════════════════════════════════════════════════════
class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.trivia_challenges: Dict[int, TriviaView] = {}
        self.hangman_challenges: Dict[int, HangmanChallenge] = {}
        self.http_session: Optional[aiohttp.ClientSession] = None
        self._cleanup_task: Optional[asyncio.Task] = None

    async def cog_load(self):
        self.http_session = aiohttp.ClientSession()
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def cog_unload(self):
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        self._cleanup_task = None
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()

    async def _cleanup_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            now = time.time()
            expired = [uid for uid, c in list(self.hangman_challenges.items()) if c.expires < now]
            for uid in expired:
                del self.hangman_challenges[uid]
            await asyncio.sleep(60)

    async def _dm_opponent(self, opponent: discord.Member, text: str) -> bool:
        try:
            await opponent.send(text)
            return True
        except discord.Forbidden:
            return False

    async def _fetch_roast(self) -> str:
        if not self.http_session:
            return "Roast service unavailable."
        try:
            async with self.http_session.get(
                "https://evilinsult.com/generate_insult.php?lang=en&type=text"
            ) as r:
                if r.status == 200:
                    text = await r.text()
                    return text.replace("&quot;", '"').replace("&amp;", "&").strip()
        except Exception:
            pass
        return "You're actually too special to roast right now."

    # ── on_message listener: drives Hangman letter guessing ────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        challenge = self.hangman_challenges.get(message.author.id)
        if not challenge or challenge.channel_id != message.channel.id:
            return

        content = message.content.strip().lower()
        if not content:
            return

        if len(content) != 1 or not content.isalpha():
            warn = discord.Embed(
                description=(
                    f"{EMOJIS["cross"]} Guess **one letter at a time** — you can't just blurt out the word!\n"
                    f"Reply with a single letter, like `e` or `a`."
                ),
                color=LOSS_COLOR,
            )
            try:
                await message.reply(embed=warn, mention_author=False, delete_after=6)
            except Exception:
                pass
            return

        letter = content
        if letter in challenge.guessed:
            try:
                await message.add_reaction(f"{EMOJIS['reroll']}")
            except Exception:
                pass
            return

        challenger = message.guild.get_member(challenge.challenger_id)
        opponent = message.author

        challenge.guess(letter)

        old_channel = self.bot.get_channel(challenge.channel_id)
        if old_channel:
            try:
                old_msg = await old_channel.fetch_message(challenge.message_id)
                await old_msg.delete()
            except Exception:
                pass

            new_embed = challenge.get_embed(challenger or opponent, opponent)
            try:
                new_msg = await old_channel.send(embed=new_embed)
                challenge.message_id = new_msg.id
            except Exception:
                pass

        try:
            await message.delete()
        except Exception:
            pass

        if challenge.ended:
            self.hangman_challenges.pop(opponent.id, None)

    # ── reverse ────────────────────────────────────────────────────────────────
    @commands.command(aliases=["rev"])
    async def reverse(self, ctx, *, text: str):
        embed = make_embed(ctx, f"{EMOJIS["uno_reverse"]} Reverse",
                           f"```{text}``` → ```{text[::-1]}```", 0x1ABC9C)
        await ctx.send(embed=embed)

    async def slash_reverse(self, interaction: discord.Interaction, text: str):
        embed = make_embed(interaction, f"{EMOJIS["uno_reverse"]} Reverse",
                           f"```{text}``` → ```{text[::-1]}```", 0x1ABC9C)
        await interaction.response.send_message(embed=embed)

    # ── mock ───────────────────────────────────────────────────────────────────
    @commands.command()
    async def mock(self, ctx, *, text: str):
        mocked = "".join(random.choice([c.upper(), c.lower()]) for c in text)
        await ctx.send(embed=make_embed(ctx, f"{EMOJIS["clown"]} Mock",
                                        f"```{mocked}```", 0xE67E22))

    async def slash_mock(self, interaction: discord.Interaction, text: str):
        mocked = "".join(random.choice([c.upper(), c.lower()]) for c in text)
        await interaction.response.send_message(
            embed=make_embed(interaction, f"{EMOJIS["clown"]} Mock",
                             f"```{mocked}```", 0xE67E22))

    # ── clap ───────────────────────────────────────────────────────────────────
    @commands.command()
    async def clap(self, ctx, *, text: str):
        clapped = f" {EMOJIS["clap"]} ".join(text.split())
        await ctx.send(embed=make_embed(ctx, f"{EMOJIS["clap"]} Clap",
                                        f"```{clapped}```", 0x2ECC71))

    async def slash_clap(self, interaction: discord.Interaction, text: str):
        clapped = f" {EMOJIS["clap"]} ".join(text.split())
        await interaction.response.send_message(
            embed=make_embed(interaction, f"{EMOJIS["clap"]} Clap",
                             f"```{clapped}```", 0x2ECC71))

    # ── 8ball ──────────────────────────────────────────────────────────────────
    _8BALL = ["Yes!", "No.", "Maybe…", "Ask again later.",
              "Definitely!", "I doubt it.", "Certainly.", "Not sure."]

    @commands.command(aliases=["8b"])
    async def eightball(self, ctx, *, question: str):
        await ctx.send(embed=make_embed(
            ctx, f"{EMOJIS["black_8ball"]} 8Ball",
            f"**Q:** {question}\n**A:** {random.choice(self._8BALL)}", MAIN_COLOR))

    async def slash_eightball(self, interaction: discord.Interaction, question: str):
        await interaction.response.send_message(embed=make_embed(
            interaction, f"{EMOJIS["black_8ball"]} 8Ball",
            f"**Q:** {question}\n**A:** {random.choice(self._8BALL)}", MAIN_COLOR))

    # ── roast ──────────────────────────────────────────────────────────────────
    @commands.command()
    async def roast(self, ctx, member: Optional[discord.Member] = None):
        target = (member or ctx.author).mention
        await ctx.send(embed=make_embed(
            ctx, f"{EMOJIS["fire"]} Roasted!",
            f"{target}: {await self._fetch_roast()}", LOSS_COLOR))

    async def slash_roast(self, interaction: discord.Interaction,
                          member: Optional[discord.Member] = None):
        await interaction.response.defer()
        target = (member or interaction.user).mention
        await interaction.followup.send(embed=make_embed(
            interaction, f"{EMOJIS["fire"]} Roasted!",
            f"{target}: {await self._fetch_roast()}", LOSS_COLOR))

    # ── RPS ────────────────────────────────────────────────────────────────────
    @commands.command(name="rps", aliases=["rockpaperscissors"])
    @booster_only()
    async def rps(self, ctx, opponent: discord.Member):
        if opponent == ctx.author or opponent.bot:
            return await ctx.send(embed=error_embed(ctx, "Error", "Invalid opponent."))
        await self._start_rps(ctx.channel, ctx.author, opponent)

    async def slash_rps(self, interaction: discord.Interaction, opponent: discord.Member):
        if opponent == interaction.user or opponent.bot:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Error", "Invalid opponent."), ephemeral=True)
        await interaction.response.send_message(
            embed=discord.Embed(description="Sending the duel request…", color=MAIN_COLOR),
            ephemeral=True,
        )
        await self._start_rps(interaction.channel, interaction.user, opponent)

    async def _start_rps(self, channel, challenger, opponent):
        invite = await send_duel_invite(
            channel, challenger, opponent, "Rock Paper Scissors", GAME_EMOJI["rps"]
        )
        if not invite.accepted:
            return
        view = RPSView(challenger, opponent)
        embed = discord.Embed(
            title=f"{EMOJIS["rps_challenge"]} Rock Paper Scissors — Fight!",
            description=(
                f"{challenger.mention} vs {opponent.mention}\n"
                f"Both players pick a move below. May the best hand win. {EMOJIS["fist_bump"]}\n{BREAKER_LINE}"
            ),
            color=MAIN_COLOR,
        )
        prepare_v2_view(view, embed)
        await channel.send(view=view)

    # ── Tic‑Tac‑Toe ────────────────────────────────────────────────────────────
    @commands.command(name="tictactoe", aliases=["ttt"])
    @booster_only()
    async def tictactoe(self, ctx, opponent: discord.Member):
        if opponent == ctx.author or opponent.bot:
            return await ctx.send(embed=error_embed(ctx, "Error", "Invalid opponent."))
        await self._start_ttt(ctx.channel, ctx.author, opponent)

    async def slash_tictactoe(self, interaction: discord.Interaction, opponent: discord.Member):
        if opponent == interaction.user or opponent.bot:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Error", "Invalid opponent."), ephemeral=True)
        await interaction.response.send_message(
            embed=discord.Embed(description="Sending the duel request…", color=MAIN_COLOR),
            ephemeral=True,
        )
        await self._start_ttt(interaction.channel, interaction.user, opponent)

    async def _start_ttt(self, channel, challenger, opponent):
        invite = await send_duel_invite(
            channel, challenger, opponent, "Tic-Tac-Toe", GAME_EMOJI["ttt"]
        )
        if not invite.accepted:
            return
        view = TicTacToeView(challenger, opponent)
        # Build a dummy context-like object for make_embed (or just pass None and handle)
        # We'll use a simple namespace for author and guild
        class DummyCtx:
            def __init__(self, author, guild):
                self.author = author
                self.guild = guild
        dummy = DummyCtx(challenger, channel.guild)
        embed = make_embed(
            dummy,
            f"{EMOJIS["game_controller"]} Tic Tac Toe",
            f"{challenger.mention} {EMOJIS["x"]} vs {EMOJIS["o"]} {opponent.mention}\n\n{EMOJIS["bullseye"]} {challenger.mention}'s turn",
            MAIN_COLOR,
        )
        prepare_v2_view(view, embed)
        await channel.send(view=view)

    # ── Trivia (slash only) ─────────────────────────────────────────────────────
    async def slash_trivia(self, interaction: discord.Interaction,
                           opponent: discord.Member, question: str, answer: str):
        if opponent == interaction.user or opponent.bot:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Error", "Invalid opponent."), ephemeral=True)
        if opponent.id in self.trivia_challenges:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Error", f"{opponent.mention} already has a pending challenge."),
                ephemeral=True)

        await interaction.response.send_message(
            embed=discord.Embed(description="Sending the duel request…", color=MAIN_COLOR),
            ephemeral=True,
        )

        invite = await send_duel_invite(
            interaction.channel, interaction.user, opponent,
            "Trivia", GAME_EMOJI["trivia"], extra="*(A question is locked and loaded.)*"
        )
        if not invite.accepted:
            return

        view = TriviaView(question.strip(), answer.strip(),
                          interaction.user, opponent, interaction.channel)
        self.trivia_challenges[opponent.id] = view

        embed = discord.Embed(
            title=f"{EMOJIS["brain"]} Trivia Challenge",
            description=(
                f"{interaction.user.mention} is testing {opponent.mention}'s big brain! {EMOJIS["brain"]}\n"
                f"Click **Show Question** → **Answer** to respond.\n{EMOJIS['cooldown']} 1 minutes on the clock."
                f"\n{BREAKER_LINE}"
            ),
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Only {opponent.display_name} can answer.")
        prepare_v2_view(view, embed)
        view.add_text(opponent.mention)
        msg = await interaction.channel.send(view=view)
        view.message = msg
        self.bot.loop.create_task(self._trivia_timeout(view, opponent.id))

    async def _trivia_timeout(self, view: TriviaView, opponent_id: int):
        await asyncio.sleep(60)
        if not view.answered:
            for c in view.children:
                c.disabled = True
            if view.message:
                try:
                    await view.message.edit(view=view)
                except Exception:
                    pass
        self.trivia_challenges.pop(opponent_id, None)

    # ── Hangman (slash only) ─────────────────────────────────────────────────────
    async def slash_hangman(self, interaction: discord.Interaction,
                            opponent: discord.Member, word: str):
        if opponent == interaction.user or opponent.bot:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Error", "Invalid opponent."), ephemeral=True)
        if not word.isalpha() or len(word) < 2:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Error", "Letters only, min 2 characters."), ephemeral=True)
        if opponent.id in self.hangman_challenges:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Error", f"{opponent.mention} already has an active hangman game."),
                ephemeral=True)

        await interaction.response.send_message(
            embed=discord.Embed(description="Sending the duel request…", color=MAIN_COLOR),
            ephemeral=True,
        )

        invite = await send_duel_invite(
            interaction.channel, interaction.user, opponent,
            "Hangman", GAME_EMOJI["hangman"], extra="*(A secret word is waiting…)*"
        )
        if not invite.accepted:
            return

        word = word.lower()
        embed = discord.Embed(
            title=f"{EMOJIS["hangman"]} Hangman — Game Start!",
            description=(
                f"{HangmanChallenge.HANGMAN_STAGES[0]}\n"
                f"## `{' '.join(['_' for _ in word])}`\n"
                f"Mistakes: 0/{HangmanChallenge.MAX_MISTAKES}\n\n"
                f"{opponent.mention}, reply in this channel with **one letter** to guess — "
                f"no whole-word guesses allowed!"
                f"\n{BREAKER_LINE}"
            ),
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        msg = await interaction.channel.send(content=f"{opponent.mention}", embed=embed)

        self.hangman_challenges[opponent.id] = HangmanChallenge(
            word, interaction.user.id, opponent.id, interaction.channel_id, msg.id
        )
        await self._dm_opponent(
            opponent,
            f"{EMOJIS["hangman"]} **Hangman from {interaction.user.display_name}**\n"
            f"Head to {interaction.channel.mention} and reply with one letter at a time. 10 min time limit."
        )

    @commands.command(name="trivia")
    @booster_only()
    async def trivia_cmd(self, ctx: commands.Context, opponent: discord.Member, *, question_and_answer: str):
        """Challenge a member: *trivia @member question | answer"""
        if opponent == ctx.author or opponent.bot:
            return await ctx.send(embed=error_embed(ctx, "Error", "Invalid opponent."))
        if opponent.id in self.trivia_challenges:
            return await ctx.send(embed=error_embed(ctx, "Error", f"{opponent.mention} already has a pending challenge."))
        question, separator, answer = question_and_answer.partition("|")
        if not separator or not question.strip() or not answer.strip():
            return await ctx.send(embed=error_embed(ctx, "Usage", f"Use `{ctx.prefix}trivia @member question | answer`."))

        invite = await send_duel_invite(
            ctx.channel, ctx.author, opponent,
            "Trivia", GAME_EMOJI["trivia"], extra="*(A question is locked and loaded.)*"
        )
        if not invite.accepted:
            return

        view = TriviaView(question.strip(), answer.strip(), ctx.author, opponent, ctx.channel)
        self.trivia_challenges[opponent.id] = view
        embed = discord.Embed(
            title=f"{EMOJIS['brain']} Trivia Challenge",
            description=(
                f"{ctx.author.mention} is testing {opponent.mention}'s big brain! {EMOJIS['brain']}\n"
                f"Click **Show Question** → **Answer** to respond.\n{EMOJIS['cooldown']} 1 minutes on the clock."
                f"\n{BREAKER_LINE}"
            ),
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Only {opponent.display_name} can answer.")
        prepare_v2_view(view, embed)
        view.add_text(opponent.mention)
        msg = await ctx.channel.send(view=view)
        view.message = msg
        self.bot.loop.create_task(self._trivia_timeout(view, opponent.id))

    @commands.command(name="hangman")
    @booster_only()
    async def hangman_cmd(self, ctx: commands.Context, opponent: discord.Member, word: str):
        """Challenge a member to hangman: *hangman @member word"""
        if opponent == ctx.author or opponent.bot:
            return await ctx.send(embed=error_embed(ctx, "Error", "Invalid opponent."))
        if not word.isalpha() or len(word) < 2:
            return await ctx.send(embed=error_embed(ctx, "Error", "Letters only, minimum 2 characters."))
        if opponent.id in self.hangman_challenges:
            return await ctx.send(embed=error_embed(ctx, "Error", f"{opponent.mention} already has an active hangman game."))

        invite = await send_duel_invite(
            ctx.channel, ctx.author, opponent,
            "Hangman", GAME_EMOJI["hangman"], extra="*(A secret word is waiting…)*"
        )
        if not invite.accepted:
            return

        word = word.lower()
        embed = discord.Embed(
            title=f"{EMOJIS['hangman']} Hangman — Game Start!",
            description=(
                f"{HangmanChallenge.HANGMAN_STAGES[0]}\n"
                f"## `{' '.join(['_' for _ in word])}`\n"
                f"Mistakes: 0/{HangmanChallenge.MAX_MISTAKES}\n\n"
                f"{opponent.mention}, reply in this channel with **one letter** to guess — "
                f"no whole-word guesses allowed!\n{BREAKER_LINE}"
            ),
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        msg = await ctx.channel.send(content=opponent.mention, embed=embed)
        self.hangman_challenges[opponent.id] = HangmanChallenge(
            word, ctx.author.id, opponent.id, ctx.channel.id, msg.id
        )
        await self._dm_opponent(
            opponent,
            f"{EMOJIS['hangman']} **Hangman from {ctx.author.display_name}**\n"
            f"Head to {ctx.channel.mention} and reply with one letter at a time. 10 min time limit."
        )

    @commands.command(name="exit")
    async def exit_game_cmd(self, ctx: commands.Context, opponent: discord.Member):
        """Force-end a hangman/trivia game you started (booster/staff only)."""
        if not ctx.guild:
            return await ctx.send(embed=error_embed(ctx, "Error", "Server only."))
        member = ctx.author
        hangman = self.hangman_challenges.get(opponent.id)
        trivia = self.trivia_challenges.get(opponent.id)
        if not hangman and not trivia:
            return await ctx.send(embed=error_embed(ctx, "Error", f"No active game found involving {opponent.mention}."))
        starter_id = hangman.challenger_id if hangman else trivia.challenger.id
        if not can_force_end(member, starter_id, self.bot):
            return await ctx.send(embed=error_embed(
                ctx, "Not Allowed", "Only staff, or the booster who started this game, can end it."))

        if hangman:
            self.hangman_challenges.pop(opponent.id, None)
            channel = self.bot.get_channel(hangman.channel_id)
            if channel:
                try:
                    old_msg = await channel.fetch_message(hangman.message_id)
                    await old_msg.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
                await channel.send(embed=discord.Embed(
                    description=(
                        f"{EMOJIS['stop']} Hangman game ended early by {member.mention}.\n"
                        f"The word was **`{hangman.word}`**.\n{BREAKER_LINE}"
                    ), color=LOSS_COLOR,
                ))

        if trivia:
            self.trivia_challenges.pop(opponent.id, None)
            trivia.answered = True
            for child in trivia.children:
                child.disabled = True
            if trivia.message:
                try:
                    await trivia.message.edit(view=trivia)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            await ctx.channel.send(embed=discord.Embed(
                description=f"{EMOJIS['stop']} Trivia challenge ended early by {member.mention}.\n{BREAKER_LINE}",
                color=LOSS_COLOR,
            ))
        await ctx.send(embed=success_embed(ctx, "Game Ended", f"Ended the active game with {opponent.mention}."))

    # ── Exit / force-end ─────────────────────────────────────────────────────────
    async def slash_exit(self, interaction: discord.Interaction,
                         opponent: discord.Member):
        member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
        if not member:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Error", "Server only."), ephemeral=True)

        hangman = self.hangman_challenges.get(opponent.id)
        trivia = self.trivia_challenges.get(opponent.id)

        if not hangman and not trivia:
            return await interaction.response.send_message(
                embed=error_embed(interaction, "Error", f"No active game found involving {opponent.mention}."),
                ephemeral=True)

        starter_id = hangman.challenger_id if hangman else trivia.challenger.id

        if not can_force_end(member, starter_id, interaction.client):
            return await interaction.response.send_message(
                embed=error_embed(
                    interaction, "Not Allowed",
                    "Only the staff, or the booster who **started** this game, can end it."
                ),
                ephemeral=True,
            )

        if hangman:
            self.hangman_challenges.pop(opponent.id, None)
            channel = self.bot.get_channel(hangman.channel_id)
            if channel:
                try:
                    old_msg = await channel.fetch_message(hangman.message_id)
                    await old_msg.delete()
                except Exception:
                    pass
                await channel.send(embed=discord.Embed(
                    description=(
                        f"{EMOJIS["stop"]} Hangman game ended early by {interaction.user.mention}.\n"
                        f"The word was **`{hangman.word}`**.\n{BREAKER_LINE}"
                    ),
                    color=LOSS_COLOR,
                ))

        if trivia:
            self.trivia_challenges.pop(opponent.id, None)
            trivia.answered = True
            for c in trivia.children:
                c.disabled = True
            if trivia.message:
                try:
                    await trivia.message.edit(view=trivia)
                except Exception:
                    pass
            await interaction.channel.send(embed=discord.Embed(
                description=f"{EMOJIS["stop"]} Trivia challenge ended early by {interaction.user.mention}.\n{BREAKER_LINE}",
                color=LOSS_COLOR,
            ))

        await interaction.response.send_message(
            embed=success_embed(interaction, "Game Ended", f"Ended the active game with {opponent.mention}."),
            ephemeral=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
