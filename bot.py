"""
YouTube Downloader Telegram Bot - Local Test Version for Windows
"""

import os
import re
import json
import re
import secrets
import tempfile
import urllib.parse
import asyncio
import logging
import zipfile
from pathlib import Path
from typing import Optional, Dict, Tuple, Any
from datetime import timedelta, datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
import yt_dlp
import httpx
from aiohttp import web

# ==================== CONFIGURATION ====================
# Bot token should come from environment to avoid hardcoding secrets
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Webhook settings (for Render web service)
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "telegram").strip().lstrip("/")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
if not WEBHOOK_URL and RENDER_EXTERNAL_URL:
    WEBHOOK_URL = f"{RENDER_EXTERNAL_URL.rstrip('/')}/{WEBHOOK_PATH}"

# Render provides PORT for web services
PORT = int(os.getenv("PORT", "8080"))

# Download folder (local folder)
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Optional: restrict bot usage to specific Telegram user IDs
ALLOWED_USERS = {
    int(x.strip())
    for x in os.getenv("ALLOWED_USERS", "").split(",")
    if x.strip().isdigit()
}

# Quality options (keep it simple: audio or video)
QUALITY_OPTIONS = {
    "audio": {
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "ext": "mp3"
    },
    "video": {"format": "best[ext=mp4]/best", "ext": "mp4"},
}

# ==================== LOGGING ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Optional: cookies for yt-dlp
# You can set YTDLP_COOKIES_FILE to a Netscape cookies file path,
# or YTDLP_COOKIES to the raw Netscape file content (or a path).
COOKIE_FILE_PATH = ""
_cookies_file_env = os.getenv("YTDLP_COOKIES_FILE", "").strip()
_cookies_env = os.getenv("YTDLP_COOKIES", "").strip()
if _cookies_file_env and Path(_cookies_file_env).exists():
    COOKIE_FILE_PATH = _cookies_file_env
elif _cookies_env:
    if Path(_cookies_env).exists():
        COOKIE_FILE_PATH = _cookies_env
    else:
        try:
            fd, cookie_path = tempfile.mkstemp(prefix="ytdlp_", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(_cookies_env)
            COOKIE_FILE_PATH = cookie_path
        except Exception as e:
            logger.warning(f"Failed to write YTDLP_COOKIES to file: {e}")

# Show raw yt-dlp errors to user (debug)
SHOW_ERRORS = os.getenv("SHOW_ERRORS", "").strip().lower() in {"1", "true", "yes"}

# Optional: proxy for yt-dlp (e.g. http://user:pass@host:port or socks5://host:port)
YTDLP_PROXY = os.getenv("YTDLP_PROXY", "").strip()

# Telegram upload limit (MB). Default set conservatively.
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "49"))

# Auto-cleaner: delete files older than this many hours
CLEANUP_MAX_AGE_HOURS = float(os.getenv("CLEANUP_MAX_AGE_HOURS", "6"))

# Delete sent files after this many seconds
DELETE_AFTER_SEND_SECONDS = int(os.getenv("DELETE_AFTER_SEND_SECONDS", "30"))

# Bot username to append to captions
BOT_USERNAME = os.getenv("BOT_USERNAME", "@QuickTokDLBot").strip() or "@QuickTokDLBot"

# Channel join requirement (set to @channelusername or channel ID)
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "").strip()

