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
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive and running!"

def run_web_server():
    # Render дає порт через змінну PORT, або використовуємо 5000
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.start()

# --- 2. НАЛАШТУВАННЯ (Змінні середовища) ---
# Спробуємо взяти з config.py (для локального запуску)
# Якщо не вийде - беремо з Environment Variables (для Render)
try:
    import config
    SUPABASE_URL = config.SUPABASE_URL
    SUPABASE_KEY = config.SUPABASE_KEY
    TG_BOT_TOKEN = config.TG_BOT_TOKEN
except ImportError:
    SUPABASE_URL = os.environ.get("SUPABASE_URL")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
    TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")

# Налаштування логування
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- ТВОЇ ФУНКЦІЇ ---

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
            f"Вітаю у TimeHub!\n\n"
            f"Ваш профіль майстра налаштовано.\n"
            f"ID: `{user.id}`\n"
            f"Робочий час: 09:00 - 18:00\n\n"
            f"Оберіть дію нижче або використовуйте команди:\n"
            f"/add - Додати послугу\n"
            f"/list - Переглянути послуги\n"
            f"/bookings - Записи клієнтів\n"
            f"/help - Допомога",
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
            "Формат: /add Назва Ціна Час\n\n"
            "Приклад:\n"
            "`/add Манікюр 450 60`\n"
            "`/add Стрижка чоловіча 300 45`",
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
            f"✅ Послугу додано!\n\n"
            f"Назва: {name}\n"
            f"Вартість: {price} грн\n"
            f"Тривалість: {duration} хв",
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

        text = "Ваші послуги:\n\n"
        keyboard = []

        for service in services:
            text += f"{service['name']}\n"
            text += f"Ціна: {service['price']} грн | Час: {service['duration']} хв\n"
            text += f"ID: `{service['id']}`\n\n"

            keyboard.append([InlineKeyboardButton(
                f"Видалити '{service['name']}'",
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
            await update.message.reply_text("У вас поки немає записів клієнтів.")
            return

        text = "📅 **Записи клієнтів:**\n\n"

        for booking in bookings:
            service_name = booking['services']['name'] if booking.get('services') else "Невідома послуга"
            # Обробка часу
            raw_time = booking['booking_time']
            try:
                # Видаляємо зайві мілісекунди або таймзону для спрощення
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
        "Команди TimeHub:\n\n"
        "/start - Налаштувати профіль\n"
        "/add Назва Ціна Час - Додати послугу\n"
        "/list - Переглянути всі послуги\n"
        "/bookings - Записи клієнтів\n"
        "/help - Ця довідка\n\n"
        "Приклад додавання послуги:\n"
        "`/add Манікюр 450 60`"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def button_handler(query_update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = query_update.callback_query
    await query.answer()

    user = query.from_user

    if query.data == "help_add":
        await query.edit_message_text(
            "Щоб додати послугу, використайте команду:\n\n"
            "`/add Назва Ціна Час`\n\n"
            "Приклади:\n"
            "`/add Манікюр 450 60`\n"
            "`/add Стрижка жіноча 500 45`\n"
            "`/add Масаж обличчя 350 30`",
            parse_mode="Markdown"
        )

    elif query.data == "list_services":
        # (Той самий код, що в list_services, але для кнопки)
        try:
            response = supabase.table("services").select("*").eq("master_id", user.id).execute()
            services = response.data
            if not services:
                await query.edit_message_text("У вас поки немає послуг.")
                return
            text = "Ваші послуги:\n\n"
            for service in services:
                text += f"{service['name']}\n"
                text += f"Ціна: {service['price']} грн | Час: {service['duration']} хв\n\n"
            await query.edit_message_text(text, parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"Помилка: {e}")

    elif query.data == "view_bookings":
        # (Той самий код, що в view_bookings, але спрощено для кнопки)
        await query.message.reply_text("Натисніть /bookings щоб побачити деталі.")

    elif query.data.startswith("delete_"):
        service_id = query.data.split("_")[1]
        try:
            supabase.table("services").delete().eq("id", service_id).execute()
            await query.edit_message_text("✅ Послугу видалено!")
        except Exception as e:
            await query.edit_message_text(f"Помилка видалення: {e}")

if __name__ == '__main__':
    # ЗАПУСКАЄМО ФЕЙКОВИЙ СЕРВЕР У ФОНІ
    keep_alive()

    # ЗАПУСКАЄМО БОТА
    app = ApplicationBuilder().token(TG_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_service))
    app.add_handler(CommandHandler("list", list_services))
    app.add_handler(CommandHandler("bookings", view_bookings))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("TimeHub bot started successfully!")
    app.run_polling()