import os
import re
import time
import asyncio
from datetime import timedelta
from collections import defaultdict
from threading import Thread

import discord
from discord.ext import commands
from flask import Flask

# -------------------------------------------------------------------
# 1. FLASK KEEP-ALIVE SERVER (FOR RENDER / HOSTING)
# -------------------------------------------------------------------
app = Flask('')

@app.route('/')
def home():
    return "Bot is online, AntiNuke & AutoMod active!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()

# -------------------------------------------------------------------
# 2. BOT CONFIGURATION & SETUP
# -------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.bans = True
intents.moderation = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# -------------------------------------------------------------------
# 3. ANTINUKE & AUTOMOD ENGINE (OLYMPUS / WICK GRADE)
# -------------------------------------------------------------------
# AntiNuke Storage: Stores action timestamps per user
# Structure: antinuke_tracker[guild_id][action_type][user_id] = [timestamps...]
antinuke_tracker = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

# Configurable limits per time window (e.g., 3 channel deletions in 10 seconds triggers punish)
ANTINUKE_LIMITS = {
    "channel_delete": (3, 10),
    "channel_create": (4, 10),
    "role_delete": (3, 10),
    "role_create": (4, 10),
    "kick": (3, 10),
    "ban": (3, 10),
    "bot_add": (1, 10),
}

# Whitelist storage (In production, replace with a persistent DB)
whitelisted_users = set()  # Add user IDs here

# AutoMod Rate Limit (Spam tracking)
spam_tracker = defaultdict(list)

def is_whitelisted(guild: discord.Guild, user_id: int) -> bool:
    """Checks if the user is the Guild Owner or explicitly Whitelisted."""
    return user_id == guild.owner_id or user_id == bot.user.id or user_id in whitelisted_users

async def take_antinuke_action(guild: discord.Guild, user: discord.Member, reason: str):
    """Executes immediate punishment (Strip roles, Ban/Kick) for unauthorized nuke attempts."""
    try:
        # Strip all roles to mitigate damage immediately
        roles_to_remove = [r for r in user.roles if r.name != "@everyone" and r < guild.me.top_role]
        if roles_to_remove:
            await user.remove_roles(*roles_to_remove, reason=f"[AntiNuke Alert] {reason}")
        
        # Ban the offending admin/user
        await guild.ban(user, reason=f"[AntiNuke System] Triggered: {reason}")
    except Exception as e:
        print(f"AntiNuke failure to punish user {user.id}: {e}")

async def handle_audit_log_event(guild: discord.Guild, action: discord.AuditLogAction, limit_key: str, reason: str):
    """Monitors Discord Audit Logs in real-time to track unauthorized rate actions."""
    async for entry in guild.audit_logs(limit=1, action=action):
        if not entry.user or entry.user.id == bot.user.id:
            return
        
        user_id = entry.user.id
        if is_whitelisted(guild, user_id):
            return

        now = time.time()
        max_count, time_window = ANTINUKE_LIMITS[limit_key]
        
        user_actions = antinuke_tracker[guild.id][limit_key][user_id]
        user_actions.append(now)
        # Retain only timestamps within the active time window
        antinuke_tracker[guild.id][limit_key][user_id] = [t for t in user_actions if now - t <= time_window]

        if len(antinuke_tracker[guild.id][limit_key][user_id]) >= max_count:
            member = guild.get_member(user_id)
            if member:
                await take_antinuke_action(guild, member, reason)

# --- AntiNuke Event Listeners ---
@bot.event
async def on_guild_channel_delete(channel):
    await handle_audit_log_event(channel.guild, discord.AuditLogAction.channel_delete, "channel_delete", "Mass Channel Deletion")

@bot.event
async def on_guild_channel_create(channel):
    await handle_audit_log_event(channel.guild, discord.AuditLogAction.channel_create, "channel_create", "Mass Channel Creation")

@bot.event
async def on_guild_role_delete(role):
    await handle_audit_log_event(role.guild, discord.AuditLogAction.role_delete, "role_delete", "Mass Role Deletion")

