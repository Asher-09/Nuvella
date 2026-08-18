import discord
from discord.ext import commands
from discord import app_commands
import random
import asyncio
import logging
import time
from typing import Optional, Dict
import aiohttp

log = logging.getLogger(__name__)

# ── theme ──────────────────────────────────────────────────────────────────────
MAIN_COLOR   = 0x3498DB
WIN_COLOR    = 0x57F287
LOSS_COLOR   = 0xED4245

# ── emoji constants ──────────────────────────────────────────────────────────
# custom Discord emojis (server-specific)
EMOJI_BREAKER   = "<:breaker:1537913026643238932>"
EMOJI_WRONG     = "<:cross:1537915734087303199>"
EMOJI_SUCCESS   = "<a:tick:1537915747454554222>"
EMOJI_TTT       = "<a:game_controller:1538063050601664533>"
EMOJI_HANGMAN   = "<:Hangman:1538063205648171048>"
EMOJI_TRIVIA    = "<a:loading_brain:1538063301177511956>"
EMOJI_FIRE      = "<a:purple_fire:1537923927106527304>"
EMOJI_CLOWN     = "<a:clown_silly_gif:1538063462289252382>"
EMOJI_CLAP      = "<a:clap_clap:1538063558695456840>"
EMOJI_REVERSE   = "<:uno_reverse:1538063692170661959>"
EMOJI_CONFETTI  = "<a:x_confete:1538039846164234250>"

# Unicode emojis
EMOJI_RPS_CHALLENGE = "✊"
EMOJI_ROCK          = "🪨"
EMOJI_PAPER         = "📄"
EMOJI_SCISSORS      = "✂️"
EMOJI_X             = "❌"
EMOJI_O             = "🇴"
EMOJI_QUESTION      = "<:question_mark:1537914997378912307>"
EMOJI_PENCIL        = "<:q_notes:1537922139431370854>"
EMOJI_BRAIN         = "<a:loading_brain:1538063301177511956>"
EMOJI_HOURGLASS     = "<a:cooldown:1537918560616185956>"
EMOJI_STOP          = "<:stop_just_stop:1538063961579061278>"
EMOJI_8BALL         = "<a:black_8ball:1538064042072215713>"
EMOJI_SWORD         = "<a:sword:1537921132894625943>"
EMOJI_NO_ENTRY      = "<:no_entry:1538064183147626537>"

# derived
BREAKER_LINE = EMOJI_BREAKER * 10