# ==================== DOWNLOAD HANDLER ====================
class VideoDownloader:
    """Handles TikTok downloads"""
    
    def __init__(self):
        self.semaphore = asyncio.Semaphore(1)  # One download at a time for testing
    
    async def get_video_info(self, url: str) -> Optional[Dict]:
        """Extract video information"""
        base = self._base_ydl_opts()
        base.update({
            "skip_download": True,
        })

        info = await self._try_extract(url, base)
        if not info:
            return None

        return {
            "title": info.get("title", "Unknown"),
            "duration": info.get("duration", 0),
            "uploader": info.get("uploader", "Unknown"),
            "views": info.get("view_count", 0),
            "thumbnail": info.get("thumbnail", ""),
            "images": extract_image_urls(info),
            "url": url
        }
    
    async def download_video(self, url: str, quality: str, user_id: int) -> Optional[Path]:
        """Download video"""
        
        if quality not in QUALITY_OPTIONS:
            quality = "480p"
        
        quality_config = QUALITY_OPTIONS[quality]
        output_template = str(DOWNLOAD_DIR / f"%(title)s_{user_id}.%(ext)s")
        
        ydl_opts = self._base_ydl_opts()
        ydl_opts.update({
            "outtmpl": output_template,
            **quality_config
        })
        
        try:
            async with self.semaphore:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Single extraction + download to avoid duplicate network calls
                    info = await asyncio.to_thread(ydl.extract_info, url, download=True)
                    return self._find_downloaded_file(ydl, info, quality)
                    
        except Exception as e:
            logger.error(f"Download error: {e}")
            return None

    def _base_ydl_opts(self) -> Dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "retries": 3,
            "socket_timeout": 20,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "force_ipv4": True,
            "restrictfilenames": True,
            "windowsfilenames": True,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            "referer": "https://www.tiktok.com/",
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.tiktok.com/",
            },
        }
        if COOKIE_FILE_PATH:
            opts["cookiefile"] = COOKIE_FILE_PATH
        if YTDLP_PROXY:
            opts["proxy"] = YTDLP_PROXY
        return opts

    async def _try_extract(self, url: str, ydl_opts: Dict) -> Optional[Dict]:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return await asyncio.to_thread(ydl.extract_info, url, download=False)
        except Exception as e:
            logger.error(f"Error getting video info: {e}")
            return None

    def _find_downloaded_file(self, ydl: yt_dlp.YoutubeDL, info: Dict, quality: str) -> Optional[Path]:
        try:
            filename = ydl.prepare_filename(info)
            base_path = Path(filename)
            if quality == "audio":
                candidates = [
                    base_path.with_suffix(".mp3"),
                    base_path.with_suffix(".m4a"),
                    base_path.with_suffix(".webm"),
                ]
            else:
                candidates = [base_path]

            for candidate in candidates:
                if candidate.exists():
                    return candidate

            # Fallback: find any file that matches stem (postprocessors may change name)
            stem = base_path.stem
            for file in DOWNLOAD_DIR.glob(f"{stem}*"):
                if file.is_file():
                    return file
        except Exception as e:
            logger.error(f"File locate error: {e}")
        return None

# Initialize downloader
downloader = VideoDownloader()

# ==================== CLEANUP ====================
def cleanup_downloads() -> None:
    """Remove old files from downloads folder."""
    try:
        cutoff = datetime.utcnow().timestamp() - (CLEANUP_MAX_AGE_HOURS * 3600)
        for file in DOWNLOAD_DIR.glob("*"):
            try:
                if file.is_file() and file.stat().st_mtime < cutoff:
                    file.unlink()
            except Exception as e:
                logger.error(f"Cleanup error for {file}: {e}")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

async def delete_file_later(path: Path, delay_seconds: int) -> None:
    try:
        await asyncio.sleep(delay_seconds)
        if path.exists():
            path.unlink()
    except Exception as e:
        logger.error(f"Delayed delete error for {path}: {e}")

