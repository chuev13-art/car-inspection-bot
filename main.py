import os
import asyncio
import logging
import json
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN is required")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# Directories
REPORTS_FILE = "reports.json"
ATTACHMENTS_DIR = "attachments"
EXPORTS_DIR = "exports"
os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
os.makedirs(EXPORTS_DIR, exist_ok=True)

# In-memory reports storage
reports: dict = {}
reports_lock = asyncio.Lock()

# --- Utilities ---

def load_reports():
    global reports
    if os.path.exists(REPORTS_FILE):
        try:
            with open(REPORTS_FILE, "r", encoding="utf-8") as f:
                reports = json.load(f)
        except Exception:
            logger.exception("Failed to load reports.json; starting empty")
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
        except Exception:
            logger.exception("Failed to save reports.json")


def make_main_menu() -> InlineKeyboardMarkup:
    # More compact and readable menu
    keys = [
        ("🚘 Данные авто", "menu:car"),
        ("🧾 Диагностика", "menu:diagnosis"),
        ("🔋 Батарея", "menu:battery"),
        ("🎨 Кузов", "menu:body"),
        ("🪑 Салон", "menu:interior"),
        ("🛞 Колёса", "menu:wheels"),
        ("🛣 Тест-драйв", "menu:testdrive"),
        ("⚠️ Срочно", "menu:urgent"),
        ("📌 Вложения", "menu:attachments"),
        ("✅ Решение", "menu:decision"),
        ("📄 Экспорт PDF", "menu:export"),
        ("🗑 Новый отчёт", "menu:new"),
        ("❓ Помощь", "menu:help"),
    ]
    buttons = [InlineKeyboardButton(text=t, callback_data=cd) for t, cd in keys]
    # arrange 3 buttons per row
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def make_decision_kb():
    buttons = [
        InlineKeyboardButton("✅ Рекомендую", callback_data="decision:recommend"),
        InlineKeyboardButton("🤝 С торгом", callback_data="decision:trade"),
        InlineKeyboardButton("⛔ Не рекомендую", callback_data="decision:not_recommend"),
    ]
    rows = [[b] for b in buttons]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def new_report_template() -> dict:
    return {
        "car": {
            "make": "",
            "model": "",
            "year": "",
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
        "attachments": [],
        "decision": "",
        "decision_comment": "",
        "awaiting": None,
        "car_step": 0,
    }


def ensure_report(chat_id: str) -> dict:
    if chat_id not in reports:
        reports[chat_id] = new_report_template()
    return reports[chat_id]


# --- Handlers ---

@dp.message(Command(commands=["start", "new"]))
async def cmd_start(message: Message):
    chat_id = str(message.chat.id)
    reports[chat_id] = new_report_template()
    await save_reports()
    await message.answer(
        "Создан новый отчёт. Используйте меню для заполнения полей.", reply_markup=make_main_menu()
    )


@dp.message(Command(commands=["help"]))
async def cmd_help(message: Message):
    await message.answer(
        "Команды:\n/start или /new — новый отчёт\n/Export или кнопка 'Экспорт PDF' — сформировать PDF и отправить\n"
        "Заполняйте разделы через меню. Вкладка 'Вложения' принимает фото и файлы.")


@dp.callback_query(F.data.startswith("menu:"))
async def cb_menu(query: CallbackQuery):
    chat_id = str(query.message.chat.id)
    action = query.data.split(":", 1)[1]
    r = ensure_report(chat_id)

    if action == "car":
        r["car_step"] = 1
        r["awaiting"] = "car"
        await query.message.answer("Данные авто — шаг 1/6. Введите марку (например, Toyota)")
    elif action in (
        "diagnosis",
        "battery",
        "body",
        "interior",
        "wheels",
        "testdrive",
        "urgent",
    ):
        r["awaiting"] = action
        prompts = {
            "diagnosis": "Опишите результаты диагностики (кратко)",
            "battery": "Опишите состояние батареи",
            "body": "Опишите состояние кузова",
            "interior": "Опишите салон",
            "wheels": "Опишите состояние колёс/шин",
            "testdrive": "Опишите результаты тест-драйва",
            "urgent": "Укажите срочные рекомендации",
        }
        await query.message.answer(prompts[action])
    elif action == "attachments":
        r["awaiting"] = "attachments"
        await query.message.answer(
            "Отправьте фото или файлы. После загрузки всех вложений нажмите 'Экспорт PDF' в меню."
        )
    elif action == "decision":
        await query.message.answer("Выберите решение:", reply_markup=make_decision_kb())
    elif action == "export":
        await query.message.answer("Формирую PDF, подождите...")
        await send_report(chat_id)
    elif action == "new":
        reports[chat_id] = new_report_template()
        await save_reports()
        await query.message.answer("Создан новый отчёт.", reply_markup=make_main_menu())
    elif action == "help":
        await query.message.answer("Используйте кнопки меню для заполнения отчёта. /help для справки.")

    await save_reports()
    await query.answer()


@dp.callback_query(F.data.startswith("decision:"))
async def cb_decision_choice(query: CallbackQuery):
    chat_id = str(query.message.chat.id)
    choice = query.data.split(":", 1)[1]
    r = ensure_report(chat_id)
    mapping = {
        "recommend": "✅ Рекомендую",
        "trade": "🤝 С торгом",
        "not_recommend": "⛔ Не рекомендую",
    }
    r["decision"] = mapping.get(choice, choice)
    r["awaiting"] = "decision_comment"
    await save_reports()
    await query.message.answer("Введите итоговый комментарий к решению:")
    await query.answer()


@dp.message()
async def handle_message(message: Message):
    chat_id = str(message.chat.id)
    r = ensure_report(chat_id)
    awaiting = r.get("awaiting")

    # Attachments handling
    if awaiting == "attachments":
        os.makedirs(os.path.join(ATTACHMENTS_DIR, chat_id), exist_ok=True)
        # Photo
        if message.photo:
            photo = message.photo[-1]
            file = await bot.get_file(photo.file_id)
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            filename = f"{ts}_{photo.file_id}.jpg"
            dest = os.path.join(ATTACHMENTS_DIR, chat_id, filename)
            try:
                await bot.download(file.file_path, destination=dest)
                r["attachments"].append({"type": "photo", "path": dest, "name": filename})
                await message.answer("Фото добавлено.")
                await save_reports()
            except Exception:
                logger.exception("Failed to download photo")
                await message.answer("Не удалось сохранить фото.")
            return

        # Document
        if message.document:
            doc = message.document
            file = await bot.get_file(doc.file_id)
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            _, ext = os.path.splitext(doc.file_name or "")
            filename = f"{ts}_{doc.file_id}{ext}"
            dest = os.path.join(ATTACHMENTS_DIR, chat_id, filename)
            try:
                await bot.download(file.file_path, destination=dest)
                r["attachments"].append({"type": "document", "path": dest, "name": filename})
                await message.answer("Файл добавлен.")
                await save_reports()
            except Exception:
                logger.exception("Failed to download document")
                await message.answer("Не удалось сохранить файл.")
            return

        # Text note for attachments
        if message.text:
            text = message.text.strip()
            if text:
                r["attachments"].append({"type": "note", "text": text})
                await save_reports()
                await message.answer("Описание добавлено к вложениям.")
            return

    # Non-attachments flows
    if awaiting is None:
        await message.answer("Выберите раздел в меню.", reply_markup=make_main_menu())
        return

    text = (message.text or "").strip()

    # Car multi-step: make, model, year, mileage, vin, customer
    if awaiting == "car":
        step = r.get("car_step", 0)
        if step == 1:
            r["car"]["make"] = text
            r["car_step"] = 2
            await message.answer("Шаг 2/6 — Введите модель")
        elif step == 2:
            r["car"]["model"] = text
            r["car_step"] = 3
            await message.answer("Шаг 3/6 — Введите год выпуска")
        elif step == 3:
            r["car"]["year"] = text
            r["car_step"] = 4
            await message.answer("Шаг 4/6 — Введите пробег")
        elif step == 4:
            r["car"]["mileage"] = text
            r["car_step"] = 5
            await message.answer("Шаг 5/6 — Введите VIN")
        elif step == 5:
            r["car"]["vin"] = text
            r["car_step"] = 6
            await message.answer("Шаг 6/6 — Введите имя заказчика")
        elif step == 6:
            r["car"]["customer"] = text
            r["car_step"] = 0
            r["awaiting"] = None
            await save_reports()
            await message.answer("Данные авто сохранены.", reply_markup=make_main_menu())
        else:
            # start
            r["car_step"] = 1
            r["awaiting"] = "car"
            await message.answer("Шаг 1/6 — Введите марку автомобиля")
        await save_reports()
        return

    if awaiting == "decision_comment":
        r["decision_comment"] = text
        r["awaiting"] = None
        await save_reports()
        await message.answer("Комментарий к решению сохранён.", reply_markup=make_main_menu())
        return

    if awaiting in (
        "diagnosis",
        "battery",
        "body",
        "interior",
        "wheels",
        "testdrive",
        "urgent",
    ):
        r[awaiting] = text
        r["awaiting"] = None
        await save_reports()
        await message.answer(f"{awaiting.capitalize()} сохранено.", reply_markup=make_main_menu())
        return

    # fallback
    r["awaiting"] = None
    await save_reports()
    await message.answer("Непонятный ввод — откройте меню.", reply_markup=make_main_menu())


# --- PDF generation and sending ---

def render_report_html(r: dict) -> str:
    car = r.get("car", {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def esc(s: Optional[str]) -> str:
        return s or "—"

    # simple HTML template used only to generate PDF, not saved as file
    html = f"""
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: DejaVu Sans, Arial, sans-serif; font-size: 12px; }}
        h1 {{ text-align: center; }}
        .section {{ margin-bottom: 12px; }}
        .label {{ font-weight: bold; }}
      </style>
    </head>
    <body>
      <h1>Отчёт по осмотру автомобиля</h1>
      <div class="section"><span class="label">Дата:</span> {now}</div>

      <div class="section"><span class="label">Марка:</span> {esc(car.get('make'))} &nbsp; <span class="label">Модель:</span> {esc(car.get('model'))} &nbsp; <span class="label">Год:</span> {esc(car.get('year'))}</div>
      <div class="section"><span class="label">Пробег:</span> {esc(car.get('mileage'))} &nbsp; <span class="label">VIN:</span> {esc(car.get('vin'))}</div>
      <div class="section"><span class="label">Заказчик:</span> {esc(car.get('customer'))}</div>

      <div class="section"><span class="label">Диагностика:</span><div>{esc(r.get('diagnosis'))}</div></div>
      <div class="section"><span class="label">Батарея:</span><div>{esc(r.get('battery'))}</div></div>
      <div class="section"><span class="label">Кузов:</span><div>{esc(r.get('body'))}</div></div>
      <div class="section"><span class="label">Салон:</span><div>{esc(r.get('interior'))}</div></div>
      <div class="section"><span class="label">Колёса:</span><div>{esc(r.get('wheels'))}</div></div>
      <div class="section"><span class="label">Тест-драйв:</span><div>{esc(r.get('testdrive'))}</div></div>
      <div class="section"><span class="label">Срочные рекомендации:</span><div>{esc(r.get('urgent'))}</div></div>

      <div class="section"><span class="label">Вложения:</span>
    """
    # attachments list
    if r.get("attachments"):
        for a in r["attachments"]:
            if a.get("type") == "note":
                html += f"<div>- {a.get('text')}</div>"
            else:
                html += f"<div>- {a.get('name')}</div>"
    else:
        html += "<div>—</div>"

    html += f"""
      </div>
      <div class="section"><span class="label">Итог:</span><div>{esc(r.get('decision'))}</div></div>
      <div class="section"><span class="label">Комментарий:</span><div>{esc(r.get('decision_comment'))}</div></div>
    </body>
    </html>
    """
    return html


async def generate_pdf_bytes(html_text: str) -> bytes:
    try:
        from weasyprint import HTML as WPHTML
    except Exception as e:
        logger.exception("weasyprint not available")
        raise RuntimeError("weasyprint not installed; install with 'pip install weasyprint' to enable PDF export") from e

    # generate PDF in memory
    try:
        pdf_bytes = await asyncio.to_thread(lambda: WPHTML(string=html_text).write_pdf())
        return pdf_bytes
    except Exception:
        logger.exception("Failed to render PDF")
        raise


async def send_pdf_to_chat(chat_id: str, pdf_bytes: bytes, filename: Optional[str] = None):
    # save to file then send as FSInputFile to avoid pydantic issues
    os.makedirs(os.path.join(EXPORTS_DIR, chat_id), exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = filename or f"report_{ts}.pdf"
    path = os.path.join(EXPORTS_DIR, chat_id, filename)

    try:
        with open(path, "wb") as f:
            f.write(pdf_bytes)
    except Exception:
        logger.exception("Failed to write PDF to disk")
        await bot.send_message(int(chat_id), "Не удалось сохранить PDF на сервере.")
        return

    try:
        pdf_file = FSInputFile(path, filename=filename)
        await bot.send_document(int(chat_id), document=pdf_file, caption="Отчёт (PDF)")
    except Exception:
        logger.exception("Failed to send PDF file")
        try:
            await bot.send_message(int(chat_id), "Не удалось отправить PDF-файл.")
        except Exception:
            logger.exception("Failed to notify user about failed PDF send")


async def send_report(chat_id: str):
    if chat_id not in reports:
        await bot.send_message(int(chat_id), "Отчёт не найден. Создайте новый: /start")
        return

    r = reports[chat_id]

    html_text = render_report_html(r)

    # generate PDF bytes
    try:
        pdf_bytes = await generate_pdf_bytes(html_text)
    except RuntimeError as e:
        await bot.send_message(int(chat_id), str(e))
        return
    except Exception:
        await bot.send_message(int(chat_id), "Ошибка при формировании PDF. Подробности в логах." )
        return

    # send attachments first (photos/documents)
    if r.get("attachments"):
        for a in r["attachments"]:
            try:
                if a.get("type") == "photo" and os.path.exists(a.get("path")):
                    photo = FSInputFile(a.get("path"), filename=a.get("name"))
                    await bot.send_photo(int(chat_id), photo)
                elif a.get("type") == "document" and os.path.exists(a.get("path")):
                    doc = FSInputFile(a.get("path"), filename=a.get("name"))
                    await bot.send_document(int(chat_id), document=doc)
            except Exception:
                logger.exception("Failed to send attachment %s", a)

    # send generated PDF
    await send_pdf_to_chat(chat_id, pdf_bytes)


# --- Entrypoint ---

async def main():
    load_reports()
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
