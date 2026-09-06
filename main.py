import os
import asyncio
import logging
import html
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import Command, Text

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN is required")

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# In-memory storage for reports per user (chat). For production use persistent storage.
reports = {}

MENU_KEYS = [
    ("🚘 Данные авто", "menu:car"),
    ("💻 Диагностика", "menu:diagnosis"),
    ("🔋 Батарея", "menu:battery"),
    ("🎨 Кузов", "menu:body"),
    ("🪑 Салон", "menu:interior"),
    ("🛞 Колёса", "menu:wheels"),
    ("🛣 Тест-драйв", "menu:testdrive"),
    ("⚠️ Срочно", "menu:urgent"),
    ("📌 Вложения", "menu:attachments"),
    ("✅ Решение", "menu:decision"),
    ("📄 Сформировать отчёт", "menu:export"),
    ("🗑 Новый отчёт", "menu:new"),
]


def make_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    buttons = [InlineKeyboardButton(text=t, callback_data=cd) for t, cd in MENU_KEYS]
    kb.add(*buttons)
    return kb


def make_decision_kb():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("✅ Рекомендую", callback_data="decision:recommend"),
        InlineKeyboardButton("🤝 С торгом", callback_data="decision:trade"),
        InlineKeyboardButton("⛔ Не рекомендую", callback_data="decision:not_recommend"),
    )
    return kb


def new_report():
    return {
        "car": {
            "make_model_year": "",
            "mileage": "",
            "vin": "",
            "customer": "",
        },
        "diagnosis": "",
        "battery": "",
        "body": "",
        "interior": "",
        "wheels": "",
        "testdrive": "",
        "urgent": "",
        "attachments": "",
        "decision": "",
        "decision_comment": "",
        "awaiting": None,
        "car_step": 0,
    }


async def show_menu(chat_id):
    await bot.send_message(chat_id, "Выберите раздел для заполнения:", reply_markup=make_menu())


@dp.message(Command(commands=["start", "new"]))
async def cmd_start(message: Message):
    reports[message.chat.id] = new_report()
    await message.answer(
        "Создан новый отчёт.\n\nКоманда /start или /new создаёт новый отчёт и открывает меню.",
        reply_markup=make_menu(),
    )


@dp.callback_query(Text(startswith="menu:"))
async def cb_menu(query: CallbackQuery):
    chat_id = query.message.chat.id
    data = query.data.split(":", 1)[1]

    # Ensure report exists
    if chat_id not in reports:
        reports[chat_id] = new_report()

    r = reports[chat_id]

    if data == "car":
        r["car_step"] = 1
        r["awaiting"] = "car"
        await query.message.answer("1/4 — Введите: Марка, модель, год")
    elif data in ("diagnosis", "battery", "body", "interior", "wheels", "testdrive", "urgent", "attachments"):
        # These ask for one short text
        r["awaiting"] = data
        prompts = {
            "diagnosis": "Коротко опишите результаты диагностики",
            "battery": "Коротко опишите состояние батареи",
            "body": "Коротко опишите состояние кузова",
            "interior": "Коротко опишите салон",
            "wheels": "Коротко опишите колёса/шины",
            "testdrive": "Коротко опишите результаты тест-драйва",
            "urgent": "Коротко укажите срочные рекомендации",
            "attachments": "Коротко опишите вложения (фото/файлы)",
        }
        await query.message.answer(prompts[data])
    elif data == "decision":
        await query.message.answer("Выберите решение:", reply_markup=make_decision_kb())
    elif data == "export":
        await send_report(chat_id)
    elif data == "new":
        reports[chat_id] = new_report()
        await query.message.answer("Создан новый отчёт.", reply_markup=make_menu())

    await query.answer()


@dp.callback_query(Text(startswith="decision:"))
async def cb_decision_choice(query: CallbackQuery):
    chat_id = query.message.chat.id
    choice = query.data.split(":", 1)[1]
    if chat_id not in reports:
        reports[chat_id] = new_report()
    r = reports[chat_id]
    mapping = {
        "recommend": "✅ Рекомендую",
        "trade": "🤝 С торгом",
        "not_recommend": "⛔ Не рекомендую",
    }
    r["decision"] = mapping.get(choice, choice)
    r["awaiting"] = "decision_comment"
    await query.message.answer("Введите краткий итоговый комментарий (после выбора кнопки решения):")
    await query.answer()


