import os
import sys
import datetime
from pathlib import Path

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

if not TOKEN:
    print("[Bot] FATAL: DISCORD_BOT_TOKEN is not set.", flush=True)
    sys.exit(1)
if not os.getenv("TURSO_URL") or not os.getenv("TURSO_TOKEN"):
    print("[Bot] WARNING: TURSO_URL or TURSO_TOKEN is missing.", flush=True)
if not GUILD_ID:
    print("[Bot] WARNING: GUILD_ID not set — global sync can take up to 1 hour.", flush=True)

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

# ─────────────────────── log embed helpers ────────────────────────────────────

# Maps action type → (color, emoji, label)
LOG_STYLES: dict[str, tuple[int, str, str]] = {
    "kick":        (0xF4A300, "\U0001f462", "Member Kicked"),
    "ban":         (0xED4245, "\U0001f528", "Member Banned"),
    "unban":       (0x57F287, "\u2705",     "Member Unbanned"),
    "softban":     (0xFEE75C, "\U0001f9fc", "Member Softbanned"),
    "timeout":     (0xEB459E, "\u23f1\ufe0f","Member Timed Out"),
    "untimeout":   (0x57F287, "\u23f0",     "Timeout Removed"),
    "warn":        (0xFEE75C, "\u26a0\ufe0f","Member Warned"),
    "clearwarnings":(0x57F287,"\U0001f9f9", "Warnings Cleared"),
    "purge":       (0x5865F2, "\U0001f5d1\ufe0f","Messages Purged"),
    "filter":      (0xED4245, "\U0001f6ab", "Word Filter Triggered"),
    "blacklist":   (0xED4245, "\U0001f6ab", "User Blacklisted"),
    "unblacklist": (0x57F287, "\u2705",     "User Unblacklisted"),
    "auto-ban":    (0xED4245, "\U0001f528", "Auto-Ban (Blacklist)"),
    "lock":        (0xF4A300, "\U0001f512", "Channel Locked"),
    "unlock":      (0x57F287, "\U0001f513", "Channel Unlocked"),
    "slowmode":    (0x5865F2, "\u23f1\ufe0f","Slowmode Changed"),
    "nick":        (0x5865F2, "\u270f\ufe0f","Nickname Changed"),
    "role-add":    (0x57F287, "\u2705",     "Role Added"),
    "role-remove": (0xED4245, "\u274c",     "Role Removed"),
    "join":        (0x57F287, "\U0001f7e2", "Member Joined"),
    "leave":       (0x747F8D, "\U0001f534", "Member Left"),
    "edit":        (0x5865F2, "\u270f\ufe0f","Message Edited"),
    "delete":      (0xF4A300, "\U0001f5d1\ufe0f","Message Deleted"),
    "spam":        (0xEB459E, "\U0001f4e8", "Spam Detected"),
    "announce":    (0x5865F2, "\U0001f4e3", "Announcement Sent"),
}


def _build_log_embed(
    action: str,
    *,
    target: discord.Member | discord.User | str | None = None,
    moderator: discord.Member | discord.User | None = None,
    reason: str | None = None,
    channel: discord.abc.GuildChannel | None = None,
    extra_fields: list[tuple[str, str, bool]] | None = None,
    thumbnail_url: str | None = None,
) -> discord.Embed:
    style = LOG_STYLES.get(action, (0x5865F2, "\U0001f4cb", action.title()))
    color, emoji, label = style

    embed = discord.Embed(
        title=f"{emoji}  {label}",
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )

    # Target field
    if target is not None:
        if isinstance(target, (discord.Member, discord.User)):
            val = f"{target.mention}\n`{target}` (ID: `{target.id}`)"
            if thumbnail_url is None:
                thumbnail_url = target.display_avatar.url
        else:
            val = str(target)
        embed.add_field(name="\U0001f465 Target", value=val, inline=True)

    # Moderator field
    if moderator is not None:
        embed.add_field(
            name="\U0001f6e1\ufe0f Moderator",
            value=f"{moderator.mention}\n`{moderator}` (ID: `{moderator.id}`)",
            inline=True,
        )

    # Channel field
    if channel is not None:
        embed.add_field(name="\U0001f4ac Channel", value=channel.mention, inline=True)

    # Reason field
    embed.add_field(
        name="\U0001f4dd Reason",
        value=reason or "No reason provided",
        inline=False,
    )

    # Optional extra fields
    if extra_fields:
        for name, value, inline in extra_fields:
            embed.add_field(name=name, value=value, inline=inline)

    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)

    embed.set_footer(text=f"Action: {action.upper()}")
    return embed


