import os
from tqdm import tqdm
from telethon import utils


class DownloadManager:
    """
    Manages the download process for Telegram media with progress tracking
     and file verification.
    """

    def __init__(self, client):
        """
        Initializes the DownloadManager.

        Args:
            client (TelegramClient): The authenticated Telethon client.
        """
        self.client = client

    async def download_media_with_progress(self, message, download_dir="downloads"):
        """
        Downloads media from a Telegram message with a visible progress bar.

        Args:
            message (Message): The Telethon message object containing media.
            download_dir (str): The local directory to save the file in.

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

        # Skip logic: If file already exists, don't download it again
        if os.path.exists(file_path):
            print(f"Skipping existing file: {filename}")
            return file_path

        # Remote size for verification and progress bar
        remote_size = self._get_remote_size(message)

        # tqdm progress bar setup
        pbar = tqdm(
            total=remote_size,
            unit='B',
            unit_scale=True,
            desc=f"Downloading {filename}",
            leave=False
        )

        def progress_callback(current, total):
            pbar.update(current - pbar.n)

        try:
            # The actual download
            path = await self.client.download_media(
                message,
                file=file_path,
                progress_callback=progress_callback
            )
            pbar.close()

            if path:
                # Verification
                if self.verify_file_integrity(path, remote_size):
                    print(f"\n[SUCCESS] Downloaded and verified: {path}")
                    return path
                else:
                    print(f"\n[ERROR] Verification failed for: {path}")
                    return None
            else:
                pbar.close()
                print(f"\n[ERROR] Download failed for message {message.id}")
                return None

        except Exception as e:
            pbar.close()
            print(f"\n[EXCEPTION] Error downloading message {message.id}: {e}")
            return None

    def get_safe_filename(self, message):
        """
        Generates a unique filename based on original filename and timestamp.
        Convention: [original_name]_[msg_id]_[timestamp].[ext]

        Args:
            message (Message): The Telethon message object.

        Returns:
            str: A safe, unique filename.
        """
        # Get timestamp
        timestamp = message.date.strftime("%Y%m%d_%H%M%S")

        # Try to get existing filename and extension
        original_name = None
        extension = ""

        if message.file:
            if message.file.name:
                original_name, extension = os.path.splitext(message.file.name)
            else:
                extension = message.file.ext or ""

        if not original_name:
            # Fallback based on media type
            if message.voice:
                original_name = "voice_note"
                if not extension:
                    extension = ".ogg"
            elif message.audio:
                original_name = "audio"
                if not extension:
                    extension = ".mp3"
            elif message.video:
                original_name = "video"
                if not extension:
                    extension = ".mp4"
            elif message.photo:
                original_name = "photo"
                if not extension:
                    extension = ".jpg"
            else:
                original_name = "media_file"
                if not extension:
                    extension = ".bin"

        # Clean the extension (ensure it starts with a dot)
        if extension and not extension.startswith('.'):
            extension = f".{extension}"

        # Combine into requested format: [name]_[id]_[timestamp].[ext]
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
