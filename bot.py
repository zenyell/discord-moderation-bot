import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from datetime import timedelta
from collections import defaultdict, deque
import time
import database as db

load_dotenv()

TOKEN        = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID     = int(os.getenv("GUILD_ID", 0)) if os.getenv("GUILD_ID") else None
AUTO_ROLE_ID = int(os.getenv("AUTO_ROLE_ID", 0)) if os.getenv("AUTO_ROLE_ID") else None
BAD_WORDS    = [w.strip().lower() for w in os.getenv("BAD_WORDS", "").split(",") if w.strip()]
SPAM_LIMIT   = int(os.getenv("SPAM_MESSAGE_LIMIT", 6))
SPAM_WINDOW  = int(os.getenv("SPAM_WINDOW_SECONDS", 8))
SPAM_TIMEOUT = int(os.getenv("SPAM_TIMEOUT_MINUTES", 5))

db.init_db()

intents = discord.Intents.default()
intents.members        = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
TEST_GUILD = discord.Object(id=GUILD_ID) if GUILD_ID else None

_spam: dict[int, deque] = defaultdict(lambda: deque())


def _is_mod(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.moderate_members


async def _check_mod(interaction: discord.Interaction) -> bool:
    if not _is_mod(interaction):
        await interaction.response.send_message("❌ You need **Moderate Members** permission.", ephemeral=True)
        return False
    return True


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync(guild=TEST_GUILD)
    except Exception:
        synced = await bot.tree.sync()
    print(f"[Bot] Logged in as {bot.user} | Synced {len(synced)} commands")


@bot.event
async def on_member_join(member: discord.Member):
    if AUTO_ROLE_ID:
        role = member.guild.get_role(AUTO_ROLE_ID)
        if role:
            await member.add_roles(role, reason="Auto-role on join")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    content_lower = message.content.lower()
    if any(w in content_lower for w in BAD_WORDS):
        await message.delete()
        await message.channel.send(f"{message.author.mention} Watch your language!", delete_after=5)
        db.log_action(str(message.guild.id), "filter", str(message.author.id), str(bot.user.id), "Bad word")
        return
    uid = message.author.id
    now = time.monotonic()
    q = _spam[uid]
    q.append(now)
    while q and now - q[0] > SPAM_WINDOW:
        q.popleft()
    if len(q) >= SPAM_LIMIT:
        q.clear()
        try:
            await message.author.timeout(timedelta(minutes=SPAM_TIMEOUT), reason="Spam")
            await message.channel.send(f"{message.author.mention} Spam detected — timed out for {SPAM_TIMEOUT}m.", delete_after=10)
            db.log_action(str(message.guild.id), "timeout", str(message.author.id), str(bot.user.id), "Spam")
        except discord.Forbidden:
            pass
    await bot.process_commands(message)


tree = bot.tree


@tree.command(name="kick", description="Kick a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to kick", reason="Reason")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not await _check_mod(interaction): return
    await member.kick(reason=reason)
    db.log_action(str(interaction.guild_id), "kick", str(member.id), str(interaction.user.id), reason)
    await interaction.response.send_message(f"👢 **{member}** was kicked. Reason: {reason}")


@tree.command(name="ban", description="Ban a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to ban", reason="Reason")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not await _check_mod(interaction): return
    await member.ban(reason=reason)
    db.log_action(str(interaction.guild_id), "ban", str(member.id), str(interaction.user.id), reason)
    await interaction.response.send_message(f"🔨 **{member}** was banned. Reason: {reason}")


@tree.command(name="unban", description="Unban a user by ID", guild=TEST_GUILD)
@app_commands.describe(user_id="User ID to unban", reason="Reason")
async def unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    if not await _check_mod(interaction): return
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        db.log_action(str(interaction.guild_id), "unban", user_id, str(interaction.user.id), reason)
        await interaction.response.send_message(f"✅ **{user}** was unbanned.")
    except Exception as e:
        await interaction.response.send_message(f"❌ Could not unban: {e}", ephemeral=True)


@tree.command(name="timeout", description="Timeout a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to timeout", minutes="Duration in minutes", reason="Reason")
async def timeout_cmd(interaction: discord.Interaction, member: discord.Member, minutes: int = 10, reason: str = "No reason provided"):
    if not await _check_mod(interaction): return
    await member.timeout(timedelta(minutes=minutes), reason=reason)
    db.log_action(str(interaction.guild_id), "timeout", str(member.id), str(interaction.user.id), reason)
    await interaction.response.send_message(f"⏱️ **{member}** timed out for {minutes}m. Reason: {reason}")


@tree.command(name="purge", description="Delete messages in bulk", guild=TEST_GUILD)
@app_commands.describe(amount="Number of messages to delete (1-100)")
async def purge(interaction: discord.Interaction, amount: int):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ You need **Manage Messages** permission.", ephemeral=True)
        return
    amount = max(1, min(amount, 100))
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    db.log_action(str(interaction.guild_id), "purge", str(interaction.channel.id), str(interaction.user.id), f"Deleted {len(deleted)}")
    await interaction.followup.send(f"🗑️ Deleted {len(deleted)} messages.", ephemeral=True)


@tree.command(name="warn", description="Warn a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to warn", reason="Reason")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not await _check_mod(interaction): return
    db.add_warning(str(interaction.guild_id), str(member.id), str(interaction.user.id), reason)
    db.log_action(str(interaction.guild_id), "warn", str(member.id), str(interaction.user.id), reason)
    await interaction.response.send_message(f"⚠️ **{member}** has been warned. Reason: {reason}")


@tree.command(name="warnings", description="View warnings for a member", guild=TEST_GUILD)
@app_commands.describe(member="Member to check")
async def warnings(interaction: discord.Interaction, member: discord.Member):
    if not await _check_mod(interaction): return
    rows = db.get_warnings(str(interaction.guild_id), str(member.id))
    if not rows:
        await interaction.response.send_message(f"✅ **{member}** has no warnings.")
        return
    lines = [f"`{i+1}.` {r['reason'] or 'No reason'} — {r['created_at'][:19]}" for i, r in enumerate(rows)]
    await interaction.response.send_message(f"⚠️ **{member}** has **{len(rows)}** warning(s):\n" + "\n".join(lines))


@tree.command(name="modlogs", description="View recent mod actions", guild=TEST_GUILD)
@app_commands.describe(limit="How many entries (default 10)")
async def modlogs(interaction: discord.Interaction, limit: int = 10):
    if not await _check_mod(interaction): return
    rows = db.recent_logs(str(interaction.guild_id), limit)
    if not rows:
        await interaction.response.send_message("No mod logs yet.")
        return
    lines = [f"`{r['action'].upper()}` → <@{r['target_id']}> by <@{r['moderator_id']}> — {r['reason'] or '—'}" for r in rows]
    await interaction.response.send_message("📋 **Recent Mod Logs:**\n" + "\n".join(lines))


bot.run(TOKEN)