async def _send_log_embed(guild: discord.Guild, embed: discord.Embed):
    """Send a pre-built embed to the configured log channel."""
    try:
        ch_id = await db.get_setting("log_channel_id")
        if not ch_id:
            return
        channel = guild.get_channel(int(ch_id))
        if channel:
            await channel.send(embed=embed)
    except Exception:
        pass


# Legacy plain-text helper kept for any callers not yet migrated (should be none after this update)
async def _send_log(guild: discord.Guild, message: str):
    try:
        ch_id = await db.get_setting("log_channel_id")
        if not ch_id:
            return
        channel = guild.get_channel(int(ch_id))
        if channel:
            await channel.send(message)
    except Exception:
        pass


def _get_guild_id(interaction):
    return str(interaction.guild_id)


def _is_mod(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.moderate_members


async def _spam_settings():
    try:
        limit   = int(await db.get_setting("spam_limit")   or DEFAULT_SPAM_LIMIT)
        window  = int(await db.get_setting("spam_window")  or DEFAULT_SPAM_WINDOW)
        timeout = int(await db.get_setting("spam_timeout") or DEFAULT_SPAM_TIMEOUT)
    except Exception:
        limit, window, timeout = DEFAULT_SPAM_LIMIT, DEFAULT_SPAM_WINDOW, DEFAULT_SPAM_TIMEOUT
    return limit, window, timeout


async def _check_command(interaction: discord.Interaction, command_name: str) -> bool:
    guild_id = _get_guild_id(interaction)
    try:
        cs = await db.get_command_setting(guild_id, command_name)
    except Exception as e:
        print(f"[Bot] DB error in _check_command({command_name}): {e}", flush=True)
        if not _is_mod(interaction) and not interaction.user.guild_permissions.administrator:
            await interaction.followup.send("\u274c You need **Moderate Members** permission.", ephemeral=True)
            return False
        return True

    if not cs["enabled"]:
        await interaction.followup.send(
            f"\u274c The `/{command_name}` command is currently **disabled**.", ephemeral=True
        )
        return False

    user_id    = str(interaction.user.id)
    user_roles = [str(r.id) for r in interaction.user.roles]
    is_admin   = interaction.user.guild_permissions.administrator

    bl_users = [x.strip() for x in cs["blacklist_users"].split(",") if x.strip()]
    if user_id in bl_users:
        await interaction.followup.send(
            f"\u274c You have been **blacklisted** from using `/{command_name}`.", ephemeral=True
        )
        return False

    bl_mods = [x.strip() for x in cs["blacklist_mods"].split(",") if x.strip()]
    if user_id in bl_mods:
        await interaction.followup.send(
            f"\u274c You are **not allowed** to use `/{command_name}`.", ephemeral=True
        )
        return False

    wl_users = [x.strip() for x in cs["whitelist_users"].split(",") if x.strip()]
    wl_roles = [x.strip() for x in cs["whitelist_roles"].split(",") if x.strip()]
    if wl_users or wl_roles:
        allowed = (user_id in wl_users) or any(r in user_roles for r in wl_roles) or is_admin
        if not allowed:
            await interaction.followup.send(
                f"\u274c You need a **whitelisted role or user ID** to use `/{command_name}`.", ephemeral=True
            )
            return False

    if not _is_mod(interaction) and not is_admin:
        await interaction.followup.send(
            "\u274c You need **Moderate Members** permission.", ephemeral=True
        )
        return False

    return True


async def _get_bad_words(guild_id):
    return list(set(BAD_WORDS + await db.get_blacklisted_words_list(guild_id)))


@bot.event
async def on_ready():
    try:
        await db.init_db()
    except Exception as e:
        print(f"[Bot] WARNING: DB init failed: {e}", flush=True)
    try:
        synced = await bot.tree.sync(guild=TEST_GUILD)
    except Exception as e:
        print(f"[Bot] Guild sync failed ({e}), falling back to global sync.", flush=True)
        synced = await bot.tree.sync()
    print(f"[Bot] Logged in as {bot.user} | Synced {len(synced)} commands", flush=True)
    heartbeat.start(bot)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "\u274c You don't have permission to use this command."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = f"\u274c I'm missing permissions: `{', '.join(error.missing_permissions)}`"
    elif isinstance(error, app_commands.CommandOnCooldown):
        msg = f"\u23f3 Command on cooldown. Try again in `{error.retry_after:.1f}s`."
    else:
        print(f"[Bot] Unhandled error in /{interaction.command and interaction.command.name}: {error}", flush=True)
        msg = "\u274c Something went wrong. Please try again."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


@bot.event
async def on_member_join(member: discord.Member):
    guild_id = str(member.guild.id)
    try:
        if await db.is_user_blacklisted(guild_id, str(member.id)):
            await member.ban(reason="User is blacklisted")
            await db.log_action(guild_id, "auto-ban", str(member.id), str(bot.user.id), "Blacklisted user rejoined")
            embed = _build_log_embed(
                "auto-ban",
                target=member,
                reason="Blacklisted user rejoined the server",
                extra_fields=[("\U0001f4c5 Joined At", discord.utils.format_dt(member.joined_at or datetime.datetime.now(datetime.timezone.utc), style="F"), False)],
            )
            await _send_log_embed(member.guild, embed)
            return

        autoroles = await db.get_autoroles(guild_id)
        for row in autoroles:
            role = member.guild.get_role(int(row["role_id"]))
            if role:
                try:
                    await member.add_roles(role, reason="Auto-role on join")
                except Exception:
                    pass
        if not autoroles:
            role_id = await db.get_setting("auto_role_id") or (str(AUTO_ROLE_ID) if AUTO_ROLE_ID else None)
            if role_id:
                role = member.guild.get_role(int(role_id))
                if role:
                    await member.add_roles(role, reason="Auto-role on join")

        if await db.get_setting("log_join", "1") == "1":
            embed = _build_log_embed(
                "join",
                target=member,
                extra_fields=[
                    ("\U0001f4c5 Account Created", discord.utils.format_dt(member.created_at, style="R"), True),
                    ("\U0001f522 Member Count", str(member.guild.member_count), True),
                ],
            )
            await _send_log_embed(member.guild, embed)
    except Exception as e:
        print(f"[Bot] on_member_join error: {e}", flush=True)


@bot.event
async def on_member_remove(member: discord.Member):
    try:
        if await db.get_setting("log_leave", "1") == "1":
            roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
            embed = _build_log_embed(
                "leave",
                target=member,
                extra_fields=[
                    ("\U0001f4c5 Joined At", discord.utils.format_dt(member.joined_at, style="R") if member.joined_at else "Unknown", True),
                    (f"\U0001f3f7\ufe0f Roles [{len(roles)}]", " ".join(roles[:10]) or "None", False),
                ],
            )
            await _send_log_embed(member.guild, embed)
    except Exception as e:
        print(f"[Bot] on_member_remove error: {e}", flush=True)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author.bot or not before.guild or before.content == after.content:
        return
    try:
        if await db.get_setting("log_edit", "1") == "1":
            embed = _build_log_embed(
                "edit",
                target=before.author,
                channel=before.channel,
                extra_fields=[
                    ("\U0001f4e4 Before", before.content[:400] or "*empty*", False),
                    ("\U0001f4e5 After",  after.content[:400]  or "*empty*", False),
                    ("\U0001f517 Jump to Message", f"[Click here]({after.jump_url})", False),
                ],
            )
            await _send_log_embed(before.guild, embed)
    except Exception as e:
        print(f"[Bot] on_message_edit error: {e}", flush=True)


@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    try:
        if await db.get_setting("log_delete", "1") == "1":
            attachments = ", ".join(a.url for a in message.attachments) if message.attachments else None
            extra = [("\U0001f4ac Content", message.content[:800] or "*empty*", False)]
            if attachments:
                extra.append(("\U0001f4ce Attachments", attachments[:400], False))
            embed = _build_log_embed(
                "delete",
                target=message.author,
                channel=message.channel,
                extra_fields=extra,
            )
            await _send_log_embed(message.guild, embed)
    except Exception as e:
        print(f"[Bot] on_message_delete error: {e}", flush=True)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    guild_id    = str(message.guild.id)
    content_low = message.content.lower()
    try:
        bad_words = await _get_bad_words(guild_id)
        if any(w in content_low for w in bad_words):
            await message.delete()
            await message.channel.send(f"{message.author.mention} That word is not allowed!", delete_after=5)
            await db.log_action(guild_id, "filter", str(message.author.id), str(bot.user.id), "Blacklisted word")
            embed = _build_log_embed(
                "filter",
                target=message.author,
                channel=message.channel,
                reason="Message contained a blacklisted word",
                extra_fields=[("\U0001f4ac Message Preview", message.content[:300], False)],
            )
            await _send_log_embed(message.guild, embed)
            return

        triggers = await db.get_all_triggers(guild_id)
        for t in triggers:
            if t["phrase"] in content_low:
                await message.channel.send(t["response"])
                break

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
                    f"{message.author.mention} Spam detected {DASH} timed out for {spam_timeout}m.", delete_after=10
                )
                await db.log_action(guild_id, "timeout", str(message.author.id), str(bot.user.id), "Spam")
                embed = _build_log_embed(
                    "spam",
                    target=message.author,
                    channel=message.channel,
                    reason="Automatic spam detection",
                    extra_fields=[
                        ("\u23f1\ufe0f Duration", f"{spam_timeout} minute(s)", True),
                        ("\U0001f4e8 Messages", f"{spam_limit} in {spam_window}s", True),
                    ],
                )
                await _send_log_embed(message.guild, embed)
            except discord.Forbidden:
                pass
    except Exception as e:
        print(f"[Bot] on_message error: {e}", flush=True)
    await bot.process_commands(message)


