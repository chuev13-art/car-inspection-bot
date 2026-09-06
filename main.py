import os
import asyncio
import logging
import html
import json
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

# Persistence
REPORTS_FILE = "reports.json"
ATTACHMENTS_DIR = "attachments"
reports = {}
reports_lock = asyncio.Lock()


def load_reports():
    global reports
    if os.path.exists(REPORTS_FILE):
        try:
            with open(REPORTS_FILE, "r", encoding="utf-8") as f:
                reports = json.load(f)
        except Exception as e:
            logging.warning("Failed to load reports.json: %s", e)
            reports = {}
    else:
        reports = {}


async def save_reports():
    async with reports_lock:
        try:
            tmp = REPORTS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(reports, f, ensure_ascii=False, indent=2)
            os.replace(tmp, REPORTS_FILE)
        except Exception as e:
            logging.exception("Failed to save reports: %s", e)


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
        "attachments": [],  # list of dicts: {type: 'photo'/'document'/'note', path/name or text}
        "decision": "",
        "decision_comment": "",
        "awaiting": None,
        "car_step": 0,
    }


async def show_menu(chat_id):
    await bot.send_message(chat_id, "Выберите раздел для заполнения:", reply_markup=make_menu())


@dp.message(Command(commands=["start", "new"]))
async def cmd_start(message: Message):
    chat_id = str(message.chat.id)
    reports[chat_id] = new_report()
    await save_reports()
    await message.answer(
        "Создан новый отчёт.\n\nКоманда /start или /new создаёт новый отчёт и открывает меню.",
        reply_markup=make_menu(),
    )


@dp.callback_query(Text(startswith="menu:"))
async def cb_menu(query: CallbackQuery):
    chat_id = str(query.message.chat.id)
    data = query.data.split(":", 1)[1]

    # Ensure report exists
    if chat_id not in reports:
        reports[chat_id] = new_report()
        await save_reports()

    r = reports[chat_id]

    if data == "car":
        r["car_step"] = 1
        r["awaiting"] = "car"
        await query.message.answer("1/4 — Введите: Марка, модель, год")
    elif data in ("diagnosis", "battery", "body", "interior", "wheels", "testdrive", "urgent"):
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
        }
        await query.message.answer(prompts[data])
    elif data == "attachments":
        r["awaiting"] = "attachments"
        await query.message.answer(
            "Отправьте фото/файлы вложений или краткое текстовое описание. Можно отправить несколько сообщений — каждое добавит один вложенный элемент. После завершения нажмите любую кнопку в меню.")
    elif data == "decision":
        await query.message.answer("Выберите решение:", reply_markup=make_decision_kb())
    elif data == "export":
        await send_report(chat_id)
    elif data == "new":
        reports[chat_id] = new_report()
        await save_reports()
        await query.message.answer("Создан новый отчёт.", reply_markup=make_menu())

    await save_reports()
    await query.answer()


@dp.callback_query(Text(startswith="decision:"))
async def cb_decision_choice(query: CallbackQuery):
    chat_id = str(query.message.chat.id)
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
    await save_reports()
    await query.message.answer("Введите краткий итоговый комментарий (после выбора кнопки решения):")
    await query.answer()


