import os
import re
from telethon import utils


class DownloadManager:
    """
    Manages the download process for Telegram media with flexible progress tracking
    and cloud-native storage support.
    """

    def __init__(self, client):
        """
        Initializes the DownloadManager.

        Args:
            client (TelegramClient): The authenticated Telethon client.
        """
        self.client = client

    async def download_media_with_progress(self, message, download_dir="/tmp", progress_callback=None):
        """
        Downloads media from a Telegram message with an optional progress callback.

        Args:
            message (Message): The Telethon message object containing media.
            download_dir (str): The local directory to save the file in. Defaults to /tmp for cloud.
            progress_callback (callable): A function to call with (current, total) bytes.

        Returns:
            str: The local path to the downloaded file, or None if failed.
        """
        if not message.media:
            return None

        # Create download directory if it doesn't exist
        os.makedirs(download_dir, exist_ok=True)

        # Generate a safe filename
        filename = self.get_safe_filename(message)
        file_path = os.path.join(download_dir, filename)

        # Remote size for verification
        remote_size = self._get_remote_size(message)

        try:
            # The actual download
            path = await self.client.download_media(
                message,
                file=file_path,
                progress_callback=progress_callback
            )

            if path:
                # Verification
                if self.verify_file_integrity(path, remote_size):
                    return path
                else:
                    print(f"[ERROR] Verification failed for: {path}")
                    if os.path.exists(path):
                        os.remove(path)
                    return None
            else:
                return None

        except Exception as e:
            print(f"[EXCEPTION] Error downloading message {message.id}: {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
            return None

    def get_safe_filename(self, message):
        """
        Generates a unique filename while preserving correct extensions for all media.
        """
        from telethon.tl.types import DocumentAttributeAudio
        
        timestamp = message.date.strftime("%Y%m%d_%H%M%S")
        original_name = None
        
        # 1. Always get the correct extension first
        extension = ".bin"
        if message.file and message.file.ext:
            extension = message.file.ext
        
        # 2. Try Audio Metadata (Performer - Title)
        if message.audio or message.voice:
            attr = next((a for a in message.media.document.attributes if isinstance(a, DocumentAttributeAudio)), None)
            if attr:
                parts = []
                if attr.performer: parts.append(attr.performer)
                if attr.title: parts.append(attr.title)
                if parts:
                    original_name = " - ".join(parts)

        # 3. Fallback to original filename
        if not original_name and message.file and message.file.name:
            original_name, _ = os.path.splitext(message.file.name)

        # 4. Fallback to type-based naming
        if not original_name:
            if message.voice: original_name = "voice_note"
            elif message.audio: original_name = "audio"
            elif message.video: original_name = "video"
            elif message.photo: original_name = "photo"
            else: original_name = "media_file"

        # SANITIZATION
        original_name = re.sub(r'[\\/*?:"<>|]', "", original_name)
        original_name = original_name.replace(";", "_").strip()

        return f"{original_name}_{message.id}_{timestamp}{extension}"

    def _get_remote_size(self, message):
        """
        Extracts the file size from the media object.

        Args:
            message (Message): The Telethon message object.

        Returns:
            int: The size of the file in bytes.
        """
        if message.file:
            return message.file.size
        return 0

    def verify_file_integrity(self, file_path, remote_size):
        """
        Compares local file size with remote size.

        Args:
            file_path (str): Path to the local file.
            remote_size (int): Expected size in bytes.

        Returns:
            bool: True if sizes match, False otherwise.
        """
        if not os.path.exists(file_path):
            return False
        local_size = os.path.getsize(file_path)
        return local_size == remote_size