tree = bot.tree


# ─────────────────────────────── moderation commands ─────────────────────────

@tree.command(name="kick", description="Kick a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to kick", reason="Reason")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await interaction.response.defer()
    if not await _check_command(interaction, "kick"): return
    try:
        await member.kick(reason=reason)
        await db.log_action(_get_guild_id(interaction), "kick", str(member.id), str(interaction.user.id), reason)
        if await db.get_setting("log_kick", "1") == "1":
            embed = _build_log_embed("kick", target=member, moderator=interaction.user, reason=reason)
            await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\U0001f462 **{member}** was kicked. Reason: {reason}")
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to kick that member.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="ban", description="Ban a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to ban", reason="Reason")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await interaction.response.defer()
    if not await _check_command(interaction, "ban"): return
    try:
        await member.ban(reason=reason)
        await db.log_action(_get_guild_id(interaction), "ban", str(member.id), str(interaction.user.id), reason)
        if await db.get_setting("log_ban", "1") == "1":
            embed = _build_log_embed("ban", target=member, moderator=interaction.user, reason=reason)
            await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\U0001f528 **{member}** was banned. Reason: {reason}")
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to ban that member.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="unban", description="Unban a user by ID", guild=TEST_GUILD)
@app_commands.describe(user_id="User ID to unban", reason="Reason")
async def unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    await interaction.response.defer()
    if not await _check_command(interaction, "unban"): return
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        await db.log_action(_get_guild_id(interaction), "unban", user_id, str(interaction.user.id), reason)
        if await db.get_setting("log_unban", "1") == "1":
            embed = _build_log_embed("unban", target=user, moderator=interaction.user, reason=reason)
            await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\u2705 **{user}** was unbanned.")
    except discord.NotFound:
        await interaction.followup.send("\u274c User not found or not banned.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to unban.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Could not unban: {e}", ephemeral=True)


