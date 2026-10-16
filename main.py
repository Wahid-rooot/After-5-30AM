import os
import re
import asyncio
from datetime import datetime, timedelta
import discord
from discord.ext import commands
from discord import app_commands

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
    guild = member.guild
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.kick):
        if entry.target.id == member.id:
            executor = entry.user
            if executor and not is_whitelisted(executor.id, guild.owner_id):
                if check_rate_limit(executor.id):
                    await execute_antinuke_punishment(guild, executor, "Mass Kick Triggered")

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
        if entry.target.id == user.id:
            executor = entry.user
            if executor and not is_whitelisted(executor.id, guild.owner_id):
                if check_rate_limit(executor.id):
                    await execute_antinuke_punishment(guild, executor, "Mass Ban Triggered")

@bot.event
async def on_member_join(member: discord.Member):
    # Detect unauthorized Bot Additions
    if member.bot:
        guild = member.guild
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.bot_add):
            executor = entry.user
            if executor and not is_whitelisted(executor.id, guild.owner_id):
                await member.ban(reason=f"[{BOT_NAME} ANTINUKE] Unauthorized Bot Addition")
                await execute_antinuke_punishment(guild, executor, f"Added Unauthorized Bot ({member.mention})")


# -------------------------------------------------------------------
# COMMANDS: OLYMPUS SECURITY & WHITELIST MANAGEMENT
# -------------------------------------------------------------------
@bot.tree.command(name="antinuke-setlog", description=f"Set the {BOT_NAME} security logging channel.")
@app_commands.checks.has_permissions(administrator=True)
async def set_log(interaction: discord.Interaction, channel: discord.TextChannel):
    SECURITY_CONFIG["log_channel_id"] = channel.id
    await interaction.response.send_message(f"✅ **{BOT_NAME}** security log channel updated to {channel.mention}")

@bot.tree.command(name="whitelist", description=f"Add a trusted user to {BOT_NAME} Antinuke bypass whitelist.")
@app_commands.checks.has_permissions(administrator=True)
async def whitelist(interaction: discord.Interaction, user: discord.User):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message(f"❌ Only the Server Owner can manage the {BOT_NAME} Whitelist!", ephemeral=True)
        return
    SECURITY_CONFIG["whitelist"].add(user.id)
    await interaction.response.send_message(f"🛡️ **{user.name}** (`{user.id}`) has been added to the **{BOT_NAME}** Antinuke Whitelist.")

@bot.tree.command(name="unwhitelist", description=f"Remove a user from {BOT_NAME} Antinuke whitelist.")
@app_commands.checks.has_permissions(administrator=True)
async def unwhitelist(interaction: discord.Interaction, user: discord.User):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message(f"❌ Only the Server Owner can manage the {BOT_NAME} Whitelist!", ephemeral=True)
        return
    SECURITY_CONFIG["whitelist"].discard(user.id)
    await interaction.response.send_message(f"⚠️ **{user.name}** (`{user.id}`) removed from **{BOT_NAME}** Whitelist.")


# -------------------------------------------------------------------
# MODERATION COMMANDS (/lock, /warn, /timeout, /mute, /kick, /ban)
# -------------------------------------------------------------------

# 🔒 /LOCK
@bot.tree.command(name="lock", description="Locks current channel and stops non-staff members from sending messages.")
@app_commands.checks.has_permissions(manage_channels=True)
async def lock_channel(interaction: discord.Interaction, channel: discord.TextChannel = None):
    target_channel = channel or interaction.channel
    overwrite = target_channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await target_channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=f"Locked by {interaction.user} via {BOT_NAME}")
    
    embed = discord.Embed(
        title="🔒 Channel Locked",
        description=f"{target_channel.mention} is now locked. Regular members can no longer send messages.",
        color=discord.Color.red()
    )
    embed.set_footer(text=f"Action enforced by {BOT_NAME}")
    await interaction.response.send_message(embed=embed)

# 🔓 /UNLOCK
@bot.tree.command(name="unlock", description="Unlocks target channel.")
@app_commands.checks.has_permissions(manage_channels=True)
async def unlock_channel(interaction: discord.Interaction, channel: discord.TextChannel = None):
    target_channel = channel or interaction.channel
    overwrite = target_channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = True
    await target_channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=f"Unlocked by {interaction.user} via {BOT_NAME}")
    
    embed = discord.Embed(
        title="🔓 Channel Unlocked",
        description=f"{target_channel.mention} is now unlocked.",
        color=discord.Color.green()
    )
    embed.set_footer(text=f"Action enforced by {BOT_NAME}")
    await interaction.response.send_message(embed=embed)

# ⚠️ /WARN
@bot.tree.command(name="warn", description="Issue a formal warning to a member.")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn_member(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    warnings_db[member.id] = warnings_db.get(member.id, 0) + 1
    total_warns = warnings_db[member.id]
    
    await interaction.response.send_message(f"⚠️ Warned {member.mention} | **Reason:** {reason} (Total Warns: {total_warns})")

# ⏱️ /TIMEOUT & /MUTE
@bot.tree.command(name="timeout", description="Temporarily mute/timeout a member.")
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout_member(interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = "No reason provided"):
    duration = timedelta(minutes=minutes)
    until = datetime.now().astimezone() + duration
    await member.timeout(until, reason=f"{reason} (via {BOT_NAME})")
    await interaction.response.send_message(f"🔇 Timed out {member.mention} for **{minutes} minutes** | **Reason:** {reason}")

@bot.tree.command(name="mute", description="Mute a member indefinitely or for specified minutes.")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute_member(interaction: discord.Interaction, member: discord.Member, minutes: int = 60, reason: str = "Muted by Staff"):
    duration = timedelta(minutes=minutes)
    until = datetime.now().astimezone() + duration
    await member.timeout(until, reason=f"{reason} (via {BOT_NAME})")
    await interaction.response.send_message(f"🤐 {member.mention} has been muted for {minutes}m.")

# 👢 /KICK
@bot.tree.command(name="kick", description="Kick a member from the server.")
@app_commands.checks.has_permissions(kick_members=True)
async def kick_member(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.kick(reason=f"{reason} (via {BOT_NAME})")
    await interaction.response.send_message(f"👢 Kicked {member.mention} | **Reason:** {reason}")

# 🔨 /BAN
@bot.tree.command(name="ban", description="Ban a member from the server.")
@app_commands.checks.has_permissions(ban_members=True)
async def ban_member(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await member.ban(reason=f"{reason} (via {BOT_NAME})")
    await interaction.response.send_message(f"🔨 Banned {member.mention} | **Reason:** {reason}")


# -------------------------------------------------------------------
# RUN BOT
# -------------------------------------------------------------------
bot.run(os.environ.get("DISCORD_TOKEN"))
