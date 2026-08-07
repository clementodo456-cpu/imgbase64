# 🖼 @imgbase64bot - Telegram Base64 Converter

A production-ready Telegram Bot built with Python (`python-telegram-bot` v21+), `Pillow`, `SQLite`, and `aiohttp`. It supports fast bidirectional conversion between Images and Base64 Data URIs, optimized for Render Web Service deployments.

---

## ✨ Features
* **Image → Base64:** Upload photo/document; receive Data URI or `.txt` file for large outputs.
* **Base64 → Image:** Paste raw Base64 strings or Data URIs; receive decoded image files.
* **Auto Format Detection:** Uses `Pillow` byte-level verification rather than depending on raw extensions.
* **Rate Limiting & Validation:** Prevents server abuse and malicious file execution.
* **SQLite Metrics:** Tracks total users, overall conversions, and broken down counts.
* **Admin Statistics:** Admin `/stats` command restricted via `ADMIN_ID`.
* **Webhook Server:** `aiohttp` integrated with built-in `/health` check for Render.

---

## ⚙️ Configuration (.env)

Create a `.env` file or populate environment variables in Render:

| Variable | Required | Description |
| :--- | :--- | :--- |
| `BOT_TOKEN` | **Yes** | Telegram Bot API Token obtained from [@BotFather](https://t.me/BotFather) |
| `ADMIN_ID` | **Yes** | Your numeric Telegram User ID for `/stats` access |
| `RENDER_EXTERNAL_URL` | **Yes (Prod)** | Full HTTPS URL of your Render service (e.g., `https://app.onrender.com`) |
| `DATABASE_PATH` | No | SQLite DB path (Default: `bot.db`) |
| `MAX_FILE_SIZE_MB` | No | File upload/download limit in MB (Default: `10`) |
| `PORT` | No | Web server port provided by hosting service (Default: `10000`) |

---

## 🚀 Local Development Setup

1. **Clone repository:**
   ```bash
   git clone [https://github.com/your-username/imgbase64bot.git](https://github.com/your-username/imgbase64bot.git)
   cd imgbase64bot