@tree.command(name="timeout", description="Timeout a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to timeout", minutes="Duration in minutes", reason="Reason")
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = "No reason provided"):
    await interaction.response.defer()
    if not await _check_command(interaction, "timeout"): return
    try:
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        await db.log_action(_get_guild_id(interaction), "timeout", str(member.id), str(interaction.user.id), reason)
        if await db.get_setting("log_timeout", "1") == "1":
            until = datetime.datetime.now(datetime.timezone.utc) + timedelta(minutes=minutes)
            embed = _build_log_embed(
                "timeout",
                target=member,
                moderator=interaction.user,
                reason=reason,
                extra_fields=[
                    ("\u23f1\ufe0f Duration", f"{minutes} minute(s)", True),
                    ("\U0001f4c5 Expires", discord.utils.format_dt(until, style="R"), True),
                ],
            )
            await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\u23f1\ufe0f **{member}** timed out for {minutes}m. Reason: {reason}")
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to timeout that member.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="untimeout", description="Remove timeout from a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to remove timeout from", reason="Reason")
async def untimeout_cmd(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await interaction.response.defer()
    if not await _check_command(interaction, "untimeout"): return
    try:
        await member.timeout(None, reason=reason)
        await db.log_action(_get_guild_id(interaction), "untimeout", str(member.id), str(interaction.user.id), reason)
        embed = _build_log_embed("untimeout", target=member, moderator=interaction.user, reason=reason)
        await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\u2705 Timeout removed from **{member}**.")
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to modify that member.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="purge", description="Delete messages in bulk", guild=TEST_GUILD)
@app_commands.describe(amount="Number of messages to delete (1-100)")
async def purge(interaction: discord.Interaction, amount: int):
    await interaction.response.defer(ephemeral=True)
    if not await _check_command(interaction, "purge"): return
    try:
        amount = max(1, min(amount, 100))
        deleted = await interaction.channel.purge(limit=amount)
        await db.log_action(_get_guild_id(interaction), "purge", str(interaction.channel.id), str(interaction.user.id), f"Deleted {len(deleted)}")
        if await db.get_setting("log_purge", "1") == "1":
            embed = _build_log_embed(
                "purge",
                moderator=interaction.user,
                channel=interaction.channel,
                reason=f"Bulk delete requested",
                extra_fields=[("\U0001f5d1\ufe0f Messages Deleted", str(len(deleted)), True)],
            )
            await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\U0001f5d1\ufe0f Deleted {len(deleted)} messages.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to delete messages here.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="warn", description="Warn a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to warn", reason="Reason")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await interaction.response.defer()
    if not await _check_command(interaction, "warn"): return
    try:
        await db.add_warning(_get_guild_id(interaction), str(member.id), str(interaction.user.id), reason)
        await db.log_action(_get_guild_id(interaction), "warn", str(member.id), str(interaction.user.id), reason)
        if await db.get_setting("log_warn", "1") == "1":
            warns = await db.get_warnings(_get_guild_id(interaction), str(member.id))
            embed = _build_log_embed(
                "warn",
                target=member,
                moderator=interaction.user,
                reason=reason,
                extra_fields=[("\u26a0\ufe0f Total Warnings", str(len(warns)), True)],
            )
            await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\u26a0\ufe0f **{member}** has been warned. Reason: {reason}")
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="warnings", description="View warnings for a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to check")
async def warnings_cmd(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    if not await _check_command(interaction, "warnings"): return
    try:
        rows = await db.get_warnings(_get_guild_id(interaction), str(member.id))
        if not rows:
            await interaction.followup.send(f"\u2705 **{member}** has no warnings.")
            return
        lines = [
            f"`{i+1}.` {r['reason'] or 'No reason'} {DASH} {r['created_at'][:19]}"
            for i, r in enumerate(rows)
        ]
        await interaction.followup.send(f"\u26a0\ufe0f **{member}** has **{len(rows)}** warning(s):\n" + "\n".join(lines))
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="clearwarnings", description="Clear all warnings for a member", guild=TEST_GUILD)
@app_commands.describe(member="Member whose warnings to clear")
async def clearwarnings_cmd(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    if not await _check_command(interaction, "clearwarnings"): return
    try:
        old_count = len(await db.get_warnings(_get_guild_id(interaction), str(member.id)))
        await db.clear_warnings(_get_guild_id(interaction), str(member.id))
        await db.log_action(_get_guild_id(interaction), "clearwarnings", str(member.id), str(interaction.user.id))
        embed = _build_log_embed(
            "clearwarnings",
            target=member,
            moderator=interaction.user,
            extra_fields=[("\u26a0\ufe0f Warnings Cleared", str(old_count), True)],
        )
        await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\u2705 All warnings for **{member}** have been cleared.")
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="modlogs", description="View recent mod actions", guild=TEST_GUILD)
@app_commands.describe(limit="How many entries (default 10)")
async def modlogs(interaction: discord.Interaction, limit: int = 10):
    await interaction.response.defer()
    if not await _check_command(interaction, "modlogs"): return
    try:
        rows = await db.recent_logs(_get_guild_id(interaction), limit)
        if not rows:
            await interaction.followup.send("No mod logs yet.")
            return
        lines = [
            f"`{r['action'].upper()}` {ARROW} <@{r['target_id']}> by <@{r['moderator_id']}> {DASH} {r['reason'] or DASH}"
            for r in rows
        ]
        await interaction.followup.send("\U0001f4cb **Recent Mod Logs:**\n" + "\n".join(lines))
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="blacklist", description="Add a user to the blacklist (auto-ban on join)", guild=TEST_GUILD)
@app_commands.describe(member="Member to blacklist", reason="Reason")
async def blacklist_cmd(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await interaction.response.defer()
    if not await _check_command(interaction, "blacklist"): return
    try:
        await db.add_blacklisted_user(_get_guild_id(interaction), str(member.id), reason, str(interaction.user.id))
        await db.log_action(_get_guild_id(interaction), "blacklist", str(member.id), str(interaction.user.id), reason)
        embed = _build_log_embed("blacklist", target=member, moderator=interaction.user, reason=reason)
        await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\U0001f6ab **{member}** has been blacklisted. Reason: {reason}")
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="unblacklist", description="Remove a user from the blacklist", guild=TEST_GUILD)
@app_commands.describe(user_id="User ID to remove from blacklist")
async def unblacklist_cmd(interaction: discord.Interaction, user_id: str):
    await interaction.response.defer()
    if not await _check_command(interaction, "unblacklist"): return
    try:
        await db.remove_blacklisted_user(_get_guild_id(interaction), user_id)
        await db.log_action(_get_guild_id(interaction), "unblacklist", user_id, str(interaction.user.id))
        embed = _build_log_embed(
            "unblacklist",
            moderator=interaction.user,
            reason="Removed from blacklist",
            extra_fields=[("\U0001f194 User ID", f"`{user_id}`", True)],
        )
        await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\u2705 User `{user_id}` removed from blacklist.")
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="addword", description="Add a word to the word blacklist", guild=TEST_GUILD)
@app_commands.describe(word="Word to block")
async def addword(interaction: discord.Interaction, word: str):
    await interaction.response.defer(ephemeral=True)
    if not await _check_command(interaction, "addword"): return
    try:
        await db.add_blacklisted_word(_get_guild_id(interaction), word, str(interaction.user.id))
        await interaction.followup.send(f"\u2705 Word `{word}` added to blacklist.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="removeword", description="Remove a word from the word blacklist", guild=TEST_GUILD)
@app_commands.describe(word="Word to unblock")
async def removeword(interaction: discord.Interaction, word: str):
    await interaction.response.defer(ephemeral=True)
    if not await _check_command(interaction, "removeword"): return
    try:
        await db.remove_blacklisted_word(_get_guild_id(interaction), word)
        await interaction.followup.send(f"\u2705 Word `{word}` removed from blacklist.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


# ─────────────────────────────── channel & role commands ──────────────────────

@tree.command(name="slowmode", description="Set slowmode delay in a channel", guild=TEST_GUILD)
@app_commands.describe(seconds="Slowmode delay in seconds (0 to disable, max 21600)", channel="Channel to apply slowmode (defaults to current)")
async def slowmode_cmd(interaction: discord.Interaction, seconds: int, channel: discord.TextChannel = None):
    await interaction.response.defer()
    if not await _check_command(interaction, "slowmode"): return
    target = channel or interaction.channel
    try:
        seconds = max(0, min(seconds, 21600))
        await target.edit(slowmode_delay=seconds)
        await db.log_action(_get_guild_id(interaction), "slowmode", str(target.id), str(interaction.user.id), f"{seconds}s")
        embed = _build_log_embed(
            "slowmode",
            moderator=interaction.user,
            channel=target,
            reason="Slowmode updated",
            extra_fields=[("\u23f1\ufe0f New Delay", "Disabled" if seconds == 0 else f"{seconds}s", True)],
        )
        await _send_log_embed(interaction.guild, embed)
        if seconds == 0:
            await interaction.followup.send(f"\u23f1\ufe0f Slowmode **disabled** in {target.mention}.")
        else:
            await interaction.followup.send(f"\u23f1\ufe0f Slowmode set to **{seconds}s** in {target.mention}.")
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to edit that channel.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="lock", description="Lock a channel so members cannot send messages", guild=TEST_GUILD)
@app_commands.describe(channel="Channel to lock (defaults to current)", reason="Reason")
async def lock_cmd(interaction: discord.Interaction, channel: discord.TextChannel = None, reason: str = "No reason provided"):
    await interaction.response.defer()
    if not await _check_command(interaction, "lock"): return
    target = channel or interaction.channel
    try:
        overwrite = target.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await target.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
        await db.log_action(_get_guild_id(interaction), "lock", str(target.id), str(interaction.user.id), reason)
        embed = _build_log_embed("lock", moderator=interaction.user, channel=target, reason=reason)
        await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\U0001f512 {target.mention} has been **locked**. Reason: {reason}")
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to edit that channel.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="unlock", description="Unlock a channel so members can send messages again", guild=TEST_GUILD)
@app_commands.describe(channel="Channel to unlock (defaults to current)", reason="Reason")
async def unlock_cmd(interaction: discord.Interaction, channel: discord.TextChannel = None, reason: str = "No reason provided"):
    await interaction.response.defer()
    if not await _check_command(interaction, "unlock"): return
    target = channel or interaction.channel
    try:
        overwrite = target.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = None
        await target.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=reason)
        await db.log_action(_get_guild_id(interaction), "unlock", str(target.id), str(interaction.user.id), reason)
        embed = _build_log_embed("unlock", moderator=interaction.user, channel=target, reason=reason)
        await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\U0001f513 {target.mention} has been **unlocked**.")
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to edit that channel.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="nick", description="Change a member's nickname", guild=TEST_GUILD)
@app_commands.describe(member="Member to rename", nickname="New nickname (leave blank to reset)")
async def nick_cmd(interaction: discord.Interaction, member: discord.Member, nickname: str = None):
    await interaction.response.defer()
    if not await _check_command(interaction, "nick"): return
    try:
        old_nick = member.display_name
        await member.edit(nick=nickname)
        await db.log_action(_get_guild_id(interaction), "nick", str(member.id), str(interaction.user.id), f"{old_nick} -> {nickname or 'reset'}")
        embed = _build_log_embed(
            "nick",
            target=member,
            moderator=interaction.user,
            extra_fields=[
                ("\u270f\ufe0f Old Nickname", old_nick, True),
                ("\u2728 New Nickname",       nickname or "*(reset)*", True),
            ],
        )
        await _send_log_embed(interaction.guild, embed)
        if nickname:
            await interaction.followup.send(f"\u270f\ufe0f **{member.name}**'s nickname set to **{nickname}**.")
        else:
            await interaction.followup.send(f"\u270f\ufe0f **{member.name}**'s nickname has been **reset**.")
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to change that member's nickname.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="role", description="Add or remove a role from a member", guild=TEST_GUILD)
@app_commands.describe(action="add or remove", member="Target member", role="Role to add/remove")
@app_commands.choices(action=[
    app_commands.Choice(name="add",    value="add"),
    app_commands.Choice(name="remove", value="remove"),
])
async def role_cmd(interaction: discord.Interaction, action: app_commands.Choice[str], member: discord.Member, role: discord.Role):
    await interaction.response.defer()
    if not await _check_command(interaction, "role"): return
    try:
        if action.value == "add":
            await member.add_roles(role, reason=f"By {interaction.user}")
            await db.log_action(_get_guild_id(interaction), "role-add", str(member.id), str(interaction.user.id), str(role.id))
            embed = _build_log_embed(
                "role-add",
                target=member,
                moderator=interaction.user,
                extra_fields=[("\U0001f3f7\ufe0f Role", role.mention, True)],
            )
            await _send_log_embed(interaction.guild, embed)
            await interaction.followup.send(f"\u2705 Added {role.mention} to **{member.display_name}**.")
        else:
            await member.remove_roles(role, reason=f"By {interaction.user}")
            await db.log_action(_get_guild_id(interaction), "role-remove", str(member.id), str(interaction.user.id), str(role.id))
            embed = _build_log_embed(
                "role-remove",
                target=member,
                moderator=interaction.user,
                extra_fields=[("\U0001f3f7\ufe0f Role", role.mention, True)],
            )
            await _send_log_embed(interaction.guild, embed)
            await interaction.followup.send(f"\u2705 Removed {role.mention} from **{member.display_name}**.")
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to manage that role.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


