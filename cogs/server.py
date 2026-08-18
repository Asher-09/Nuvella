import discord
from discord.ext import commands

# ─── Ice‑theme colours & breakers ───
MAIN_COLOR  = 0x3498db
WIN_COLOR   = 0x57F287
LOSS_COLOR  = 0xED4245
BREAKER     = "<:breaker:1537913026643238932>"
BREAKER_LINE = BREAKER * 10

# ─── Emojis ──────────────────────────
EMOJI_SUCCESS = "<a:tick:1537915747454554222>"
EMOJI_WRONG   = "<:cross:1537915734087303199>"
EMOJI_UP      = "<:chart:1537919996984959046>"
EMOJI_LOCK    = "<:lock:1537916486532730890>"

# ─── Embed builders ─────────────────
def make_embed(ctx, title, desc, color, success=False):
    user = ctx.author
    guild = ctx.guild
    embed = discord.Embed(
        title=title,
        description=f"{desc}\n{BREAKER_LINE}" if desc else None,
        color=color if not success else WIN_COLOR,
        timestamp=discord.utils.utcnow()
    )
    if guild:
        embed.set_author(
            name=guild.name,
            icon_url=guild.icon.url if guild.icon else None
        )
    embed.set_footer(
        text=f"Requested by {user}",
        icon_url=user.display_avatar.url
    )
    return embed

def error_embed(ctx, msg):
    return make_embed(ctx, f"{EMOJI_WRONG} Error", msg, LOSS_COLOR)

# ─── Cog ────────────────────────────
class Server(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── members ─────────────────────
    @commands.command()
    async def members(self, ctx):
        total = ctx.guild.member_count
        humans = sum(1 for m in ctx.guild.members if not m.bot)
        bots = total - humans
        embed = make_embed(ctx, f"{EMOJI_SUCCESS} Members",
                           f"Total: {total}\nHumans: {humans}\nBots: {bots}",
                           WIN_COLOR)
        await ctx.send(embed=embed)

    # ── boosts ──────────────────────
    @commands.command()
    async def boosts(self, ctx):
        level = ctx.guild.premium_tier
        count = ctx.guild.premium_subscription_count
        embed = make_embed(ctx, f"{EMOJI_UP} Server Boosts",
                           f"Level {level} ({count} boosts)",
                           0xf1c40f)
        await ctx.send(embed=embed)

    # ── rolesnumber ─────────────────
    @commands.command()
    async def rolesnumber(self, ctx):
        count = len(ctx.guild.roles)
        embed = make_embed(ctx, f"{EMOJI_SUCCESS} Roles",
                           f"This server has **{count}** roles.",
                           MAIN_COLOR)
        await ctx.send(embed=embed)

    # ── channels info ───────────────
    @commands.command()
    async def channels(self, ctx):
        text = len(ctx.guild.text_channels)
        voice = len(ctx.guild.voice_channels)
        embed = make_embed(ctx, f"{EMOJI_LOCK} Channels",
                           f"Text: {text}\nVoice: {voice}",
                           0x2c3e50)
        await ctx.send(embed=embed)

    # ── vcs count ───────────────────
    @commands.command()
    async def vcs(self, ctx):
        count = len(ctx.guild.voice_channels)
        embed = make_embed(ctx, f"{EMOJI_UP} Voice Channels",
                           str(count),
                           MAIN_COLOR)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Server(bot))