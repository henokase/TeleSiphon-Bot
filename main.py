import os
import asyncio
import re
from telethon import events
from telethon.tl.types import (
    InputMessagesFilterMusic,
    InputMessagesFilterVideo,
    InputMessagesFilterVoice,
    InputMessagesFilterPhotos,
    DocumentAttributeAudio
)
from downloader import DownloadManager
from bot_client import client

# Shared Configuration from ENV
DEFAULT_SOURCE = os.getenv("DEFAULT_SOURCE")
DEFAULT_DESTINATION = os.getenv("DEFAULT_DESTINATION")
DOWNLOAD_LIMIT_ENV = int(os.getenv("DOWNLOAD_LIMIT", 0))

# State Management for Setup
user_states = {}

class SiphonState:
    def __init__(self, chat_id):
        # Initial values from .env
        self.source = DEFAULT_SOURCE
        self.destination = DEFAULT_DESTINATION
        
        # Try to convert string IDs from .env to integers
        if isinstance(self.source, str) and self.source.replace('-', '').isdigit():
            self.source = int(self.source)
        if isinstance(self.destination, str) and self.destination.replace('-', '').isdigit():
            self.destination = int(self.destination)

        self.limit = DOWNLOAD_LIMIT_ENV or 5
        self.media_type = "All"
        self.waiting_for = None  # 'init', 'setup_source', 'setup_dest', 'media', 'limit', 'source_input', 'dest_input'
        self.interaction_chat_id = chat_id
        self.interaction_msg_ids = []  # IDs of all bot messages to delete later
        self.summary_msg_id = None    # Special ID for the "Done" message to keep during reset

downloader = DownloadManager(client)

async def register_msg(state, msg):
    """Adds a message ID to the tracking list for deletion."""
    if msg and hasattr(msg, 'id'):
        state.interaction_msg_ids.append(msg.id)
    return msg

async def clear_traces(state, is_exit=False):
    """Deletes all tracked messages in the current session."""
    if is_exit:
        # Delete absolutely everything
        ids_to_delete = state.interaction_msg_ids
        state.interaction_msg_ids = []
    else:
        # Delete everything except the summary (if it exists)
        ids_to_delete = [mid for mid in state.interaction_msg_ids if mid != state.summary_msg_id]
        if state.summary_msg_id in state.interaction_msg_ids:
            state.interaction_msg_ids = [state.summary_msg_id]
        else:
            state.interaction_msg_ids = []

    if ids_to_delete:
        try:
            await client.delete_messages(state.interaction_chat_id, ids_to_delete)
        except Exception as e:
            print(f"Cleanup error: {e}")

