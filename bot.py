"""
YouTube Downloader Telegram Bot - Local Test Version for Windows
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict
from datetime import timedelta

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
    "1080p": {"format": "best[height<=1080][ext=mp4]", "ext": "mp4"},
}

# ==================== LOGGING ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== DOWNLOAD HANDLER ====================
class YouTubeDownloader:
    """Handles YouTube downloads"""
    
    def __init__(self):
        self.semaphore = asyncio.Semaphore(1)  # One download at a time for testing
    
    async def get_video_info(self, url: str) -> Optional[Dict]:
        """Extract video information"""
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
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
        output_template = str(DOWNLOAD_DIR / f"%(title)s_{user_id}.%(ext)s")
        
        ydl_opts = {
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
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
    welcome = (
        "🎥 *YouTube Downloader Bot*\n\n"
        "Send me a YouTube link and I'll download it!\n\n"
        "Available qualities:\n"
        "• Audio only (MP3)\n"
        "• 360p\n"
        "• 480p\n"
        "• 720p\n"
        "• 1080p\n\n"
        "⚠️ *Testing locally on Windows*"
    )
    await update.message.reply_text(welcome, parse_mode='Markdown')

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle YouTube URLs"""
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
        await processing_msg.edit_text("❌ Couldn't fetch video info. Make sure the URL is correct.")
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
        [
            InlineKeyboardButton("720p", callback_data=f"dl_720p_{url}"),
            InlineKeyboardButton("1080p", callback_data=f"dl_1080p_{url}"),
        ],
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
    
    await query.edit_message_caption(f"⏳ Downloading {quality}...\nPlease wait...")
    
    # Download
    filepath = await downloader.download_video(url, quality, update.effective_user.id)
    
    if not filepath or not filepath.exists():
        await query.edit_message_caption(
            "❌ Download failed. The video might be too long or restricted."
        )
        return
    
    # Check file size
    size_mb = filepath.stat().st_size / (1024 * 1024)
    
    await query.edit_message_caption(f"📤 Uploading... ({size_mb:.1f} MB)")
    
    try:
        with open(filepath, 'rb') as f:
            if quality == "audio":
                await context.bot.send_audio(
                    chat_id=update.effective_chat.id,
                    audio=f,
                    title=filepath.stem,
                    performer="YouTube",
                    caption=f"✅ Download complete!\n📁 Size: {size_mb:.1f} MB"
                )
            else:
                await context.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=f,
                    caption=f"✅ Download complete!\n📁 Size: {size_mb:.1f} MB",
                    supports_streaming=True
                )
        
        # Clean up
        filepath.unlink()
        await query.message.delete()
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        await query.edit_message_caption("❌ Upload failed. The file might be too large.")
        
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

# ==================== MAIN ====================
def main():
    """Start bot"""
    if not BOT_TOKEN:
        print("❌ ERROR: BOT_TOKEN is not set.")
        print("Set it in your environment, e.g. in PowerShell:")
        print("  $env:BOT_TOKEN = '123456:ABC...'")
        return
    
    print("🤖 Starting YouTube Downloader Bot...")
    print(f"📁 Downloads folder: {DOWNLOAD_DIR.absolute()}")
    print("Press Ctrl+C to stop")
    
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)
    
    # Start bot
    if WEBHOOK_URL:
        # Ensure webhook_url matches the configured path
        if WEBHOOK_PATH and not WEBHOOK_URL.rstrip("/").endswith(f"/{WEBHOOK_PATH}"):
            WEBHOOK_URL = f"{WEBHOOK_URL.rstrip('/')}/{WEBHOOK_PATH}"

        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=WEBHOOK_URL,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
