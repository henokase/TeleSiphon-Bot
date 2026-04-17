"""
TeleSiphon Download Manager
--------------------------
Handles the authenticated retrieval of media from Telegram's MTProto servers.
Includes support for cloud-native ephemeral storage (/tmp), safe filename 
sanitization using media metadata, and post-download integrity verification.
"""

import os
import re
from telethon import utils

class DownloadManager:
    """
    Manages the lifecycle of media downloads including path resolution,
    metadata extraction for naming, and integrity checks.
    """

    def __init__(self, client):
        """
        Initializes the manager with an active Telethon client.

        Args:
            client (TelegramClient): Authenticated Telethon session.
        """
        self.client = client

    async def download_media_with_progress(self, message, download_dir="/tmp", progress_callback=None):
        """
        Downloads media with real-time progress updates and integrity validation.

        Args:
            message (Message): Telethon message object containing media.
            download_dir (str): Local directory for temporary storage.
            progress_callback (callable): Optional async/sync function for progress tracking.

        Returns:
            str: Absolute path to the verified local file, or None on failure.
        """
        if not message.media:
            return None

        os.makedirs(download_dir, exist_ok=True)

        # Generate unique and descriptive filename
        filename = self.get_safe_filename(message)
        file_path = os.path.join(download_dir, filename)

        # Retrieve remote size for post-download verification
        remote_size = self._get_remote_size(message)

        try:
            downloaded_path = await self.client.download_media(
                message,
                file=file_path,
                progress_callback=progress_callback
            )

            if downloaded_path:
                if self.verify_file_integrity(downloaded_path, remote_size):
                    return downloaded_path
                
                print(f"[ERROR] Integrity verification failed: {downloaded_path}")
                if os.path.exists(downloaded_path):
                    os.remove(downloaded_path)
            
            return None

        except Exception as e:
            print(f"[ERROR] Download failure for message {message.id}: {e}")
            if os.path.exists(file_path):
                os.remove(file_path)
            return None

    def get_safe_filename(self, message) -> str:
        """
        Generates a sanitized filename by prioritizing media metadata (Audio Title/Performer)
        or original filenames, falling back to type-based names.

        Args:
            message (Message): Telethon message object.

        Returns:
            str: Sanitized filename with correct extension.
        """
        from telethon.tl.types import DocumentAttributeAudio
        
        timestamp = message.date.strftime("%Y%m%d_%H%M%S")
        candidate_name = None
        
        # Determine appropriate file extension
        extension = message.file.ext if (message.file and message.file.ext) else ".bin"
        
        # Strategy A: Extract Audio Metadata
        if message.audio or message.voice:
            attr = next((a for a in message.media.document.attributes if isinstance(a, DocumentAttributeAudio)), None)
            if attr:
                parts = []
                if attr.performer: parts.append(attr.performer)
                if attr.title: parts.append(attr.title)
                if parts:
                    candidate_name = " - ".join(parts)

        # Strategy B: Original Filename
        if not candidate_name and message.file and message.file.name:
            candidate_name, _ = os.path.splitext(message.file.name)

        # Strategy C: Type-based Generic Name
        if not candidate_name:
            if message.voice: candidate_name = "voice_note"
            elif message.audio: candidate_name = "audio"
            elif message.video: candidate_name = "video"
            elif message.photo: candidate_name = "photo"
            else: candidate_name = "media_file"

        # Production-grade sanitization
        candidate_name = re.sub(r'[\\/*?:"<>|]', "", candidate_name)
        candidate_name = candidate_name.replace(";", "_").strip()

        return f"{candidate_name}_{message.id}_{timestamp}{extension}"

    def _get_remote_size(self, message) -> int:
        """Helper to extract remote file size in bytes."""
        return message.file.size if message.file else 0

    def verify_file_integrity(self, file_path: str, remote_size: int) -> bool:
        """Verifies that the downloaded file size matches the remote specification."""
        if not os.path.exists(file_path):
            return False
        return os.path.getsize(file_path) == remote_size
