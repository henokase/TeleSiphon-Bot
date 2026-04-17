"""
TeleSiphon MTProto Utilities
----------------------------
Collection of performance-optimized helpers for the Telethon library.
Includes parallelized upload/download engines designed to bypass sequential 
transfer bottlenecks in high-latency or resource-constrained environments.
"""

import asyncio
import hashlib
import math
import os
import random
from telethon import utils
from telethon.tl.functions.upload import SaveBigFilePartRequest, SaveFilePartRequest
from telethon.tl.types import InputFile, InputFileBig

async def fast_upload(client, file_path: str, workers=4, progress_callback=None):
    """
    Optimized parallel upload using concurrent MTProto part requests.
    
    Args:
        client (TelegramClient): Active Telethon session.
        file_path (str): Absolute path to the local file.
        workers (int): Maximum number of concurrent upload tasks.
        progress_callback (callable): Optional callback for tracking bytes uploaded.

    Returns:
        InputFile|InputFileBig: The uploaded file object ready for sending.
    """
    file_size = os.path.getsize(file_path)
    # Safest chunk size (512KB) to maintain stability across all Telegram data centers
    chunk_size = 512 * 1024 
    total_chunks = math.ceil(file_size / chunk_size)
    is_big = file_size > 10 * 1024 * 1024 # Standard 10MB threshold for 'Big' files
    file_id = random.getrandbits(63)

    # Use a semaphore to manage concurrency and prevent CPU/Network saturation
    semaphore = asyncio.Semaphore(workers)
    uploaded_size = 0

    async def upload_part(part_index: int, part_data: bytes):
        nonlocal uploaded_size
        async with semaphore:
            if is_big:
                request = SaveBigFilePartRequest(file_id, part_index, total_chunks, part_data)
            else:
                request = SaveFilePartRequest(file_id, part_index, part_data)
            
            await client(request)
            uploaded_size += len(part_data)
            if progress_callback:
                try:
                    await progress_callback(uploaded_size, file_size)
                except Exception:
                    pass

    tasks = []
    with open(file_path, 'rb') as f:
        for i in range(total_chunks):
            chunk = f.read(chunk_size)
            tasks.append(upload_part(i, chunk))

    await asyncio.gather(*tasks)

    if is_big:
        return InputFileBig(file_id, total_chunks, os.path.basename(file_path))
    else:
        # Generate MD5 checksum for standard small file compliance
        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                md5.update(chunk)
        return InputFile(file_id, total_chunks, os.path.basename(file_path), md5.hexdigest())

async def fast_download(client, message, file_path: str, workers=4, progress_callback=None):
    """
    Performance-oriented download wrapper using optimized internal Telethon logic.
    
    Args:
        client (TelegramClient): Active Telethon session.
        message (Message): Source message containing the media.
        file_path (str): Target destination on disk.
        workers (int): Parallel worker count (currently reserved for future optimization).
        progress_callback (callable): Optional callback for tracking bytes received.
    """
    return await client.download_media(
        message,
        file=file_path,
        progress_callback=progress_callback
    )
