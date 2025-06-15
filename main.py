import discord
from discord.ext import commands
from discord.ui import Button, View
from dotenv import load_dotenv
import json
import os
import asyncio
import re

load_dotenv()

PREFIX = "st!"
SETUP_FILE = "ticket_setup.json"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# Load setup data
def load_setup_data():
    if not os.path.exists(SETUP_FILE):
        return {}
    with open(SETUP_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_setup_data(data):
    with open(SETUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

setup_data = load_setup_data()

def generate_ticket_channel_name(user: discord.User):
    clean_name = "".join(c for c in user.name.lower() if c.isalnum())
    return f"ticket-{clean_name}-{user.discriminator}"

async def can_manage_tickets(interaction: discord.Interaction):
    perms = interaction.channel.permissions_for(interaction.user)
    return perms.administrator or perms.manage_channels or interaction.user.guild_permissions.administrator

# View for the ticket panel (only Create Ticket button)
class TicketPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.success, custom_id="open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_ticket(interaction)

# View for inside a ticket (Close, Save Transcript, Delete)
class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        if not await can_manage_tickets(interaction):
            await interaction.response.send_message("You do not have permission to close this ticket.", ephemeral=True)
            return
        overwrite = channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        try:
            await channel.edit(name=f"closed-{channel.name}")
        except Exception as e:
            await interaction.followup.send(f"Failed to rename channel: {e}", ephemeral=True)
        await interaction.response.send_message("Ticket closed and renamed.", ephemeral=True)
        await log_action(interaction.guild.id, f"Ticket {channel.name} closed by {interaction.user.mention}")

    @discord.ui.button(label="Save Transcript", style=discord.ButtonStyle.primary, custom_id="save_transcript")
    async def save_transcript_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await can_manage_tickets(interaction):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        messages = []
        async for message in interaction.channel.history(limit=None, oldest_first=True):
            timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            author = f"{message.author}#{message.author.discriminator}"
            content = message.content
            attachments = ""
            if message.attachments:
                attachments = " [Attachments: " + ", ".join(a.url for a in message.attachments) + "]"
            messages.append(f"[{timestamp}] {author}: {content}{attachments}")
        transcript = "\n".join(messages)
        filename = f"transcript_{interaction.channel.name}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(transcript)
        await interaction.followup.send(file=discord.File(filename), ephemeral=True)
        os.remove(filename)

    @discord.ui.button(label="Delete Ticket", style=discord.ButtonStyle.secondary, custom_id="delete_ticket")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await can_manage_tickets(interaction):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return
        await interaction.response.send_message("Deleting in 5 seconds...", ephemeral=True)
        await asyncio.sleep(5)
        await log_action(interaction.guild.id, f"Ticket {interaction.channel.name} deleted by {interaction.user.mention}")
        await interaction.channel.delete()

# Logging
async def log_action(guild_id, message):
    log_id = setup_data.get(str(guild_id), {}).get("log_channel")
    if log_id:
        channel = bot.get_channel(log_id)
        if channel:
            await channel.send(message)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx, category_id: int, panel_channel: discord.TextChannel, *, welcome_text: str = None):
    category = ctx.guild.get_channel(category_id)
    if not category or category.type != discord.ChannelType.category:
        await ctx.send("Invalid category ID.")
        return
    if not welcome_text:
        welcome_text = "Welcome {user}"
    if "{user}" not in welcome_text:
        await ctx.send("Welcome text must contain '{user}'.")
        return
    setup_data[str(ctx.guild.id)] = {
        "category_id": category_id,
        "panel_channel_id": panel_channel.id,
        "welcome_text": welcome_text
    }
    save_setup_data(setup_data)
    await panel_channel.send("Click below to open a support ticket:", view=TicketPanelView())
    await ctx.send(f"Setup complete. Panel posted in {panel_channel.mention}.")

@bot.command()
@commands.has_permissions(administrator=True)
async def addstaff(ctx, *roles: discord.Role):
    setup_data.setdefault(str(ctx.guild.id), {})["staff_roles"] = [r.id for r in roles]
    save_setup_data(setup_data)
    await ctx.send("Staff roles updated.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setlog(ctx, channel: discord.TextChannel):
    setup_data.setdefault(str(ctx.guild.id), {})["log_channel"] = channel.id
    save_setup_data(setup_data)
    await ctx.send(f"Log channel set to {channel.mention}")

async def create_ticket(interaction: discord.Interaction):
    ctx = interaction
    guild_id = str(interaction.guild.id)
    category_id = setup_data[guild_id].get("category_id")
    welcome_text = setup_data[guild_id].get("welcome_text", "Welcome {user}")
    category = interaction.guild.get_channel(category_id)
    existing = discord.utils.get(interaction.guild.channels, name=generate_ticket_channel_name(interaction.user))
    if existing:
        await interaction.response.send_message(f"You already have a ticket: {existing.mention}", ephemeral=True)
        return
    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
    }
    for role_id in setup_data[guild_id].get("staff_roles", []):
        role = interaction.guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    ticket_chan = await interaction.guild.create_text_channel(
        generate_ticket_channel_name(interaction.user), category=category, overwrites=overwrites)
    await ticket_chan.send(welcome_text.replace("{user}", interaction.user.mention), view=TicketView())
    await interaction.response.send_message(f"Ticket created: {ticket_chan.mention}", ephemeral=True)
    await log_action(interaction.guild.id, f"🎟️ Ticket created: {ticket_chan.mention} by {interaction.user.mention}")

@bot.command()
async def adduser(ctx, member: discord.Member):
    if ctx.channel.name.startswith("ticket-"):
        await ctx.channel.set_permissions(member, view_channel=True, send_messages=True, read_message_history=True)
        await ctx.send(f"{member.mention} added to ticket.")

bot.run(os.getenv("DISCORD_TOKEN"))