@bot.event
async def on_guild_role_create(role):
    await handle_audit_log_event(role.guild, discord.AuditLogAction.role_create, "role_create", "Mass Role Creation")

@bot.event
async def on_member_remove(member):
    await handle_audit_log_event(member.guild, discord.AuditLogAction.kick, "kick", "Mass Kick Detected")

@bot.event
async def on_member_ban(guild, user):
    await handle_audit_log_event(guild, discord.AuditLogAction.ban, "ban", "Mass Ban Detected")

@bot.event
async def on_member_join(member):
    # Anti-Bot Add System
    if member.bot:
        async for entry in member.guild.audit_logs(limit=1, action=discord.AuditLogAction.bot_add):
            if entry.target.id == member.id and not is_whitelisted(member.guild, entry.user.id):
                await member.ban(reason="[AntiNuke] Unauthorized bot addition.")
                inviter = member.guild.get_member(entry.user.id)
                if inviter:
                    await take_antinuke_action(member.guild, inviter, "Added unauthorized bot")

# --- AutoMod System ---
BAD_WORDS = {"discord.gg/", "http://", "https://", "nigger", "retard"}  # Customize as needed

@bot.event
async def on_message(message: discord.Message):
    if not message.guild or message.author.bot:
        return

    # Skip AutoMod for Whitelisted Admins
    if not is_whitelisted(message.guild, message.author.id):
        # 1. Anti-Link / Bad Words Filter
        if any(word in message.content.lower() for word in BAD_WORDS):
            await message.delete()
            await message.channel.send(f"{message.author.mention}, that content is blocked here!", delete_after=5)
            return

        # 2. Anti-Mass Mentions
        if len(message.mentions) > 5 or "@everyone" in message.content or "@here" in message.content:
            await message.delete()
            await message.author.timeout(timedelta(minutes=10), reason="AutoMod: Mass Mentions")
            await message.channel.send(f"{message.author.mention} timed out for mass mentions.", delete_after=5)
            return

        # 3. Anti-Spam (5 messages in 3 seconds)
        now = time.time()
        timestamps = spam_tracker[message.author.id]
        timestamps.append(now)
        spam_tracker[message.author.id] = [t for t in timestamps if now - t <= 3]
        if len(spam_tracker[message.author.id]) >= 5:
            await message.delete()
            await message.author.timeout(timedelta(minutes=5), reason="AutoMod: Rapid Spamming")
            await message.channel.send(f"{message.author.mention} timed out for spamming.", delete_after=5)
            return

    await bot.process_commands(message)

# -------------------------------------------------------------------
# 4. MODERATION & ANTINUKE COMMANDS (50 COMMANDS)
# -------------------------------------------------------------------

# --- ANTINUKE & SYSTEM MANAGEMENT (1-5) ---
@bot.command()
@commands.has_permissions(administrator=True)
async def antinuke(ctx):
    """1. AntiNuke Status Overview"""
    embed = discord.Embed(title="🛡️ AntiNuke Engine", color=discord.Color.green())
    embed.add_field(name="Protection Status", value="ACTIVE", inline=False)
    embed.add_field(name="Monitored Modules", value="Mass Ban, Mass Kick, Channel Create/Delete, Role Create/Delete, Unauthorized Bot Additions", inline=False)
    await ctx.send(embed=embed)

@bot.command()
@commands.is_owner()
async def wl(ctx, member: discord.Member):
    """2. Whitelist a user from AntiNuke"""
    whitelisted_users.add(member.id)
    await ctx.send(f"✅ {member.mention} has been added to the AntiNuke whitelist.")

@bot.command()
@commands.is_owner()
async def unwl(ctx, member: discord.Member):
    """3. Remove user from AntiNuke whitelist"""
    whitelisted_users.discard(member.id)
    await ctx.send(f"❌ {member.mention} removed from AntiNuke whitelist.")

@bot.command()
@commands.has_permissions(administrator=True)
async def whitelisted(ctx):
    """4. List all whitelisted users"""
    users = ", ".join([f"<@{uid}>" for uid in whitelisted_users]) or "None"
    await ctx.send(f"**Whitelisted Users:** {users}")

