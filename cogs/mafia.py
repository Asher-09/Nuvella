import asyncio
import json
import random
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import discord
from discord.ext import commands


def _allowed_channel(ctx: commands.Context) -> bool:
    if not ctx.guild:
        return False
    cog = ctx.cog
    if cog is None or not hasattr(cog, "allowed_channel_id"):
        return False
    return ctx.channel.id == cog.allowed_channel_id


def _is_staff(ctx: commands.Context) -> bool:
    if not ctx.guild:
        return False
    cog = ctx.cog
    if cog is None:
        return False
    member = ctx.author
    if member.id == cog.owner_id:
        return True
    return any(role.id in cog.staff_role_ids for role in member.roles)


class MafiaGame:
    """Internal state for a single Mafia game."""

    def __init__(self, guild_id: int, channel_id: int, settings: Dict[str, Any], started_by: int):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.settings = settings
        self.started_by = started_by

        self.players: Dict[int, str] = {}
        self.roles: Dict[int, str] = {}
        self.alive_players: Set[int] = set()
        self.dead_players: Set[int] = set()

        self.phase: str = "lobby"
        self.round: int = 0
        self.night_actions: Dict[str, Optional[int]] = {}
        self.votes: Dict[int, int] = {}

        self.mafia_kills: int = 0
        self.doctor_saves_remaining: int = settings["doctor_saves"]

        self.lobby_message: Optional[discord.Message] = None
        self.phase_message: Optional[discord.Message] = None
        self.vote_view_message: Optional[discord.Message] = None
        self.night_view_messages: List[discord.Message] = []

        self.role_delivery_failures: Set[int] = set()
        self.roles_assigned: bool = False
        self.game_over: bool = False
        self.winner: Optional[str] = None
        self.task: Optional[asyncio.Task] = None


class LobbyView(discord.ui.View):
    def __init__(self, cog: "MafiaCog", game: MafiaGame):
        super().__init__(timeout=None)
        self.cog = cog
        self.game = game

        join_btn = discord.ui.Button(
            label="JOIN",
            style=discord.ButtonStyle.green,
            custom_id="mafia_lobby:join",
        )
        join_btn.callback = self.join_callback
        self.add_item(join_btn)

        leave_btn = discord.ui.Button(
            label="LEAVE",
            style=discord.ButtonStyle.gray,
            custom_id="mafia_lobby:leave",
        )
        leave_btn.callback = self.leave_callback
        self.add_item(leave_btn)

        start_btn = discord.ui.Button(
            label="START GAME",
            style=discord.ButtonStyle.blurple,
            custom_id="mafia_lobby:start",
        )
        start_btn.callback = self.start_callback
        self.add_item(start_btn)

        cancel_btn = discord.ui.Button(
            label="CANCEL GAME",
            style=discord.ButtonStyle.red,
            custom_id="mafia_lobby:cancel",
        )
        cancel_btn.callback = self.cancel_callback
        self.add_item(cancel_btn)

    async def join_callback(self, interaction: discord.Interaction):
        await self.cog._handle_join_button(interaction, self.game)

    async def leave_callback(self, interaction: discord.Interaction):
        await self.cog._handle_leave_button(interaction, self.game)

    async def start_callback(self, interaction: discord.Interaction):
        await self.cog._handle_start_button(interaction, self.game)

    async def cancel_callback(self, interaction: discord.Interaction):
        await self.cog._handle_cancel_button(interaction, self.game)


