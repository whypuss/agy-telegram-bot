"""
Media Ingestion & Cache Management for Antigravity Telegram Bot.

Handles:
- Downloading incoming Photos, Voice notes, Audios, and Documents to local cache
- Providing cached local paths to Antigravity CLI for multimodal input (Gemini Vision/Audio/File reading)
- Detecting outbound media (e.g. generated images or files) to send natively to Telegram
- Cache cleanup utilities
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import List, Optional, Tuple

from telegram import PhotoSize, Voice, Audio, Document, File

from config import CACHE_DIR

logger = logging.getLogger("agy-tg-bot.media")


def ensure_cache_dir() -> Path:
    """Ensure the cache directory exists."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


async def download_telegram_file(file_obj: File, dest_path: Path) -> Path:
    """Download a Telegram File object to the specified destination path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    await file_obj.download_to_drive(custom_path=dest_path)
    logger.info("Downloaded Telegram file to %s (%d bytes)", dest_path, dest_path.stat().st_size)
    return dest_path


async def save_photo(photo: PhotoSize) -> Path:
    """Download and cache an incoming Telegram photo (highest resolution)."""
    ensure_cache_dir()
    file_obj = await photo.get_file()
    
    timestamp = int(time.time())
    file_id = photo.file_unique_id
    dest_path = CACHE_DIR / f"photo_{timestamp}_{file_id}.jpg"
    
    return await download_telegram_file(file_obj, dest_path)


async def save_voice(voice: Voice) -> Path:
    """Download and cache an incoming Telegram voice note (.ogg)."""
    ensure_cache_dir()
    file_obj = await voice.get_file()
    
    timestamp = int(time.time())
    file_id = voice.file_unique_id
    dest_path = CACHE_DIR / f"voice_{timestamp}_{file_id}.ogg"
    
    return await download_telegram_file(file_obj, dest_path)


async def save_audio(audio: Audio) -> Path:
    """Download and cache an incoming Telegram audio file."""
    ensure_cache_dir()
    file_obj = await audio.get_file()
    
    timestamp = int(time.time())
    filename = audio.file_name or f"audio_{timestamp}_{audio.file_unique_id}.mp3"
    dest_path = CACHE_DIR / f"{timestamp}_{filename}"
    
    return await download_telegram_file(file_obj, dest_path)


async def save_document(document: Document) -> Path:
    """Download and cache an incoming Telegram document/file."""
    ensure_cache_dir()
    file_obj = await document.get_file()
    
    timestamp = int(time.time())
    filename = document.file_name or f"doc_{timestamp}_{document.file_unique_id}"
    dest_path = CACHE_DIR / f"{timestamp}_{filename}"
    
    return await download_telegram_file(file_obj, dest_path)


def cleanup_cache(max_age_seconds: int = 86400) -> int:
    """
    Remove files in CACHE_DIR older than max_age_seconds (default 24h).
    Returns the number of deleted files.
    """
    if not CACHE_DIR.exists():
        return 0

    now = time.time()
    deleted_count = 0
    for item in CACHE_DIR.iterdir():
        if item.is_file():
            try:
                mtime = item.stat().st_mtime
                if now - mtime > max_age_seconds:
                    item.unlink()
                    deleted_count += 1
            except Exception as e:
                logger.warning("Failed to remove cache file %s: %s", item, e)
                
    if deleted_count > 0:
        logger.info("Cleaned up %d expired media files from cache", deleted_count)
    return deleted_count


# ---------------------------------------------------------------------------
# Outbound Media Detection
# ---------------------------------------------------------------------------

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
_DOC_EXTS = {".pdf", ".csv", ".json", ".zip", ".tar", ".gz", ".txt", ".py", ".md", ".html", ".docx", ".xlsx"}

def detect_local_files_in_response(text: str) -> List[Tuple[str, str]]:
    """
    Scan agent response for references to existing local files or images.
    Returns a list of (media_type, file_path_str) where media_type is 'image' or 'document'.
    """
    if not text:
        return []

    found_files = []
    
    # 1. Match Markdown image syntax: ![alt](/path/to/file)
    for match in re.finditer(r'!\[.*?\]\((/[^)]+)\)', text):
        path_str = match.group(1).strip()
        p = Path(path_str)
        if p.exists() and p.is_file() and p.suffix.lower() in _IMAGE_EXTS:
            found_files.append(("image", str(p)))

    # 2. Match absolute paths on new lines or quotes: e.g. `/Users/.../output.png` or `file:///...`
    for match in re.finditer(r'(?:file://)?(/Users/[^\s`\'"<>)]+|/[a-zA-Z0-9_\-./]+[a-zA-Z0-9_\-]\.[a-zA-Z0-9]{2,5})', text):
        path_str = match.group(1).strip()
        p = Path(path_str)
        if p.exists() and p.is_file():
            ext = p.suffix.lower()
            if ext in _IMAGE_EXTS and ("image", str(p)) not in found_files:
                found_files.append(("image", str(p)))
            elif ext in _DOC_EXTS and ("document", str(p)) not in found_files:
                # Limit sending documents to intentional artifact files
                if "artifact" in str(p).lower() or "output" in str(p).lower() or "export" in str(p).lower():
                    found_files.append(("document", str(p)))

    return found_files
