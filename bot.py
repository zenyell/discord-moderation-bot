import os
import sys
from pathlib import Path

# Load .env from the same directory as this file, regardless of cwd
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

import discord
from discord import app_commands
from discord.ext import commands
from datetime import timedelta
from collections import defaultdict, deque
import time
import database as db
import heartbeat

TOKEN        = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID     = int(os.getenv("GUILD_ID", 0)) if os.getenv("GUILD_ID") else None
AUTO_ROLE_ID = int(os.getenv("AUTO_ROLE_ID", 0)) if os.getenv("AUTO_ROLE_ID") else None
BAD_WORDS    = [w.strip().lower() for w in os.getenv("BAD_WORDS", "").split(",") if w.strip()]

print(f"[Bot] TOKEN present: {bool(TOKEN)}  GUILD_ID: {GUILD_ID}", flush=True)

# Spam defaults — overridden live from DB each message
DEFAULT_SPAM_LIMIT   = int(os.getenv("SPAM_MESSAGE_LIMIT", 6))
DEFAULT_SPAM_WINDOW  = int(os.getenv("SPAM_WINDOW_SECONDS", 8))
DEFAULT_SPAM_TIMEOUT = int(os.getenv("SPAM_TIMEOUT_MINUTES", 5))

intents = discord.Intents.default()
intents.members         = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
TEST_GUILD = discord.Object(id=GUILD_ID) if GUILD_ID else None

_spam: dict[int, deque] = defaultdict(lambda: deque())

ARROW = "\u2192"
DASH  = "\u2014"

# Badges mapping: Discord flag -> display label
BADGE_FLAGS = {
    discord.PublicUserFlags.staff:                    "<:discord_staff:> Discord Staff",
    discord.PublicUserFlags.partner:                  "<:partner:> Partner",
    discord.PublicUserFlags.hypesquad:                "\U0001f3e0 HypeSquad Events",
    discord.PublicUserFlags.bug_hunter:               "\U0001f41b Bug Hunter",
    discord.PublicUserFlags.hypesquad_bravery:        "\U0001f7e3 Bravery",
    discord.PublicUserFlags.hypesquad_brilliance:     "\U0001f534 Brilliance",
    discord.PublicUserFlags.hypesquad_balance:        "\U0001f7e2 Balance",
    discord.PublicUserFlags.early_supporter:          "\U0001f47e Early Supporter",
    discord.PublicUserFlags.bug_hunter_level_2:       "\U0001f41b Bug Hunter Lv2",
    discord.PublicUserFlags.verified_bot_developer:   "\U0001f6e0\ufe0f Verified Dev",
    discord.PublicUserFlags.active_developer:         "\u2699\ufe0f Active Dev",
}


def _get_guild_id(interaction):
    return str(interaction.guild_id)


