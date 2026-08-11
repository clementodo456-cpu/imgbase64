import asyncio
import base64
import io
import logging
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime
from typing import Optional, Tuple

from dotenv import load_dotenv
from PIL import Image, UnidentifiedImageError
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Load environment variables
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
DATABASE_PATH = os.getenv("DATABASE_PATH", "bot.db")
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# Logging setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("imgbase64bot")

# Simple rate limiter dictionary: user_id -> last_timestamp
USER_RATE_LIMIT = {}
RATE_LIMIT_SECONDS = 2.0


# Database Management
def init_db():
    """Initialize SQLite database schema."""
    with closing(sqlite3.connect(DATABASE_PATH)) as conn:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    total_conversions INTEGER DEFAULT 0,
                    image_to_base64_count INTEGER DEFAULT 0,
                    base64_to_image_count INTEGER DEFAULT 0
                )
                """
            )


def update_user_activity(user_id: int, conv_type: Optional[str] = None):
    """Log or update user activity and conversion metrics."""
    now_str = datetime.utcnow().isoformat()
    with closing(sqlite3.connect(DATABASE_PATH)) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            exists = cursor.fetchone()

            if not exists:
                cursor.execute(
                    """
                    INSERT INTO users (user_id, first_seen, last_seen, total_conversions, image_to_base64_count, base64_to_image_count)
                    VALUES (?, ?, ?, 0, 0, 0)
                    """,
                    (user_id, now_str, now_str),
                )
            else:
                cursor.execute("UPDATE users SET last_seen = ? WHERE user_id = ?", (now_str, user_id))

            if conv_type == "img2b64":
                cursor.execute(
                    """
                    UPDATE users
                    SET total_conversions = total_conversions + 1,
                        image_to_base64_count = image_to_base64_count + 1
                    WHERE user_id = ?
                    """,
                    (user_id,),
                )
            elif conv_type == "b642img":
                cursor.execute(
                    """
                    UPDATE users
                    SET total_conversions = total_conversions + 1,
                        base64_to_image_count = base64_to_image_count + 1
                    WHERE user_id = ?
                    """,
                    (user_id,),
                )


def get_admin_stats() -> dict:
    """Fetch aggregated conversion statistics from SQLite."""
    with closing(sqlite3.connect(DATABASE_PATH)) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(user_id) FROM users")
        total_users = cursor.fetchone()[0] or 0

        cursor.execute(
            "SELECT SUM(total_conversions), SUM(image_to_base64_count), SUM(base64_to_image_count) FROM users"
        )
        sums = cursor.fetchone()

        return {
            "total_users": total_users,
            "total_conversions": sums[0] or 0,
            "img2b64": sums[1] or 0,
            "b642img": sums[2] or 0,
        }


# Helper Functions
def check_rate_limit(user_id: int) -> bool:
    """Returns True if user is rate limited, False otherwise."""
    current_time = time.time()
    last_time = USER_RATE_LIMIT.get(user_id, 0)
    if current_time - last_time < RATE_LIMIT_SECONDS:
        return True
    USER_RATE_LIMIT[user_id] = current_time
    return False


def get_main_keyboard() -> InlineKeyboardMarkup:
    """Generates standard inline menu buttons."""
    keyboard = [
        [
            InlineKeyboardButton("🖼 Image → Base64", callback_data="mode_img2b64"),
            InlineKeyboardButton("🔄 Base64 → Image", callback_data="mode_b642img"),
        ],
        [InlineKeyboardButton("ℹ️ Help", callback_data="mode_help")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """Generates a return/cancel inline button."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="mode_main")]])


def parse_data_uri(text: str) -> Tuple[Optional[str], str]:
    """Parses data URI format or returns raw Base64 string."""
    text = text.strip()
    data_uri_pattern = re.compile(r"^data:(image/[a-zA-Z0-9\+\-\.]+);base64,(.+)$", re.DOTALL)
    match = data_uri_pattern.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    return None, text


