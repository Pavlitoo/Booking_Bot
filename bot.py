import logging
import asyncio
import os
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from supabase import create_client, Client
from datetime import datetime

# --- 1. ФЕЙКОВИЙ ВЕБ-СЕРВЕР ДЛЯ RENDER ---
# Це потрібно, щоб Render не вимикав бота через 60 секунд
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive and running!"

def run_web_server():
    # Render автоматично видає порт через змінну PORT
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# --- 2. НАЛАШТУВАННЯ (РОЗУМНИЙ ІМПОРТ) ---
# На комп'ютері беремо з config.py, на сервері - з Environment Variables
try:
    import config
    SUPABASE_URL = config.SUPABASE_URL
    SUPABASE_KEY = config.SUPABASE_KEY
    TG_BOT_TOKEN = config.TG_BOT_TOKEN
except ImportError:
    # Якщо config.py не знайдено (на Render), беремо змінні середовища
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
    TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Підключення до бази
if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ ПОМИЛКА: Не знайдено ключі Supabase! Перевір змінні середовища.")
    
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- 3. ФУНКЦІЇ БОТА ---

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
            f"Ваш профіль налаштовано.\n"
            f"🆔 ID: `{user.id}`\n\n"
            f"👇 Натисніть **Menu** (зліва знизу) або оберіть дію:",
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
            "📝 **Як додати послугу:**\n\n"
            "Формат: `/add Назва Ціна Час`\n\n"
            "Приклад:\n"
            "`/add Манікюр 450 60`\n"
            "`/add Стрижка 300 45`",
            parse_mode="Markdown"
        )
        return

    price = args[-2]
    duration = args[-1]
    name = " ".join(args[:-2])

    print(f"Adding service: {name}, {price} UAH, {duration} min")

    service_data = {
        "master_id": user.id,
        "name": name,
        "price": int(price),
        "duration": int(duration)
    }

    try:
        supabase.table("services").insert(service_data).execute()
        await update.message.reply_text(
            f"✅ **Послугу додано!**\n\n"
            f"💅 Назва: {name}\n"
            f"💰 Вартість: {price} грн\n"
            f"⏱ Тривалість: {duration} хв",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Error adding service: {e}")
        await update.message.reply_text(f"Не вдалося додати послугу: {e}")

async def list_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    try:
        response = supabase.table("services").select("*").eq("master_id", user.id).execute()
        services = response.data

        if not services:
            await update.message.reply_text(
                "У вас поки немає послуг.\n"
                "Додайте першу командою:\n"
                "`/add Назва Ціна Час`",
                parse_mode="Markdown"
            )
            return

        text = "📋 **Ваші послуги:**\n\n"
        keyboard = []

        for service in services:
            text += f"🔹 {service['name']} — {service['price']} грн ({service['duration']} хв)\n"
            keyboard.append([InlineKeyboardButton(
                f"❌ Видалити {service['name']}",
                callback_data=f"delete_{service['id']}"
            )])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)

    except Exception as e:
        print(f"Error listing services: {e}")
        await update.message.reply_text("Помилка отримання послуг.")

async def view_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    try:
        response = supabase.table("bookings").select(
            "*, services(name, price, duration)"
        ).eq("master_id", user.id).order("booking_time").execute()

        bookings = response.data

        if not bookings:
            await update.message.reply_text("📭 У вас поки немає записів клієнтів.")
            return

        text = "📅 **Записи клієнтів:**\n\n"

        for booking in bookings:
            service_name = booking['services']['name'] if booking.get('services') else "Невідома послуга"
            raw_time = booking['booking_time']
            
            # Обробка формату часу
            try:
                if "+" in raw_time:
                    booking_time = datetime.fromisoformat(raw_time.split("+")[0])
                elif "Z" in raw_time:
                    booking_time = datetime.fromisoformat(raw_time.replace("Z", ""))
                else:
                    booking_time = datetime.fromisoformat(raw_time)
                date_str = booking_time.strftime("%d.%m о %H:%M")
            except:
                date_str = raw_time

            client_phone = booking.get('client_phone', 'Без телефону')

            text += f"👤 **{booking['client_name']}**\n"
            text += f"📞 `{client_phone}`\n"
            text += f"💅 {service_name}\n"
            text += f"🕒 {date_str}\n"
            text += f"──────────────\n"

        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as e:
        print(f"Error viewing bookings: {e}")
        await update.message.reply_text(f"Помилка: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "⚙️ **Команди TimeHub:**\n\n"
        "/start - Налаштування профілю\n"
        "/add - Додати нову послугу\n"
        "/list - Мої послуги (та видалення)\n"
        "/bookings - Перегляд записів\n\n"
        "💡 *Приклад додавання:*\n"
        "`/add Манікюр 450 60`"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def button_handler(query_update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = query_update.callback_query
    await query.answer()

    user = query.from_user

    if query.data == "help_add":
        await query.edit_message_text(
            "📝 **Як додати послугу:**\n\n"
            "`/add Назва Ціна Час`\n\n"
            "Приклади:\n"
            "`/add Манікюр 450 60`\n"
            "`/add Стрижка 300 45`",
            parse_mode="Markdown"
        )

    elif query.data == "list_services":
        # Викликаємо логіку списку (копія функції для кнопки)
        try:
            response = supabase.table("services").select("*").eq("master_id", user.id).execute()
            services = response.data
            if not services:
                await query.edit_message_text("Послуг немає.")
                return
            
            text = "📋 **Ваші послуги:**\n\n"
            for s in services:
                text += f"🔹 {s['name']} — {s['price']} грн ({s['duration']} хв)\n"
            await query.edit_message_text(text, parse_mode="Markdown")
        except:
            await query.edit_message_text("Помилка завантаження.")

    elif query.data == "view_bookings":
        await query.message.reply_text("👇 Ваші записи (завантажую...):")
        # Викликаємо функцію перегляду
        await view_bookings(query_update, context)

    elif query.data.startswith("delete_"):
        service_id = query.data.split("_")[1]
        try:
            supabase.table("services").delete().eq("id", service_id).execute()
            await query.edit_message_text("✅ Послугу успішно видалено!")
        except Exception as e:
            await query.edit_message_text(f"Помилка видалення: {e}")

if __name__ == '__main__':
    # 1. ЗАПУСКАЄМО ФЕЙКОВИЙ ВЕБ-СЕРВЕР (ЩОБ RENDER НЕ ВИМИКАВ БОТА)
    keep_alive()

    # 2. ЗАПУСКАЄМО БОТА
    print("🤖 TimeHub Bot запускається...")
    
    if not TG_BOT_TOKEN:
        print("❌ КРИТИЧНА ПОМИЛКА: Токен не знайдено! Бот не може стартувати.")
    else:
        app_bot = ApplicationBuilder().token(TG_BOT_TOKEN).build()

        app_bot.add_handler(CommandHandler("start", start))
        app_bot.add_handler(CommandHandler("add", add_service))
        app_bot.add_handler(CommandHandler("list", list_services))
        app_bot.add_handler(CommandHandler("bookings", view_bookings))
        app_bot.add_handler(CommandHandler("help", help_command))
        app_bot.add_handler(CallbackQueryHandler(button_handler))

        print("✅ Бот працює! Очікую повідомлення...")
        app_bot.run_polling()