GAME_EMOJI = {
    "rps":      EMOJI_RPS_CHALLENGE,
    "ttt":      EMOJI_TTT,
    "hangman":  EMOJI_HANGMAN,
    "trivia":   EMOJI_TRIVIA,
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
    return make_embed(ctx, f"{EMOJI_WRONG} {title}", desc, LOSS_COLOR)

def success_embed(ctx, title: str, desc: str) -> discord.Embed:
    return make_embed(ctx, f"{EMOJI_SUCCESS} {title}", desc, MAIN_COLOR, success=True)

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
class DuelInviteView(discord.ui.View):
    def __init__(self, challenger: discord.User, opponent: discord.User,
                 game_name: str, timeout: float = 60.0):
        super().__init__(timeout=timeout)
        self.challenger = challenger
        self.opponent = opponent
        self.game_name = game_name
        self.accepted: Optional[bool] = None
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "This duel isn't addressed to you <a:eyes_sharking:1538064382062362765>", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji=EMOJI_SWORD)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.accepted = True
        for c in self.children:
            c.disabled = True
        embed = discord.Embed(
            description=f"{EMOJI_SUCCESS} **{self.opponent.display_name}** accepted the challenge! Let's go.",
            color=WIN_COLOR,
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji=EMOJI_NO_ENTRY)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.accepted = False
        for c in self.children:
            c.disabled = True
        embed = discord.Embed(
            description=f"{EMOJI_WRONG} **{self.opponent.display_name}** chickened out and declined.",
            color=LOSS_COLOR,
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        if self.accepted is None:
            for c in self.children:
                c.disabled = True
            if self.message:
                embed = discord.Embed(
                    description=f"{EMOJI_HOURGLASS} **{self.opponent.display_name}** didn't respond in time. Challenge expired.",
                    color=LOSS_COLOR,
                )
                try:
                    await self.message.edit(embed=embed, view=self)
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
    msg = await channel.send(content=f"{opponent.mention}", embed=embed, view=view)
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
                title = f"{EMOJI_CONFETTI} Hangman — Solved!"
                color = WIN_COLOR
                desc = (
                    f"{opponent.mention} cracked it! The word was **`{self.word}`**\n"
                    f"{stage}"
                )
            else:
                title = f"{EMOJI_HANGMAN} Hangman — Game Over"
                color = LOSS_COLOR
                desc = (
                    f"💀 {opponent.mention} got hanged! The word was **`{self.word}`**\n"
                    f"{stage}"
                )
        else:
            title = f"{EMOJI_HANGMAN} Hangman"
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
class TriviaView(discord.ui.View):
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

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("This challenge isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Show Question", style=discord.ButtonStyle.blurple, emoji=EMOJI_QUESTION)
    async def show_question(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(description=f"**{EMOJI_QUESTION} Question:** {self.question}", color=MAIN_COLOR),
            ephemeral=True,
        )

    @discord.ui.button(label="Answer", style=discord.ButtonStyle.green, emoji=EMOJI_PENCIL)
    async def answer_button(self, interaction: discord.Interaction, button: discord.ui.Button):
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

            for child in self.children:
                child.disabled = True
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass

            result = discord.Embed(
                title=f"{EMOJI_TRIVIA} Trivia Result",
                description=(
                    f"{self.opponent.mention} answered: **{text}**\n"
                    + (f"{EMOJI_SUCCESS} Correct! Big brain confirmed. {EMOJI_BRAIN}" if correct
                       else f"{EMOJI_WRONG} Wrong! The answer was **{self.answer}**")
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
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                timeout_embed = discord.Embed(
                    description=f"{EMOJI_HOURGLASS} {self.opponent.mention} ran out of time! Answer was **{self.answer}**\n{BREAKER_LINE}",
                    color=LOSS_COLOR,
                )
                await self.message.edit(embed=timeout_embed, view=self)
            except Exception:
                pass

# ══════════════════════════════════════════════════════════════════════════════
#  ROCK‑PAPER‑SCISSORS VIEW
# ══════════════════════════════════════════════════════════════════════════════
class RPSView(discord.ui.View):
    WINS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    MOVE_EMOJI = {"rock": EMOJI_ROCK, "paper": EMOJI_PAPER, "scissors": EMOJI_SCISSORS}

    def __init__(self, p1: discord.User, p2: discord.User):
        super().__init__(timeout=60)
        self.p1 = p1
        self.p2 = p2
        self.p1_move = None
        self.p2_move = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user not in (self.p1, self.p2):
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Rock", style=discord.ButtonStyle.primary, emoji=EMOJI_ROCK, row=0)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._move(interaction, "rock")

    @discord.ui.button(label="Paper", style=discord.ButtonStyle.primary, emoji=EMOJI_PAPER, row=0)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._move(interaction, "paper")

    @discord.ui.button(label="Scissors", style=discord.ButtonStyle.primary, emoji=EMOJI_SCISSORS, row=0)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._move(interaction, "scissors")

    async def _move(self, interaction: discord.Interaction, move: str):
        if interaction.user == self.p1:
            if self.p1_move is not None:
                await interaction.response.send_message("Already chose! No backsies. <a:wink_espeon:1538064579115089983>", ephemeral=True)
                return
            self.p1_move = move
        else:
            if self.p2_move is not None:
                await interaction.response.send_message("Already chose! No backsies. <a:wink_espeon:1538064579115089983>", ephemeral=True)
                return
            self.p2_move = move

        await interaction.response.send_message(
            f"Locked in **{move}** {self.MOVE_EMOJI[move]} — waiting for opponent…", ephemeral=True
        )

        if self.p1_move is not None and self.p2_move is not None:
            for c in self.children:
                c.disabled = True
            w = self._winner()
            desc = (
                f"{self.p1.mention} chose **{self.p1_move}** {self.MOVE_EMOJI[self.p1_move]}\n"
                f"{self.p2.mention} chose **{self.p2_move}** {self.MOVE_EMOJI[self.p2_move]}\n\n"
                + ("<a:a_handshake:1538064715606138934> It's a tie! Great minds think alike." if w == 0
                   else f"<:s_trophy:1538039103113662574> **{(self.p1 if w == 1 else self.p2).display_name}** wins! {EMOJI_CONFETTI}")
            )
            embed = discord.Embed(
                title=f"{EMOJI_RPS_CHALLENGE} Rock Paper Scissors — Results",
                description=desc + f"\n{BREAKER_LINE}",
                color=MAIN_COLOR if w == 0 else WIN_COLOR,
                timestamp=discord.utils.utcnow(),
            )
            try:
                await interaction.message.edit(embed=embed, view=self)
            except discord.NotFound:
                await interaction.channel.send(embed=embed)

    def _winner(self) -> int:
        if self.p1_move == self.p2_move:
            return 0
        return 1 if self.WINS[self.p1_move] == self.p2_move else 2

# ══════════════════════════════════════════════════════════════════════════════
#  TIC‑TAC‑TOE VIEW
# ══════════════════════════════════════════════════════════════════════════════
class TicTacToeView(discord.ui.View):
    WINS = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]

    def __init__(self, p1: discord.User, p2: discord.User):
        super().__init__(timeout=120)
        self.p1 = p1
        self.p2 = p2
        self.current = p1
        self.board = [None] * 9
        self.winner = None
        self.symbols = {p1.id: EMOJI_X, p2.id: EMOJI_O}
        for i in range(9):
            btn = discord.ui.Button(label="\u200b", style=discord.ButtonStyle.secondary, row=i // 3)
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, idx: int):
        async def callback(interaction: discord.Interaction):
            await self._click(interaction, idx)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user not in (self.p1, self.p2):
            await interaction.response.send_message("Not your game!", ephemeral=True)
            return False
        if interaction.user != self.current:
            await interaction.response.send_message("Not your turn — patience. <a:cooldown:1537918560616185956>", ephemeral=True)
            return False
        return True

    async def _click(self, interaction: discord.Interaction, idx: int):
        if self.board[idx] is not None or self.winner:
            return
        self.board[idx] = interaction.user.id
        btn = self.children[idx]
        btn.label = self.symbols[interaction.user.id]
        btn.style = (discord.ButtonStyle.danger
                     if btn.label == EMOJI_X else discord.ButtonStyle.success)
        btn.disabled = True
        self.winner = self._check_win()

        if self.winner:
            desc = (f"{self.p1.mention} {EMOJI_X} vs {EMOJI_O} {self.p2.mention}\n\n"
                    f"<:s_trophy:1538039103113662574> **{interaction.user.display_name} wins!** {EMOJI_CONFETTI}")
            for c in self.children:
                c.disabled = True
        elif None not in self.board:
            desc = f"{self.p1.mention} {EMOJI_X} vs {EMOJI_O} {self.p2.mention}\n\n<a:a_handshake:1538064715606138934> It's a draw!"
            self.winner = "draw"
            for c in self.children:
                c.disabled = True
        else:
            self.current = self.p2 if self.current == self.p1 else self.p1
            desc = f"{self.p1.mention} {EMOJI_X} vs {EMOJI_O} {self.p2.mention}\n\n<:bullseye:1538065001955467305> {self.current.mention}'s turn"

        embed = make_embed(
            interaction,
            f"{EMOJI_TTT} Tic Tac Toe",
            desc, MAIN_COLOR,
            success=(bool(self.winner) and self.winner != "draw"),
        )
        await interaction.response.edit_message(embed=embed, view=self)

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
        self._cleanup_task = bot.loop.create_task(self._cleanup_loop())

    async def cog_load(self):
        self.http_session = aiohttp.ClientSession()

    async def cog_unload(self):
        self._cleanup_task.cancel()
        if self.http_session:
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
                    f"{EMOJI_WRONG} Guess **one letter at a time** — you can't just blurt out the word!\n"
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
                await message.add_reaction("<a:6_reroll:1538066510981890098>")
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
        embed = make_embed(ctx, f"{EMOJI_REVERSE} Reverse",
                           f"```{text}``` → ```{text[::-1]}```", 0x1ABC9C)
        await ctx.send(embed=embed)

    @app_commands.command(name="reverse", description="Reverse text")
    async def slash_reverse(self, interaction: discord.Interaction, text: str):
        embed = make_embed(interaction, f"{EMOJI_REVERSE} Reverse",
                           f"```{text}``` → ```{text[::-1]}```", 0x1ABC9C)
        await interaction.response.send_message(embed=embed)

    # ── mock ───────────────────────────────────────────────────────────────────
    @commands.command()
    async def mock(self, ctx, *, text: str):
        mocked = "".join(random.choice([c.upper(), c.lower()]) for c in text)
        await ctx.send(embed=make_embed(ctx, f"{EMOJI_CLOWN} Mock",
                                        f"```{mocked}```", 0xE67E22))

    @app_commands.command(name="mock", description="SpOnGeBoB-ify text")
    async def slash_mock(self, interaction: discord.Interaction, text: str):
        mocked = "".join(random.choice([c.upper(), c.lower()]) for c in text)
        await interaction.response.send_message(
            embed=make_embed(interaction, f"{EMOJI_CLOWN} Mock",
                             f"```{mocked}```", 0xE67E22))

    # ── clap ───────────────────────────────────────────────────────────────────
    @commands.command()
    async def clap(self, ctx, *, text: str):
        clapped = f" {EMOJI_CLAP} ".join(text.split())
        await ctx.send(embed=make_embed(ctx, f"{EMOJI_CLAP} Clap",
                                        f"```{clapped}```", 0x2ECC71))

    @app_commands.command(name="clap", description="Add claps between words")
    async def slash_clap(self, interaction: discord.Interaction, text: str):
        clapped = f" {EMOJI_CLAP} ".join(text.split())
        await interaction.response.send_message(
            embed=make_embed(interaction, f"{EMOJI_CLAP} Clap",
                             f"```{clapped}```", 0x2ECC71))

    # ── 8ball ──────────────────────────────────────────────────────────────────
    _8BALL = ["Yes!", "No.", "Maybe…", "Ask again later.",
              "Definitely!", "I doubt it.", "Certainly.", "Not sure."]

    @commands.command(aliases=["8b"])
    async def eightball(self, ctx, *, question: str):
        await ctx.send(embed=make_embed(
            ctx, f"{EMOJI_8BALL} 8Ball",
            f"**Q:** {question}\n**A:** {random.choice(self._8BALL)}", MAIN_COLOR))

    @app_commands.command(name="eightball", description="Ask the magic 8-ball")
    async def slash_eightball(self, interaction: discord.Interaction, question: str):
        await interaction.response.send_message(embed=make_embed(
            interaction, f"{EMOJI_8BALL} 8Ball",
            f"**Q:** {question}\n**A:** {random.choice(self._8BALL)}", MAIN_COLOR))

    # ── roast ──────────────────────────────────────────────────────────────────
    @commands.command()
    async def roast(self, ctx, member: Optional[discord.Member] = None):
        target = (member or ctx.author).mention
        await ctx.send(embed=make_embed(
            ctx, f"{EMOJI_FIRE} Roasted!",
            f"{target}: {await self._fetch_roast()}", LOSS_COLOR))

    @app_commands.command(name="roast", description="Roast someone")
    async def slash_roast(self, interaction: discord.Interaction,
                          member: Optional[discord.Member] = None):
        await interaction.response.defer()
        target = (member or interaction.user).mention
        await interaction.followup.send(embed=make_embed(
            interaction, f"{EMOJI_FIRE} Roasted!",
            f"{target}: {await self._fetch_roast()}", LOSS_COLOR))

    # ── RPS ────────────────────────────────────────────────────────────────────
    @commands.command(name="rps", aliases=["rockpaperscissors"])
    @booster_only()
    async def rps(self, ctx, opponent: discord.Member):
        if opponent == ctx.author or opponent.bot:
            return await ctx.send(embed=error_embed(ctx, "Error", "Invalid opponent."))
        await self._start_rps(ctx.channel, ctx.author, opponent)

    @app_commands.command(name="rps", description="Challenge someone to Rock-Paper-Scissors")
    @app_booster_only()
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
            title=f"{EMOJI_RPS_CHALLENGE} Rock Paper Scissors — Fight!",
            description=(
                f"{challenger.mention} vs {opponent.mention}\n"
                f"Both players pick a move below. May the best hand win. <:fist_bump:1538066698727329913>\n{BREAKER_LINE}"
            ),
            color=MAIN_COLOR,
        )
        await channel.send(embed=embed, view=view)

    # ── Tic‑Tac‑Toe ────────────────────────────────────────────────────────────
    @commands.command(name="tictactoe", aliases=["ttt"])
    @booster_only()
    async def tictactoe(self, ctx, opponent: discord.Member):
        if opponent == ctx.author or opponent.bot:
            return await ctx.send(embed=error_embed(ctx, "Error", "Invalid opponent."))
        await self._start_ttt(ctx.channel, ctx.author, opponent)

    @app_commands.command(name="tictactoe", description="Challenge someone to Tic-Tac-Toe")
    @app_booster_only()
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
            f"{EMOJI_TTT} Tic Tac Toe",
            f"{challenger.mention} {EMOJI_X} vs {EMOJI_O} {opponent.mention}\n\n<:bullseye:1538065001955467305> {challenger.mention}'s turn",
            MAIN_COLOR,
        )
        await channel.send(embed=embed, view=view)

    # ── Trivia (slash only) ─────────────────────────────────────────────────────
    @app_commands.command(name="trivia", description="Challenge someone to a trivia question")
    @app_booster_only()
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
            title=f"{EMOJI_TRIVIA} Trivia Challenge",
            description=(
                f"{interaction.user.mention} is testing {opponent.mention}'s big brain! {EMOJI_BRAIN}\n"
                "Click **Show Question** → **Answer** to respond.\n<a:cooldown:1537918560616185956> 1 minutes on the clock."
                f"\n{BREAKER_LINE}"
            ),
            color=MAIN_COLOR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Only {opponent.display_name} can answer.")
        msg = await interaction.channel.send(content=f"{opponent.mention}", embed=embed, view=view)
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
    @app_commands.command(name="hangman", description="Challenge someone to Hangman")
    @app_booster_only()
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
            title=f"{EMOJI_HANGMAN} Hangman — Game Start!",
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
            f"{EMOJI_HANGMAN} **Hangman from {interaction.user.display_name}**\n"
            f"Head to {interaction.channel.mention} and reply with one letter at a time. 10 min time limit."
        )

    # ── Exit / force-end ─────────────────────────────────────────────────────────
    @app_commands.command(name="exit", description="Force-end a hangman or trivia game you started (booster/staff only)")
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
                        f"{EMOJI_STOP} Hangman game ended early by {interaction.user.mention}.\n"
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
                description=f"{EMOJI_STOP} Trivia challenge ended early by {interaction.user.mention}.\n{BREAKER_LINE}",
                color=LOSS_COLOR,
            ))

        await interaction.response.send_message(
            embed=success_embed(interaction, "Game Ended", f"Ended the active game with {opponent.mention}."),
            ephemeral=True,
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))