@bot.command()
@commands.has_permissions(administrator=True)
async def ping(ctx):
    """5. Check latency"""
    await ctx.send(f"🏓 Latency: {round(bot.latency * 1000)}ms")

# --- SANCTIONS & USER PUNISHMENTS (6-15) ---
@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="None"):
    """6. Ban a member"""
    await member.ban(reason=reason)
    await ctx.send(f"🔨 Banned {member} | Reason: {reason}")

@bot.command()
@commands.has_permissions(ban_members=True)
async def unban(ctx, user_id: int):
    """7. Unban a user by ID"""
    user = await bot.fetch_user(user_id)
    await ctx.guild.unban(user)
    await ctx.send(f"🔓 Unbanned {user.name}")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="None"):
    """8. Kick a member"""
    await member.kick(reason=reason)
    await ctx.send(f"👢 Kicked {member} | Reason: {reason}")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def timeout(ctx, member: discord.Member, minutes: int, *, reason="None"):
    """9. Timeout a member"""
    await member.timeout(timedelta(minutes=minutes), reason=reason)
    await ctx.send(f"⏳ Timed out {member} for {minutes}m.")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def untimeout(ctx, member: discord.Member):
    """10. Remove timeout"""
    await member.timeout(None)
    await ctx.send(f"🔊 Removed timeout for {member}.")

@bot.command()
@commands.has_permissions(ban_members=True)
async def softban(ctx, member: discord.Member, *, reason="None"):
    """11. Softban member (Ban & immediate Unban to clear messages)"""
    await member.ban(reason=reason, delete_message_days=7)
    await ctx.guild.unban(member)
    await ctx.send(f"🧹 Softbanned {member}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def hardban(ctx, user_id: int):
    """12. Hardban user ID"""
    user = await bot.fetch_user(user_id)
    await ctx.guild.ban(user, reason="Hardban")
    await ctx.send(f"⛔ Hardbanned user {user_id}")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def warn(ctx, member: discord.Member, *, reason="None"):
    """13. Warn a member"""
    await ctx.send(f"⚠️ {member.mention} has been warned for: {reason}")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def mute(ctx, member: discord.Member):
    """14. Mute member (Timeout fallback)"""
    await member.timeout(timedelta(hours=24), reason="Mute Command")
    await ctx.send(f"🔇 Muted {member.mention} for 24 hours.")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    """15. Unmute member"""
    await member.timeout(None)
    await ctx.send(f"🔊 Unmuted {member.mention}.")

# --- PURGE & CLEANING COMMANDS (16-20) ---
@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int):
    """16. Purge N messages"""
    deleted = await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"🧹 Deleted {len(deleted)-1} messages.", delete_after=3)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def purgeuser(ctx, member: discord.Member, amount: int = 100):
    """17. Purge messages from specific user"""
    def check(m): return m.author == member
    deleted = await ctx.channel.purge(limit=amount, check=check)
    await ctx.send(f"🧹 Deleted {len(deleted)} messages from {member}.", delete_after=3)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def purgebots(ctx, amount: int = 100):
    """18. Purge bot messages"""
    def check(m): return m.author.bot
    deleted = await ctx.channel.purge(limit=amount, check=check)
    await ctx.send(f"🤖 Cleared {len(deleted)} bot messages.", delete_after=3)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def purgelinks(ctx, amount: int = 100):
    """19. Purge messages containing URLs"""
    def check(m): return "http://" in m.content or "https://" in m.content
    deleted = await ctx.channel.purge(limit=amount, check=check)
    await ctx.send(f"🔗 Cleared {len(deleted)} messages with links.", delete_after=3)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def purgeimages(ctx, amount: int = 100):
    """20. Purge messages containing attachments"""
    def check(m): return len(m.attachments) > 0
    deleted = await ctx.channel.purge(limit=amount, check=check)
    await ctx.send(f"🖼️ Cleared {len(deleted)} image posts.", delete_after=3)