class NightActionView(discord.ui.View):
    def __init__(
        self,
        cog: "MafiaCog",
        game: MafiaGame,
        action_type: str,
        user_id: int,
    ):
        super().__init__(timeout=game.settings["night_duration"])
        self.cog = cog
        self.game = game
        self.action_type = action_type
        self.user_id = user_id

        options = self._build_options()
        if options:
            select = discord.ui.Select(
                placeholder="Choose a player",
                options=options,
                custom_id=f"mafia_night:{action_type}:{user_id}",
            )
            select.callback = self.select_callback
            self.add_item(select)

    def _build_options(self) -> List[discord.SelectOption]:
        options: List[discord.SelectOption] = []
        for pid in self.game.alive_players:
            if pid == self.user_id:
                continue
            label = self.game.players.get(pid, f"User {pid}")
            if len(label) > 100:
                label = label[:100]
            options.append(discord.SelectOption(label=label, value=str(pid)))
        return options

    async def select_callback(self, interaction: discord.Interaction):
        await self.cog._handle_night_action_select(
            interaction,
            self.game,
            self.action_type,
            self.user_id,
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class VoteView(discord.ui.View):
    def __init__(self, cog: "MafiaCog", game: MafiaGame):
        super().__init__(timeout=game.settings["vote_duration"])
        self.cog = cog
        self.game = game
        self.message: Optional[discord.Message] = None

        options: List[discord.SelectOption] = []
        for pid in game.alive_players:
            label = game.players.get(pid, f"User {pid}")
            if len(label) > 100:
                label = label[:100]
            options.append(discord.SelectOption(label=label, value=str(pid)))

        select = discord.ui.Select(
            placeholder="Vote for a player",
            options=options,
            custom_id="mafia_vote",
        )
        select.callback = self.vote_callback
        self.add_item(select)

    async def vote_callback(self, interaction: discord.Interaction):
        await self.cog._handle_vote_select(interaction, self.game)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class MafiaCog(commands.Cog, name="Mafia"):
    """Production-quality Mafia game cog."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        config_path = Path(__file__).resolve().parent.parent / "config.json"
        with config_path.open(encoding="utf-8") as f:
            self.config = json.load(f)

        self.owner_id: int = int(self.config.get("owner_id", 0))
        self.allowed_channel_id: int = int(self.config.get("allowed_channel_id", 0))

        roles_config: Dict[str, Any] = self.config.get("roles", {})
        self.staff_role_ids: Set[int] = {
            int(role_id) for role_id in roles_config.values() if role_id is not None
        }

        self.games: Dict[int, MafiaGame] = {}

        self.default_settings: Dict[str, int] = {
            "min_players": 8,
            "max_players": 16,
            "night_duration": 60,
            "day_duration": 120,
            "vote_duration": 60,
            "doctor_saves": 2,
            "mafia_kill_target": 5,
        }

    def _log(self, message: str):
        print(f"[MAFIA] {message}")

    def _is_staff_member(self, member: discord.Member) -> bool:
        if member.id == self.owner_id:
            return True
        return any(role.id in self.staff_role_ids for role in member.roles)

    def _is_staff_interaction(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            return interaction.user.id == self.owner_id
        return self._is_staff_member(member)

    def _get_member(self, guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
        return guild.get_member(user_id)

    def _lobby_embed(self, game: MafiaGame) -> discord.Embed:
        embed = discord.Embed(
            title="🔪 MAFIA",
            description=f"Players: {len(game.players)}/{game.settings['max_players']}",
            color=discord.Color.red(),
        )
        embed.add_field(
            name="Minimum Players",
            value=str(game.settings["min_players"]),
            inline=True,
        )
        embed.set_footer(text="Use the buttons below to join or manage the game.")
        return embed

    async def _update_lobby_message(self, game: MafiaGame):
        if game.lobby_message is not None:
            try:
                view = LobbyView(self, game)
                await game.lobby_message.edit(embed=self._lobby_embed(game), view=view)
            except discord.HTTPException:
                self._log(f"Failed to update lobby message | guild_id={game.guild_id}")

    def _try_add_player(self, game: MafiaGame, member: discord.Member) -> Tuple[bool, str]:
        if member.bot:
            return False, "Bots cannot join the game."
        if member.id in game.players:
            return False, "You are already in the game."
        if len(game.players) >= game.settings["max_players"]:
            return False, "The game is full."
        game.players[member.id] = member.display_name
        self._log(
            f"User joined | user_id={member.id} | name={member.display_name} | "
            f"player_count={len(game.players)}"
        )
        return True, "You joined the game."

    def _try_remove_player(
        self,
        game: MafiaGame,
        member: discord.Member,
        force: bool = False,
    ) -> Tuple[bool, str]:
        if member.id not in game.players:
            return False, "You are not in the game."
        if game.phase != "lobby" and not force:
            return False, "Game has already started. Ask staff to remove you."
        if game.phase == "lobby":
            game.players.pop(member.id, None)
            game.roles.pop(member.id, None)
            self._log(
                f"User left | user_id={member.id} | name={member.display_name} | "
                f"player_count={len(game.players)}"
            )
            return True, "You left the game."

        # Force-remove from an active game.
        if member.id in game.alive_players:
            game.alive_players.discard(member.id)
            game.dead_players.add(member.id)
        elif member.id in game.dead_players:
            game.dead_players.discard(member.id)

        game.players.pop(member.id, None)
        game.roles.pop(member.id, None)
        game.alive_players.discard(member.id)
        game.dead_players.discard(member.id)

        self._log(
            f"Player force-removed by staff | user_id={member.id} | name={member.display_name}"
        )
        return True, f"Removed {member.display_name} from the game."

    def _assign_roles(self, game: MafiaGame) -> Dict[int, str]:
        players = list(game.players.keys())
        random.shuffle(players)

        mafia_count = max(1, len(players) // 5)
        doctor_count = 1 if len(players) >= game.settings["min_players"] else 0
        detective_count = 1 if len(players) >= game.settings["min_players"] else 0

        roles: Dict[int, str] = {}
        idx = 0
        for _ in range(mafia_count):
            if idx >= len(players):
                break
            roles[players[idx]] = "Mafia"
            idx += 1
        for _ in range(doctor_count):
            if idx >= len(players):
                break
            roles[players[idx]] = "Doctor"
            idx += 1
        for _ in range(detective_count):
            if idx >= len(players):
                break
            roles[players[idx]] = "Detective"
            idx += 1
        while idx < len(players):
            roles[players[idx]] = "Civilian"
            idx += 1

        self._log(
            f"Roles assigned | players={len(players)} | "
            f"mafia={mafia_count} | doctor={doctor_count} | detective={detective_count}"
        )
        return roles

    def _role_dm_embed(self, role: str, game: MafiaGame) -> discord.Embed:
        if role == "Mafia":
            embed = discord.Embed(
                title="🔪 You are MAFIA.",
                description=(
                    "Your objective:\n"
                    f"Reach {game.settings['mafia_kill_target']} successful Mafia kills.\n"
                    "During the night, choose a player to kill."
                ),
                color=discord.Color.red(),
            )
        elif role == "Doctor":
            embed = discord.Embed(
                title="🩺 You are the DOCTOR.",
                description=(
                    "You can save a player from a Mafia attack.\n"
                    f"You have {game.doctor_saves_remaining} saves for the entire game."
                ),
                color=discord.Color.green(),
            )
        elif role == "Detective":
            embed = discord.Embed(
                title="🕵️ You are the DETECTIVE.",
                description="You can investigate players and determine whether they are Mafia.",
                color=discord.Color.blue(),
            )
        else:
            embed = discord.Embed(
                title="👤 You are a CIVILIAN.",
                description="Find and eliminate the Mafia.",
                color=discord.Color.light_gray(),
            )
        embed.set_footer(text="This role is secret. Do not share it publicly.")
        return embed

    async def _send_role_dms(self, game: MafiaGame, guild: discord.Guild) -> Set[int]:
        failed: Set[int] = set()
        for user_id, role in game.roles.items():
            member = guild.get_member(user_id)
            if member is None:
                failed.add(user_id)
                self._log(f"Role DM failed (member left) | user_id={user_id}")
                continue
            try:
                embed = self._role_dm_embed(role, game)
                await member.send(embed=embed)
                self._log(f"Role DM sent | user_id={user_id} | role={role}")
            except discord.HTTPException:
                failed.add(user_id)
                self._log(f"Role DM failed | user_id={user_id} | name={member.display_name}")
        return failed

    async def _prepare_game_start(
        self,
        game: MafiaGame,
        guild: discord.Guild,
    ) -> Tuple[bool, str, Optional[Set[int]]]:
        if game.phase != "lobby":
            return False, "Game is not in lobby.", None
        if len(game.players) < game.settings["min_players"]:
            return (
                False,
                f"Need at least {game.settings['min_players']} players.",
                None,
            )

        game.roles = self._assign_roles(game)
        failed = await self._send_role_dms(game, guild)

        if failed:
            game.role_delivery_failures = failed
            return False, "Some players could not receive role DMs.", failed

        game.roles_assigned = True
        game.alive_players = set(game.players.keys())
        game.dead_players = set()
        game.mafia_kills = 0
        game.doctor_saves_remaining = game.settings["doctor_saves"]
        game.round = 0
        game.phase = "starting"
        return True, "Game prepared successfully.", None

    def _disable_lobby_view(self, game: MafiaGame):
        if game.lobby_message is None:
            return
        try:
            view = LobbyView(self, game)
            for child in view.children:
                child.disabled = True
            self.bot.loop.create_task(
                game.lobby_message.edit(
                    embed=discord.Embed(
                        title="🔪 MAFIA",
                        description="Game starting...",
                        color=discord.Color.red(),
                    ),
                    view=view,
                )
            )
        except discord.HTTPException:
            self._log(f"Failed to disable lobby view | guild_id={game.guild_id}")

    async def _run_game_loop(self, game: MafiaGame):
        current = asyncio.current_task()
        try:
            while not game.game_over:
                game.round += 1
                game.phase = "night"
                await self._night_phase(game)
                if game.game_over:
                    break

                game.phase = "day"
                await self._day_phase(game)
                if game.game_over:
                    break
        except asyncio.CancelledError:
            self._log(f"Game loop cancelled | guild_id={game.guild_id}")
            raise
        except Exception:
            self._log(f"Unhandled error in game loop:\n{traceback.format_exc()}")
            try:
                channel = self.bot.get_channel(game.channel_id)
                if channel:
                    await channel.send("⚠️ An unexpected error occurred. The game has been ended.")
            except discord.HTTPException:
                pass
            await self._end_game(game, "error")
        finally:
            if game.task is None or game.task is current:
                await self._end_game(game, game.winner or "error")

    async def _night_phase(self, game: MafiaGame):
        game.night_actions = {"mafia": None, "doctor": None, "detective": None}
        channel = self.bot.get_channel(game.channel_id)
        if channel is None:
            await self._end_game(game, "error")
            return

        embed = discord.Embed(
            title="🌙 NIGHT PHASE",
            description=(
                f"Night {game.round}. Submit your action privately.\n"
                f"You have {game.settings['night_duration']} seconds."
            ),
            color=discord.Color.dark_blue(),
        )
        game.phase_message = await channel.send(embed=embed)
        self._log(f"Night started | round={game.round} | guild_id={game.guild_id}")

        await self._send_night_action_dms(game)

        await asyncio.sleep(game.settings["night_duration"])
        if game.game_over:
            return
        await self._resolve_night(game)

    async def _send_night_action_dms(self, game: MafiaGame):
        game.night_view_messages = []
        guild = self.bot.get_guild(game.guild_id)
        if guild is None:
            return

        for uid in list(game.alive_players):
            role = game.roles.get(uid)
            if role == "Mafia":
                await self._send_night_action(game, guild, uid, "mafia")
            elif role == "Doctor" and game.doctor_saves_remaining > 0:
                await self._send_night_action(game, guild, uid, "doctor")
            elif role == "Detective":
                await self._send_night_action(game, guild, uid, "detective")

    async def _send_night_action(
        self,
        game: MafiaGame,
        guild: discord.Guild,
        user_id: int,
        action_type: str,
    ):
        member = guild.get_member(user_id)
        if member is None:
            return
        view = NightActionView(self, game, action_type, user_id)
        if not view.children:
            try:
                await member.send("No valid targets are available right now.")
            except discord.HTTPException:
                pass
            return

        action_names = {
            "mafia": "Choose a player to kill.",
            "doctor": "Choose a player to save.",
            "detective": "Choose a player to investigate.",
        }
        embed = discord.Embed(
            title="🌙 Night Action",
            description=action_names.get(action_type, "Choose a player."),
            color=discord.Color.dark_blue(),
        )
        try:
            message = await member.send(embed=embed, view=view)
            game.night_view_messages.append(message)
        except discord.HTTPException:
            self._log(
                f"Night action DM failed | user_id={user_id} | action={action_type}"
            )

    async def _handle_night_action_select(
        self,
        interaction: discord.Interaction,
        game: MafiaGame,
        action_type: str,
        user_id: int,
    ):
        if interaction.user.id != user_id:
            await interaction.response.send_message(
                "This action is not for you.", ephemeral=True
            )
            return
        if game.phase != "night":
            await interaction.response.send_message(
                "Night actions are no longer accepted.", ephemeral=True
            )
            return
        if user_id not in game.alive_players:
            await interaction.response.send_message(
                "You are not alive.", ephemeral=True
            )
            return

        select = interaction.data.get("values", [])
        if not select:
            await interaction.response.send_message("No target selected.", ephemeral=True)
            return

        try:
            target_id = int(select[0])
        except ValueError:
            await interaction.response.send_message("Invalid target.", ephemeral=True)
            return

        if target_id not in game.alive_players:
            await interaction.response.send_message("Invalid target.", ephemeral=True)
            return

        if action_type == "mafia":
            game.night_actions["mafia"] = target_id
            self._log(
                f"Mafia selected target | user_id={user_id} | target_id={target_id}"
            )
        elif action_type == "doctor":
            if game.doctor_saves_remaining <= 0:
                await interaction.response.send_message(
                    "You have no saves remaining.", ephemeral=True
                )
                return
            game.night_actions["doctor"] = target_id
            self._log(
                f"Doctor selected target | user_id={user_id} | target_id={target_id}"
            )
        elif action_type == "detective":
            game.night_actions["detective"] = target_id
            self._log(
                f"Detective investigated target | user_id={user_id} | target_id={target_id}"
            )
        else:
            await interaction.response.send_message("Unknown action.", ephemeral=True)
            return

        try:
            await interaction.response.edit_message(
                content=f"Action recorded: {game.players.get(target_id, target_id)}",
                view=None,
            )
        except discord.HTTPException:
            pass

    async def _resolve_night(self, game: MafiaGame):
        channel = self.bot.get_channel(game.channel_id)
        if channel is None:
            await self._end_game(game, "error")
            return

        mafia_target = game.night_actions.get("mafia")
        doctor_target = game.night_actions.get("doctor")
        detective_target = game.night_actions.get("detective")

        if detective_target:
            detective_id = next(
                (
                    uid
                    for uid in game.alive_players
                    if game.roles.get(uid) == "Detective"
                ),
                None,
            )
            if detective_id:
                await self._send_detective_result(game, detective_id, detective_target)

        saved = False
        killed_id: Optional[int] = None

        if mafia_target and mafia_target in game.alive_players:
            if doctor_target == mafia_target and game.doctor_saves_remaining > 0:
                game.doctor_saves_remaining -= 1
                saved = True
                self._log(
                    f"Player saved by Doctor | target_id={mafia_target} | "
                    f"saves_left={game.doctor_saves_remaining}"
                )
            else:
                killed_id = mafia_target

        if killed_id is not None:
            game.mafia_kills += 1
            self._kill_player(game, killed_id)
            self._log(
                f"Player killed by Mafia | target_id={killed_id} | "
                f"mafia_kills={game.mafia_kills}/{game.settings['mafia_kill_target']}"
            )

        embed = discord.Embed(
            title="☀️ MORNING",
            color=discord.Color.gold(),
        )
        if killed_id is not None:
            name = game.players.get(killed_id, str(killed_id))
            embed.add_field(
                name="Death",
                value=f"{name} was killed during the night.",
                inline=False,
            )
        elif saved:
            name = game.players.get(mafia_target, str(mafia_target))
            embed.add_field(
                name="Saved",
                value=f"The Doctor saved {name}.",
                inline=False,
            )
        else:
            embed.add_field(
                name="No Death",
                value="No one died during the night.",
                inline=False,
            )
        await channel.send(embed=embed)

        winner = self._check_win_condition(game)
        if winner:
            await self._end_game(game, winner)

    async def _send_detective_result(
        self,
        game: MafiaGame,
        detective_id: int,
        target_id: int,
    ):
        guild = self.bot.get_guild(game.guild_id)
        if guild is None:
            return
        detective = guild.get_member(detective_id)
        if detective is None:
            return

        target_name = game.players.get(target_id, str(target_id))
        is_mafia = game.roles.get(target_id) == "Mafia"
        result_text = (
            f"{target_name} is Mafia." if is_mafia else f"{target_name} is NOT Mafia."
        )
        embed = discord.Embed(
            title="🕵️ Investigation Result",
            description=result_text,
            color=discord.Color.blue(),
        )
        try:
            await detective.send(embed=embed)
        except discord.HTTPException:
            self._log(
                f"Detective result DM failed | detective_id={detective_id} | "
                f"target_id={target_id}"
            )

    async def _day_phase(self, game: MafiaGame):
        game.votes = {}
        channel = self.bot.get_channel(game.channel_id)
        if channel is None:
            await self._end_game(game, "error")
            return

        embed = discord.Embed(
            title="☀️ DAY PHASE",
            description=(
                f"Discuss who you think is Mafia.\n"
                f"You have {game.settings['day_duration']} seconds to discuss."
            ),
            color=discord.Color.gold(),
        )
        game.phase_message = await channel.send(embed=embed)
        self._log(f"Day started | round={game.round} | guild_id={game.guild_id}")

        await asyncio.sleep(game.settings["day_duration"])
        if game.game_over:
            return
        await self._open_voting(game)

    async def _open_voting(self, game: MafiaGame):
        game.phase = "voting"
        channel = self.bot.get_channel(game.channel_id)
        if channel is None:
            await self._end_game(game, "error")
            return

        embed = discord.Embed(
            title="🗳️ VOTING",
            description=(
                f"Vote for who you think is Mafia.\n"
                f"You have {game.settings['vote_duration']} seconds to vote."
            ),
            color=discord.Color.purple(),
        )
        view = VoteView(self, game)
        message = await channel.send(embed=embed, view=view)
        game.vote_view_message = message
        view.message = message
        self._log(f"Voting opened | guild_id={game.guild_id}")

        await asyncio.sleep(game.settings["vote_duration"])
        if game.game_over:
            return
        await self._resolve_votes(game)

    async def _handle_vote_select(self, interaction: discord.Interaction, game: MafiaGame):
        if interaction.guild is None or interaction.guild.id != game.guild_id:
            await interaction.response.send_message("Invalid interaction.", ephemeral=True)
            return
        voter_id = interaction.user.id
        if voter_id not in game.alive_players:
            await interaction.response.send_message(
                "Only living players can vote.", ephemeral=True
            )
            return
        if voter_id in game.votes:
            await interaction.response.send_message(
                "You have already voted.", ephemeral=True
            )
            return

        select = interaction.data.get("values", [])
        if not select:
            await interaction.response.send_message("No vote selected.", ephemeral=True)
            return

        try:
            target_id = int(select[0])
        except ValueError:
            await interaction.response.send_message("Invalid vote.", ephemeral=True)
            return

        if target_id not in game.alive_players:
            await interaction.response.send_message("Invalid vote target.", ephemeral=True)
            return
        if target_id == voter_id:
            await interaction.response.send_message(
                "You cannot vote for yourself.", ephemeral=True
            )
            return

        game.votes[voter_id] = target_id
        self._log(
            f"Vote submitted | voter_id={voter_id} | target_id={target_id}"
        )
        await interaction.response.send_message(
            f"Vote recorded: {game.players.get(target_id, target_id)}",
            ephemeral=True,
        )

    async def _resolve_votes(self, game: MafiaGame):
        channel = self.bot.get_channel(game.channel_id)
        if channel is None:
            await self._end_game(game, "error")
            return

        if game.vote_view_message is not None:
            try:
                await game.vote_view_message.edit(view=None)
            except discord.HTTPException:
                pass

        if not game.votes:
            await channel.send("No votes were cast. No one is eliminated.")
        else:
            counts: Dict[int, int] = {}
            for target_id in game.votes.values():
                counts[target_id] = counts.get(target_id, 0) + 1

            max_votes = max(counts.values())
            top = [pid for pid, count in counts.items() if count == max_votes]

            if len(top) > 1:
                await channel.send("Tie vote! Nobody is eliminated.")
            else:
                eliminated_id = top[0]
                if eliminated_id in game.alive_players:
                    self._kill_player(game, eliminated_id)
                    name = game.players.get(eliminated_id, str(eliminated_id))
                    await channel.send(f"☠️ {name} was eliminated by vote.")
                    self._log(f"Player eliminated by vote | target_id={eliminated_id}")

        winner = self._check_win_condition(game)
        if winner:
            await self._end_game(game, winner)

    def _kill_player(self, game: MafiaGame, user_id: int):
        if user_id in game.alive_players:
            game.alive_players.discard(user_id)
            game.dead_players.add(user_id)
            self._log(f"Player killed | user_id={user_id}")

    def _check_win_condition(self, game: MafiaGame) -> Optional[str]:
        mafia_alive = [
            uid for uid in game.alive_players if game.roles.get(uid) == "Mafia"
        ]
        if game.mafia_kills >= game.settings["mafia_kill_target"]:
            return "mafia"
        if not mafia_alive:
            return "civilians"
        return None

    async def _end_game(self, game: MafiaGame, winner: str):
        if game.game_over and game.winner is not None:
            return
        game.game_over = True
        game.phase = "game_over"
        game.winner = winner

        current = asyncio.current_task()
        if game.task and game.task is not current:
            game.task.cancel()

        await self._disable_game_views(game)

        channel = self.bot.get_channel(game.channel_id)
        if channel is not None:
            if winner == "mafia":
                embed = discord.Embed(
                    title="🏆 GAME OVER",
                    description="🔪 MAFIA WINS",
                    color=discord.Color.red(),
                )
                embed.add_field(
                    name="Successful kills",
                    value=str(game.mafia_kills),
                )
            elif winner == "civilians":
                embed = discord.Embed(
                    title="🏆 GAME OVER",
                    description="👥 CIVILIANS WIN",
                    color=discord.Color.green(),
                )
                embed.add_field(
                    name="Cause",
                    value="All Mafia members have been eliminated.",
                )
            elif winner == "stopped":
                embed = discord.Embed(
                    title="🛑 GAME STOPPED",
                    description="The game was stopped by staff.",
                    color=discord.Color.orange(),
                )
            else:
                embed = discord.Embed(
                    title="⚠️ GAME ENDED",
                    description="The game has ended due to an error.",
                    color=discord.Color.orange(),
                )
            await channel.send(embed=embed)

        self.games.pop(game.guild_id, None)
        self._log(f"Game ended | guild_id={game.guild_id} | winner={winner}")

    async def _disable_game_views(self, game: MafiaGame):
        if game.lobby_message is not None:
            try:
                view = LobbyView(self, game)
                for child in view.children:
                    child.disabled = True
                await game.lobby_message.edit(view=view)
            except discord.HTTPException:
                pass

        for message in game.night_view_messages:
            try:
                await message.edit(view=None)
            except discord.HTTPException:
                pass

        if game.vote_view_message is not None:
            try:
                await game.vote_view_message.edit(view=None)
            except discord.HTTPException:
                pass

    @commands.group(name="mafia", invoke_without_command=True)
    @commands.check(_allowed_channel)
    async def mafia(self, ctx: commands.Context):
        """Mafia game group command."""
        await self._status(ctx)

    @mafia.command(name="start")
    @commands.check(_is_staff)
    async def mafia_start(self, ctx: commands.Context):
        """Create a Mafia lobby."""
        game = self.games.get(ctx.guild.id)
        if game is not None and not game.game_over:
            if game.phase == "lobby":
                await ctx.send("A lobby already exists.")
                return
            await ctx.send("A game is already running.")
            return

        settings = self.default_settings.copy()
        game = MafiaGame(
            guild_id=ctx.guild.id,
            channel_id=self.allowed_channel_id,
            settings=settings,
            started_by=ctx.author.id,
        )
        self.games[ctx.guild.id] = game

        view = LobbyView(self, game)
        game.lobby_message = await ctx.channel.send(
            embed=self._lobby_embed(game),
            view=view,
        )
        self._log(
            f"Game created | guild_id={ctx.guild.id} | channel_id={ctx.channel.id}"
        )
        await ctx.send("Mafia lobby created.", delete_after=5)

    @mafia.command(name="join")
    async def mafia_join(self, ctx: commands.Context):
        """Join the current lobby."""
        game = self.games.get(ctx.guild.id)
        if game is None or game.phase != "lobby":
            await ctx.send("No lobby is currently open.")
            return

        ok, message = self._try_add_player(game, ctx.author)
        if not ok:
            await ctx.send(message)
            return
        await self._update_lobby_message(game)
        await ctx.send(message, delete_after=5)

    @mafia.command(name="leave")
    async def mafia_leave(self, ctx: commands.Context):
        """Leave the current lobby."""
        game = self.games.get(ctx.guild.id)
        if game is None:
            await ctx.send("No active Mafia game.")
            return

        ok, message = self._try_remove_player(game, ctx.author, force=False)
        if not ok:
            await ctx.send(message)
            return
        await self._update_lobby_message(game)
        await ctx.send(message, delete_after=5)

    @mafia.command(name="startgame")
    @commands.check(_is_staff)
    async def mafia_startgame(self, ctx: commands.Context):
        """Assign roles, send DMs, and start the game."""
        game = self.games.get(ctx.guild.id)
        if game is None:
            await ctx.send("No lobby exists. Use `$mafia start` first.")
            return
        if game.phase != "lobby":
            await ctx.send("Game has already started or is not in lobby.")
            return

        ok, message, failed = await self._prepare_game_start(game, ctx.guild)
        if not ok:
            if failed:
                for uid in failed:
                    member = ctx.guild.get_member(uid)
                    if member:
                        await ctx.send(
                            f"⚠️ {member.mention}, I couldn't send your Mafia role through DM. "
                            "Please enable DMs from server members and try again.",
                            delete_after=60,
                        )
            await ctx.send(f"Game start aborted: {message}")
            return

        self._disable_lobby_view(game)
        game.task = self.bot.loop.create_task(self._run_game_loop(game))
        await ctx.send("Game starting! Roles have been sent.")
        self._log(f"Game started | guild_id={ctx.guild.id} | started_by={ctx.author.id}")

    @mafia.command(name="stop")
    @commands.check(_is_staff)
    async def mafia_stop(self, ctx: commands.Context):
        """Force-stop the current game."""
        game = self.games.get(ctx.guild.id)
        if game is None:
            await ctx.send("No active Mafia game.")
            return

        self._log(
            f"Game forcefully stopped by STAFF | user_id={ctx.author.id} | "
            f"guild_id={ctx.guild.id}"
        )
        await self._end_game(game, "stopped")

    @mafia.command(name="status")
    async def mafia_status(self, ctx: commands.Context):
        """Show current game status."""
        await self._status(ctx)

    async def _status(self, ctx: commands.Context):
        game = self.games.get(ctx.guild.id)
        if game is None:
            await ctx.send("No active Mafia game.")
            return

        embed = discord.Embed(
            title="MAFIA STATUS",
            color=discord.Color.red(),
        )
        embed.add_field(name="Phase", value=game.phase, inline=True)
        embed.add_field(
            name="Players",
            value=f"{len(game.players)}/{game.settings['max_players']}",
            inline=True,
        )
        if game.phase != "lobby":
            embed.add_field(name="Alive", value=str(len(game.alive_players)), inline=True)
            embed.add_field(name="Dead", value=str(len(game.dead_players)), inline=True)
            embed.add_field(name="Round", value=str(game.round), inline=True)
            embed.add_field(
                name="Mafia Kills",
                value=f"{game.mafia_kills}/{game.settings['mafia_kill_target']}",
                inline=True,
            )
            embed.add_field(
                name="Doctor Saves Left",
                value=str(game.doctor_saves_remaining),
                inline=True,
            )
        if game.role_delivery_failures:
            names = [
                game.players.get(uid, str(uid))
                for uid in game.role_delivery_failures
            ]
            embed.add_field(
                name="Role Delivery Failures",
                value=", ".join(names) if names else "None",
                inline=False,
            )
        await ctx.send(embed=embed)

    @mafia.command(name="remove")
    @commands.check(_is_staff)
    async def mafia_remove(self, ctx: commands.Context, member: discord.Member):
        """Remove a player from the current game (staff only)."""
        game = self.games.get(ctx.guild.id)
        if game is None:
            await ctx.send("No active Mafia game.")
            return

        if member.id not in game.players:
            await ctx.send("That user is not in the game.")
            return

        ok, message = self._try_remove_player(game, member, force=True)
        if not ok:
            await ctx.send(message)
            return

        if game.phase == "lobby":
            await self._update_lobby_message(game)
        else:
            winner = self._check_win_condition(game)
            if winner:
                await self._end_game(game, winner)
                return

        await ctx.send(message)
        self._log(
            f"Staff removed player | staff_id={ctx.author.id} | "
            f"target_id={member.id} | guild_id={ctx.guild.id}"
        )

    @mafia.command(name="settings")
    @commands.check(_is_staff)
    async def mafia_settings(self, ctx: commands.Context):
        """Show current configurable settings."""
        embed = discord.Embed(title="MAFIA SETTINGS", color=discord.Color.blurple())
        for key, value in self.default_settings.items():
            embed.add_field(name=key, value=str(value), inline=True)
        await ctx.send(embed=embed)

    @mafia.command(name="set")
    @commands.check(_is_staff)
    async def mafia_set(self, ctx: commands.Context, key: str, value: int):
        """Change a setting. Key examples: min_players, max_players, night_duration."""
        key = key.lower()
        valid_keys = set(self.default_settings.keys())
        if key not in valid_keys:
            await ctx.send(
                f"Invalid setting. Valid keys: {', '.join(sorted(valid_keys))}"
            )
            return
        if value < 1:
            await ctx.send("Value must be at least 1.")
            return
        if key == "max_players" and value > 25:
            await ctx.send("max_players cannot exceed 25.")
            return
        if key == "min_players" and value > self.default_settings.get("max_players", 16):
            await ctx.send("min_players cannot exceed max_players.")
            return

        self.default_settings[key] = value

        game = self.games.get(ctx.guild.id)
        if game and game.phase == "lobby":
            game.settings[key] = value
            await self._update_lobby_message(game)

        await ctx.send(f"Setting `{key}` set to `{value}`.")
        self._log(f"Setting changed | staff_id={ctx.author.id} | {key}={value}")

    async def _handle_join_button(self, interaction: discord.Interaction, game: MafiaGame):
        if interaction.guild is None or interaction.guild.id != game.guild_id:
            await interaction.response.send_message("Invalid interaction.", ephemeral=True)
            return
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message("I cannot find you.", ephemeral=True)
            return
        if game.phase != "lobby":
            await interaction.response.send_message(
                "The game has already started.", ephemeral=True
            )
            return

        # Defer first to avoid interaction expiration
        await interaction.response.defer(ephemeral=True)

        ok, message = self._try_add_player(game, member)
        if not ok:
            await interaction.followup.send(message, ephemeral=True)
            return
        await self._update_lobby_message(game)
        await interaction.followup.send(message, ephemeral=True)

    async def _handle_leave_button(self, interaction: discord.Interaction, game: MafiaGame):
        if interaction.guild is None or interaction.guild.id != game.guild_id:
            await interaction.response.send_message("Invalid interaction.", ephemeral=True)
            return
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message("I cannot find you.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        ok, message = self._try_remove_player(game, member, force=False)
        if not ok:
            await interaction.followup.send(message, ephemeral=True)
            return
        await self._update_lobby_message(game)
        await interaction.followup.send(message, ephemeral=True)

    async def _handle_start_button(self, interaction: discord.Interaction, game: MafiaGame):
        if interaction.guild is None or interaction.guild.id != game.guild_id:
            await interaction.response.send_message("Invalid interaction.", ephemeral=True)
            return
        if not self._is_staff_interaction(interaction):
            await interaction.response.send_message(
                "Only staff can start the game.", ephemeral=True
            )
            return
        if game.phase != "lobby":
            await interaction.response.send_message(
                "The game has already started.", ephemeral=True
            )
            return

        await interaction.response.defer()

        ok, message, failed = await self._prepare_game_start(game, interaction.guild)
        if not ok:
            if failed:
                for uid in failed:
                    member = interaction.guild.get_member(uid)
                    if member:
                        await interaction.followup.send(
                            f"⚠️ {member.mention}, I couldn't send your Mafia role through DM. "
                            "Please enable DMs from server members and try again.",
                            ephemeral=True,
                        )
            await interaction.followup.send(
                f"Game start aborted: {message}",
                ephemeral=True,
            )
            return

        view = LobbyView(self, game)
        for child in view.children:
            child.disabled = True

        await interaction.edit_original_response(
            embed=discord.Embed(
                title="🔪 MAFIA",
                description="Game starting...",
                color=discord.Color.red(),
            ),
            view=view,
        )

        game.task = self.bot.loop.create_task(self._run_game_loop(game))
        self._log(
            f"Game started via button | guild_id={game.guild_id} | "
            f"started_by={interaction.user.id}"
        )

    async def _handle_cancel_button(self, interaction: discord.Interaction, game: MafiaGame):
        if interaction.guild is None or interaction.guild.id != game.guild_id:
            await interaction.response.send_message("Invalid interaction.", ephemeral=True)
            return
        if not self._is_staff_interaction(interaction):
            await interaction.response.send_message(
                "Only staff can cancel the game.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        self._log(
            f"Game cancelled via button | guild_id={game.guild_id} | "
            f"user_id={interaction.user.id}"
        )
        await self._end_game(game, "stopped")
        await interaction.followup.send("Game cancelled.", ephemeral=True)

    async def cog_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CheckFailure):
            if ctx.channel.id != self.allowed_channel_id:
                await ctx.send(
                    f"Mafia can only be started in <#{self.allowed_channel_id}>."
                )
            else:
                await ctx.send("You do not have permission to use this command.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Missing required argument.")
        else:
            self._log(f"Command error: {type(error).__name__}: {error}")
            traceback.print_exc()
            await ctx.send("An unexpected error occurred. Please try again later.")

    def cog_unload(self):
        for game in self.games.values():
            if game.task:
                game.task.cancel()


async def setup(bot: commands.Bot):
    await bot.add_cog(MafiaCog(bot))