@dp.message()
async def handle_attachments_and_files(message: Message):
    # This handler checks if the user is currently sending attachments
    chat_id = str(message.chat.id)
    if chat_id not in reports:
        return  # let other handlers prompt to create a report

    r = reports[chat_id]
    if r.get("awaiting") != "attachments":
        return  # not in attachments mode

    # Ensure attachments dir exists
    os.makedirs(os.path.join(ATTACHMENTS_DIR, chat_id), exist_ok=True)

    # Photos
    if message.photo:
        # take largest photo
        photo = message.photo[-1]
        file_id = photo.file_id
        file = await bot.get_file(file_id)
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"{ts}_{file_id}.jpg"
        dest_path = os.path.join(ATTACHMENTS_DIR, chat_id, filename)
        try:
            await bot.download(file.file_path, destination=dest_path)
            r["attachments"].append({"type": "photo", "path": dest_path, "file_name": filename})
            await message.answer("Фото добавлено.")
            await save_reports()
        except Exception as e:
            logging.exception("Failed to download photo: %s", e)
            await message.answer("Не удалось сохранить фото.")
        return

    # Documents
    if message.document:
        doc = message.document
        file_id = doc.file_id
        file = await bot.get_file(file_id)
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        _, ext = os.path.splitext(doc.file_name or "")
        ext = ext or ""
        filename = f"{ts}_{file_id}{ext}"
        dest_path = os.path.join(ATTACHMENTS_DIR, chat_id, filename)
        try:
            await bot.download(file.file_path, destination=dest_path)
            r["attachments"].append({"type": "document", "path": dest_path, "file_name": filename})
            await message.answer("Файл добавлен.")
            await save_reports()
        except Exception as e:
            logging.exception("Failed to download document: %s", e)
            await message.answer("Не удалось сохранить файл.")
        return

    # Text description
    if message.text:
        text = message.text.strip()
        if text:
            r["attachments"].append({"type": "note", "text": text})
            await save_reports()
            await message.answer("Описание добавлено к вложениям.")
        return


@dp.message()
async def handle_text(message: Message):
    chat_id = str(message.chat.id)
    text = (message.text or "").strip()
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
            await save_reports()
            await message.answer("Данные авто записаны.", reply_markup=make_menu())
        else:
            # safety fallback
            r["car_step"] = 0
            r["awaiting"] = None
            await message.answer("Непредвиденное состояние. Открылось меню.", reply_markup=make_menu())
        await save_reports()
        return

    if awaiting == "decision_comment":
        r["decision_comment"] = text
        r["awaiting"] = None
        await save_reports()
        await message.answer("Решение и комментарий записаны.", reply_markup=make_menu())
        return

    # Single-field sections
    if awaiting in ("diagnosis", "battery", "body", "interior", "wheels", "testdrive", "urgent"):
        r[awaiting] = text
        r["awaiting"] = None
        await save_reports()
        await message.answer(f"{awaiting.capitalize()} записано.", reply_markup=make_menu())
        return

    # attachments handled by another handler; if we reach here while awaiting attachments, ignore
    if awaiting == "attachments":
        await message.answer("Отправьте фото/файлы или краткое текстовое описание. Можно отправить несколько сообщений — каждое добавит элемент вложений.")
        return

    # Fallback
    r["awaiting"] = None
    await save_reports()
    await message.answer("Я не ожидал этот текст — открылось меню.", reply_markup=make_menu())


async def send_report(chat_id: str):
    if chat_id not in reports:
        await bot.send_message(int(chat_id), "Отчёт не найден. Создайте новый: /start")
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
        f"<b>Вложения:</b>\n"
    )

    # Append attachments summary
    if r.get("attachments"):
        for a in r["attachments"]:
            if a.get("type") == "note":
                html_text += esc(a.get("text")) + "\n"
            else:
                html_text += esc(a.get("file_name")) + "\n"
    else:
        html_text += "—\n"

    html_text += (
        "\n"
        f"<b>Итог:</b>\n{esc(r.get('decision'))}\n\n"
        f"<b>Комментарий:</b>\n{esc(r.get('decision_comment'))}\n"
    )

    await bot.send_message(int(chat_id), html_text)

    # Send attachments as files/photos
    if r.get("attachments"):
        for a in r["attachments"]:
            try:
                if a.get("type") == "photo":
                    path = a.get("path")
                    if os.path.exists(path):
                        with open(path, "rb") as f:
                            await bot.send_photo(int(chat_id), f)
                elif a.get("type") == "document":
                    path = a.get("path")
                    if os.path.exists(path):
                        with open(path, "rb") as f:
                            await bot.send_document(int(chat_id), f)
                elif a.get("type") == "note":
                    await bot.send_message(int(chat_id), f"Вложение: {a.get('text')}")
            except Exception:
                logging.exception("Failed to send attachment")


async def main():
    # load persisted reports
    load_reports()
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
