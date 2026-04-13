import os
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import (
    InputMessagesFilterMusic,
    InputMessagesFilterVideo,
    InputMessagesFilterVoice,
    InputMessagesFilterPhotos,
    DocumentAttributeAudio
)
from downloader import DownloadManager

# Load environment variables
load_dotenv()

# Configuration from .env
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
TARGET_CHAT = os.getenv("TARGET_CHAT_ID")
FORWARD_CHAT = os.getenv("FORWARD_CHAT_ID")
DOWNLOAD_LIMIT = int(os.getenv("DOWNLOAD_LIMIT", 0))


def clear_screen():
    """Clears the terminal console."""
    os.system('cls' if os.name == 'nt' else 'clear')


async def main():
    """Main entry point for TeleSiphon."""
    if not API_ID or not API_HASH:
        print("[ERROR] API_ID and API_HASH must be set in the .env file.")
        return

    # Initialize Telegram Client
    client = TelegramClient('telesiphon', int(API_ID), API_HASH)

    print("Starting TeleSiphon...")
    await client.start()

    # Shared Configuration
    target = TARGET_CHAT
    forward_target = FORWARD_CHAT
    downloader = DownloadManager(client)
    limit = DOWNLOAD_LIMIT if DOWNLOAD_LIMIT > 0 else None

    # Resolve source chat
    if not target:
        target = input("Enter Source Chat ID, Username, or Invite Link: ")

    # Pre-processing target: convert to int if possible
    processed_target = target
    if str(target).replace('-', '').isdigit():
        processed_target = int(target)

    try:
        source_entity = await client.get_entity(processed_target)
        title = getattr(source_entity, 'title', getattr(source_entity, 'username', 'Unknown'))

        # Safe print for Windows consoles
        try:
            print(f"Source Chat Resolved: {title}")
        except UnicodeEncodeError:
            print(f"Source Chat Resolved: {title.encode('ascii', 'ignore').decode('ascii')} (Unicode suppressed)")

    except Exception as e:
        print(f"[ERROR] Could not resolve source chat '{target}': {e}")
        await client.disconnect()
        return

    while True:
        clear_screen()

        print("=== TeleSiphon Main Menu ===")
        print(f"Connected. Source: {title}")
        print("1. Download Media")
        print("2. Forward Media (Download & Re-upload)")
        print("3. Exit")

        main_choice = input("\nSelect an action (1-3): ")

        if main_choice == "3":
            print("Exiting...")
            break

        if main_choice not in ["1", "2"]:
            input("Invalid selection. Press Enter to continue...")
            continue

        is_forward_mode = (main_choice == "2")

        # Resolve forward target if needed
        if is_forward_mode and not forward_target:
            forward_target = input("Enter Forward Target Chat ID, Username, or Invite Link: ")

        forward_entity = None
        if is_forward_mode:
            try:
                processed_forward = forward_target
                if str(forward_target).replace('-', '').isdigit():
                    processed_forward = int(forward_target)
                forward_entity = await client.get_entity(processed_forward)
                f_title = getattr(forward_entity, 'title', getattr(forward_entity, 'username', 'Unknown'))
                print(f"Forward Target Resolved: {f_title}")
            except Exception as e:
                print(f"[ERROR] Could not resolve forward target '{forward_target}': {e}")
                input("Press Enter to return to menu...")
                continue

        # Media Selection Menu
        print("\nSelect Media Types:")
        print("1. Voices")
        print("2. Audios")
        print("3. Videos")
        print("4. Images")
        print("5. All")

        media_choice = input("\nEnter your choice (1-5): ")

        all_filters = {
            "1": {"Voices": InputMessagesFilterVoice()},
            "2": {"Audios": InputMessagesFilterMusic()},
            "3": {"Videos": InputMessagesFilterVideo()},
            "4": {"Images": InputMessagesFilterPhotos()},
            "5": {
                "Voices": InputMessagesFilterVoice(),
                "Audios": InputMessagesFilterMusic(),
                "Videos": InputMessagesFilterVideo(),
                "Images": InputMessagesFilterPhotos()
            }
        }

        selected_filters = all_filters.get(media_choice, all_filters["5"])
        total_processed = 0

        for category, msg_filter in selected_filters.items():
            print(f"\n--- Scanning for {category} (Limit: {limit if limit else 'None'}) ---")
            count = 0
            async for message in client.iter_messages(source_entity, filter=msg_filter, limit=limit):
                if message.media:
                    # Step 1: Download
                    local_path = await downloader.download_media_with_progress(message)

                    if local_path:
                        # Step 2: Forward (if mode active)
                        if is_forward_mode:
                            print("Re-uploading to target...")
                            try:
                                # Optimized is_voice detection
                                is_voice = False
                                if message.audio:
                                    is_voice = any(
                                        getattr(attr, 'voice', False)
                                        for attr in message.media.document.attributes
                                        if isinstance(attr, DocumentAttributeAudio)
                                    )

                                # Send file preserving rich text entities
                                await client.send_file(
                                    forward_entity,
                                    local_path,
                                    caption=message.message,
                                    formatting_entities=message.entities,
                                    voice_note=is_voice,
                                    supports_streaming=True if category == "Videos" else False
                                )
                                print(f"Successfully forwarded: {os.path.basename(local_path)}")
                            except Exception as e:
                                print(f"Failed to forward {os.path.basename(local_path)}: {e}")

                        count += 1
                        total_processed += 1
            print(f"Finished {category}. Processed: {count}")

        print(f"\n[FINISH] Operation Complete. Total files processed: {total_processed}")
        input("\nPress Enter to return to the Main Menu...")

    await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[STOP] Process interrupted by user.")