def _is_mod(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.moderate_members


async def _spam_settings():
    """Read spam settings live from DB so dashboard changes apply instantly."""
    try:
        limit   = int(await db.get_setting("spam_limit")   or DEFAULT_SPAM_LIMIT)
        window  = int(await db.get_setting("spam_window")  or DEFAULT_SPAM_WINDOW)
        timeout = int(await db.get_setting("spam_timeout") or DEFAULT_SPAM_TIMEOUT)
    except Exception:
        limit, window, timeout = DEFAULT_SPAM_LIMIT, DEFAULT_SPAM_WINDOW, DEFAULT_SPAM_TIMEOUT
    return limit, window, timeout


async def _send_log(guild: discord.Guild, message: str):
    """Send a message to the configured log channel if set and enabled."""
    try:
        ch_id = await db.get_setting("log_channel_id")
        if not ch_id:
            return
        channel = guild.get_channel(int(ch_id))
        if channel:
            await channel.send(message)
    except Exception:
        pass


async def _check_command(interaction: discord.Interaction, command_name: str) -> bool:
    """
    Enforce the per-command policy stored in the database.
    Priority: disabled > user blacklist > mod blacklist > whitelist (users then roles) > mod perm.
    Admins bypass whitelist but not blacklist.
    """
    guild_id = _get_guild_id(interaction)
    cs = await db.get_command_setting(guild_id, command_name)

    if not cs["enabled"]:
        await interaction.response.send_message(
            f"\u274c The `/{command_name}` command is currently **disabled**.", ephemeral=True
        )
        return False

    user_id    = str(interaction.user.id)
    user_roles = [str(r.id) for r in interaction.user.roles]
    is_admin   = interaction.user.guild_permissions.administrator

    # ---------- blacklists (even admins are blocked) ----------
    bl_users = [x.strip() for x in cs["blacklist_users"].split(",") if x.strip()]
    if user_id in bl_users:
        await interaction.response.send_message(
            f"\u274c You have been **blacklisted** from using `/{command_name}`.", ephemeral=True
        )
        return False

    bl_mods = [x.strip() for x in cs["blacklist_mods"].split(",") if x.strip()]
    if user_id in bl_mods:
        await interaction.response.send_message(
            f"\u274c You are **not allowed** to use `/{command_name}`.", ephemeral=True
        )
        return False

    # ---------- whitelist (admins bypass) ----------
    wl_users = [x.strip() for x in cs["whitelist_users"].split(",") if x.strip()]
    wl_roles = [x.strip() for x in cs["whitelist_roles"].split(",") if x.strip()]

    if wl_users or wl_roles:
        allowed = (user_id in wl_users) or any(r in user_roles for r in wl_roles) or is_admin
        if not allowed:
            await interaction.response.send_message(
                f"\u274c You need a **whitelisted role or user ID** to use `/{command_name}`.", ephemeral=True
            )
            return False

    # ---------- basic mod permission ----------
    if not _is_mod(interaction):
        await interaction.response.send_message(
            "\u274c You need **Moderate Members** permission.", ephemeral=True
        )
        return False

    return True


async def _get_bad_words(guild_id):
    return list(set(BAD_WORDS + await db.get_blacklisted_words_list(guild_id)))


@bot.event
async def on_ready():
    await db.init_db()
    try:
        synced = await bot.tree.sync(guild=TEST_GUILD)
    except Exception:
        synced = await bot.tree.sync()
    print(f"[Bot] Logged in as {bot.user} | Synced {len(synced)} commands")
    heartbeat.start(bot)


@bot.event
async def on_member_join(member: discord.Member):
    guild_id = str(member.guild.id)
    if await db.is_user_blacklisted(guild_id, str(member.id)):
        await member.ban(reason="User is blacklisted")
        await db.log_action(guild_id, "auto-ban", str(member.id), str(bot.user.id), "Blacklisted user rejoined")
        return

    # Auto-roles from DB (dashboard-configured) take priority over env var
    autoroles = await db.get_autoroles(guild_id)
    for row in autoroles:
        role = member.guild.get_role(int(row["role_id"]))
        if role:
            try:
                await member.add_roles(role, reason="Auto-role on join")
            except Exception:
                pass

    # Fallback to env-var AUTO_ROLE_ID if no DB autoroles configured
    if not autoroles:
        role_id = await db.get_setting("auto_role_id") or (str(AUTO_ROLE_ID) if AUTO_ROLE_ID else None)
        if role_id:
            role = member.guild.get_role(int(role_id))
            if role:
                await member.add_roles(role, reason="Auto-role on join")

    # Log join event if enabled
    if await db.get_setting("log_join", "1") == "1":
        await _send_log(member.guild, f"\U0001f7e2 **{member}** joined the server.")


@bot.event
async def on_member_remove(member: discord.Member):
    if await db.get_setting("log_leave", "1") == "1":
        await _send_log(member.guild, f"\U0001f534 **{member}** left the server.")


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or not before.guild:
        return
    if before.content == after.content:
        return
    if await db.get_setting("log_edit", "1") == "1":
        await _send_log(
            before.guild,
            f"\u270f\ufe0f **{before.author}** edited a message in {before.channel.mention}\n"
            f"**Before:** {before.content[:200]}\n"
            f"**After:** {after.content[:200]}"
        )


@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    if await db.get_setting("log_delete", "1") == "1":
        await _send_log(
            message.guild,
            f"\U0001f5d1\ufe0f **{message.author}** deleted a message in {message.channel.mention}\n"
            f"**Content:** {message.content[:300]}"
        )


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    guild_id    = str(message.guild.id)
    content_low = message.content.lower()

    # Word filter
    bad_words = await _get_bad_words(guild_id)
    if any(w in content_low for w in bad_words):
        await message.delete()
        await message.channel.send(
            f"{message.author.mention} That word is not allowed!", delete_after=5
        )
        await db.log_action(guild_id, "filter", str(message.author.id), str(bot.user.id), "Blacklisted word")
        return

    # Trigger responses (from DB / dashboard)
    triggers = await db.get_all_triggers(guild_id)
    for t in triggers:
        if t["phrase"] in content_low:
            await message.channel.send(t["response"])
            break

    # Spam detection — reads limits live from DB
    spam_limit, spam_window, spam_timeout = await _spam_settings()
    uid = message.author.id
    now = time.monotonic()
    q   = _spam[uid]
    q.append(now)
    while q and now - q[0] > spam_window:
        q.popleft()
    if len(q) >= spam_limit:
        q.clear()
        try:
            await message.author.timeout(timedelta(minutes=spam_timeout), reason="Spam")
            await message.channel.send(
                f"{message.author.mention} Spam detected {DASH} timed out for {spam_timeout}m.",
                delete_after=10
            )
            await db.log_action(guild_id, "timeout", str(message.author.id), str(bot.user.id), "Spam")
        except discord.Forbidden:
            pass

    await bot.process_commands(message)


tree = bot.tree


# ─────────────────────────────── moderation commands ─────────────────────────

@tree.command(name="kick", description="Kick a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to kick", reason="Reason")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not await _check_command(interaction, "kick"): return
    await member.kick(reason=reason)
    await db.log_action(_get_guild_id(interaction), "kick", str(member.id), str(interaction.user.id), reason)
    if await db.get_setting("log_kick", "1") == "1":
        await _send_log(interaction.guild, f"\U0001f462 **{member}** was kicked by {interaction.user.mention}. Reason: {reason}")
    await interaction.response.send_message(f"\U0001f462 **{member}** was kicked. Reason: {reason}")


@tree.command(name="ban", description="Ban a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to ban", reason="Reason")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not await _check_command(interaction, "ban"): return
    await member.ban(reason=reason)
    await db.log_action(_get_guild_id(interaction), "ban", str(member.id), str(interaction.user.id), reason)
    if await db.get_setting("log_ban", "1") == "1":
        await _send_log(interaction.guild, f"\U0001f528 **{member}** was banned by {interaction.user.mention}. Reason: {reason}")
    await interaction.response.send_message(f"\U0001f528 **{member}** was banned. Reason: {reason}")


@tree.command(name="unban", description="Unban a user by ID", guild=TEST_GUILD)
@app_commands.describe(user_id="User ID to unban", reason="Reason")
async def unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    if not await _check_command(interaction, "unban"): return
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        await db.log_action(_get_guild_id(interaction), "unban", user_id, str(interaction.user.id), reason)
        if await db.get_setting("log_unban", "1") == "1":
            await _send_log(interaction.guild, f"\u2705 **{user}** was unbanned by {interaction.user.mention}. Reason: {reason}")
        await interaction.response.send_message(f"\u2705 **{user}** was unbanned.")
    except Exception as e:
        await interaction.response.send_message(f"\u274c Could not unban: {e}", ephemeral=True)


@tree.command(name="timeout", description="Timeout a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to timeout", minutes="Duration in minutes", reason="Reason")
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = "No reason provided"):
    if not await _check_command(interaction, "timeout"): return
    await member.timeout(timedelta(minutes=minutes), reason=reason)
    await db.log_action(_get_guild_id(interaction), "timeout", str(member.id), str(interaction.user.id), reason)
    if await db.get_setting("log_timeout", "1") == "1":
        await _send_log(interaction.guild, f"\u23f1\ufe0f **{member}** timed out for {minutes}m by {interaction.user.mention}. Reason: {reason}")
    await interaction.response.send_message(f"\u23f1\ufe0f **{member}** timed out for {minutes}m. Reason: {reason}")


