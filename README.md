# YouTube Downloader Telegram Bot

A Telegram bot to download YouTube videos in various qualities or as MP3 audio.

## Local Setup
1. Get a bot token from [@BotFather](https://t.me/BotFather).
2. Open `bot.py` and replace `YOUR_BOT_TOKEN_HERE` with your token.
3. Install [FFmpeg](https://ffmpeg.org/download.html) (required for audio extraction).
4. Run `test.bat` or use:
   ```bash
   pip install -r requirements.txt
   python bot.py
   ```

## Render Deployment
1. Push this repository to GitHub.
2. Connect the repository to [Render](https://dashboard.render.com).
3. Render will auto-detect `render.yaml`.
4. Set the `BOT_TOKEN` environment variable in the Render dashboard.
