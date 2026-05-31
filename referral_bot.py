import os
import logging
import random
import sqlite3
import time
import asyncio

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== CONFIG ==================
TOKEN = os.getenv("8805644620:AAEJBMCRhxi3OXnQ0gGnnb6whrWOFH4sifk")  # set in Render env
ADMIN_ID = 6510529863

# Private Channel
CHANNEL_ID = -1002316967647
CHANNEL_PUBLIC_LINK = "https://t.me/c/1234567890"

# Private Chat
CHAT_ID = -1002441671350
CHAT_PUBLIC_LINK = "https://t.me/+WtR02CJEtzdjNTRl"

# Items List (from your private channel)
ITEMS_CHANNEL_ID = -1002316967647
ITEMS_MESSAGE_ID = 5

DB_PATH = "bot.db"

# 🔥 Replace this with your real header image file_id
HEADER_PHOTO_FILE_ID = "AgACAgUAAxkBAAFLKDtqHIZ1awsQH0lszwH3Uz5FMn5PDgACfhFrG6Pp6VRlkuVd61-3uQEAAwIAA3kAAzsE"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

captcha_pending = {}
ADMIN_STATES = {"broadcast_waiting": False}
ADMIN_SALE = {
    "waiting_for_buyer": False,
    "waiting_for_amount": False,
    "current_buyer": None,
}

