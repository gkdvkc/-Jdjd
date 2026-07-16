import asyncio
import logging
import sqlite3
import json
import base64
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

# ========== تنظیمات ==========
TOKEN = "8810741889:AAEjL5vlgL0mxZeAmRGWtDuU7kKFCKwJQ2M"
MARZBAN_URL = "http://localhost:8000/api"  # اگه پنل جای دیگه‌ست عوض کن
ADMIN_IDS = [123456789]  # آیدی عددی خودت رو بذار

# ========== لاگ ==========
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== دیتابیس ==========
DB_NAME = "bot.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT UNIQUE,
            password TEXT,
            expires_at TEXT,
            traffic_limit INTEGER,
            used_traffic INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def db_execute(query, params=(), fetchone=False, fetchall=False):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(query, params)
    if fetchone:
        result = c.fetchone()
    elif fetchall:
        result = c.fetchall()
    else:
        result = None
    conn.commit()
    conn.close()
    return result

def get_user(telegram_id: int):
    row = db_execute("SELECT * FROM users WHERE id=?", (telegram_id,), fetchone=True)
    if row:
        return {"id": row[0], "username": row[1], "first_name": row[2], "last_name": row[3], "created_at": row[4]}
    return None

def add_user(telegram_id: int, username: str, first_name: str, last_name: str):
    db_execute("INSERT OR IGNORE INTO users (id, username, first_name, last_name, created_at) VALUES (?, ?, ?, ?, ?)",
               (telegram_id, username, first_name, last_name, datetime.now().isoformat()))

def get_configs_by_user(telegram_id: int):
    rows = db_execute("SELECT * FROM configs WHERE user_id=?", (telegram_id,), fetchall=True)
    return [{"id": r[0], "user_id": r[1], "username": r[2], "password": r[3], "expires_at": r[4],
             "traffic_limit": r[5], "used_traffic": r[6], "status": r[7], "created_at": r[8]} for r in rows]

def add_config(telegram_id: int, username: str, password: str, expires_at: str, traffic_limit: int):
    db_execute("INSERT INTO configs (user_id, username, password, expires_at, traffic_limit, created_at) VALUES (?, ?, ?, ?, ?, ?)",
               (telegram_id, username, password, expires_at, traffic_limit, datetime.now().isoformat()))

def update_config_status(username: str, status: str):
    db_execute("UPDATE configs SET status=? WHERE username=?", (status, username))

def delete_config(username: str):
    db_execute("DELETE FROM configs WHERE username=?", (username,))

def get_active_configs():
    rows = db_execute("SELECT * FROM configs WHERE status='active'", fetchall=True)
    return [{"id": r[0], "user_id": r[1], "username": r[2], "password": r[3], "expires_at": r[4],
             "traffic_limit": r[5], "used_traffic": r[6], "status": r[7], "created_at": r[8]} for r in rows]

def is_admin(telegram_id: int) -> bool:
    return telegram_id in ADMIN_IDS

async def marzban_request(method: str, endpoint: str, data: Dict = None):
    url = f"{MARZBAN_URL}{endpoint}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.request(method, url, json=data, headers=headers) as resp:
                if resp.status in (200, 201):
                    return await resp.json()
                else:
                    logger.error(f"Marzban API error: {resp.status} - {await resp.text()}")
                    return None
        except Exception as e:
            logger.error(f"Marzban request failed: {e}")
            return None

async def create_marzban_user(username: str, password: str, expire_days: int, traffic_gb: int):
    expire_date = (datetime.now() + timedelta(days=expire_days)).isoformat() + "Z"
    payload = {
        "username": username,
        "password": password,
        "expire": expire_date,
        "data_limit": traffic_gb * 1024**3,
        "status": "active"
    }
    return await marzban_request("POST", "/user", payload)

async def get_marzban_user(username: str):
    return await marzban_request("GET", f"/user/{username}")

async def update_marzban_user(username: str, data: Dict):
    return await marzban_request("PUT", f"/user/{username}", data)

