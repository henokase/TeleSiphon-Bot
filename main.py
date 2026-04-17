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
        
        self.interaction_chat_id = chat_id
        self.interaction_msg_ids = []  # IDs for session cleanup
        self.summary_msg_id = None    # ID of the final completion message

# --- Core Instances ---
downloader = DownloadManager(client)

# --- Helper Functions ---

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
            await show_source_setup(event, state)
        elif input_text == "3" or input_upper == "X":
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
            await show_dest_setup(event, state)
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
            await proceed_to_media_or_topic(event, state)
        elif input_text == "2":
            state.destination = state.interaction_chat_id
            await proceed_to_media_or_topic(event, state)
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
            await proceed_to_media_or_topic(event, state)
        return

    # --- Routing: Filtering & Limit Selection ---
    if state.waiting_for == 'media':
        media_map = {"1": "Voices", "2": "Audios", "3": "Videos", "4": "Photos", "5": "Documents", "6": "All"}
        if input_text in media_map:
            state.media_type = media_map[input_text]
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

# --- UI Generation Helpers ---

async def show_initial_menu(event, state):
    state.waiting_for = 'init'
    text = (
        "**🚀 TeleSiphon - Initial Setup**\n\n"
        f"**Source:** `{state.source or 'Not Set'}`\n"
        f"**Destination:** `{state.destination or 'Not Set'}`\n\n"
        "**How would you like to proceed?**\n"
        "1️⃣ Continue with Defaults\n"
        "2️⃣ Change Settings\n"
        "3️⃣ Exit"
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

# --- Primary Mirroring Pipeline Engine ---

async def start_siphon_process(event, state):
    """
    Executes the mirroring process based on the confirmed session state.
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

    # Configuration for server-side filtering
    all_filters = {
        "Voices": InputMessagesFilterVoice(),
        "Audios": InputMessagesFilterMusic(),
        "Videos": InputMessagesFilterVideo(),
        "Photos": InputMessagesFilterPhotos(),
        "Documents": InputMessagesFilterDocument()
    }

    total_processed = 0

    if state.source_type == 'topic':
        # --- Logic: Topic-Specific Flow (Buffered Single Pass) ---
        current_header = f"🔄 **Scanning Topic...** (Limit: {state.limit} per type)"
        await status_msg.edit(current_header)
        
        categories_to_fetch = ["Voices", "Audios", "Videos", "Photos", "Documents"] if state.media_type == "All" else [state.media_type]
        media_buffer = {cat: [] for cat in categories_to_fetch}

        # Scan topic history and buffer requested media types
        async for message in client.iter_messages(source_entity, reply_to=state.topic_id):
            if all(len(msgs) >= state.limit for msgs in media_buffer.values()):
                break

            if not message.media:
                continue

            category = "Documents" # Fallback categorization
            if message.voice: category = "Voices"
            elif message.audio: category = "Audios"
            elif message.video: category = "Videos"
            elif message.photo: category = "Photos"

            if category in media_buffer and len(media_buffer[category]) < state.limit:
                media_buffer[category].append(message)

        # Process the collected buffer
        for category, messages in media_buffer.items():
            if not messages: continue
            
            for index, message in enumerate(messages, 1):
                current_status = f"🔄 **Mirroring {category}...** ({index}/{len(messages)})"
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
                        await status_msg.edit(f"📤 **Uploading {category}...**")
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
                        # Log error internally and continue
                        print(f"[ERROR] Mirroring failed: {e}")
                    finally:
                        if os.path.exists(local_path): os.remove(local_path)
                
                await asyncio.sleep(1.5)

    else:
        # --- Logic: Whole Group Flow (Efficient Server-Side Search) ---
        target_categories = [state.media_type] if state.media_type != "All" else all_filters.keys()
        
        for category in target_categories:
            msg_filter = all_filters[category]
            current_header = f"🔄 **Siphoning {category}...** (Limit: {state.limit})"
            await status_msg.edit(current_header)

            async for message in client.iter_messages(source_entity, filter=msg_filter, limit=state.limit):
                if not message.media: continue

                async def progress(current, total):
                    bar = get_progress_bar(current, total)
                    try: 
                        await status_msg.edit(f"{current_header}\n`{bar}`")
                    except Exception: 
                        pass

                local_path = await downloader.download_media_with_progress(message, progress_callback=progress)
                if local_path:
                    try:
                        from telethon_utils import fast_upload
                        await status_msg.edit(f"📤 **Uploading {category}...**")
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
                
                await asyncio.sleep(1.5)

    # Completion handling
    final_summary = f"🏁 **Siphon Complete!**\nTotal: `{total_processed}` mirrored to `{getattr(dest_entity, 'title', state.destination)}`."
    done_msg = await event.respond(final_summary)
    state.summary_msg_id = done_msg.id
    await register_msg(state, done_msg)

    # Automatic cleanup and reset
    await asyncio.sleep(3)
    await clear_traces(state)
    await show_initial_menu(event, state)
    await register_msg(state, done_msg)

    # Reset for another round
    await asyncio.sleep(3)
    await clear_traces(state)
    await show_initial_menu(event, state)
