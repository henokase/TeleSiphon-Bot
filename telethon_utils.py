import asyncio
import hashlib
import math
import os
import random
from telethon import utils
from telethon.tl.functions.upload import SaveBigFilePartRequest, SaveFilePartRequest
from telethon.tl.types import InputFile, InputFileBig

async def fast_upload(client, file_path, workers=4, progress_callback=None):
    """
    Parallel upload for Telethon. Optimized for cloud environments with high latency.
    """
    file_size = os.path.getsize(file_path)
    # Larger chunks for better network efficiency
    chunk_size = 512 * 1024 if file_size <= 10 * 1024 * 1024 else 1024 * 1024
    total_chunks = math.ceil(file_size / chunk_size)
    is_big = file_size > 10 * 1024 * 1024
    file_id = random.getrandbits(63)

    # Semaphore to limit parallel tasks (to avoid CPU throttling on Render)
    semaphore = asyncio.Semaphore(workers)
    
    # Track progress
    uploaded_size = 0

    async def upload_part(part_index, part_data):
        nonlocal uploaded_size
        async with semaphore:
            if is_big:
                request = SaveBigFilePartRequest(file_id, part_index, total_chunks, part_data)
            else:
                request = SaveFilePartRequest(file_id, part_index, part_data)
            
            await client(request)
            uploaded_size += len(part_data)
            if progress_callback:
                # Telethon progress callback usually takes (current, total)
                try:
                    await progress_callback(uploaded_size, file_size)
                except:
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
        # Calculate real MD5 for full compliance on small files
        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                md5.update(chunk)
        return InputFile(file_id, total_chunks, os.path.basename(file_path), md5.hexdigest())

async def fast_download(client, message, file_path, workers=4, progress_callback=None):
    """
    Parallel download for Telethon. Uses multiple workers to saturate bandwidth.
    """
    # Simply using sequential download for now but with larger chunk size
    # Telethon's download_media is actually quite optimized if you provide a good chunk size
    # However, for 0.1 CPU, sequential with large chunks is often safer
    return await client.download_media(
        message,
        file=file_path,
        progress_callback=progress_callback
    )
