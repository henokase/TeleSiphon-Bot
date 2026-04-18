"""
TeleSiphon Main Module
----------------------
This module implements the core interaction logic for the TeleSiphon UserBot.
It handles state management, interactive menu flows, forum topic discovery, 
and the primary mirroring pipeline (download -> parallel upload -> destination send).

Dependencies:
    - telethon: For Telegram MTProto interaction.
    - downloader: Custom local/cloud media manager.
    - telethon_utils: Optimized MTProto performance helpers.
"""

import os
import asyncio
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from telethon import events
from telethon.tl.types import (
    InputMessagesFilterMusic,
    InputMessagesFilterVideo,
    InputMessagesFilterVoice,
    InputMessagesFilterPhotos,
    InputMessagesFilterDocument,
    DocumentAttributeAudio
)
from telethon.tl.functions.messages import GetForumTopicsRequest
from downloader import DownloadManager
from bot_client import client

# --- Configuration & State ---
DEFAULT_SOURCE = os.getenv("DEFAULT_SOURCE")
DEFAULT_DESTINATION = os.getenv("DEFAULT_DESTINATION")
DOWNLOAD_LIMIT_ENV = int(os.getenv("DOWNLOAD_LIMIT", 0))

# Global registry to track per-user interaction states
user_states = {}

class SiphonState:
    """
    Tracks the configuration and interaction state for a single mirroring session.
    """
    def __init__(self, chat_id):
        """
        Initializes a new session state with defaults from environment variables.
        
        Args:
            chat_id (int): ID of the chat where the interaction was triggered.
        """
        self.source = DEFAULT_SOURCE
        self.destination = DEFAULT_DESTINATION
        
        # Sanitize and convert IDs from environment
        if isinstance(self.source, str) and self.source.replace('-', '').isdigit():
            self.source = int(self.source)
        if isinstance(self.destination, str) and self.destination.replace('-', '').isdigit():
            self.destination = int(self.destination)

        self.limit = DOWNLOAD_LIMIT_ENV or 5
        self.media_type = "All"
        self.waiting_for = None  # State machine pointer
        self.source_type = 'group' # 'group' or 'topic'
        self.topic_id = None
        self.available_topics = {} 
        
        self.date_mode = False
        self.start_date = None
        self.end_date = None
        
        self.interaction_chat_id = chat_id
        self.interaction_msg_ids = []  # IDs for session cleanup
        self.summary_msg_id = None    # ID of the final completion message

# --- Core Instances ---
downloader = DownloadManager(client)

# --- Helper Functions ---

async def get_chat_name(chat_id) -> str:
    """Resolves a chat ID to its display name (title or username)."""
    if not chat_id:
        return "Not Set"
    try:
        entity = await client.get_entity(chat_id)
        return getattr(entity, 'title', None) or getattr(entity, 'username', None) or str(chat_id)
    except Exception:
        return str(chat_id)

def parse_message_link(link: str) -> dict:
    """
    Parses a Telegram message link and extracts chat_id, message_id, and optionally topic_id.
    
    Supported formats:
    - https://t.me/c/chat_id/message_id
    - https://t.me/c/chat_id/topic_id/message_id
    - https://t.me/username/message_id
    
    Returns:
        dict with 'chat_id', 'message_id', 'topic_id' (optional), or None if invalid.
    """
    result = {'chat_id': None, 'message_id': None, 'topic_id': None}
    
    link = link.strip()
    if not link:
        return None
    
    parsed = urlparse(link)
    if parsed.netloc not in ('t.me', 'telegram.me', ''):
        return None
    
    path_parts = parsed.path.strip('/').split('/')
    
    if path_parts[0] == 'c' and len(path_parts) >= 3:
        try:
            result['chat_id'] = int(path_parts[1])
            if len(path_parts) >= 4:
                result['topic_id'] = int(path_parts[2])
                result['message_id'] = int(path_parts[3])
            else:
                result['message_id'] = int(path_parts[2])
        except (ValueError, IndexError):
            return None
    elif len(path_parts) >= 2:
        result['username'] = path_parts[0]
        try:
            result['message_id'] = int(path_parts[1])
        except (ValueError, IndexError):
            return None
    else:
        return None
    
    return result if result['message_id'] else None