# ================== DB SETUP ==================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            ref_code TEXT,
            referred_by INTEGER,
            is_verified INTEGER DEFAULT 0,
            reward REAL DEFAULT 0,
            attempts_left INTEGER DEFAULT 3
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS ui_sessions (
            user_id INTEGER PRIMARY KEY,
            message_id INTEGER
        )
        """
    )
    conn.commit()
    conn.close()


def get_conn():
    return sqlite3.connect(DB_PATH)


def ensure_user(user_id: int, username: str | None, referred_by: int | None):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if not row:
        ref_code = str(user_id)
        c.execute(
            "INSERT INTO users (user_id, username, ref_code, referred_by) VALUES (?, ?, ?, ?)",
            (user_id, username, ref_code, referred_by),
        )
    else:
        c.execute(
            "UPDATE users SET username = ? WHERE user_id = ?",
            (username, user_id),
        )
    conn.commit()
    conn.close()


def set_verified(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET is_verified = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_user(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT user_id, username, ref_code, referred_by, is_verified, reward, attempts_left "
        "FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = c.fetchone()
    conn.close()
    return row


def get_referrals(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT user_id, username FROM users WHERE referred_by = ?",
        (user_id,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


def add_reward(user_id: int, amount: float):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE users SET reward = reward + ? WHERE user_id = ?",
        (amount, user_id),
    )
    conn.commit()
    conn.close()


def decrement_attempt(user_id: int) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT attempts_left FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    attempts = row[0] if row else 0
    if attempts > 0:
        c.execute(
            "UPDATE users SET attempts_left = attempts_left - 1 WHERE user_id = ?",
            (user_id,),
        )
        attempts -= 1
    conn.commit()
    conn.close()
    return attempts


def set_ui_message(user_id: int, message_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO ui_sessions (user_id, message_id) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET message_id = excluded.message_id",
        (user_id, message_id),
    )
    conn.commit()
    conn.close()


def get_ui_message(user_id: int) -> int | None:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT message_id FROM ui_sessions WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


# ================== INVITE LINK GENERATOR ==================
async def generate_channel_invite(context):
    try:
        now = int(time.time())
        expire_time = now + 3600  # 1 hour

        invite_link = await context.bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            expire_date=expire_time,
            creates_join_request=True,
        )

        return invite_link.invite_link

    except Exception as e:
        logger.error(f"Invite link error: {e}")
        return None


# ================== UI RENDERERS ==================
def build_main_menu_caption(user_row, bot_username: str, is_admin: bool) -> str:
    user_id, username, ref_code, referred_by, is_verified, reward, attempts_left = user_row
    uname = f"@{username}" if username else "(no username)"
    ref_link = f"https://t.me/{bot_username}?start={ref_code}"

    lines = [
        "⛥ **Spidey's Multiverse** ⛥",
        "",
        f"👤 User: {uname}",
        f"💰 Balance: `${reward:.2f}`",
        f"🎯 Attempts left: `{attempts_left}`",
        "",
        "🔗 Your referral link:",
        f"`{ref_link}`",
        "",
        "🏠 Home",
    ]
    if is_admin:
        lines.append("🛠 Admin access enabled")

    return "\n".join(lines)


def build_main_menu_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("👤 Profile", callback_data="user_profile"),
            InlineKeyboardButton("👥 Referrals", callback_data="user_refs"),
        ],
        [
            InlineKeyboardButton("🛒 Items", callback_data="user_items"),
            InlineKeyboardButton("💬 Chat", callback_data="user_chat"),
        ],
        [
            InlineKeyboardButton("🔗 Join Channel", callback_data="user_join_channel"),
        ],
    ]
    if is_admin:
        rows.append(
            [InlineKeyboardButton("🛠 Admin Panel", callback_data="admin_panel")]
        )
    return InlineKeyboardMarkup(rows)


def build_profile_caption(user_row) -> str:
    user_id, username, ref_code, referred_by, is_verified, reward, attempts_left = user_row
    uname = f"@{username}" if username else "(no username)"
    status = "✅ Verified" if is_verified else "❌ Not verified"
    ref_by = f"`{referred_by}`" if referred_by else "None"

    return (
        "👤 **Profile**\n\n"
        f"🆔 ID: `{user_id}`\n"
        f"👤 Username: {uname}\n"
        f"🛡 Status: {status}\n"
        f"🧲 Referred by: {ref_by}\n"
        f"💰 Balance: `${reward:.2f}`\n"
        f"🎯 Attempts left: `{attempts_left}`"
    )


def build_profile_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("⬅️ Back", callback_data="back_home")]]
    if is_admin:
        rows[0].append(InlineKeyboardButton("🛠 Admin", callback_data="admin_panel"))
    return InlineKeyboardMarkup(rows)


def build_refs_caption(refs) -> str:
    if not refs:
        return "👥 **Your Referrals**\n\nYou have no referrals yet."
    lines = ["👥 **Your Referrals**", ""]
    for uid, uname in refs:
        uname_display = f"@{uname}" if uname else "(no username)"
        lines.append(f"⬛ {uname_display} — `{uid}`")
    return "\n".join(lines)


def build_refs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back", callback_data="back_home")]]
    )


def build_admin_menu_caption() -> str:
    return (
        "🛠 **Admin Dashboard**\n\n"
        "Manage users, referrals, credits, sales and broadcasts."
    )


def build_admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👥 Users", callback_data="admin_users"),
                InlineKeyboardButton("🔗 Referrals", callback_data="admin_referrals"),
            ],
            [
                InlineKeyboardButton("💰 Credits", callback_data="admin_credits"),
                InlineKeyboardButton("💵 Record Sale", callback_data="admin_sale"),
            ],
            [
                InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
                InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="back_home")],
        ]
    )


# ================== CORE UI HELPERS ==================
async def show_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass
    await asyncio.sleep(0.3)


async def ensure_ui_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_row
):
    user = update.effective_user
    chat_id = user.id
    is_admin = user.id == ADMIN_ID

    msg_id = get_ui_message(user.id)
    caption = build_main_menu_caption(user_row, context.bot.username, is_admin)
    keyboard = build_main_menu_keyboard(is_admin)

    if msg_id:
        try:
            await context.bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=caption,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            return
        except Exception as e:
            logger.warning(f"Failed to edit existing UI message: {e}")

    sent = await context.bot.send_photo(
        chat_id=chat_id,
        photo=HEADER_PHOTO_FILE_ID,
        caption=caption,
        reply_markup=keyboard,
        parse_mode="Markdown",
    )
    set_ui_message(user.id, sent.message_id)


async def update_ui(
    query_or_update, context: ContextTypes.DEFAULT_TYPE, caption: str, keyboard: InlineKeyboardMarkup
):
    if isinstance(query_or_update, Update):
        chat_id = query_or_update.effective_user.id
        msg_id = get_ui_message(chat_id)
        if not msg_id:
            return
        await show_typing(context, chat_id)
        await context.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=msg_id,
            caption=caption,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    else:
        query = query_or_update
        chat_id = query.from_user.id
        msg = query.message
        await show_typing(context, chat_id)
        await msg.edit_caption(
            caption=caption,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )


# ================== HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not user.username:
        await update.message.reply_text(
            "❌ You must set a Telegram username to continue.\n"
            "Go to Settings → Username and create one, then /start again."
        )
        return

    args = context.args
    referred_by = None
    if args:
        try:
            referred_by = int(args[0])
            if referred_by == user.id:
                referred_by = None
        except ValueError:
            referred_by = None

    ensure_user(user.id, user.username, referred_by)

    a, b = random.randint(1, 9), random.randint(1, 9)
    captcha_pending[user.id] = a + b

    await update.message.reply_text(
        f"🛡 Verification\n\nSolve this to continue:\n\n`{a} + {b} = ?`",
        parse_mode="Markdown",
    )


async def captcha_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if user.id not in captcha_pending:
        return

    if not text.isdigit():
        await update.message.reply_text("Please send a number.")
        return

    expected = captcha_pending[user.id]
    if int(text) == expected:
        set_verified(user.id)
        del captcha_pending[user.id]

        row = get_user(user.id)
        await ensure_ui_message(update, context, row)
    else:
        await update.message.reply_text("❌ Wrong answer. Try again.")


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row = get_user(user.id)
    if not row:
        await update.message.reply_text("Use /start first.")
        return

    _, _, _, _, is_verified, _, _ = row
    if not is_verified:
        await update.message.reply_text("You are not verified yet. Use /start.")
        return

    await ensure_ui_message(update, context, row)


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    row = get_user(update.effective_user.id)
    if not row:
        await update.message.reply_text("Use /start first.")
        return
    await ensure_ui_message(update, context, row)
    caption = build_admin_menu_caption()
    keyboard = build_admin_menu_keyboard()
    await update_ui(update, context, caption, keyboard)


# ================== ADMIN TEXT FLOWS ==================
async def admin_broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    if not ADMIN_STATES["broadcast_waiting"]:
        return

    msg = update.message.text
    ADMIN_STATES["broadcast_waiting"] = False

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE is_verified = 1")
    users = [row[0] for row in c.fetchall()]
    conn.close()

    sent = 0
    for uid in users:
        try:
            await context.bot.send_message(uid, msg)
            sent += 1
        except Exception as e:
            logger.warning(f"Failed to send to {uid}: {e}")

    caption = f"📢 **Broadcast sent**\n\nDelivered to `{sent}` users."
    keyboard = build_admin_menu_keyboard()
    await update_ui(update, context, caption, keyboard)


async def admin_sale_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    text = update.message.text.strip()

    if ADMIN_SALE["waiting_for_buyer"]:
        if not text.isdigit():
            await update.message.reply_text("❌ Invalid ID. Send a numeric Telegram ID.")
            return

        ADMIN_SALE["current_buyer"] = int(text)
        ADMIN_SALE["waiting_for_buyer"] = False
        ADMIN_SALE["waiting_for_amount"] = True

        await update.message.reply_text("💵 Send the SALE AMOUNT:")
        return

    if ADMIN_SALE["waiting_for_amount"]:
        try:
            amount = float(text)
        except Exception:
            await update.message.reply_text("❌ Invalid amount. Send a number.")
            return

        buyer_id = ADMIN_SALE["current_buyer"]

        ADMIN_SALE["waiting_for_buyer"] = False
        ADMIN_SALE["waiting_for_amount"] = False
        ADMIN_SALE["current_buyer"] = None

        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT referred_by FROM users WHERE user_id = ?", (buyer_id,))
        row = c.fetchone()
        conn.close()

        if not row or not row[0]:
            await update.message.reply_text("❌ This user has no referrer.")
            return

        referrer_id = row[0]

        add_reward(referrer_id, amount)

        caption = (
            "💵 **Sale recorded**\n\n"
            f"Buyer ID: `{buyer_id}`\n"
            f"Referrer ID: `{referrer_id}`\n"
            f"Credited: `${amount:.2f}`"
        )
        keyboard = build_admin_menu_keyboard()
        await update_ui(update, context, caption, keyboard)


# ================== BUTTON HANDLER ==================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_row = get_user(user_id)
    if not user_row:
        await query.message.reply_text("Use /start first.")
        return

    is_admin = user_id == ADMIN_ID
    data = query.data

    if data == "back_home":
        caption = build_main_menu_caption(user_row, context.bot.username, is_admin)
        keyboard = build_main_menu_keyboard(is_admin)
        await update_ui(query, context, caption, keyboard)
        return

    if data == "user_profile":
        caption = build_profile_caption(user_row)
        keyboard = build_profile_keyboard(is_admin)
        await update_ui(query, context, caption, keyboard)
        return

    if data == "user_refs":
        refs = get_referrals(user_id)
        caption = build_refs_caption(refs)
        keyboard = build_refs_keyboard()
        await update_ui(query, context, caption, keyboard)
        return

    if data == "user_items":
        try:
            await context.bot.forward_message(
                chat_id=user_id,
                from_chat_id=ITEMS_CHANNEL_ID,
                message_id=ITEMS_MESSAGE_ID,
            )
        except Exception:
            pass
        caption = "🛒 **Items**\n\nI’ve sent you the items list above.\n\nUse the buttons below to continue."
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back", callback_data="back_home")]]
        )
        await update_ui(query, context, caption, keyboard)
        return

    if data == "user_chat":
        caption = (
            "💬 **Join Chat**\n\n"
            "Tap the button below to join the community chat."
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "💬 Open Chat", url=CHAT_PUBLIC_LINK
                    )
                ],
                [InlineKeyboardButton("⬅️ Back", callback_data="back_home")],
            ]
        )
        await update_ui(query, context, caption, keyboard)
        return

    if data == "user_join_channel":
        _, _, _, _, is_verified, _, attempts_left = user_row
        if not is_verified:
            caption = "❌ You are not verified yet. Use /start."
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="back_home")]]
            )
            await update_ui(query, context, caption, keyboard)
            return

        if attempts_left <= 0:
            caption = (
                "❌ You used all 3 attempts.\nContact support for manual access."
            )
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="back_home")]]
            )
            await update_ui(query, context, caption, keyboard)
            return

        link = await generate_channel_invite(context)
        if link is None:
            caption = "⚠️ Could not create invite link. Try again in 10 seconds."
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back", callback_data="back_home")]]
            )
            await update_ui(query, context, caption, keyboard)
            return

        attempts_left = decrement_attempt(user_id)
        caption = (
            "🔗 **Channel Access**\n\n"
            f"Your private invite link:\n{link}\n\n"
            "⏳ Expires in 1 hour\n"
            "📝 Requires admin approval\n"
            f"🎯 Attempts left: `{attempts_left}`"
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back", callback_data="back_home")]]
        )
        await update_ui(query, context, caption, keyboard)
        return

    if data == "admin_panel" and is_admin:
        caption = build_admin_menu_caption()
        keyboard = build_admin_menu_keyboard()
        await update_ui(query, context, caption, keyboard)
        return

    if not is_admin:
        return

    if data == "admin_users":
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT user_id, username, is_verified FROM users")
        rows = c.fetchall()
        conn.close()

        if not rows:
            caption = "No users found."
        else:
            lines = ["👥 **User List**", ""]
            for uid, uname, ver in rows:
                uname = f"@{uname}" if uname else "(no username)"
                status = "✅" if ver else "❌"
                lines.append(f"{uname} — `{uid}` — {status}")
            caption = "\n".join(lines)

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
        )
        await update_ui(query, context, caption, keyboard)
        return

    if data == "admin_referrals":
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT user_id, username, referred_by FROM users")
        rows = c.fetchall()
        conn.close()

        if not rows:
            caption = "No users found."
        else:
            lines = ["🔗 **Referral Tracking**", ""]
            for uid, uname, ref in rows:
                uname = f"@{uname}" if uname else "(no username)"
                if ref:
                    conn = get_conn()
                    c = conn.cursor()
                    c.execute("SELECT username FROM users WHERE user_id = ?", (ref,))
                    ref_row = c.fetchone()
                    conn.close()
                    ref_name = f"@{ref_row[0]}" if ref_row and ref_row[0] else f"{ref}"
                    lines.append(f"{uname} → referred by {ref_name}")
                else:
                    lines.append(f"{uname} → no referrer")
            caption = "\n".join(lines)

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
        )
        await update_ui(query, context, caption, keyboard)
        return

    if data == "admin_credits":
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT user_id, username, reward, referred_by FROM users")
        rows = c.fetchall()
        conn.close()

        if not rows:
            caption = "No users found."
        else:
            lines = ["💰 **User Credits & Referrals**", ""]
            for uid, uname, reward, ref in rows:
                uname = f"@{uname}" if uname else "(no username)"
                if ref:
                    conn = get_conn()
                    c = conn.cursor()
                    c.execute("SELECT username FROM users WHERE user_id = ?", (ref,))
                    ref_row = c.fetchone()
                    conn.close()
                    ref_name = f"@{ref_row[0]}" if ref_row and ref_row[0] else f"{ref}"
                else:
                    ref_name = "None"
                lines.append(
                    f"{uname} — `${reward:.2f}` — referred by {ref_name}"
                )
            caption = "\n".join(lines)

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
        )
        await update_ui(query, context, caption, keyboard)
        return

    if data == "admin_sale":
        ADMIN_SALE["waiting_for_buyer"] = True
        ADMIN_SALE["waiting_for_amount"] = False
        ADMIN_SALE["current_buyer"] = None
        caption = (
            "💵 **Record Sale**\n\n"
            "Send the BUYER'S Telegram ID as a normal message."
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
        )
        await update_ui(query, context, caption, keyboard)
        return

    if data == "admin_broadcast":
        ADMIN_STATES["broadcast_waiting"] = True
        caption = (
            "📢 **Broadcast Mode**\n\n"
            "Send the message you want to broadcast to all verified users."
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
        )
        await update_ui(query, context, caption, keyboard)
        return

    if data == "admin_stats":
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE is_verified = 1")
        verified = c.fetchone()[0]
        conn.close()

        caption = (
            "📊 **Bot Stats**\n\n"
            f"Total users: `{total}`\n"
            f"Verified users: `{verified}`"
        )
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
        )
        await update_ui(query, context, caption, keyboard)
        return


# ================== TEXT FALLBACK ==================
async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id in captcha_pending:
        await captcha_answer(update, context)
        return

    if user.id == ADMIN_ID:
        if ADMIN_STATES["broadcast_waiting"]:
            await admin_broadcast_handler(update, context)
            return
        if ADMIN_SALE["waiting_for_buyer"] or ADMIN_SALE["waiting_for_amount"]:
            await admin_sale_handler(update, context)
            return

    await update.message.reply_text(
        "🖤 This bot works via buttons only.\nUse the UI below to navigate."
    )


# ================== MAIN ==================
def main():
    init_db()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("admin", admin))

    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_text))

    app.run_polling()


if __name__ == "__main__":
    main()
