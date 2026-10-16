import os
import re
import threading
from flask import Flask
from datetime import datetime, timedelta
import discord
from discord.ext import commands
from discord import app_commands

# -------------------------------------------------------------------
# KEEP ALIVE WEB SERVER (For UptimeRobot 24/7 Pinging)
# -------------------------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "NoName Security Bot is Online!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

# -------------------------------------------------------------------
# BOT CONFIGURATION & INITIALIZATION (NoName Security)
# -------------------------------------------------------------------
BOT_NAME = "NoName"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.moderation = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Server In-Memory Storage (Whitelists, Logs, Rate Limits)
SECURITY_CONFIG = {
    "whitelist": set(),         # User IDs exempt from Antinuke
    "log_channel_id": None,     # Antinuke & Moderation log channel
    "antinuke_enabled": True,
    "antiraid_enabled": True,
    "max_actions": 3,           # Threshold for mass actions
    "time_window": 10,          # Seconds window for antinuke triggers
    "min_account_age_days": 3   # Account age filter for Antiraid
}

# Tracking dicts for rate-limiting action triggers
user_action_tracker = {}  # {user_id: [timestamps]}
user_msg_tracker = {}     # {user_id: [timestamps]}
warnings_db = {}          # {user_id: warning_count}

# Regex pattern for Anti-Link
LINK_REGEX = re.compile(r"https?://(?:www\.|(?!www))[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|www\.[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|https?://discord\.gg/[a-zA-Z0-9]+")

# -------------------------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------------------------
async def send_log(guild: discord.Guild, embed: discord.Embed):
    """Dispatches security events to the configured antinuke log channel."""
    channel_id = SECURITY_CONFIG["log_channel_id"]
    if channel_id:
        channel = guild.get_channel(channel_id)
        if channel:
            await channel.send(embed=embed)

def is_whitelisted(user_id: int, owner_id: int) -> bool:
    """Check if a user is Server Owner or explicitly Whitelisted."""
    return user_id == owner_id or user_id == bot.user.id or user_id in SECURITY_CONFIG["whitelist"]

def check_rate_limit(user_id: int) -> bool:
    """Evaluates whether an admin has executed dangerous actions too fast (Antinuke Trigger)."""
    now = datetime.utcnow()
    timestamps = user_action_tracker.get(user_id, [])
    # Filter out actions older than time window
    timestamps = [ts for ts in timestamps if (now - ts).total_seconds() < SECURITY_CONFIG["time_window"]]
    timestamps.append(now)
    user_action_tracker[user_id] = timestamps
    return len(timestamps) >= SECURITY_CONFIG["max_actions"]

async def execute_antinuke_punishment(guild: discord.Guild, executor: discord.Member, reason: str):
    """Olympus-style Instant Action: Removes executor's roles & bans to prevent further damage."""
    try:
        # Strip all dangerous roles instantly
        await executor.edit(roles=[], reason=f"[{BOT_NAME} ANTINUKE] {reason}")
        # Ban the rogue admin
        await guild.ban(executor, reason=f"[{BOT_NAME} OLYMPUS PROTECTION] {reason}")
        
        embed = discord.Embed(
            title=f"🛡️ {BOT_NAME.upper()} ANTINUKE ACTIVATED",
            description=f"**Executor:** {executor.mention} (`{executor.id}`)\n**Action:** Banned & Cleared Roles\n**Reason:** {reason}",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"Protected by {BOT_NAME} Security")
        await send_log(guild, embed)
    except Exception as e:
        print(f"Failed to execute antinuke ban on {executor.id}: {e}")

# -------------------------------------------------------------------
# EVENT HANDLERS (ANTINUKE, ANTIRAID, AUTOMOD)
# -------------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"[{BOT_NAME}] Logged in successfully as {bot.user} (ID: {bot.user.id})")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{BOT_NAME} Security | /help"))
    try:
        synced = await bot.tree.sync()
        print(f"[{BOT_NAME}] Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"[{BOT_NAME}] Failed to sync slash commands: {e}")

# --- ANTIRAID (ACCOUNT AGE CHECK) ---
@bot.event
async def on_member_join(member: discord.Member):
    if not SECURITY_CONFIG["antiraid_enabled"]:
        return

    # Account Age Verification
    account_age = (datetime.utcnow() - member.created_at.replace(tzinfo=None)).days
    if account_age < SECURITY_CONFIG["min_account_age_days"]:
        try:
            await member.send(f"You were kicked from **{member.guild.name}** by **{BOT_NAME}** due to Anti-Raid account age limits (Min {SECURITY_CONFIG['min_account_age_days']} days old).")
        except:
            pass
        await member.kick(reason=f"[{BOT_NAME} ANTIRAID] Account age below minimum threshold.")
        
        embed = discord.Embed(
            title=f"🚨 {BOT_NAME.upper()} ANTIRAID KICK",
            description=f"**User:** {member.mention} (`{member.id}`)\n**Account Age:** {account_age} days\n**Action:** Kicked automatically.",
            color=discord.Color.orange(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"Protected by {BOT_NAME} Security")
        await send_log(member.guild, embed)

# --- AUTOMOD (ANTI-LINK & ANTI-SPAM) ---
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    author_id = message.author.id
    if is_whitelisted(author_id, message.guild.owner_id):
        await bot.process_commands(message)
        return

    # 1. ANTI-LINK
    if LINK_REGEX.search(message.content):
        await message.delete()
        await message.channel.send(f"⚠️ {message.author.mention}, links are prohibited in this server! *(Blocked by {BOT_NAME})*", delete_after=5)
        return

    # 2. ANTI-SPAM (5 messages in 3 seconds trigger)
    now = datetime.utcnow()
    user_msgs = user_msg_tracker.get(author_id, [])
    user_msgs = [ts for ts in user_msgs if (now - ts).total_seconds() < 3]
    user_msgs.append(now)
    user_msg_tracker[author_id] = user_msgs

    if len(user_msgs) >= 5:
        await message.channel.purge(limit=5, check=lambda m: m.author.id == author_id)
        # Timeout spammer for 5 minutes
        timeout_until = datetime.now().astimezone() + timedelta(minutes=5)
        await message.author.timeout(timeout_until, reason=f"[{BOT_NAME} AUTOMOD] Mass Spam Detected")
        await message.channel.send(f"🔇 {message.author.mention} was muted for 5 minutes due to spamming. *(Automated by {BOT_NAME})*", delete_after=8)
        return

    await bot.process_commands(message)

# --- AUDIT LOG MONITORS (MASS CHANNEL/ROLE DELETE, KICKS, BANS, BOT ADDS) ---
@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    guild = channel.guild
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
        executor = entry.user
        if executor and not is_whitelisted(executor.id, guild.owner_id):
            if check_rate_limit(executor.id):
                await execute_antinuke_punishment(guild, executor, "Mass Channel Deletion Triggered")

@bot.event
async def on_guild_role_delete(role: discord.Role):
    guild = role.guild
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.role_delete):
        executor = entry.user
        if executor and not is_whitelisted(executor.id, guild.owner_id):
            if check_rate_limit(executor.id):
                await execute_antinuke_punishment(guild, executor, "Mass Role Deletion Triggered")

@bot.event
async def on_member_remove(member: discord.Member):
    guild