async def get_message_by_link(link: str):
    """
    Fetches a message by its Telegram link.
    
    Args:
        link: Telegram message link (e.g., https://t.me/c/123456/1 or https://t.me/username/1)
    
    Returns:
        Telethon Message object, or None if not found/invalid.
    """
    parsed = parse_message_link(link)
    if not parsed:
        return None
    
    try:
        if 'username' in parsed:
            entity = await client.get_entity(parsed['username'])
        else:
            chat_id = parsed['chat_id']
            if chat_id > 0:
                entity = await client.get_entity(-1000000000000 - chat_id)
            else:
                entity = await client.get_entity(chat_id)
        
        msg = await client.get_messages(entity, ids=parsed['message_id'])
        
        if parsed.get('topic_id') and hasattr(entity, 'forum') and entity.forum:
            for reply in await client.iter_messages(entity, limit=50, reverse=False):
                if reply.reply_to and reply.reply_to.reply_to_msg_id == parsed['topic_id'] and reply.id == parsed['message_id']:
                    return reply
            return None
        
        return msg
    except Exception as e:
        print(f"[ERROR] Failed to get message by link {link}: {e}")
        return None

async def parse_and_fetch_messages(links_text: str) -> list:
    """
    Parses multiple message links (comma-separated) and fetches associated messages.
    
    Args:
        links_text: Comma-separated message links
        
    Returns:
        List of (Message, parsed_info) tuples.
    """
    messages = []
    links = [l.strip() for l in links_text.split(',') if l.strip()]
    
    for link in links:
        msg = await get_message_by_link(link)
        if msg:
            messages.append(msg)
    
    return messages

def parse_date_input(date_str: str, end_of_day: bool = False) -> datetime:
    """
    Parses various date input formats and returns a datetime object.
    
    Args:
        date_str: The date string to parse.
        end_of_day: If True, returns datetime at 23:59:59 (for end date).
    
    Supported formats:
    - YYYY-MM-DD, YYYY-M-DD, YYYY-MM-D
    - YY-MM-DD (year as 2000 + YY, e.g., 26 -> 2026)
    - MM-DD (use current year)
    - DD (use current year and month)
    
    Single digit months/days without leading zero are supported.
    """
    date_str = date_str.strip().replace('/', '-').replace('.', '-')
    
    parts = date_str.split('-')
    if not parts or not parts[0]:
        return None
    
    now = datetime.now()
    year = now.year
    month = now.month
    day = 1
    
    def parse_2digit(s: str) -> int:
        """Parses a string as 1-2 digit number."""
        if not s:
            return 1
        return int(s)
    
    if len(parts) == 1:
        day = parse_2digit(parts[0])
    elif len(parts) == 2:
        month = parse_2digit(parts[0])
        day = parse_2digit(parts[1])
    elif len(parts) >= 3:
        year_val = parse_2digit(parts[0])
        if year_val < 100:
            year = 2000 + year_val
        else:
            year = year_val
        month = parse_2digit(parts[1])
        day = parse_2digit(parts[2])
    
    try:
        if end_of_day:
            return datetime(year, month, day, 23, 59, 59)
        return datetime(year, month, day)
    except ValueError:
        day = min(day, 28)
        if end_of_day:
            return datetime(year, month, day, 23, 59, 59)
        return datetime(year, month, day)

def parse_date_range(date_text: str) -> tuple:
    """
    Parses date text that can be a single date or two dates separated by comma.
    
    Returns:
        (start_date, end_date) tuple of datetime objects.
        
    Logic:
        - Single date: start_date = end_date = that date (only that day)
        - "date1,date2": range from date1 to date2
        - "date1," (trailing comma): start_date to now
    """
    date_text = date_text.strip()
    
    has_trailing_comma = date_text.endswith(',')
    dates = [d.strip() for d in date_text.split(',') if d.strip()]
    
    if not dates:
        return None, None
    
    start_date = parse_date_input(dates[0])
    
    if len(dates) > 1:
        end_date = parse_date_input(dates[1], end_of_day=True)
    elif has_trailing_comma:
        end_date = datetime.now()
    else:
        end_date = parse_date_input(dates[0], end_of_day=True)
    
    return start_date, end_date

def is_within_date_range(msg_date: datetime, start_date: datetime, end_date: datetime) -> bool:
    """Checks if a message date is within the given range (inclusive)."""
    return start_date <= msg_date.replace(tzinfo=None) <= end_date.replace(tzinfo=None)

async def register_msg(state: SiphonState, msg):
    """
    Registers a message ID for subsequent session cleanup.
    
    Args:
        state (SiphonState): Current session state.
        msg: Telethon message object.
    """
    if msg and hasattr(msg, 'id'):
        state.interaction_msg_ids.append(msg.id)
    return msg