def get_progress_bar(current, total, length=10):
    """Generates a simple text-based progress bar."""
    filled_length = int(length * current // total)
    bar = '█' * filled_length + '░' * (length - filled_length)
    percent = f"{100 * (current / total):.1f}%"
    return f"[{bar}] {percent}"

async def get_owner_id():
    me = await client.get_me()
    return me.id

@client.on(events.NewMessage(pattern=r'\.siphon'))
async def siphon_cmd_handler(event):
    owner_id = await get_owner_id()
    if event.sender_id != owner_id:
        return

    # Clean up previous session if any
    old_state = user_states.get(event.sender_id)
    if old_state:
        await clear_traces(old_state, is_exit=True)

    state = SiphonState(event.chat_id)
    user_states[event.sender_id] = state
    await show_initial_menu(event, state)

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

@client.on(events.NewMessage)
async def unified_input_handler(event):
    owner_id = await get_owner_id()
    if event.sender_id != owner_id:
        return

    state = user_states.get(event.sender_id)
    if not state or not state.waiting_for:
        return

    # Track user's own input message for deletion
    await register_msg(state, event)

    inp = event.text.strip()
    inp_upper = inp.upper()

    # 1. Initial Menu
    if state.waiting_for == 'init':
        if inp == "1":
            if not state.source or not state.destination:
                m = await event.respond("⚠️ Source and Destination must be set first!")
                await register_msg(state, m)
                return
            await show_media_menu(event, state)
        elif inp == "2":
            await show_source_setup(event, state)
        elif inp == "3":
            m = await event.respond("👋 Exiting TeleSiphon...")
            await register_msg(state, m)
            await asyncio.sleep(1)
            await clear_traces(state, is_exit=True)
            user_states.pop(event.sender_id, None)
        return

    # 2. Source Setup Branch
    if state.waiting_for == 'setup_source':
        if inp == "1":
            await show_dest_setup(event, state)
        elif inp == "2":
            state.waiting_for = 'source_input'
            m = await event.respond("👉 **Forward a message** from source, or paste ID/Username.")
            await register_msg(state, m)
        return

    # 3. Source Input Logic
    if state.waiting_for == 'source_input':
        target = None
        if event.fwd_from:
            target = event.fwd_from.from_id or event.fwd_from.channel_id
        elif inp.replace('-', '').isdigit():
            target = int(inp)
        else:
            target = inp
        
        if target:
            state.source = target
            await show_dest_setup(event, state)
        return

    # 4. Destination Setup Branch
    if state.waiting_for == 'setup_dest':
        if inp == "1":
            await show_media_menu(event, state)
        elif inp == "2":
            state.destination = state.interaction_chat_id
            await show_media_menu(event, state)
        elif inp == "3":
            state.waiting_for = 'dest_input'
            m = await event.respond("👉 **Forward a message** from destination, or paste ID/Username.")
            await register_msg(state, m)
        return

    # 5. Destination Input Logic
    if state.waiting_for == 'dest_input':
        target = None
        if event.fwd_from:
            target = event.fwd_from.from_id or event.fwd_from.channel_id
        elif inp.replace('-', '').isdigit():
            target = int(inp)
        else:
            target = inp
        
        if target:
            state.destination = target
            await show_media_menu(event, state)
        return

    # 6. Media Selection
    if state.waiting_for == 'media':
        media_map = {"1": "Voices", "2": "Audios", "3": "Videos", "4": "Photos", "5": "All"}
        if inp in media_map:
            state.media_type = media_map[inp]
            await show_limit_menu(event, state)
        return

    # 7. Limit Selection
    if state.waiting_for == 'limit':
        limit_map = {"A": 5, "B": 10, "C": 50, "D": 100}
        if inp_upper in limit_map:
            state.limit = limit_map[inp_upper]
            await start_siphon_process(event, state)
        elif inp.isdigit():
            state.limit = int(inp)
            await start_siphon_process(event, state)
        return

async def show_source_setup(event, state):
    state.waiting_for = 'setup_source'
    text = (
        f"**🔄 Source Setup** (Current: `{state.source}`)\n\n"
        "1️⃣ Keep Current\n"
        "2️⃣ Enter New ID / Forward Message"
    )
    msg = await event.respond(text)
    await register_msg(state, msg)

async def show_dest_setup(event, state):
    state.waiting_for = 'setup_dest'
    text = (
        f"**🎯 Destination Setup** (Current: `{state.destination}`)\n\n"
        "1️⃣ Keep Current\n"
        "2️⃣ Use Current Chat (where we are now)\n"
        "3️⃣ Enter New ID / Forward Message"
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
        "5️⃣ All"
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
        "💬 Or just type a **Custom Number** (e.g. `1` or `200`)."
    )
    msg = await event.respond(text)
    await register_msg(state, msg)

async def start_siphon_process(event, state):
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
        "Photos": InputMessagesFilterPhotos()
    }

    selected_filters = {state.media_type: all_filters[state.media_type]} if state.media_type != "All" else all_filters
    total_processed = 0
    
    for category, msg_filter in selected_filters.items():
        current_header = f"🔄 **Siphoning {category}...** (Limit: {state.limit})"
        await status_msg.edit(current_header)

        async for message in client.iter_messages(source_entity, filter=msg_filter, limit=state.limit):
            if message.media:
                last_update = 0
                async def progress(current, total):
                    nonlocal last_update
                    import time
                    now = time.time()
                    if now - last_update < 1.0 and current < total:
                        return
                    last_update = now
                    
                    bar = get_progress_bar(current, total)
                    try: await status_msg.edit(f"{current_header}\n`{bar}`")
                    except: pass

                local_path = await downloader.download_media_with_progress(message, progress_callback=progress)
                
                if local_path:
                    try:
                        from telethon_utils import fast_upload
                        
                        # 2. Parallel Upload (Pre-stage in cloud)
                        current_header = f"📤 **Uploading {category}...**"
                        await status_msg.edit(current_header)
                        
                        uploaded_file = await fast_upload(
                            client, 
                            local_path, 
                            workers=4, # Safe for 0.1 CPU
                            progress_callback=progress
                        )
                        
                        # 3. Mirroring to Target
                        await status_msg.edit(f"🛰 **Finalizing Mirror...**\n`{os.path.basename(local_path)}`")
                        
                        is_voice = message.audio and any(
                            getattr(a, 'voice', False) 
                            for a in message.media.document.attributes 
                            if isinstance(a, DocumentAttributeAudio)
                        )

                        await client.send_file(
                            dest_entity,
                            uploaded_file,
                            caption=message.message,
                            formatting_entities=message.entities,
                            voice_note=is_voice,
                            attributes=message.media.document.attributes if hasattr(message.media, 'document') else None,
                            supports_streaming=True if category == "Videos" else False
                        )
                    except Exception as upload_err:
                        print(f"Upload error: {upload_err}")
                    finally:
                        # 4. Cleanup
                        if os.path.exists(local_path):
                            os.remove(local_path)
                
                total_processed += 1
                await asyncio.sleep(1.5)

    # Final Summary - Register it specially
    done_msg = await event.respond(f"🏁 **Siphon Complete!**\nTotal: `{total_processed}` mirrored to `{getattr(dest_entity, 'title', state.destination)}`.")
    state.summary_msg_id = done_msg.id
    await register_msg(state, done_msg)

    # Reset for another round
    await asyncio.sleep(3)
    await clear_traces(state) # No keep_summary needed, default behavior is to keep summary
    await show_initial_menu(event, state)