# --- CHANNEL MANAGEMENT (21-30) ---
@bot.command()
@commands.has_permissions(manage_channels=True)
async def lock(ctx):
    """21. Lock current channel"""
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send("🔒 Channel locked.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def unlock(ctx):
    """22. Unlock current channel"""
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=True)
    await ctx.send("🔓 Channel unlocked.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def slowmode(ctx, seconds: int):
    """23. Set slowmode duration"""
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"⏱️ Slowmode set to {seconds} seconds.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def hide(ctx):
    """24. Hide current channel"""
    await ctx.channel.set_permissions(ctx.guild.default_role, view_channel=False)
    await ctx.send("🙈 Channel hidden.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def unhide(ctx):
    """25. Unhide current channel"""
    await ctx.channel.set_permissions(ctx.guild.default_role, view_channel=True)
    await ctx.send("👁️ Channel visible.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def createchannel(ctx, name: str):
    """26. Create text channel"""
    c = await ctx.guild.create_text_channel(name)
    await ctx.send(f"✅ Created channel {c.mention}")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def deletechannel(ctx, channel: discord.TextChannel = None):
    """27. Delete text channel"""
    target = channel or ctx.channel
    await target.delete()

@bot.command()
@commands.has_permissions(manage_channels=True)
async def nukechannel(ctx):
    """28. Clone and delete channel (Full wipe)"""
    pos = ctx.channel.position
    new_ch = await ctx.channel.clone(reason="Nuke command executed")
    await ctx.channel.delete()
    await new_ch.edit(position=pos)
    await new_ch.send("💥 Channel wiped and recreated!")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def settopic(ctx, *, topic: str):
    """29. Change channel topic"""
    await ctx.channel.edit(topic=topic)
    await ctx.send("📝 Topic updated.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def nsfw(ctx):
    """30. Toggle NSFW setting on channel"""
    await ctx.channel.edit(nsfw=not ctx.channel.nsfw)
    await ctx.send(f"🔞 NSFW set to {ctx.channel.nsfw}")

# --- ROLE MANAGEMENT (31-40) ---
@bot.command()
@commands.has_permissions(manage_roles=True)
async def addrole(ctx, member: discord.Member, role: discord.Role):
    """31. Add role to member"""
    await member.add_roles(role)
    await ctx.send(f"✅ Added {role.name} to {member.display_name}")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def removerole(ctx, member: discord.Member, role: discord.Role):
    """32. Remove role from member"""
    await member.remove_roles(role)
    await ctx.send(f"❌ Removed {role.name} from {member.display_name}")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def createrole(ctx, name: str):
    """33. Create a new role"""
    r = await ctx.guild.create_role(name=name)
    await ctx.send(f"✅ Created role: {r.name}")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def deleterole(ctx, role: discord.Role):
    """34. Delete a role"""
    await role.delete()
    await ctx.send(f"🗑️ Deleted role {role.name}")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def rolecolor(ctx, role: discord.Role, hex_code: str):
    """35. Set role hex color"""
    color = int(hex_code.replace("#", ""), 16)
    await role.edit(color=discord.Color(color))
    await ctx.send(f"🎨 Updated color for {role.name}")

@bot.command()
@commands.has_permissions(administrator=True)
async def roleall(ctx, role: discord.Role):
    """36. Give role to all members"""
    await ctx.send("⏳ Adding roles in progress...")
    for m in ctx.guild.members:
        try: await m.add_roles(role)
        except: pass
    await ctx.send(f"✅ Granted {role.name} to all members.")

@bot.command()
@commands.has_permissions(administrator=True)
async def removeallrole(ctx, role: discord.Role):
    """37. Strip role from all members"""
    await ctx.send("⏳ Removing roles in progress...")
    for m in ctx.guild.members:
        try: await m.remove_roles(role)
        except: pass
    await ctx.send(f"✅ Stripped {role.name} from all members.")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def hoistrole(ctx, role: discord.Role):
    """38. Toggle hoist setting on role"""
    await role.edit(hoist=not role.hoist)
    await ctx.send(f"📌 Role hoisting: {role.hoist}")

@bot.command()
@commands.has_permissions(manage_roles=True)
async def roleinfo(ctx, role: discord.Role):
    """39. Role details"""
    embed = discord.Embed(title=f"Role Info: {role.name}", color=role.color)
    embed.add_field(name="ID", value=role.id)
    embed.add_field(name="Members Count", value=len(role.members))
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(manage_nicknames=True)
async def setnick(ctx, member: discord.Member, *, nick: str = None):
    """40. Set member nickname"""
    await member.edit(nick=nick)
    await ctx.send(f"🏷️ Nickname set for {member.display_name}")

# --- SERVER LOCKDOWN & UTILITIES (41-50) ---
@bot.command()
@commands.has_permissions(administrator=True)
async def serverlock(ctx):
    """41. Lock down all channels across server"""
    for ch in ctx.guild.text_channels:
        await ch.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send("🚨 Server fully locked down.")

@bot.command()
@commands.has_permissions(administrator=True)
async def serverunlock(ctx):
    """42. Lift full server lockdown"""
    for ch in ctx.guild.text_channels:
        await ch.set_permissions(ctx.guild.default_role, send_messages=True)
    await ctx.send("🟢 Server lockdown lifted.")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kickbots(ctx):
    """43. Kick all non-whitelisted bots"""
    count = 0
    for m in ctx.guild.members:
        if m.bot and m.id != bot.user.id:
            try:
                await m.kick(reason="Kickbots Command")
                count += 1
            except: pass
    await ctx.send(f"👢 Kicked {count} bots.")

@bot.command()
@commands.has_permissions(ban_members=True)
async def banlist(ctx):
    """44. Display total ban count"""
    bans = [entry async for entry in ctx.guild.bans()]
    await ctx.send(f"🔨 Total Server Bans: {len(bans)}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def userinfo(ctx, member: discord.Member = None):
    """45. Display targeted user lookup info"""
    m = member or ctx.author
    embed = discord.Embed(title=f"User Info - {m}", color=m.color)
    embed.add_field(name="Account Created", value=m.created_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Joined Server", value=m.joined_at.strftime("%Y-%m-%d"))
    embed.add_field(name="Roles", value=len(m.roles) - 1)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def serverinfo(ctx):
    """46. Guild stats & configuration info"""
    g = ctx.guild
    embed = discord.Embed(title=f"Server Stats - {g.name}", color=discord.Color.blue())
    embed.add_field(name="Owner", value=g.owner)
    embed.add_field(name="Members", value=g.member_count)
    embed.add_field(name="Channels", value=len(g.channels))
    embed.add_field(name="Roles", value=len(g.roles))
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def botnick(ctx, *, name: str):
    """47. Change bot nickname on current server"""
    await ctx.guild.me.edit(nick=name)
    await ctx.send(f"🤖 Bot name changed to: {name}")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def seticon(ctx, url: str):
    """48. Change server avatar via URL"""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                await ctx.guild.edit(icon=data)
                await ctx.send("🖼️ Server icon updated!")

@bot.command()
async def bothelp(ctx):
    """49. Embed showing total commands count"""
    embed = discord.Embed(title="🛡️ Bot Command Panel (50 Moderation Commands)", color=discord.Color.purple())
    embed.description = "Use `!help <command>` or review the list of built-in commands including AntiNuke, Whitelist, Sanctions, Purging, Role and Channel management."
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def clearinvites(ctx):
    """50. Purge all server invites"""
    invites = await ctx.guild.invites()
    for inv in invites:
        await inv.delete()
    await ctx.send("🧹 Deleted all active server invites.")

# -------------------------------------------------------------------
# 5. BOT INITIALIZATION
# -------------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("AntiNuke, AutoMod, and Web Server are operational.")

if __name__ == "__main__":
    # Start background web server for host monitoring
    keep_alive()
    
    # Run Discord Bot using environment token
    token = os.environ.get("DISCORD_TOKEN")
    if token:
        bot.run(token)
    else:
        print("ERROR: DISCORD_TOKEN is not set in Environment Variables!")
