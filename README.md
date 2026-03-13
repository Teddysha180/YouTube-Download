# YouTube Downloader Telegram Bot

A Telegram bot to download YouTube videos in various qualities or as MP3 audio.

## Local Setup
1. Get a bot token from [@BotFather](https://t.me/BotFather).
2. Set `BOT_TOKEN` in your environment.
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
5. After the first deploy, set `WEBHOOK_URL` to `https://<service>.onrender.com/telegram` in Render env vars.

## Environment Variables
- `BOT_TOKEN` (required): Telegram bot token
- `WEBHOOK_PATH` (optional): Defaults to `telegram`
- `WEBHOOK_URL` (optional): Webhook URL on Render
- `ALLOWED_USERS` (optional): Comma-separated Telegram user IDs
- `REQUIRED_CHANNEL` (optional): Channel username, e.g. `@arts_of_drawings`
- `REQUIRED_CHANNEL_URL` (optional): Channel URL, e.g. `https://t.me/arts_of_drawings`
