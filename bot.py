import logging
import asyncio
import os
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from supabase import create_client, Client
from datetime import datetime

# --- 1. ХИТРІСТЬ ДЛЯ RENDER (Фейковий сервер) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive and running!"

def run_web_server():
    # Render видає порт автоматично, ми його ловимо
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# --- 2. НАЛАШТУВАННЯ (Беремо з сервера або локально) ---
try:
    import config
    SUPABASE_URL = config.SUPABASE_URL
    SUPABASE_KEY = config.SUPABASE_KEY
    TG_BOT_TOKEN = config.TG_BOT_TOKEN
except ImportError:
    # На Render файлу config.py не буде, беремо зі змінних
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
    TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")

# Логування
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- ФУНКЦІЇ БОТА ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    print(f"Start command from {user.full_name}")

    master_data = {
        "id": user.id,
        "username": user.username or "Unknown",
        "full_name": user.full_name,
        "work_start": "09:00",
        "work_end": "18:00"
    }

    try:
        supabase.table("masters").upsert(master_data).execute()

        keyboard = [
            [InlineKeyboardButton("Додати послугу", callback_data="help_add")],
            [InlineKeyboardButton("Мої послуги", callback_data="list_services")],
            [InlineKeyboardButton("Записи клієнтів", callback_data="view_bookings")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"✅ **Вітаю у TimeHub!**\n\n"
            f"Бот працює на сервері 24/7.\n"
            f"Натисніть кнопку **Menu** зліва знизу, щоб відкрити запис.",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    except Exception as e:
        print(f"Error: {e}")
        await update.message.reply_text("Помилка бази даних.")

async def add_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    if len(args) < 3:
        await update.message.reply_text(
            "Формат: /add Назва Ціна Час\nПриклад: `/add Манікюр 450 60`",
            parse_mode="Markdown"
        )
        return

    price = args[-2]
    duration = args[-1]
    name = " ".join(args[:-2])

    service_data = {"master_id": user.id, "name": name, "price": int(price), "duration": int(duration)}

    try:
        supabase.table("services").insert(service_data).execute()
        await update.message.reply_text(f"✅ Послугу **{name}** додано!", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Помилка: {e}")

async def list_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        response = supabase.table("services").select("*").eq("master_id", user.id).execute()
        services = response.data
        if not services:
            await update.message.reply_text("Послуг немає. Додайте через /add")
            return
        
        text = "📋 **Ваші послуги:**\n\n"
        keyboard = []
        for service in services:
            text += f"🔹 {service['name']} — {service['price']} грн ({service['duration']} хв)\n"
            keyboard.append([InlineKeyboardButton(f"❌ Видалити {service['name']}", callback_data=f"delete_{service['id']}")])
        
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await update.message.reply_text(f"Помилка: {e}")

async def view_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        response = supabase.table("bookings").select("*, services(name)").eq("master_id", user.id).order("booking_time").execute()
        bookings = response.data
        if not bookings:
            await update.message.reply_text("📭 Записів поки немає.")
            return

        text = "📅 **Записи клієнтів:**\n\n"
        for booking in bookings:
            service_name = booking['services']['name'] if booking.get('services') else "—"
            raw_time = booking['booking_time']
            try:
                # Очистка часу від Z або +00:00
                if "+" in raw_time: booking_time = datetime.fromisoformat(raw_time.split("+")[0])
                elif "Z" in raw_time: booking_time = datetime.fromisoformat(raw_time.replace("Z", ""))
                else: booking_time = datetime.fromisoformat(raw_time)
                date_str = booking_time.strftime("%d.%m о %H:%M")
            except: date_str = raw_time

            client_phone = booking.get('client_phone', 'Немає')
            text += f"👤 {booking['client_name']}\n📞 `{client_phone}`\n💅 {service_name} — {date_str}\n──────────\n"

        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Помилка: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Команди:\n/start\n/add Назва Ціна Час\n/list\n/bookings")

async def button_handler(query_update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = query_update.callback_query
    await query.answer()
    user = query.from_user

    if query.data == "help_add":
        await query.edit_message_text("Пиши: `/add Манікюр 300 60`", parse_mode="Markdown")
    
    elif query.data == "list_services":
        # Виклик функції списку (дублювання логіки для кнопки)
        await list_services(query_update, context) # Це спрощено, краще викликати окремо
        
    elif query.data == "view_bookings":
        await query.message.reply_text("👇 Ваші записи:")
        await view_bookings(query_update, context)

    elif query.data.startswith("delete_"):
        service_id = query.data.split("_")[1]
        try:
            supabase.table("services").delete().eq("id", service_id).execute()
            await query.edit_message_text("✅ Послугу видалено!")
        except Exception as e:
            await query.edit_message_text(f"Помилка: {e}")

if __name__ == '__main__':
    # 1. Запускаємо сервер, щоб Render не вимкнув нас
    keep_alive()

    # 2. Запускаємо бота
    app = ApplicationBuilder().token(TG_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_service))
    app.add_handler(CommandHandler("list", list_services))
    app.add_handler(CommandHandler("bookings", view_bookings))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Bot is running...")
    app.run_polling()