@tree.command(name="purge", description="Delete messages in bulk", guild=TEST_GUILD)
@app_commands.describe(amount="Number of messages to delete (1-100)")
async def purge(interaction: discord.Interaction, amount: int):
    if not await _check_command(interaction, "purge"): return
    amount = max(1, min(amount, 100))
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await db.log_action(_get_guild_id(interaction), "purge", str(interaction.channel.id), str(interaction.user.id), f"Deleted {len(deleted)}")
    if await db.get_setting("log_purge", "1") == "1":
        await _send_log(interaction.guild, f"\U0001f5d1\ufe0f **{interaction.user}** purged {len(deleted)} messages in {interaction.channel.mention}.")
    await interaction.followup.send(f"\U0001f5d1\ufe0f Deleted {len(deleted)} messages.", ephemeral=True)


@tree.command(name="warn", description="Warn a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to warn", reason="Reason")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not await _check_command(interaction, "warn"): return
    await db.add_warning(_get_guild_id(interaction), str(member.id), str(interaction.user.id), reason)
    await db.log_action(_get_guild_id(interaction), "warn", str(member.id), str(interaction.user.id), reason)
    if await db.get_setting("log_warn", "1") == "1":
        await _send_log(interaction.guild, f"\u26a0\ufe0f **{member}** was warned by {interaction.user.mention}. Reason: {reason}")
    await interaction.response.send_message(f"\u26a0\ufe0f **{member}** has been warned. Reason: {reason}")