# ─────────────────────────────── utility commands ─────────────────────────────

@tree.command(name="softban", description="Ban then immediately unban a member (clears their messages)", guild=TEST_GUILD)
@app_commands.describe(member="Member to softban", delete_days="Days of messages to delete (0-7)", reason="Reason")
async def softban_cmd(interaction: discord.Interaction, member: discord.Member, delete_days: int = 1, reason: str = "No reason provided"):
    await interaction.response.defer()
    if not await _check_command(interaction, "softban"): return
    try:
        delete_days = max(0, min(delete_days, 7))
        await member.ban(delete_message_days=delete_days, reason=f"Softban: {reason}")
        await interaction.guild.unban(member, reason="Softban unban")
        await db.log_action(_get_guild_id(interaction), "softban", str(member.id), str(interaction.user.id), reason)
        embed = _build_log_embed(
            "softban",
            target=member,
            moderator=interaction.user,
            reason=reason,
            extra_fields=[("\U0001f5d1\ufe0f Messages Deleted", f"{delete_days} day(s)", True)],
        )
        await _send_log_embed(interaction.guild, embed)
        await interaction.followup.send(f"\U0001f9fc **{member}** was softbanned ({delete_days}d of messages deleted). Reason: {reason}")
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to ban/unban that member.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="announce", description="Send an announcement embed to a channel", guild=TEST_GUILD)
@app_commands.describe(
    title="Announcement title",
    message="Announcement body text",
    channel="Channel to send to (defaults to current)",
    color="Embed color hex (e.g. ff5733 — optional)",
    ping_everyone="Ping @everyone with the announcement"
)
async def announce_cmd(
    interaction: discord.Interaction,
    title: str,
    message: str,
    channel: discord.TextChannel = None,
    color: str = None,
    ping_everyone: bool = False
):
    await interaction.response.defer(ephemeral=True)
    if not await _check_command(interaction, "announce"): return
    target = channel or interaction.channel
    try:
        embed_color = discord.Color.blurple()
        if color:
            try:
                embed_color = discord.Color(int(color.lstrip("#"), 16))
            except ValueError:
                pass
        embed = discord.Embed(title=title, description=message, color=embed_color)
        embed.set_footer(text=f"Announced by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        content = "@everyone" if ping_everyone else None
        await target.send(content=content, embed=embed)
        await db.log_action(_get_guild_id(interaction), "announce", str(target.id), str(interaction.user.id), title[:100])
        log_embed = _build_log_embed(
            "announce",
            moderator=interaction.user,
            channel=target,
            extra_fields=[
                ("\U0001f4e3 Title",    title[:200], False),
                ("\U0001f4ac Preview", message[:200], False),
                ("\U0001f4e2 Pinged @everyone", "Yes" if ping_everyone else "No", True),
            ],
        )
        await _send_log_embed(interaction.guild, log_embed)
        await interaction.followup.send(f"\u2705 Announcement sent to {target.mention}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("\u274c I don't have permission to send messages in that channel.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="serverinfo", description="Display server information and stats", guild=TEST_GUILD)
async def serverinfo_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    guild = interaction.guild
    try:
        bots    = sum(1 for m in guild.members if m.bot)
        humans  = guild.member_count - bots
        online  = sum(1 for m in guild.members if m.status != discord.Status.offline)
        txt_ch  = len(guild.text_channels)
        voc_ch  = len(guild.voice_channels)
        roles   = len(guild.roles) - 1
        boosts  = guild.premium_subscription_count
        created = discord.utils.format_dt(guild.created_at, style="D")

        embed = discord.Embed(
            title=f"\U0001f3f0 {guild.name}",
            color=discord.Color.blurple()
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)

        embed.add_field(name="Owner",        value=f"<@{guild.owner_id}>",    inline=True)
        embed.add_field(name="Created",      value=created,                   inline=True)
        embed.add_field(name="Boost Level",  value=f"Level {guild.premium_tier} ({boosts} boosts)", inline=True)
        embed.add_field(name="Members",      value=f"\U0001f465 {guild.member_count} ({humans} humans, {bots} bots)", inline=False)
        embed.add_field(name="Online",       value=f"\U0001f7e2 ~{online}",   inline=True)
        embed.add_field(name="Channels",     value=f"\U0001f4ac {txt_ch} text  \U0001f50a {voc_ch} voice", inline=True)
        embed.add_field(name="Roles",        value=str(roles),                inline=True)
        embed.add_field(name="Server ID",    value=f"`{guild.id}`",           inline=True)
        embed.add_field(name="Verification", value=str(guild.verification_level).title(), inline=True)

        if guild.description:
            embed.add_field(name="Description", value=guild.description, inline=False)

        embed.set_footer(text=f"Requested by {interaction.user}", icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


@tree.command(name="avatar", description="Get a member's avatar", guild=TEST_GUILD)
@app_commands.describe(member="Member whose avatar to fetch (defaults to you)")
async def avatar_cmd(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.defer()
    target = member or interaction.user
    try:
        embed = discord.Embed(
            title=f"{target.display_name}'s Avatar",
            color=target.color or discord.Color.blurple()
        )
        embed.set_image(url=target.display_avatar.url)
        embed.add_field(name="Global Avatar", value=f"[Link]({target.display_avatar.url})", inline=True)
        try:
            full_user = await bot.fetch_user(target.id)
            if full_user.avatar and full_user.avatar.url != target.display_avatar.url:
                embed.add_field(name="Server Avatar", value=f"[Link]({target.display_avatar.url})", inline=True)
        except Exception:
            pass
        embed.set_footer(text=f"ID: {target.id}")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"\u274c Error: {e}", ephemeral=True)


# ─────────────────────────────── user lookup ──────────────────────────────────

@tree.command(name="userinfo", description="Full Discord profile + mod history for a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to look up")
async def userinfo(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.defer()
    if not await _check_command(interaction, "userinfo"): return

    try:
        guild_id    = _get_guild_id(interaction)
        warns       = await db.get_warnings(guild_id, str(member.id))
        logs        = await db.logs_for_user(guild_id, str(member.id))
        blacklisted = await db.is_user_blacklisted(guild_id, str(member.id))
    except Exception:
        warns, logs, blacklisted = [], [], False

    try:
        full_user    = await bot.fetch_user(member.id)
        banner_url   = full_user.banner.url if full_user.banner else None
        accent_color = full_user.accent_color or member.color or discord.Color.blurple()
        badges = [label for flag, label in BADGE_FLAGS.items() if full_user.public_flags & flag]
    except Exception:
        full_user, banner_url = None, None
        accent_color = member.color or discord.Color.blurple()
        badges = []

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

    try:
        await db.cache_profile(
            str(member.id), str(member.name),
            getattr(member, 'global_name', None) or str(member.name),
            member.display_avatar.url, banner_url or "",
            str(accent_color), "", ", ".join(badges)
        )
    except Exception:
        pass

    await interaction.followup.send(embed=embed)


# ─────────────────────────────── run ──────────────────────────────────────────

if __name__ == "__main__":
    bot.run(TOKEN)