# Handlers
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /start command."""
    user = update.effective_user
    update_user_activity(user.id)
    welcome_text = (
        f"👋 Welcome <b>{user.first_name}</b> to <b>@imgbase64bot</b>!\n\n"
        "I can convert your images into Base64 strings and decode Base64 strings back into valid images.\n\n"
        "<b>What would you like to do?</b>"
    )
    if update.message:
        await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard())
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            welcome_text, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard()
        )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /cancel command."""
    await update.message.reply_text("Action canceled. Returning to main menu.", reply_markup=get_main_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /help command."""
    help_text = (
        "<b>ℹ️ How to use @imgbase64bot</b>\n\n"
        "1️⃣ <b>Image → Base64:</b>\n"
        "Send any image directly as a Photo or Document. The bot returns the Base64 Data URI string or a .txt file if it's long.\n\n"
        "2️⃣ <b>Base64 → Image:</b>\n"
        "Send a raw Base64 string or a valid Data URI (e.g., <code>data:image/png;base64,...</code>). The bot decodes and returns the rendered image file.\n\n"
        "<b>Supported Formats:</b> JPEG, PNG, WEBP, GIF, BMP, TIFF\n"
        f"<b>Max File Size:</b> {MAX_FILE_SIZE_MB} MB\n"
    )
    if update.message:
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML, reply_markup=get_cancel_keyboard())
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            help_text, parse_mode=ParseMode.HTML, reply_markup=get_cancel_keyboard()
        )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only /stats command."""
    user_id = str(update.effective_user.id)
    if not ADMIN_ID or user_id != str(ADMIN_ID):
        await update.message.reply_text("⛔ Unauthorized command.")
        return

    stats = get_admin_stats()
    msg = (
        "<b>📊 Bot Administration Statistics</b>\n\n"
        f"<b>Total Users:</b> {stats['total_users']}\n"
        f"<b>Total Conversions:</b> {stats['total_conversions']}\n"
        f"<b>Image → Base64:</b> {stats['img2b64']}\n"
        f"<b>Base64 → Image:</b> {stats['b642img']}"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles interaction with inline keyboard buttons."""
    query = update.callback_query
    await query.answer()

    if query.data == "mode_img2b64":
        await query.edit_message_text(
            "🖼 <b>Image → Base64</b>\n\nPlease send or forward the image/photo you wish to convert.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_cancel_keyboard(),
        )
    elif query.data == "mode_b642img":
        await query.edit_message_text(
            "🔄 <b>Base64 → Image</b>\n\nPlease paste and send the Base64 string or Data URI.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_cancel_keyboard(),
        )
    elif query.data == "mode_help":
        await help_command(update, context)
    elif query.data == "mode_main":
        await start_command(update, context)


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Converts uploaded photo or file document into Base64."""
    user = update.effective_user
    if check_rate_limit(user.id):
        await update.message.reply_text("⏳ Please wait a moment before sending another request.")
        return

    message = update.message
    file_obj = None
    original_filename = "image"

    if message.photo:
        file_obj = message.photo[-1]
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
        file_obj = message.document
        if message.document.file_name:
            original_filename = os.path.splitext(message.document.file_name)[0]
    else:
        await message.reply_text("❌ Unsupported file type. Please send a valid image file.")
        return

    if file_obj.file_size and file_obj.file_size > MAX_FILE_SIZE_BYTES:
        await message.reply_text(f"❌ File size exceeds the allowed limit of {MAX_FILE_SIZE_MB} MB.")
        return

    status_msg = await message.reply_text("🔄 Processing image and converting to Base64...")

    try:
        tg_file = await context.bot.get_file(file_obj.file_id)
        image_bytes = await tg_file.download_as_bytearray()

        with Image.open(io.BytesIO(image_bytes)) as img:
            fmt = (img.format or "PNG").lower()
            mime_type = f"image/{fmt}"

        encoded_b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_uri = f"data:{mime_type};base64,{encoded_b64}"

        update_user_activity(user.id, "img2b64")

        if len(data_uri) <= 4000:
            await status_msg.edit_text(
                f"✅ <b>Conversion Successful</b> ({mime_type})\n\n<code>{data_uri}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_cancel_keyboard(),
            )
        else:
            with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp_file:
                tmp_file.write(data_uri)
                tmp_path = tmp_file.name

            try:
                await status_msg.delete()
                with open(tmp_path, "rb") as f:
                    await message.reply_document(
                        document=InputFile(f, filename=f"{original_filename}_base64.txt"),
                        caption=f"✅ Base64 string generated ({mime_type}). Large output sent as file.",
                        reply_markup=get_cancel_keyboard(),
                    )
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

    except UnidentifiedImageError:
        await status_msg.edit_text("❌ Failed to process input. The file is not a valid or supported image.")
    except Exception as e:
        logger.error(f"Error in handle_image: {e}", exc_info=True)
        await status_msg.edit_text("❌ An internal error occurred while processing the image.")


async def handle_text_base64(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Decodes Base64 or Data URI text into a rendered image file."""
    user = update.effective_user
    text = update.message.text.strip()

    if text.startswith("/"):
        return

    if check_rate_limit(user.id):
        await update.message.reply_text("⏳ Please wait a moment before sending another request.")
        return

    status_msg = await update.message.reply_text("🔄 Validating and decoding Base64 string...")

    try:
        mime_hint, raw_b64 = parse_data_uri(text)

        try:
            image_bytes = base64.b64decode(raw_b64, validate=True)
        except Exception:
            await status_msg.edit_text("❌ Invalid Base64 string. Please check your input and try again.")
            return

        if len(image_bytes) > MAX_FILE_SIZE_BYTES:
            await status_msg.edit_text(f"❌ Decoded image size exceeds the allowed limit of {MAX_FILE_SIZE_MB} MB.")
            return

        with Image.open(io.BytesIO(image_bytes)) as img:
            detected_format = (img.format or "PNG").lower()

        out_filename = f"decoded_image.{detected_format}"
        image_stream = io.BytesIO(image_bytes)
        image_stream.name = out_filename

        update_user_activity(user.id, "b642img")
        await status_msg.delete()

        await update.message.reply_document(
            document=InputFile(image_stream),
            caption=f"✅ <b>Base64 decoded successfully!</b>\nFormat: {detected_format.upper()}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_cancel_keyboard(),
        )

    except UnidentifiedImageError:
        await status_msg.edit_text("❌ Decoded data is not a valid image.")
    except Exception as e:
        logger.error(f"Error in handle_text_base64: {e}", exc_info=True)
        await status_msg.edit_text("❌ An internal error occurred while decoding your input.")


def main():
    """Initializes and runs long polling for Background Worker mode."""
    # Create and set a new event loop for Python 3.12+ compatibility
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    init_db()

    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is missing!")

    # Build Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Register Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CallbackQueryHandler(button_callback_handler))
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_base64))

    logger.info("Starting bot long polling...")

    # Clears any existing webhooks automatically and listens for updates
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