@dp.message()
async def handle_text(message: Message):
    chat_id = message.chat.id
    text = message.text.strip()
    if chat_id not in reports:
        await message.answer("Сначала создайте отчёт командой /start")
        return

    r = reports[chat_id]
    awaiting = r.get("awaiting")

    if awaiting is None:
        await message.answer("Используйте меню для выбора раздела.", reply_markup=make_menu())
        return

    if awaiting == "car":
        step = r.get("car_step", 0)
        if step == 1:
            r["car"]["make_model_year"] = text
            r["car_step"] = 2
            await message.answer("2/4 — Введите: Пробег")
        elif step == 2:
            r["car"]["mileage"] = text
            r["car_step"] = 3
            await message.answer("3/4 — Введите: VIN")
        elif step == 3:
            r["car"]["vin"] = text
            r["car_step"] = 4
            await message.answer("4/4 — Введите: Имя заказчика")
        elif step == 4:
            r["car"]["customer"] = text
            r["car_step"] = 0
            r["awaiting"] = None
            await message.answer("Данные авто записаны.", reply_markup=make_menu())
        else:
            # safety fallback
            r["car_step"] = 0
            r["awaiting"] = None
            await message.answer("Непредвиденное состояние. Открылось меню.", reply_markup=make_menu())
        return

    if awaiting == "decision_comment":
        r["decision_comment"] = text
        r["awaiting"] = None
        await message.answer("Решение и комментарий записаны.", reply_markup=make_menu())
        return

    # Single-field sections
    if awaiting in ("diagnosis", "battery", "body", "interior", "wheels", "testdrive", "urgent", "attachments"):
        r[awaiting] = text
        r["awaiting"] = None
        await message.answer(f"{awaiting.capitalize()} записано.", reply_markup=make_menu())
        return

    # Fallback
    r["awaiting"] = None
    await message.answer("Я не ожидал этот текст — открылось меню.", reply_markup=make_menu())


async def send_report(chat_id: int):
    if chat_id not in reports:
        await bot.send_message(chat_id, "Отчёт не найден. Создайте новый: /start")
        return

    r = reports[chat_id]
    car = r["car"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def esc(s):
        return html.escape(s) if s else "—"

    html_text = (
        f"<b>ОТЧЁТ ПО ОСМОТРУ АВТОМОБИЛЯ</b>\n\n"
        f"<b>Автомобиль:</b> {esc(car.get('make_model_year'))}\n"
        f"<b>Пробег:</b> {esc(car.get('mileage'))}\n"
        f"<b>VIN:</b> {esc(car.get('vin'))}\n"
        f"<b>Заказчик:</b> {esc(car.get('customer'))}\n"
        f"<b>Дата осмотра:</b> {esc(now)}\n\n"
        f"<b>Диагностика:</b>\n{esc(r.get('diagnosis'))}\n\n"
        f"<b>Батарея:</b>\n{esc(r.get('battery'))}\n\n"
        f"<b>Кузов:</b>\n{esc(r.get('body'))}\n\n"
        f"<b>Салон:</b>\n{esc(r.get('interior'))}\n\n"
        f"<b>Колёса:</b>\n{esc(r.get('wheels'))}\n\n"
        f"<b>Тест-драйв:</b>\n{esc(r.get('testdrive'))}\n\n"
        f"<b>Срочные рекомендации:</b>\n{esc(r.get('urgent'))}\n\n"
        f"<b>Вложения:</b>\n{esc(r.get('attachments'))}\n\n"
        f"<b>Итог:</b>\n{esc(r.get('decision'))}\n\n"
        f"<b>Комментарий:</b>\n{esc(r.get('decision_comment'))}\n"
    )

    await bot.send_message(chat_id, html_text)
    # Optionally also send as a file or HTML document, but requirement is to send HTML report as message.


async def main():
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