@tree.command(name="warnings", description="View warnings for a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to check")
async def warnings_cmd(interaction: discord.Interaction, member: discord.Member):
    if not await _check_command(interaction, "warnings"): return
    rows = await db.get_warnings(_get_guild_id(interaction), str(member.id))
    if not rows:
        await interaction.response.send_message(f"\u2705 **{member}** has no warnings.")
        return
    lines = [
        f"`{i+1}.` {r['reason'] or 'No reason'} {DASH} {r['created_at'][:19]}"
        for i, r in enumerate(rows)
    ]
    await interaction.response.send_message(
        f"\u26a0\ufe0f **{member}** has **{len(rows)}** warning(s):\n" + "\n".join(lines)
    )


@tree.command(name="modlogs", description="View recent mod actions", guild=TEST_GUILD)
@app_commands.describe(limit="How many entries (default 10)")
async def modlogs(interaction: discord.Interaction, limit: int = 10):
    if not await _check_command(interaction, "modlogs"): return
    rows = await db.recent_logs(_get_guild_id(interaction), limit)
    if not rows:
        await interaction.response.send_message("No mod logs yet.")
        return
    lines = [
        f"`{r['action'].upper()}` {ARROW} <@{r['target_id']}> by <@{r['moderator_id']}> {DASH} {r['reason'] or DASH}"
        for r in rows
    ]
    await interaction.response.send_message("\U0001f4cb **Recent Mod Logs:**\n" + "\n".join(lines))