async def clear_traces(state: SiphonState, is_exit=False):
    """
    Bulk deletes interaction messages to keep the chat history clean.
    
    Args:
        state (SiphonState): Current session state.
        is_exit (bool): If True, deletes all messages including the summary.
    """
    if is_exit:
        ids_to_delete = state.interaction_msg_ids
        state.interaction_msg_ids = []
    else:
        # Keep the summary message visually present during resets
        ids_to_delete = [mid for mid in state.interaction_msg_ids if mid != state.summary_msg_id]
        state.interaction_msg_ids = [state.summary_msg_id] if state.summary_msg_id in state.interaction_msg_ids else []

    if ids_to_delete:
        try:
            await client.delete_messages(state.interaction_chat_id, ids_to_delete)
        except Exception:
            pass # Silent fail for cleanup in background

def get_progress_bar(current: int, total: int, length=10) -> str:
    """
    Generates a visual text-based progress bar.
    """
    filled_length = int(length * current // total)
    bar = '█' * filled_length + '░' * (length - filled_length)
    percent = f"{100 * (current / total):.1f}%"
    return f"[{bar}] {percent}"

async def get_owner_id():
    """Retrieves the authenticated user ID."""
    me = await client.get_me()
    return me.id

async def exit_session(event, state):
    """Exits the session and cleans up traces."""
    m = await event.respond("👋 Exiting TeleSiphon...")
    await register_msg(state, m)
    await asyncio.sleep(1)
    await clear_traces(state, is_exit=True)
    user_states.pop(event.sender_id, None)

# --- Command & Interaction Handlers ---

@client.on(events.NewMessage(pattern=r'\.siphon'))
async def siphon_cmd_handler(event):
    """Entry point for the .siphon command."""
    owner_id = await get_owner_id()
    if event.sender_id != owner_id:
        return

    # Clear any stale session state for the user
    old_state = user_states.get(event.sender_id)
    if old_state:
        await clear_traces(old_state, is_exit=True)

    state = SiphonState(event.chat_id)
    user_states[event.sender_id] = state
    await show_initial_menu(event, state)

@client.on(events.NewMessage)
async def unified_input_handler(event):
    """
    State machine for processing user replies to the interactive menu.
    """
    owner_id = await get_owner_id()
    if event.sender_id != owner_id:
        return

    state = user_states.get(event.sender_id)
    if not state or not state.waiting_for:
        return

    # Auto-register user's reply for later cleanup
    await register_msg(state, event)

    input_text = event.text.strip()
    input_upper = input_text.upper()

    # --- Routing: Initial Workflow ---
    if state.waiting_for == 'init':
        if input_text == "1":
            if not state.source or not state.destination:
                m = await event.respond("⚠️ Source and Destination must be set first!")
                await register_msg(state, m)
                return
            await proceed_to_media_or_topic(event, state)
        elif input_text == "2":
            state.waiting_for = 'msg_link_input'
            m = await event.respond(
                "📎 **Siphon by Message Link**\n\n"
                "Send the message link(s). Separate multiple links with commas.\n\n"
                "Example:\n"
                "```https://t.me/c/123456/1, https://t.me/c/123456/2```\n\n"
                "0️⃣ Back\n"
                "✖️ Exit (type `X`)"
            )
            await register_msg(state, m)
            return
        elif input_text == "3":
            state.waiting_for = 'date_input'
            m = await event.respond(
                "📅 **Siphon by Date Range**\n\n"
                "Enter the date range.\n\n"
                "**Single date:** YYYY-MM-DD → only that day\n"
                "**Date range:** YYYY-MM-DD, YYYY-MM-DD\n"
                "**To now:** Add trailing comma `YYYY-MM-DD,`\n\n"
                "Short formats:\n"
                "- `MM-DD` (only that month-day)\n"
                "- `DD` (only that day of current month)\n"
                "- `26-11-21` → 2026-11-21\n\n"
                "0️⃣ Back\n"
                "✖️ Exit (type `X`)"
            )
            await register_msg(state, m)
            return
        elif input_text == "4":
            await show_source_setup(event, state)
        elif input_upper == "X":
            await exit_session(event, state)
        return

    # Global Exit check for all other menus
    if input_upper == "X":
        await exit_session(event, state)
        return

    # --- Routing: Configuration Sub-flows ---
    if state.waiting_for == 'setup_source':
        if input_text == "1":
            await show_dest_setup(event, state)
        elif input_text == "2":
            state.waiting_for = 'source_input'
            m = await event.respond("👉 **Forward a message** from source, or paste ID/Username.\n(Type `0` to cancel)")
            await register_msg(state, m)
        elif input_text == "0":
            await show_initial_menu(event, state)
        return

    if state.waiting_for == 'source_input':
        if input_text == "0":
            await show_source_setup(event, state)
            return
        target = None
        if event.fwd_from:
            target = event.fwd_from.from_id or event.fwd_from.channel_id
        elif input_text.replace('-', '').isdigit():
            target = int(input_text)
        else:
            target = input_text
        
        if target:
            state.source = target
            await show_dest_setup(event, state)
        return

    if state.waiting_for == 'source_type':
        if input_text == "1":
            state.source_type = 'group'
            state.topic_id = None
            await show_media_menu(event, state)
        elif input_text == "2":
            await show_topic_selection(event, state)
        elif input_text == "0":
            await show_initial_menu(event, state)
        return

    if state.waiting_for == 'topic_selection':
        if input_text in state.available_topics:
            state.source_type = 'topic'
            state.topic_id = state.available_topics[input_text]
            await show_media_menu(event, state)
        elif input_text == "0":
            await show_source_type_menu(event, state)
        return

    if state.waiting_for == 'setup_dest':
        if input_text == "1":
            await show_initial_menu(event, state)
        elif input_text == "2":
            state.destination = state.interaction_chat_id
            await show_initial_menu(event, state)
        elif input_text == "3":
            state.waiting_for = 'dest_input'
            m = await event.respond("👉 **Forward a message** from destination, or paste ID/Username.\n(Type `0` to cancel)")
            await register_msg(state, m)
        elif input_text == "0":
            await show_source_setup(event, state)
        return

    if state.waiting_for == 'dest_input':
        if input_text == "0":
            await show_dest_setup(event, state)
            return
        target = None
        if event.fwd_from:
            target = event.fwd_from.from_id or event.fwd_from.channel_id
        elif input_text.replace('-', '').isdigit():
            target = int(input_text)
        else:
            target = input_text
        
        if target:
            state.destination = target
            await show_initial_menu(event, state)
        return

    # --- Routing: Filtering & Limit Selection ---
    if state.waiting_for == 'media':
        media_map = {"1": "Voices", "2": "Audios", "3": "Videos", "4": "Photos", "5": "Documents", "6": "All"}
        if input_text in media_map:
            state.media_type = media_map[input_text]
            if state.date_mode:
                state.limit = 999999
                await start_siphon_process(event, state)
            else:
                await show_limit_menu(event, state)
        elif input_text == "0":
            await proceed_to_media_or_topic(event, state)
        return

    if state.waiting_for == 'limit':
        limit_presets = {"A": 5, "B": 10, "C": 50, "D": 100}
        if input_upper in limit_presets:
            state.limit = limit_presets[input_upper]
            await start_siphon_process(event, state)
        elif input_text.isdigit():
            state.limit = int(input_text)
            await start_siphon_process(event, state)
        elif input_text == "0":
            await show_media_menu(event, state)
        return

    if state.waiting_for == 'msg_link_input':
        if input_text == "0":
            await show_initial_menu(event, state)
            return
        
        await process_message_links(event, state, input_text)
        return

    if state.waiting_for == 'date_input':
        if input_text == "0":
            await show_initial_menu(event, state)
            return
        
        start_date, end_date = parse_date_range(input_text)
        
        if not start_date:
            m = await event.respond("❌ Invalid date format. Try again.")
            await register_msg(state, m)
            return
        
        state.date_mode = True
        state.start_date = start_date
        state.end_date = end_date
        
        await proceed_to_media_or_topic(event, state)
        return

# --- UI Generation Helpers ---

async def show_initial_menu(event, state):
    state.waiting_for = 'init'
    source_name = await get_chat_name(state.source) if state.source else "Not Set"
    dest_name = await get_chat_name(state.destination) if state.destination else "Not Set"
    text = (
        "**🚀 TeleSiphon - Control Panel**\n\n"
        f"**Source:** `{source_name}`\n"
        f"**Destination:** `{dest_name}`\n\n"
        "**Select Action:**\n"
        "1️⃣ Siphon Media\n"
        "2️⃣ Siphon by Message Link\n"
        "3️⃣ Siphon by Date Range\n"
        "4️⃣ Change Source & Destination\n\n"
        "✖️ Exit (type `X`)"
    )
    msg = await event.respond(text)
    await register_msg(state, msg)

async def proceed_to_media_or_topic(event, state):
    """Detects forum-enabled groups and branches the flow accordingly."""
    try:
        status = await event.respond("🔍 **Checking source features...**")
        await register_msg(state, status)
        
        entity = await client.get_entity(state.source)
        is_forum = getattr(entity, 'forum', False)
        
        await client.delete_messages(state.interaction_chat_id, [status.id])
        if status.id in state.interaction_msg_ids:
            state.interaction_msg_ids.remove(status.id)

        if is_forum:
            await show_source_type_menu(event, state)
        else:
            state.source_type = 'group'
            state.topic_id = None
            await show_media_menu(event, state)
    except Exception:
        # Fallback to standard group behavior if entity resolution fails
        await show_media_menu(event, state)

async def show_source_type_menu(event, state):
    state.waiting_for = 'source_type'
    text = (
        "**📚 Forum Detected!**\n\n"
        "How would you like to siphon?\n"
        "1️⃣ Whole Group (Everything)\n"
        "2️⃣ Specific Topic\n\n"
        "0️⃣ Back\n"
        "✖️ Exit (type `X`)"
    )
    msg = await event.respond(text)
    await register_msg(state, msg)

async def show_topic_selection(event, state):
    """Fetches and displays available topics for selection."""
    state.waiting_for = 'topic_selection'
    status = await event.respond("🔍 **Fetching topics...**")
    await register_msg(state, status)
    
    try:
        result = await client(GetForumTopicsRequest(
            peer=state.source,
            offset_date=None,
            offset_id=0,
            offset_topic=0,
            limit=50
        ))
        
        if not result.topics:
            await status.edit("⚠️ No topics found. Proceeding with Whole Group.")
            state.source_type = 'group'
            await asyncio.sleep(2)
            await show_media_menu(event, state)
            return

        state.available_topics = {}
        lines = ["**📁 Select a Topic:**\n"]
        for idx, topic in enumerate(result.topics, 1):
            state.available_topics[str(idx)] = topic.id
            lines.append(f"{idx}️⃣ {topic.title}")
        
        lines.append("\n0️⃣ Back")
        lines.append("✖️ Exit (type `X`)")
        await status.edit("\n".join(lines))
    except Exception as e:
        await status.edit(f"❌ Error fetching topics: {e}")
        state.source_type = 'group'
        await asyncio.sleep(2)
        await show_media_menu(event, state)

async def show_source_setup(event, state):
    state.waiting_for = 'setup_source'
    text = (
        f"**🔄 Source Setup** (Current: `{state.source}`)\n\n"
        "1️⃣ Keep Current\n"
        "2️⃣ Enter New ID / Forward Message\n\n"
        "0️⃣ Back\n"
        "✖️ Exit (type `X`)"
    )
    msg = await event.respond(text)
    await register_msg(state, msg)

async def show_dest_setup(event, state):
    state.waiting_for = 'setup_dest'
    text = (
        f"**🎯 Destination Setup** (Current: `{state.destination}`)\n\n"
        "1️⃣ Keep Current\n"
        "2️⃣ Use Current Chat (where we are now)\n"
        "3️⃣ Enter New ID / Forward Message\n\n"
        "0️⃣ Back\n"
        "✖️ Exit (type `X`)"
    )
    msg = await event.respond(text)
    await register_msg(state, msg)

async def show_media_menu(event, state):
    state.waiting_for = 'media'
    text = (
        f"🎙 **Select Media for {state.source}**\n\n"
        "1️⃣ Voices\n"
        "2️⃣ Audios\n"
        "3️⃣ Videos\n"
        "4️⃣ Images\n"
        "5️⃣ Documents (PDF, Zip, etc.)\n"
        "6️⃣ All\n\n"
        "0️⃣ Back\n"
        "✖️ Exit (type `X`)"
    )
    msg = await event.respond(text)
    await register_msg(state, msg)

async def show_limit_menu(event, state):
    state.waiting_for = 'limit'
    text = (
        f"📊 **Limit for {state.media_type}:**\n\n"
        "🇦 5 latest\n"
        "🇧 10 latest\n"
        "🇨 50 latest\n"
        "🇩 100 latest\n"
        "💬 Or just type a **Custom Number** (e.g. `1` or `200`).\n\n"
        "0️⃣ Back\n"
        "✖️ Exit (type `X`)"
    )
    msg = await event.respond(text)
    await register_msg(state, msg)

# --- Message Link Processing ---

async def process_message_links(event, state, links_text: str):
    """
    Processes message links and downloads media to destination.
    """
    state.waiting_for = None
    status_msg = await event.respond("🔗 **Parsing message links...**")
    await register_msg(state, status_msg)
    
    links = [l.strip() for l in links_text.split(',') if l.strip()]
    
    if not links:
        await status_msg.edit("❌ No valid links provided.")
        await asyncio.sleep(2)
        await show_initial_menu(event, state)
        return
    
    messages = []
    failed_links = []
    
    for link in links:
        msg = await get_message_by_link(link)
        if msg and msg.media:
            messages.append(msg)
        else:
            failed_links.append(link)
    
    if not messages:
        await status_msg.edit(f"❌ No media found in provided links.")
        if failed_links:
            print(f"[WARN] Failed links: {failed_links}")
        await asyncio.sleep(2)
        await show_initial_menu(event, state)
        return
    
    dest_entity = None
    try:
        dest_entity = await client.get_entity(state.destination)
    except Exception as e:
        await status_msg.edit(f"❌ **Error resolving destination:** {e}")
        return
    
    total_processed = 0
    
    await status_msg.edit(f"📥 **Found {len(messages)} media file(s). Starting download...**")
    
    for index, message in enumerate(messages, 1):
        current_status = f"📥 **Downloading...** ({index}/{len(messages)})"
        await status_msg.edit(current_status)
        
        async def progress(current, total):
            bar = get_progress_bar(current, total)
            try:
                await status_msg.edit(f"{current_status}\n`{bar}`")
            except Exception:
                pass
        
        local_path = await downloader.download_media_with_progress(message, progress_callback=progress)
        
        if local_path:
            try:
                from telethon_utils import fast_upload
                await status_msg.edit(f"📤 **Uploading...** ({index}/{len(messages)})")
                uploaded_file = await fast_upload(client, local_path, workers=4, progress_callback=progress)
                await status_msg.edit(f"🛰 **Finalizing...**\n`{os.path.basename(local_path)}`")
                
                is_voice = bool(message.voice)
                doc_attrs = message.media.document.attributes if hasattr(message.media, 'document') else None
                
                await client.send_file(
                    dest_entity,
                    uploaded_file,
                    caption=message.message,
                    formatting_entities=message.entities,
                    voice_note=is_voice,
                    attributes=doc_attrs,
                    supports_streaming=True if message.video else False
                )
                total_processed += 1
            except Exception as e:
                print(f"[ERROR] Mirroring failed: {e}")
            finally:
                if os.path.exists(local_path):
                    os.remove(local_path)
        
        await asyncio.sleep(1)
    
    final_summary = f"🏁 **Siphon Complete!**\nTotal: `{total_processed}` file(s) mirrored."
    done_msg = await event.respond(final_summary)
    state.summary_msg_id = done_msg.id
    await register_msg(state, done_msg)
    
    await asyncio.sleep(3)
    await clear_traces(state)
    await show_initial_menu(event, state)

# --- Primary Mirroring Pipeline Engine ---

async def process_photos_with_albums(event, status_msg, dest_entity, photo_messages):
    """
    Process photo messages, handling albums and individual photos separately.
    Albums are sent as groups, individual photos as single files.
    Order: albums first (chronological), then individual photos.
    """
    if not photo_messages:
        return 0
    
    albums = {}
    individuals = []
    
    for msg in photo_messages:
        gid = msg.grouped_id
        if gid:
            if gid not in albums:
                albums[gid] = []
            albums[gid].append(msg)
        else:
            individuals.append(msg)
    
    total = 0
    all_items = []
    
    for gid, msgs in albums.items():
        all_items.append(('album', msgs))
    
    for msg in individuals:
        all_items.append(('individual', [msg]))
    
    def get_item_date(item):
        item_data = item[1]
        if isinstance(item_data, list):
            return item_data[0].date if item_data[0].date else datetime.min
        return item_data.date if item_data.date else datetime.min
    
    all_items.sort(key=get_item_date)
    
    total_count = len(all_items)
    
    for idx, (item_type, msgs) in enumerate(all_items, 1):
        if item_type == 'album':
            caption = msgs[0].message
            entities = msgs[0].entities
            
            current_status = f"🖼️ **Sending Album ({len(msgs)} photos)...** ({idx}/{total_count})"
            await status_msg.edit(current_status)
            
            file_paths = []
            for m in msgs:
                path = await downloader.download_media_with_progress(m)
                if path:
                    file_paths.append(path)
            
            if file_paths:
                try:
                    uploaded = []
                    for fp in file_paths:
                        from telethon_utils import fast_upload
                        upl = await fast_upload(client, fp, workers=4)
                        uploaded.append(upl)
                        os.remove(fp)
                    
                    await client.send_file(
                        dest_entity,
                        uploaded,
                        caption=caption,
                        formatting_entities=entities
                    )
                    total += len(uploaded)
                except Exception as e:
                    print(f"[ERROR] Album send failed: {e}")
                finally:
                    for fp in file_paths:
                        if os.path.exists(fp):
                            os.remove(fp)
        else:
            msg = msgs[0]
            current_status = f"🖼️ **Sending Photo...** ({idx}/{total_count})"
            await status_msg.edit(current_status)
            
            async def progress(current, total):
                bar = get_progress_bar(current, total)
                try:
                    await status_msg.edit(f"{current_status}\n`{bar}`")
                except Exception:
                    pass
            
            local_path = await downloader.download_media_with_progress(msg, progress_callback=progress)
            if local_path:
                try:
                    from telethon_utils import fast_upload
                    await status_msg.edit(f"📤 **Uploading Photo...**")
                    uploaded = await fast_upload(client, local_path, workers=4, progress_callback=progress)
                    
                    await client.send_file(
                        dest_entity,
                        uploaded,
                        caption=msg.message,
                        formatting_entities=msg.entities
                    )
                    total += 1
                except Exception as e:
                    print(f"[ERROR] Photo send failed: {e}")
                finally:
                    if os.path.exists(local_path):
                        os.remove(local_path)
        
        await asyncio.sleep(1)
    
    return total

async def start_siphon_process(event, state):
    """
    Executes the mirroring process based on the confirmed session state.
    Supports date range filtering when state.date_mode is True.
    """
    state.waiting_for = None
    status_msg = await event.respond("🔍 **Resolving targets...**")
    await register_msg(state, status_msg)

    try:
        source_entity = await client.get_entity(state.source)
        dest_entity = await client.get_entity(state.destination)
    except Exception as e:
        await status_msg.edit(f"❌ **Error resolving entities:** {e}")
        return

    all_filters = {
        "Voices": InputMessagesFilterVoice(),
        "Audios": InputMessagesFilterMusic(),
        "Videos": InputMessagesFilterVideo(),
        "Photos": InputMessagesFilterPhotos(),
        "Documents": InputMessagesFilterDocument()
    }

    total_processed = 0
    is_date_mode = getattr(state, 'date_mode', False)
    start_date = getattr(state, 'start_date', None)
    end_date = getattr(state, 'end_date', datetime.now())
    
    if is_date_mode:
        start_str = start_date.strftime("%Y-%m-%d") if start_date else "beginning"
        end_str = end_date.strftime("%Y-%m-%d") if end_date else "now"
        await status_msg.edit(f"📅 **Filtering by date:** {start_str} → {end_str}")

    def filter_by_date(message) -> bool:
        """Date filter for messages (inclusive of both start and end)."""
        if not is_date_mode or not start_date or not end_date:
            return True
        msg_dt = message.date
        if msg_dt is None:
            return False
        return start_date.replace(tzinfo=None) <= msg_dt.replace(tzinfo=None) <= end_date.replace(tzinfo=None)

    if state.source_type == 'topic':
        current_header = f"🔄 **Scanning Topic...**"
        if is_date_mode:
            current_header += f" ({start_date.strftime('%Y-%m-%d')} - {end_date.strftime('%Y-%m-%d')})"
        await status_msg.edit(current_header)
        
        categories_to_fetch = ["Voices", "Audios", "Videos", "Photos", "Documents"] if state.media_type == "All" else [state.media_type]
        
        category_messages = {cat: [] for cat in categories_to_fetch}
        
        async for message in client.iter_messages(source_entity, reply_to=state.topic_id, reverse=is_date_mode):
            if not message.media:
                continue
            if not filter_by_date(message):
                continue
            
            category = "Documents"
            if message.voice: category = "Voices"
            elif message.audio: category = "Audios"
            elif message.video: category = "Videos"
            elif message.photo: category = "Photos"
            
            if category not in categories_to_fetch:
                continue
            
            category_messages[category].append(message)
            
            if not is_date_mode:
                total_count = sum(len(v) for v in category_messages.values())
                if total_count >= state.limit * len(categories_to_fetch):
                    break
        
        for category in categories_to_fetch:
            if category == "Photos":
                photo_msgs = category_messages["Photos"]
                if photo_msgs:
                    photo_msgs.sort(key=lambda m: m.date, reverse=False)
                    photo_count = await process_photos_with_albums(event, status_msg, dest_entity, photo_msgs)
                    total_processed += photo_count
            else:
                msgs = category_messages[category]
                msgs.sort(key=lambda m: m.date, reverse=False)
                
                for index, message in enumerate(msgs, 1):
                    current_status = f"🔄 **Mirroring {category}...** ({index}/{len(msgs)})"
                    await status_msg.edit(current_status)

                    async def progress(current, total):
                        bar = get_progress_bar(current, total)
                        try: 
                            await status_msg.edit(f"{current_status}\n`{bar}`")
                        except Exception: 
                            pass

                    local_path = await downloader.download_media_with_progress(message, progress_callback=progress)
                    if local_path:
                        try:
                            from telethon_utils import fast_upload
                            await status_msg.edit(f"📤 **Uploading {category}...** ({index}/{len(msgs)})")
                            uploaded_file = await fast_upload(client, local_path, workers=4, progress_callback=progress)
                            await status_msg.edit(f"🛰 **Finalizing Mirror...**\n`{os.path.basename(local_path)}`")
                            
                            is_voice = category == "Voices"
                            doc_attrs = message.media.document.attributes if hasattr(message.media, 'document') else None
                            
                            await client.send_file(
                                dest_entity,
                                uploaded_file,
                                caption=message.message,
                                formatting_entities=message.entities,
                                voice_note=is_voice,
                                attributes=doc_attrs,
                                supports_streaming=True if category == "Videos" else False
                            )
                            total_processed += 1
                        except Exception as e:
                            print(f"[ERROR] Mirroring failed: {e}")
                        finally:
                            if os.path.exists(local_path): os.remove(local_path)
                    
                    await asyncio.sleep(1)
    else:
        target_categories = ["Voices", "Audios", "Videos", "Photos", "Documents"] if state.media_type == "All" else [state.media_type]
        
        category_messages = {cat: [] for cat in target_categories}
        
        for category in target_categories:
            msg_filter = all_filters[category]
            current_header = f"🔄 **Siphoning {category}...**"
            if is_date_mode:
                current_header += f" ({start_date.strftime('%Y-%m-%d')} - {end_date.strftime('%Y-%m-%d')})"
            await status_msg.edit(current_header)
            
            count = 0
            async for message in client.iter_messages(source_entity, filter=msg_filter, reverse=is_date_mode):
                if not message.media:
                    continue
                if not filter_by_date(message):
                    continue
                
                category_messages[category].append(message)
                count += 1
                
                if not is_date_mode and count >= state.limit:
                    break
        
        for category in target_categories:
            if category == "Photos":
                photo_msgs = category_messages["Photos"]
                if photo_msgs:
                    photo_msgs.sort(key=lambda m: m.date, reverse=False)
                    photo_count = await process_photos_with_albums(event, status_msg, dest_entity, photo_msgs)
                    total_processed += photo_count
            else:
                msgs = category_messages[category]
                msgs.sort(key=lambda m: m.date, reverse=False)
                
                for index, message in enumerate(msgs, 1):
                    current_status = f"🔄 **Mirroring {category}...** ({index}/{len(msgs)})"
                    await status_msg.edit(current_status)

                    async def progress(current, total):
                        bar = get_progress_bar(current, total)
                        try: 
                            await status_msg.edit(f"{current_status}\n`{bar}`")
                        except Exception: 
                            pass

                    local_path = await downloader.download_media_with_progress(message, progress_callback=progress)
                    if local_path:
                        try:
                            from telethon_utils import fast_upload
                            await status_msg.edit(f"📤 **Uploading {category}...** ({index}/{len(msgs)})")
                            uploaded_file = await fast_upload(client, local_path, workers=4, progress_callback=progress)
                            await status_msg.edit(f"🛰 **Finalizing Mirror...**\n`{os.path.basename(local_path)}`")
                            
                            doc_attrs = message.media.document.attributes if hasattr(message.media, 'document') else None
                            is_voice = category == "Voices"
                            
                            await client.send_file(
                                dest_entity, 
                                uploaded_file, 
                                caption=message.message, 
                                formatting_entities=message.entities, 
                                voice_note=is_voice, 
                                attributes=doc_attrs, 
                                supports_streaming=True if category == "Videos" else False
                            )
                            total_processed += 1
                        except Exception as e:
                            print(f"[ERROR] Siphon failed: {e}")
                        finally:
                            if os.path.exists(local_path): os.remove(local_path)
                    
                    await asyncio.sleep(1)

    final_summary = f"🏁 **Siphon Complete!**\nTotal: `{total_processed}` mirrored to `{getattr(dest_entity, 'title', state.destination)}`."
    done_msg = await event.respond(final_summary)
    state.summary_msg_id = done_msg.id
    await register_msg(state, done_msg)

    await asyncio.sleep(3)
    await clear_traces(state)
    state.interaction_msg_ids = [state.summary_msg_id]
    await show_initial_menu(event, state)
