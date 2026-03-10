"""
YouTube Downloader Telegram Bot - Render.com Optimized Version
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict
import tempfile
from datetime import timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
import yt_dlp

# ==================== CONFIGURATION ====================
# Get from environment variables (set in Render dashboard)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ALLOWED_USERS = os.environ.get("ALLOWED_USERS", "").split(",") if os.environ.get("ALLOWED_USERS") else []
ALLOWED_USERS = [int(user_id) for user_id in ALLOWED_USERS if user_id]

# Render has /tmp for temporary files
DOWNLOAD_DIR = Path("/tmp/downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Quality options
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
    "360p": {"format": "best[height<=360][ext=mp4]", "ext": "mp4"},
    "480p": {"format": "best[height<=480][ext=mp4]", "ext": "mp4"},
    "720p": {"format": "best[height<=720][ext=mp4]", "ext": "mp4"},
}

# ==================== LOGGING ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== DOWNLOAD HANDLER ====================
class YouTubeDownloader:
    """Handles YouTube downloads optimized for Render"""
    
    def __init__(self):
        self.semaphore = asyncio.Semaphore(2)  # Render free tier has limited resources
    
    async def get_video_info(self, url: str) -> Optional[Dict]:
        """Extract video information"""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web"],  # Try multiple clients
                }
            }
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, url, download=False)
                
                return {
                    "title": info.get("title", "Unknown"),
                    "duration": info.get("duration", 0),
                    "uploader": info.get("uploader", "Unknown"),
                    "views": info.get("view_count", 0),
                    "thumbnail": info.get("thumbnail", ""),
                    "url": url
                }
        except Exception as e:
            logger.error(f"Error getting video info: {e}")
            return None
    
    async def download_video(self, url: str, quality: str, user_id: int) -> Optional[Path]:
        """Download video"""
        
        if quality not in QUALITY_OPTIONS:
            quality = "480p"
        
        quality_config = QUALITY_OPTIONS[quality]
        output_template = str(DOWNLOAD_DIR / f"%(title)s_{user_id}_%(id)s.%(ext)s")
        
        ydl_opts = {
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,  # Avoid special characters
            **quality_config
        }
        
        try:
            async with self.semaphore:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    await asyncio.to_thread(ydl.download, [url])
                    
                    # Find downloaded file
                    info = await asyncio.to_thread(ydl.extract_info, url, download=False)
                    filename = ydl.prepare_filename(info)
                    
                    if quality == "audio":
                        filename = Path(filename).with_suffix('.mp3')
                    
                    filepath = Path(filename)
                    if filepath.exists():
                        return filepath
                    return None
                    
        except Exception as e:
            logger.error(f"Download error: {e}")
            return None

# Initialize downloader
downloader = YouTubeDownloader()

# ==================== TELEGRAM HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    # Check authorization
    if ALLOWED_USERS and update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ You're not authorized to use this bot.")
        return
    
    welcome = (
        "🎥 *YouTube Downloader Bot*\n\n"
        "Send me a YouTube link and I'll download it!\n\n"
        "Available qualities:\n"
        "• Audio only (MP3)\n"
        "• 360p\n"
        "• 480p\n"
        "• 720p\n\n"
        "⚠️ *Note:* Files are automatically deleted after download"
    )
    await update.message.reply_text(welcome, parse_mode='Markdown')

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle YouTube URLs"""
    # Check authorization
    if ALLOWED_USERS and update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ You're not authorized to use this bot.")
        return
    
    url = update.message.text.strip()
    
    # URL validation
    if not any(domain in url for domain in ['youtube.com/watch', 'youtu.be/', 'm.youtube.com']):
        await update.message.reply_text("❌ Please send a valid YouTube URL")
        return
    
    await update.message.chat.send_action(action="typing")
    
    # Send "processing" message
    processing_msg = await update.message.reply_text("⏳ Fetching video information...")
    
    info = await downloader.get_video_info(url)
    
    if not info:
        await processing_msg.edit_text(
            "❌ Couldn't fetch video info. The video might be:\n"
            "• Private or age-restricted\n"
            "• Region-blocked\n"
            "• Too long (>1 hour)\n\n"
            "Try another video or add cookies.txt"
        )
        return
    
    # Format duration
    duration = str(timedelta(seconds=info['duration']))
    
    # Create quality selection keyboard
    keyboard = [
        [InlineKeyboardButton("🎵 Audio Only (MP3)", callback_data=f"dl_audio_{url}")],
        [
            InlineKeyboardButton("360p", callback_data=f"dl_360p_{url}"),
            InlineKeyboardButton("480p", callback_data=f"dl_480p_{url}"),
        ],
        [InlineKeyboardButton("720p", callback_data=f"dl_720p_{url}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
    ]
    
    caption = (
        f"📹 *{info['title'][:50]}*...\n" if len(info['title']) > 50 else f"📹 *{info['title']}*\n"
        f"👤 {info['uploader']}\n"
        f"⏱️ {duration}\n\n"
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

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle quality selection"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel":
        await query.edit_message_caption("✅ Download cancelled.")
        return
    
    # Parse callback
    parts = query.data.split('_', 2)
    if len(parts) != 3:
        await query.edit_message_caption("❌ Invalid selection")
        return
    
    _, quality, url = parts
    
    await query.edit_message_caption(f"⏳ Downloading {quality}...\nThis may take a minute.")
    
    # Download
    filepath = await downloader.download_video(url, quality, update.effective_user.id)
    
    if not filepath or not filepath.exists():
        await query.edit_message_caption(
            "❌ Download failed. The video might be too long or restricted."
        )
        return
    
    # Check file size (Render free tier has 512MB disk limit)
    size_mb = filepath.stat().st_size / (1024 * 1024)
    if size_mb > 50:  # 50MB limit for Telegram
        await query.edit_message_caption(f"❌ File too large ({size_mb:.1f}MB > 50MB limit)")
        filepath.unlink()
        return
    
    await query.edit_message_caption("📤 Uploading to Telegram...")
    
    try:
        with open(filepath, 'rb') as f:
            if quality == "audio":
                await context.bot.send_audio(
                    chat_id=update.effective_chat.id,
                    audio=f,
                    title=filepath.stem,
                    performer="YouTube",
                    caption="✅ Download complete!\n\n*Note:* This file will be deleted from server"
                )
            else:
                await context.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=f,
                    caption="✅ Download complete!\n\n*Note:* This file will be deleted from server",
                    supports_streaming=True,
                    read_timeout=60,
                    write_timeout=60
                )
        
        # Clean up
        filepath.unlink()
        await query.message.delete()
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await query.edit_message_caption("❌ Upload failed. The file might be corrupted.")
        
        # Clean up even on error
        if filepath.exists():
            filepath.unlink()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors"""
    logger.error(f"Error: {context.error}")
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again later."
            )
    except:
        pass

async def cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    """Periodic cleanup of old files"""
    try:
        for file in DOWNLOAD_DIR.glob("*"):
            # Delete files older than 1 hour
            if file.stat().st_mtime < (asyncio.get_event_loop().time() - 3600):
                file.unlink()
                logger.info(f"Cleaned up old file: {file}")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

def main():
    """Start bot"""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable not set!")
        return
    
    # Create application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)
    
    # Add cleanup job (run every hour)
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(cleanup_job, interval=3600, first=10)
    
    logger.info("🤖 YouTube Downloader Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