# ==================== TELEGRAM HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    if not await ensure_joined_channel(update, context):
        return
    welcome = (
        "🎥 *TikTok Downloader Bot*\n\n"
        "Send me a TikTok link and I'll download it!\n\n"
        "Options:\n"
        "• Audio only (MP3)\n"
        "• Video (MP4)\n"
    )
    await update.message.reply_text(welcome, parse_mode='Markdown')

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show basic diagnostics (safe to share)"""
    if not await ensure_joined_channel(update, context):
        return
    if ALLOWED_USERS and update.effective_user and update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return

    msg = (
        "✅ *Bot Status*\n\n"
        f"Webhook: {'set' if WEBHOOK_URL else 'not set'}\n"
        f"Cookies: {'set' if COOKIE_FILE_PATH else 'not set'}\n"
        f"Proxy: {'set' if YTDLP_PROXY else 'not set'}\n"
        f"Max upload: {MAX_UPLOAD_MB:.0f} MB"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle TikTok URLs"""
    url = update.message.text.strip()

    if not await ensure_joined_channel(update, context):
        return
    if ALLOWED_USERS and update.effective_user and update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return

    # URL validation (supports tiktok.com and vm.tiktok.com)
    if not is_valid_tiktok_url(url):
        await update.message.reply_text("❌ Please send a valid TikTok URL")
        return
    
    await update.message.chat.send_action(action="typing")
    
    # Send "processing" message
    processing_msg = await update.message.reply_text("⏳ Fetching video information...")
    
    info = await downloader.get_video_info(url)
    
    # If info fetch fails, still allow user to try downloading directly
    if not info:
        if "/photo/" in url:
            await processing_msg.edit_text("⏳ Trying photo post fallback...")
            images = await try_extract_images_from_html(url)
            if images:
                info = {
                    "title": "TikTok Photo Post",
                    "duration": 0,
                    "uploader": "Unknown",
                    "views": 0,
                    "thumbnail": "",
                    "images": images,
                    "url": url,
                }
            else:
                await processing_msg.edit_text(
                    "⚠️ Couldn't fetch photo info. You can still try downloading directly."
                )
        else:
            await processing_msg.edit_text(
                "⚠️ Couldn't fetch video info. You can still try downloading directly."
            )

        if not info:
            token = store_url_token(context, url)
            keyboard = [
                [InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data=f"dl:audio:{token}")],
                [InlineKeyboardButton("🎬 Video (MP4)", callback_data=f"dl:video:{token}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
            ]
            await update.message.reply_text(
                "Choose quality to try:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

    # If this is a photo post, download images and send as ZIP
    if info.get("images"):
        await processing_msg.edit_text("⏳ Downloading images...")
        zip_path = await download_images_as_zip(info["images"], update.effective_user.id)
        if not zip_path or not zip_path.exists():
            await processing_msg.edit_text("❌ Failed to download images.")
            return

        size_mb = zip_path.stat().st_size / (1024 * 1024)
        if size_mb > MAX_UPLOAD_MB:
            await processing_msg.edit_text(
                f"❌ ZIP too large to upload ({size_mb:.1f} MB)."
            )
            if zip_path.exists():
                zip_path.unlink()
            return

        await processing_msg.edit_text(f"📤 Uploading images ZIP... ({size_mb:.1f} MB)")
        try:
            caption_text = build_caption_text(info)
            base_caption = f"✅ Images downloaded!\n📁 Size: {size_mb:.1f} MB"
            full_caption = f"{base_caption}\n{caption_text}\n{BOT_USERNAME}" if caption_text else f"{base_caption}\n{BOT_USERNAME}"
            with open(zip_path, "rb") as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=zip_path.name,
                    caption=full_caption
                )
            asyncio.create_task(delete_file_later(zip_path, DELETE_AFTER_SEND_SECONDS))
            await processing_msg.delete()
        except Exception as e:
            logger.error(f"ZIP upload error: {e}")
            await processing_msg.edit_text("❌ Upload failed.")
            if zip_path.exists():
                zip_path.unlink()
        return

    # Format duration
    duration = str(timedelta(seconds=info['duration']))
    
    # Create quality selection keyboard
    token = store_url_token(context, url, info)
    keyboard = [
        [InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data=f"dl:audio:{token}")],
        [InlineKeyboardButton("🎬 Video (MP4)", callback_data=f"dl:video:{token}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ]
    
    caption = (
        f"📹 *{info['title'][:50]}*...\n" if len(info['title']) > 50 else f"📹 *{info['title']}*\n"
        f"👤 {info['uploader']}\n"
        f"⏱️ {duration}\n"
        f"👀 {info['views']:,} views\n\n"
        f"Choose quality:"
    )
    
    # Delete processing message
    await processing_msg.delete()
    
    # Send with thumbnail if available
    if info['thumbnail']:
        try:
            await update.message.reply_photo(
                photo=info['thumbnail'],
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            return
        except:
            pass
    
    await update.message.reply_text(
        caption,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

def store_url_token(context: ContextTypes.DEFAULT_TYPE, url: str, info: Optional[Dict] = None) -> str:
    token = secrets.token_urlsafe(6)
    url_map = context.user_data.setdefault("url_map", {})
    if info:
        url_map[token] = {
            "url": url,
            "title": info.get("title"),
            "description": info.get("description"),
            "uploader": info.get("uploader"),
            "duration": info.get("duration"),
        }
    else:
        url_map[token] = url
    # Keep map small
    if len(url_map) > 50:
        for old_key in list(url_map.keys())[:10]:
            url_map.pop(old_key, None)
    return token

def get_url_from_token(context: ContextTypes.DEFAULT_TYPE, token: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    url_map = context.user_data.get("url_map", {})
    value = url_map.get(token)
    if isinstance(value, dict):
        return value.get("url"), value
    return value, None

async def safe_edit_message(query, text: str) -> None:
    try:
        if query.message and query.message.caption is not None:
            await query.edit_message_caption(text)
        else:
            await query.edit_message_text(text)
    except Exception as e:
        logger.error(f"Edit message error: {e}")
        try:
            await query.message.reply_text(text)
        except Exception:
            pass

async def ensure_joined_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    from_callback: bool = False,
) -> bool:
    """Require user to join channel before using the bot."""
    if not REQUIRED_CHANNEL:
        return True
    user = update.effective_user
    if not user:
        return False
    try:
        member = await context.bot.get_chat_member(REQUIRED_CHANNEL, user.id)
        if member.status in {"member", "administrator", "creator"}:
            return True
    except Exception as e:
        logger.warning(f"Join check error: {e}")
        await _send_join_error(update, context)
        return False

    await _send_join_prompt(update, context, from_callback=from_callback)
    return False

async def _send_join_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    from_callback: bool = False,
) -> None:
    join_url = REQUIRED_CHANNEL
    if not join_url.startswith("http"):
        join_url = f"https://t.me/{join_url.lstrip('@')}"

    keyboard = [
        [InlineKeyboardButton("📢 Join Channel", url=join_url)],
        [InlineKeyboardButton("✅ I Joined", callback_data="check_join")],
    ]
    text = (
        "🔒 *Join Required*\n\n"
        "Please join our channel first to use this bot.\n"
        "After joining, tap ✅ I Joined."
    )
    try:
        if from_callback and update.callback_query:
            await update.callback_query.message.reply_text(
                text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif update.message:
            await update.message.reply_text(
                text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    except Exception as e:
        logger.warning(f"Join prompt send error: {e}")

async def _send_join_error(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    text = (
        "⚠️ *Unable to verify channel membership.*\n\n"
        "Please ensure the bot is an admin in the channel and try again."
    )
    try:
        if update.message:
            await update.message.reply_text(text, parse_mode="Markdown")
        elif update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text=text, parse_mode="Markdown"
            )
    except Exception as e:
        logger.warning(f"Join error send failed: {e}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle quality selection"""
    query = update.callback_query
    await query.answer()

    if query.data == "check_join":
        if await ensure_joined_channel(update, context, from_callback=True):
            await safe_edit_message(query, "✅ Thanks for joining! Send a TikTok link.")
        return

    if query.data == "cancel":
        await safe_edit_message(query, "✅ Download cancelled.")
        return
    
    # Parse callback
    parts = query.data.split(':', 2)
    if len(parts) != 3 or parts[0] != "dl":
        await safe_edit_message(query, "❌ Invalid selection")
        return
    
    _, quality, token = parts
    url, info = get_url_from_token(context, token)
    if not url:
        await safe_edit_message(query, "❌ Link expired. Please send the URL again.")
        return
    
    label = "audio" if quality == "audio" else "video"
    await safe_edit_message(query, f"⏳ Downloading {label}...\nPlease wait...")
    
    # Download
    filepath = await downloader.download_video(url, quality, update.effective_user.id)
    
    if not filepath or not filepath.exists():
        await safe_edit_message(
            query,
            "❌ Download failed. The video might be too long or restricted."
        )
        return
    
    # Check file size
    size_mb = filepath.stat().st_size / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        await safe_edit_message(
            query,
            f"❌ File too large to upload ({size_mb:.1f} MB)."
        )
        if filepath.exists():
            filepath.unlink()
        return
    
    await safe_edit_message(query, f"📤 Uploading... ({size_mb:.1f} MB)")
    
    try:
        caption_text = build_caption_text(info)
        base_caption = f"✅ Download complete!\n📁 Size: {size_mb:.1f} MB"
        full_caption = f"{base_caption}\n{caption_text}\n{BOT_USERNAME}" if caption_text else f"{base_caption}\n{BOT_USERNAME}"
        with open(filepath, 'rb') as f:
            if quality == "audio":
                await context.bot.send_audio(
                    chat_id=update.effective_chat.id,
                    audio=f,
                    title=filepath.stem,
                    performer="TikTok",
                    caption=full_caption
                )
            else:
                await context.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=f,
                    caption=full_caption,
                    supports_streaming=True
                )
        
        # Clean up message and schedule file deletion
        await query.message.delete()
        asyncio.create_task(delete_file_later(filepath, DELETE_AFTER_SEND_SECONDS))

    except Exception as e:
        logger.error(f"Upload error: {e}")
        await safe_edit_message(query, "❌ Upload failed. The file might be too large.")
        
        # Clean up even on error
        if filepath.exists():
            filepath.unlink()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Error: {context.error}")
    try:
        if update and update.effective_message:
            msg = "❌ An error occurred. Please try again later."
            if SHOW_ERRORS and context.error:
                msg = f"❌ Error: {context.error}"
            await update.effective_message.reply_text(msg)
    except:
        pass

# ==================== CAPTION TEXT ====================
def build_caption_text(info: Optional[Dict]) -> str:
    if not info:
        return ""
    lines = []
    if info.get("title"):
        lines.append(f"Title: {info.get('title')}")
    if info.get("description"):
        lines.append(f"Description: {info.get('description')}")
    if info.get("uploader"):
        lines.append(f"Uploader: {info.get('uploader')}")
    if info.get("duration") is not None:
        lines.append(f"Duration: {info.get('duration')}")
    if info.get("url"):
        lines.append(f"URL: {info.get('url')}")
    return "\n".join(lines)

# ==================== PHOTO FALLBACK ====================
def _looks_like_image_url(url: str) -> bool:
    if not url:
        return False
    lower = url.lower()
    if "tiktokcdn" not in lower and "tiktok" not in lower:
        return False
    try:
        path = urllib.parse.urlparse(lower).path
    except Exception:
        path = lower
    return any(ext in path for ext in (".jpg", ".jpeg", ".png", ".webp"))

def _collect_image_urls(obj: Any, found: set) -> None:
    if isinstance(obj, dict):
        for _, value in obj.items():
            _collect_image_urls(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _collect_image_urls(item, found)
    elif isinstance(obj, str):
        if _looks_like_image_url(obj):
            found.add(obj)

def _extract_json_blobs(html: str) -> list[dict]:
    blobs = []
    patterns = [
        r'<script id="SIGI_STATE"[^>]*>(\{.*?\})</script>',
        r'window\["SIGI_STATE"\]\s*=\s*(\{.*?\});',
        r'__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*(\{.*?\});',
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.S):
            raw = match.group(1)
            try:
                blobs.append(json.loads(raw))
            except Exception:
                continue
    return blobs

async def try_extract_images_from_html(url: str) -> list[str]:
    headers = {
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "referer": "https://www.tiktok.com/",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        cookies = None
        if COOKIE_FILE_PATH and Path(COOKIE_FILE_PATH).exists():
            cookies = _load_netscape_cookies(COOKIE_FILE_PATH)
        async with httpx.AsyncClient(
            timeout=30.0,
            headers=headers,
            cookies=cookies,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        logger.error(f"Photo fallback fetch error: {e}")
        return []

    images = set()
    for blob in _extract_json_blobs(html):
        _collect_image_urls(blob, images)

    return list(images)

def _load_netscape_cookies(path: str) -> httpx.Cookies:
    cookies = httpx.Cookies()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _, cookie_path, _, _, name, value = parts[:7]
                cookies.set(name, value, domain=domain, path=cookie_path)
    except Exception as e:
        logger.error(f"Cookie parse error: {e}")
    return cookies

# ==================== IMAGE HELPERS ====================
def extract_image_urls(info: Dict) -> list[str]:
    images: list[str] = []

    # Common keys for TikTok photo posts
    for item in info.get("images", []):
        url = item.get("url") or item.get("src") or item.get("image_url")
        if url:
            images.append(url)

    # Some extractors expose entries
    for entry in info.get("entries", []) or []:
        if isinstance(entry, dict):
            url = entry.get("url") or entry.get("src") or entry.get("image_url")
            if url:
                images.append(url)

    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for u in images:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped

async def download_images_as_zip(urls: list[str], user_id: int) -> Optional[Path]:
    if not urls:
        return None

    zip_path = DOWNLOAD_DIR / f"tiktok_images_{user_id}.zip"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for i, url in enumerate(urls, start=1):
                    try:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        filename = f"image_{i}.jpg"
                        zf.writestr(filename, resp.content)
                    except Exception as e:
                        logger.error(f"Image download error ({url}): {e}")
                        continue

        return zip_path if zip_path.exists() else None
    except Exception as e:
        logger.error(f"ZIP build error: {e}")
        if zip_path.exists():
            zip_path.unlink()
        return None

# ==================== URL HELPERS ====================
TIKTOK_HOSTS = {
    "tiktok.com",
    "www.tiktok.com",
    "m.tiktok.com",
    "vm.tiktok.com",
}

def is_valid_tiktok_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False

    host = (parsed.netloc or "").lower()
    if host not in TIKTOK_HOSTS:
        return False

    path = parsed.path or ""
    # TikTok links typically include /@user/video/<id> or short vm.tiktok.com/<id>
    if host == "vm.tiktok.com":
        return bool(path.strip("/"))
    return "/video/" in path or path.strip("/") != ""

# ==================== MAIN ====================
def main():
    """Start bot"""
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN is not set.")
        print("Set it in your environment, e.g. in PowerShell:")
        print("  $env:BOT_TOKEN = '123456:ABC...'")
        return
    
    print("🤖 Starting TikTok Downloader Bot...")
    print(f"📁 Downloads folder: {DOWNLOAD_DIR.absolute()}")
    print("Press Ctrl+C to stop")

    cleanup_downloads()
    
    # Create application with explicit timeouts to tolerate slow networks
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)
    
    # Start bot
    webhook_url = WEBHOOK_URL
    if webhook_url:
        if WEBHOOK_PATH and not webhook_url.rstrip("/").endswith(f"/{WEBHOOK_PATH}"):
            webhook_url = f"{webhook_url.rstrip('/')}/{WEBHOOK_PATH}"

        async def telegram_webhook(request: web.Request) -> web.Response:
            try:
                data = await request.json()
                update = Update.de_json(data, app.bot)
                await app.process_update(update)
            except Exception as e:
                logger.error(f"Webhook handler error: {e}")
            return web.Response(text="OK")

        async def health(request: web.Request) -> web.Response:
            return web.Response(text="OK")

        async def on_startup(app_web: web.Application) -> None:
            await app.initialize()
            await app.start()
            await app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)

        async def on_shutdown(app_web: web.Application) -> None:
            try:
                await app.bot.delete_webhook()
            except Exception:
                pass
            await app.stop()
            await app.shutdown()

        web_app = web.Application()
        web_app.router.add_get("/health", health)
        web_app.router.add_post(f"/{WEBHOOK_PATH}", telegram_webhook)
        web_app.on_startup.append(on_startup)
        web_app.on_shutdown.append(on_shutdown)

        web.run_app(web_app, host="0.0.0.0", port=PORT)
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