async def delete_marzban_user(username: str) -> bool:
    result = await marzban_request("DELETE", f"/user/{username}")
    return result is not None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id, user.username, user.first_name, user.last_name)
    keyboard = [
        [InlineKeyboardButton("📋 ایجاد کانفیگ", callback_data="create")],
        [InlineKeyboardButton("🔗 دریافت سابسکریپشن", callback_data="subscribe")],
        [InlineKeyboardButton("📊 وضعیت کاربری", callback_data="status")],
        [InlineKeyboardButton("🔄 تمدید کانفیگ", callback_data="renew")],
    ]
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("⚙️ پنل مدیریت", callback_data="admin_panel")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"سلام {user.first_name} 👋\nبه ربات مدیریت پنل کانفیگ خوش آمدید.\nلطفاً یکی از گزینه‌های زیر را انتخاب کنید:",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "create":
        await query.edit_message_text("⏳ لطفاً مدت اعتبار (به روز) و حجم ترافیک (به گیگابایت) را وارد کنید.\nمثال: `30 100`")
        context.user_data["awaiting_create"] = True
        return

    elif data == "subscribe":
        configs = get_configs_by_user(user_id)
        active_configs = [c for c in configs if c["status"] == "active"]
        if not active_configs:
            await query.edit_message_text("❌ هیچ کانفیگ فعالی ندارید.")
            return
        username = active_configs[0]["username"]
        sub_link = f"http://localhost:8000/sub/{username}?format=clash"
        await query.edit_message_text(f"🔗 لینک سابسکریپشن شما:\n`{sub_link}`", parse_mode="Markdown")
        return

    elif data == "status":
        configs = get_configs_by_user(user_id)
        if not configs:
            await query.edit_message_text("❌ هیچ کانفیگی ندارید.")
            return
        text = "📊 وضعیت کانفیگ‌های شما:\n\n"
        for cfg in configs:
            username = cfg["username"]
            status = "✅ فعال" if cfg["status"] == "active" else "❌ غیرفعال"
            expires = cfg["expires_at"][:10] if cfg["expires_at"] else "نامحدود"
            used = cfg["used_traffic"] // (1024**3) if cfg["used_traffic"] else 0
            limit = cfg["traffic_limit"] // (1024**3) if cfg["traffic_limit"] else "نامحدود"
            text += f"👤 {username}\nوضعیت: {status}\nانقضا: {expires}\nمصرف: {used} GB از {limit} GB\n\n"
        await query.edit_message_text(text)
        return

    elif data == "renew":
        configs = get_configs_by_user(user_id)
        active = [c for c in configs if c["status"] == "active"]
        if not active:
            await query.edit_message_text("❌ هیچ کانفیگ فعالی برای تمدید ندارید.")
            return
        keyboard = []
        for cfg in active:
            keyboard.append([InlineKeyboardButton(cfg["username"], callback_data=f"renew_{cfg['username']}")])
        keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")])
        await query.edit_message_text("کانفیگ مورد نظر برای تمدید را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data.startswith("renew_"):
        username = data.split("_", 1)[1]
        context.user_data["renew_username"] = username
        context.user_data["awaiting_renew"] = True
        await query.edit_message_text("⏳ تعداد روزهای تمدید را وارد کنید (مثلاً 30):")
        return

    elif data == "admin_panel":
        if not is_admin(user_id):
            await query.edit_message_text("⛔ شما دسترسی ادمین ندارید.")
            return
        keyboard = [
            [InlineKeyboardButton("📋 لیست کاربران", callback_data="admin_list")],
            [InlineKeyboardButton("❌ مسدود/رفع مسدود", callback_data="admin_ban")],
            [InlineKeyboardButton("🗑 حذف کاربر", callback_data="admin_delete")],
            [InlineKeyboardButton("🔄 پاک‌سازی خودکار", callback_data="admin_cleanup")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")]
        ]
        await query.edit_message_text("⚙️ پنل مدیریت:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif data == "admin_list":
        if not is_admin(user_id): return
        configs = get_active_configs()
        if not configs:
            await query.edit_message_text("هیچ کاربر فعالی وجود ندارد.")
            return
        text = "📋 لیست کاربران فعال:\n\n"
        for cfg in configs:
            text += f"👤 {cfg['username']} - انقضا: {cfg['expires_at'][:10]} - مصرف: {cfg['used_traffic']//(1024**3)} GB\n"
        await query.edit_message_text(text)
        return

    elif data == "admin_ban":
        if not is_admin(user_id): return
        await query.edit_message_text("نام کاربری مورد نظر برای تغییر وضعیت را وارد کنید:")
        context.user_data["admin_ban"] = True
        return

    elif data == "admin_delete":
        if not is_admin(user_id): return
        await query.edit_message_text("نام کاربری مورد نظر برای حذف را وارد کنید:")
        context.user_data["admin_delete"] = True
        return

    elif data == "admin_cleanup":
        if not is_admin(user_id): return
        now = datetime.now().isoformat()
        expired = db_execute("SELECT username FROM configs WHERE expires_at < ? AND status='active'", (now,), fetchall=True)
        count = 0
        for row in expired:
            username = row[0]
            if await delete_marzban_user(username):
                update_config_status(username, "expired")
                count += 1
        await query.edit_message_text(f"✅ {count} کاربر منقضی پاک‌سازی شدند.")
        return

    elif data == "main_menu":
        await start(update, context)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if context.user_data.get("awaiting_create"):
        context.user_data["awaiting_create"] = False
        parts = text.split()
        if len(parts) != 2:
            await update.message.reply_text("❌ فرمت صحیح نیست. دو عدد وارد کنید: روز و گیگ (مثال: 30 100)")
            return
        try:
            days = int(parts[0])
            gb = int(parts[1])
        except ValueError:
            await update.message.reply_text("❌ لطفاً اعداد معتبر وارد کنید.")
            return
        username = f"user_{user_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        password = base64.b64encode(os.urandom(12)).decode()[:12]
        result = await create_marzban_user(username, password, days, gb)
        if result:
            expires_at = (datetime.now() + timedelta(days=days)).isoformat()
            add_config(user_id, username, password, expires_at, gb * 1024**3)
            await update.message.reply_text(f"✅ کانفیگ با موفقیت ایجاد شد!\n\n👤 نام کاربری: `{username}`\n🔑 رمز: `{password}`\n📅 انقضا: {expires_at[:10]}\n📊 حجم: {gb} GB\n\n🔗 لینک سابسکریپشن: `http://localhost:8000/sub/{username}?format=clash`", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ خطا در ایجاد کانفیگ. لطفاً با ادمین تماس بگیرید.")
        await start(update, context)
        return

    if context.user_data.get("awaiting_renew"):
        context.user_data["awaiting_renew"] = False
        username = context.user_data.get("renew_username")
        try:
            days = int(text)
        except ValueError:
            await update.message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید.")
            return
        user_data = await get_marzban_user(username)
        if not user_data:
            await update.message.reply_text("❌ کاربر در پنل یافت نشد.")
            return
        new_expire = (datetime.now() + timedelta(days=days)).isoformat() + "Z"
        update_data = {"expire": new_expire}
        result = await update_marzban_user(username, update_data)
        if result:
            new_expire_local = (datetime.now() + timedelta(days=days)).isoformat()
            db_execute("UPDATE configs SET expires_at=? WHERE username=?", (new_expire_local, username))
            await update.message.reply_text(f"✅ کانفیگ {username} به مدت {days} روز تمدید شد. انقضای جدید: {new_expire_local[:10]}")
        else:
            await update.message.reply_text("❌ خطا در تمدید.")
        await start(update, context)
        return

    if context.user_data.get("admin_ban"):
        context.user_data["admin_ban"] = False
        username = text.strip()
        row = db_execute("SELECT status FROM configs WHERE username=?", (username,), fetchone=True)
        if not row:
            await update.message.reply_text("❌ کاربر یافت نشد.")
            return
        current_status = row[0]
        new_status = "disabled" if current_status == "active" else "active"
        update_data = {"status": new_status}
        result = await update_marzban_user(username, update_data)
        if result:
            update_config_status(username, new_status)
            await update.message.reply_text(f"✅ وضعیت کاربر {username} به {new_status} تغییر یافت.")
        else:
            await update.message.reply_text("❌ خطا در تغییر وضعیت.")
        await start(update, context)
        return

    if context.user_data.get("admin_delete"):
        context.user_data["admin_delete"] = False
        username = text.strip()
        if await delete_marzban_user(username):
            delete_config(username)
            await update.message.reply_text(f"✅ کاربر {username} حذف شد.")
        else:
            await update.message.reply_text("❌ خطا در حذف.")
        await start(update, context)
        return

    await update.message.reply_text("لطفاً از دکمه‌های منو استفاده کنید یا دستور /start را بزنید.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("برای استفاده از ربات، روی /start کلیک کنید و از منو انتخاب کنید.")

async def scheduled_cleanup(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now().isoformat()
    expired = db_execute("SELECT username FROM configs WHERE expires_at < ? AND status='active'", (now,), fetchall=True)
    for row in expired:
        username = row[0]
        if await delete_marzban_user(username):
            update_config_status(username, "expired")
            logger.info(f"Auto-cleaned expired user: {username}")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    job_queue = app.job_queue
    job_queue.run_repeating(scheduled_cleanup, interval=21600, first=10)
    logger.info("ربات راه‌اندازی شد...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