@tree.command(name="blacklist", description="Add a user to the blacklist (auto-ban on join)", guild=TEST_GUILD)
@app_commands.describe(member="Member to blacklist", reason="Reason")
async def blacklist_cmd(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not await _check_command(interaction, "blacklist"): return
    await db.add_blacklisted_user(_get_guild_id(interaction), str(member.id), reason, str(interaction.user.id))
    await db.log_action(_get_guild_id(interaction), "blacklist", str(member.id), str(interaction.user.id), reason)
    await interaction.response.send_message(f"\U0001f6ab **{member}** has been blacklisted. Reason: {reason}")


@tree.command(name="unblacklist", description="Remove a user from the blacklist", guild=TEST_GUILD)
@app_commands.describe(user_id="User ID to remove from blacklist")
async def unblacklist_cmd(interaction: discord.Interaction, user_id: str):
    if not await _check_command(interaction, "unblacklist"): return
    await db.remove_blacklisted_user(_get_guild_id(interaction), user_id)
    await db.log_action(_get_guild_id(interaction), "unblacklist", user_id, str(interaction.user.id))
    await interaction.response.send_message(f"\u2705 User `{user_id}` removed from blacklist.")


@tree.command(name="addword", description="Add a word to the word blacklist", guild=TEST_GUILD)
@app_commands.describe(word="Word to block")
async def addword(interaction: discord.Interaction, word: str):
    if not await _check_command(interaction, "addword"): return
    await db.add_blacklisted_word(_get_guild_id(interaction), word, str(interaction.user.id))
    await interaction.response.send_message(f"\u2705 Word `{word}` added to blacklist.", ephemeral=True)


@tree.command(name="removeword", description="Remove a word from the word blacklist", guild=TEST_GUILD)
@app_commands.describe(word="Word to unblock")
async def removeword(interaction: discord.Interaction, word: str):
    if not await _check_command(interaction, "removeword"): return
    await db.remove_blacklisted_word(_get_guild_id(interaction), word)
    await interaction.response.send_message(f"\u2705 Word `{word}` removed from blacklist.", ephemeral=True)


# ─────────────────────────────── user lookup ──────────────────────────────────

@tree.command(name="userinfo", description="Full Discord profile + mod history for a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to look up")
async def userinfo(interaction: discord.Interaction, member: discord.Member):
    if not await _check_command(interaction, "userinfo"): return

    guild_id    = _get_guild_id(interaction)
    warns       = await db.get_warnings(guild_id, str(member.id))
    logs        = await db.logs_for_user(guild_id, str(member.id))
    blacklisted = await db.is_user_blacklisted(guild_id, str(member.id))

    try:
        full_user = await bot.fetch_user(member.id)
        banner_url   = full_user.banner.url if full_user.banner else None
        accent_color = full_user.accent_color or member.color or discord.Color.blurple()
        badges = [label for flag, label in BADGE_FLAGS.items() if full_user.public_flags & flag]
    except Exception:
        full_user    = None
        banner_url   = None
        accent_color = member.color or discord.Color.blurple()
        badges       = []

    embed = discord.Embed(
        title=f"{member.display_name}",
        description=(
            f"**@{member.name}**"
            + (f"  \u2022  `{member.global_name}`" if getattr(member, 'global_name', None) and member.global_name != member.name else "")
        ),
        color=accent_color
    )

    embed.set_thumbnail(url=member.display_avatar.url)
    if banner_url:
        embed.set_image(url=banner_url)

    embed.add_field(name="User ID",     value=f"`{member.id}`",              inline=True)
    embed.add_field(name="Mention",     value=member.mention,                inline=True)
    embed.add_field(name="Bot Account", value="Yes" if member.bot else "No", inline=True)
    embed.add_field(name="Nickname",    value=member.nick or "None",         inline=True)
    embed.add_field(name="Global Name", value=getattr(member, 'global_name', None) or "None", inline=True)
    embed.add_field(name="\u200b",      value="\u200b",                      inline=True)

    created = discord.utils.format_dt(member.created_at, style="D")
    joined  = discord.utils.format_dt(member.joined_at, style="D") if member.joined_at else "Unknown"
    embed.add_field(name="Account Created", value=created, inline=True)
    embed.add_field(name="Joined Server",   value=joined,  inline=True)

    if member.timed_out_until:
        embed.add_field(name="Timed Out Until", value=discord.utils.format_dt(member.timed_out_until, style="R"), inline=True)
    else:
        embed.add_field(name="Timed Out", value="No", inline=True)

    if badges:
        embed.add_field(name="Badges", value="  ".join(badges), inline=False)

    role_list = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    if role_list:
        roles_str = "  ".join(role_list)
        if len(roles_str) > 1020:
            roles_str = roles_str[:1020] + "\u2026"
        embed.add_field(name=f"Roles [{len(role_list)}]", value=roles_str, inline=False)
    else:
        embed.add_field(name="Roles", value="None", inline=False)

    embed.add_field(name="Warnings",    value=str(len(warns)), inline=True)
    embed.add_field(name="Mod Actions", value=str(len(logs)),  inline=True)
    embed.add_field(name="Blacklisted", value="\U0001f6ab Yes" if blacklisted else "\u2705 No", inline=True)

    recent = logs[:6]
    if recent:
        action_icons = {
            "ban": "\U0001f528", "kick": "\U0001f462", "timeout": "\u23f1",
            "warn": "\u26a0\ufe0f", "unban": "\u2705", "purge": "\U0001f5d1",
            "filter": "\U0001f6ab", "blacklist": "\U0001f6ab", "auto-ban": "\U0001f528",
        }
        import datetime as _dt
        history = "\n".join(
            f"{action_icons.get(r['action'], chr(8226))} `{r['action'].upper()}` "
            f"\u2014 {r['reason'] or 'No reason'} \u2014 "
            f"<t:{int(_dt.datetime.fromisoformat(r['created_at']).timestamp())}:R>"
            for r in recent
        )
        embed.add_field(name="Recent Mod History", value=history, inline=False)

    embed.set_footer(text=f"Requested by {interaction.user}", icon_url=interaction.user.display_avatar.url)

    await db.cache_profile(
        str(member.id),
        str(member.name),
        getattr(member, 'global_name', None) or str(member.name),
        member.display_avatar.url,
        banner_url or "",
        str(accent_color),
        "",
        ", ".join(badges)
    )

    await interaction.response.send_message(embed=embed)


# ─────────────────────────────── run ──────────────────────────────────────────

if __name__ == "__main__":
    bot.run(TOKEN)
