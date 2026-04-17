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
        self.waiting_for = None  # 'init', 'setup_source', 'setup_dest', 'media', 'limit', 'source_input', 'dest_input', 'source_type', 'topic_selection'
        self.source_type = 'group' # 'group' or 'topic'
        self.topic_id = None
        self.available_topics = {} # Mapping index -> topic_id
        
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
            await proceed_to_media_or_topic(event, state)
        elif inp == "2":
            await show_source_setup(event, state)
        elif inp == "3":
            # ... exit logic ...
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

    # 4. Source Type Selection (Forum Only)
    if state.waiting_for == 'source_type':
        if inp == "1":
            state.source_type = 'group'
            state.topic_id = None
            await show_media_menu(event, state)
        elif inp == "2":
            await show_topic_selection(event, state)
        return

    # 5. Topic Selection
    if state.waiting_for == 'topic_selection':
        if inp in state.available_topics:
            state.source_type = 'topic'
            state.topic_id = state.available_topics[inp]
            await show_media_menu(event, state)
        elif inp == "0": # Back to source type
            await show_source_type_menu(event, state)
        return

    # 6. Destination Setup Branch
    if state.waiting_for == 'setup_dest':
        if inp == "1":
            await proceed_to_media_or_topic(event, state)
        elif inp == "2":
            state.destination = state.interaction_chat_id
            await proceed_to_media_or_topic(event, state)
        elif inp == "3":
            state.waiting_for = 'dest_input'
            m = await event.respond("👉 **Forward a message** from destination, or paste ID/Username.")
            await register_msg(state, m)
        return

    # 7. Destination Input Logic
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
            await proceed_to_media_or_topic(event, state)
        return

    # 8. Media Selection
    if state.waiting_for == 'media':
        media_map = {"1": "Voices", "2": "Audios", "3": "Videos", "4": "Photos", "5": "Documents", "6": "All"}
        if inp in media_map:
            state.media_type = media_map[inp]
            await show_limit_menu(event, state)
        return

    # 9. Limit Selection
    if state.waiting_for == 'limit':
        limit_map = {"A": 5, "B": 10, "C": 50, "D": 100}
        if inp_upper in limit_map:
            state.limit = limit_map[inp_upper]
            await start_siphon_process(event, state)
        elif inp.isdigit():
            state.limit = int(inp)
            await start_siphon_process(event, state)
        return

async def proceed_to_media_or_topic(event, state):
    """Centralized logic to check if source is a forum before showing media menu."""
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
    except Exception as e:
        print(f"Forum check error: {e}")
        await show_media_menu(event, state)

async def show_source_type_menu(event, state):
    state.waiting_for = 'source_type'
    text = (
        "**📚 Forum Detected!**\n\n"
        "How would you like to siphon?\n"
        "1️⃣ Whole Group (Everything)\n"
        "2️⃣ Specific Topic"
    )
    msg = await event.respond(text)
    await register_msg(state, msg)

async def show_topic_selection(event, state):
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
        "5️⃣ Documents (PDF, Zip, etc.)\n"
        "6️⃣ All"
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
        "Photos": InputMessagesFilterPhotos(),
        "Documents": InputMessagesFilterDocument()
    }

    total_processed = 0

    if state.source_type == 'topic':
        # 🟢 Topic-Specific Flow (Multi-Category Buffer for Parity)
        current_header = f"🔄 **Scanning Topic...** (Limit: {state.limit} per type)"
        await status_msg.edit(current_header)
        
        categories_to_fetch = ["Voices", "Audios", "Videos", "Photos", "Documents"] if state.media_type == "All" else [state.media_type]
        media_buffer = {cat: [] for cat in categories_to_fetch}

        # Step 1: Scan and Collect
        async for message in client.iter_messages(source_entity, reply_to=state.topic_id):
            # Stop if we hit the total limit for ALL requested categories
            if all(len(msgs) >= state.limit for msgs in media_buffer.values()):
                break

            if not message.media:
                continue

            category = "Documents" # Default to documents
            if message.voice: category = "Voices"
            elif message.audio: category = "Audios"
            elif message.video: category = "Videos"
            elif message.photo: category = "Photos"
            # If it's none of the above but has media, it stays "Documents"

            if category in media_buffer and len(media_buffer[category]) < state.limit:
                media_buffer[category].append(message)

        # Step 2: Process Buffered Messages
        for category, messages in media_buffer.items():
            if not messages: continue
            
            for index, message in enumerate(messages, 1):
                current_status = f"🔄 **Mirroring {category}...** ({index}/{len(messages)})"
                await status_msg.edit(current_status)

                last_update = 0
                async def progress(current, total):
                    nonlocal last_update
                    import time
                    now = time.time()
                    if now - last_update < 1.0 and current < total:
                        return
                    last_update = now
                    bar = get_progress_bar(current, total)
                    try: await status_msg.edit(f"{current_status}\n`{bar}`")
                    except: pass

                local_path = await downloader.download_media_with_progress(message, progress_callback=progress)
                if local_path:
                    try:
                        from telethon_utils import fast_upload
                        await status_msg.edit(f"📤 **Uploading {category}...**")
                        uploaded_file = await fast_upload(client, local_path, workers=4, progress_callback=progress)
                        await status_msg.edit(f"🛰 **Finalizing Mirror...**\n`{os.path.basename(local_path)}`")
                        
                        is_voice = category == "Voices"
                        await client.send_file(
                            dest_entity,
                            uploaded_file,
                            caption=message.message,
                            formatting_entities=message.entities,
                            voice_note=is_voice,
                            attributes=message.media.document.attributes if hasattr(message.media, 'document') else None,
                            supports_streaming=True if category == "Videos" else False
                        )
                        total_processed += 1
                    except Exception as e: print(f"Upload error: {e}")
                    finally:
                        if os.path.exists(local_path): os.remove(local_path)
                
                await asyncio.sleep(1.5)

    else:
        # 🔵 Whole Group Flow (Efficient Server-Side Filtering)
        selected_filters = {state.media_type: all_filters[state.media_type]} if state.media_type != "All" else all_filters
        
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
                            await status_msg.edit(f"📤 **Uploading {category}...**")
                            uploaded_file = await fast_upload(client, local_path, workers=4, progress_callback=progress)
                            await status_msg.edit(f"🛰 **Finalizing Mirror...**\n`{os.path.basename(local_path)}`")
                            
                            media_attrs = message.media.document.attributes if hasattr(message.media, 'document') else None
                            is_voice = category == "Voices"
                            
                            await client.send_file(dest_entity, uploaded_file, caption=message.message, formatting_entities=message.entities, voice_note=is_voice, attributes=media_attrs, supports_streaming=True if category == "Videos" else False)
                        except Exception as e: print(f"Upload error: {e}")
                        finally:
                            if os.path.exists(local_path): os.remove(local_path)
                    
                    total_processed += 1
                    await asyncio.sleep(1.5)

    # Final Summary - Register it specially
    done_msg = await event.respond(f"🏁 **Siphon Complete!**\nTotal: `{total_processed}` mirrored to `{getattr(dest_entity, 'title', state.destination)}`.")
    state.summary_msg_id = done_msg.id
    await register_msg(state, done_msg)

    # Reset for another round
    await asyncio.sleep(3)
    await clear_traces(state)
    await show_initial_menu(event, state)
