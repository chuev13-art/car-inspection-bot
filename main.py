import asyncio
import copy
import html
import json
import logging
import os
import re
import base64
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN is required")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

REPORTS_FILE = Path("reports.json")
ATTACHMENTS_DIR = Path("attachments")
EXPORTS_DIR = Path("exports")
ATTACHMENTS_DIR.mkdir(exist_ok=True)
EXPORTS_DIR.mkdir(exist_ok=True)

reports: dict[str, dict[str, Any]] = {}
reports_lock = asyncio.Lock()

TEXT_SECTIONS = {
    "diagnosis": "🧾 Диагностика",
    "battery": "🔋 Батарея",
    "body": "🎨 Кузов",
    "interior": "🪑 Салон",
    "wheels": "🛞 Колёса",
    "testdrive": "🛣 Тест-драйв",
    "urgent": "⚠️ Срочно",
}

SECTION_PROMPTS = {
    "diagnosis": "Опишите результаты компьютерной диагностики: ошибки, параметры, замечания.",
    "battery": "Опишите состояние тяговой/12V батареи: SOH, разброс ячеек, ошибки, заряд, замечания.",
    "body": "Опишите кузов: окрасы, толщины ЛКП, ремонты, коррозия, геометрия, стёкла.",
    "interior": "Опишите салон: износ, работоспособность опций, запахи, следы воды/разбора.",
    "wheels": "Опишите шины и колёса: сезон, остаток протектора, год, повреждения, диски.",
    "testdrive": "Опишите тес��-драйв: запуск, ДВС/КПП, подвеска, рулевое, тормоза, вибрации.",
    "urgent": "Укажите критичные риски и срочные рекомендации. Если их нет — напишите «Нет».",
}

ATTACHMENT_TAGS = {
    "general": "📌 Общие",
    "body": "🎨 Кузов",
    "interior": "🪑 Салон",
    "battery": "🔋 Батарея",
    "diagnosis": "🧾 Диагностика",
    "wheels": "🛞 Колёса",
    "documents": "📄 Документы",
}


def new_report_template() -> dict[str, Any]:
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "car": {
            "make": "",
            "model": "",
            "year": "",
            "mileage": "",
            "vin": "",
            "customer": "",
            "price": "",
            "seller": "",
        },
        "diagnosis": "",
        "battery": "",
        "body": "",
        "interior": "",
        "wheels": "",
        "testdrive": "",
        "urgent": "",
        "attachments": [],
        "attachment_tag": "general",
        "decision": "",
        "decision_comment": "",
        "awaiting": None,
    }


def normalize_report(report: dict[str, Any]) -> dict[str, Any]:
    base = new_report_template()
    base.update(report if isinstance(report, dict) else {})
    if not isinstance(base.get("car"), dict):
        base["car"] = {}
    car_base = new_report_template()["car"]
    car_base.update(base["car"])
    base["car"] = car_base
    if not isinstance(base.get("attachments"), list):
        base["attachments"] = []
    base["awaiting"] = None
    base.setdefault("attachment_tag", "general")
    return base


def load_reports() -> None:
    global reports
    if not REPORTS_FILE.exists():
        reports = {}
        return
    try:
        with REPORTS_FILE.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        reports = {str(chat_id): normalize_report(report) for chat_id, report in raw.items()}
    except Exception:
        logger.exception("Failed to load reports.json; starting empty")
        reports = {}


async def save_reports() -> None:
    async with reports_lock:
        try:
            tmp = REPORTS_FILE.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(reports, f, ensure_ascii=False, indent=2)
            os.replace(tmp, REPORTS_FILE)
        except Exception:
            logger.exception("Failed to save reports")


def ensure_report(chat_id: str) -> dict[str, Any]:
    if chat_id not in reports:
        reports[chat_id] = new_report_template()
    return reports[chat_id]


def touch(report: dict[str, Any]) -> None:
    report["updated_at"] = datetime.now().isoformat(timespec="seconds")


# Dynamic main menu with checkmarks
def make_main_menu(report: dict[str, Any]) -> InlineKeyboardMarkup:
    def L(key: Optional[str], label: str) -> str:
        if key == "car":
            filled = any(str(v).strip() for v in report.get("car", {}).values())
        else:
            filled = is_filled(report.get(key))
        return f"{label} {'✅' if filled else '◻️'}"

    rows = [
        [
            InlineKeyboardButton(text=L("car", "🚘 Авто"), callback_data="menu:car"),
            InlineKeyboardButton(text=L("diagnosis", "🧾 Диагностика"), callback_data="menu:diagnosis"),
        ],
        [
            InlineKeyboardButton(text=L("battery", "🔋 Батарея"), callback_data="menu:battery"),
            InlineKeyboardButton(text=L("body", "🎨 Кузов"), callback_data="menu:body"),
        ],
        [
            InlineKeyboardButton(text=L("interior", "🪑 Салон"), callback_data="menu:interior"),
            InlineKeyboardButton(text=L("wheels", "🛞 Колёса"), callback_data="menu:wheels"),
        ],
        [
            InlineKeyboardButton(text=L("testdrive", "🛣 Тест-драйв"), callback_data="menu:testdrive"),
            InlineKeyboardButton(text=L("urgent", "⚠️ Риски"), callback_data="menu:urgent"),
        ],
        [
            InlineKeyboardButton(text=L(None, "📎 Фото / файлы"), callback_data="menu:attachments"),
            InlineKeyboardButton(text=L("decision", "✅ Решение"), callback_data="menu:decision"),
        ],
        [
            InlineKeyboardButton(text="📋 Сводка", callback_data="menu:summary"),
            InlineKeyboardButton(text="📄 PDF", callback_data="menu:export"),
        ],
        [
            InlineKeyboardButton(text="🔁 Копия отчёта", callback_data="menu:clone"),
            InlineKeyboardButton(text="🆕 Новый", callback_data="menu:new"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ В меню", callback_data="menu:home")]]
    )


def section_kb(section: str, has_value: bool) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(
            text="✏️ Изменить" if has_value else "➕ Заполнить",
            callback_data=f"edit:{section}",
        )
    ]
    if has_value:
        row.append(InlineKeyboardButton(text="🗑 Очистить", callback_data=f"clear:{section}"))
    return InlineKeyboardMarkup(
        inline_keyboard=[row, [InlineKeyboardButton(text="↩️ В меню", callback_data="menu:home")]]
    )


def car_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Ввести / изменить всё", callback_data="edit:car")],
            [InlineKeyboardButton(text="🗑 Очистить данные авто", callback_data="clear:car")],
            [InlineKeyboardButton(text="↩️ В меню", callback_data="menu:home")],
        ]
    )


def decision_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Рекомендую", callback_data="decision:recommend")],
            [InlineKeyboardButton(text="🤝 Рекомендую с торгом", callback_data="decision:trade")],
            [InlineKeyboardButton(text="⛔ Не рекомендую", callback_data="decision:not_recommend")],
            [InlineKeyboardButton(text="↩️ В меню", callback_data="menu:home")],
        ]
    )


def decision_comment_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить комментарий", callback_data="decision:skip_comment")],
            [InlineKeyboardButton(text="↩️ В меню", callback_data="menu:home")],
        ]
    )


def attachment_tags_kb() -> InlineKeyboardMarkup:
    rows = []
    items = list(ATTACHMENT_TAGS.items())
    for i in range(0, len(items), 2):
        rows.append(
            [
                InlineKeyboardButton(text=label, callback_data=f"tag:{key}")
                for key, label in items[i : i + 2]
            ]
        )
    rows.append([InlineKeyboardButton(text="↩️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def attachment_mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏷 Изменить раздел", callback_data="menu:attachments")],
            [InlineKeyboardButton(text="✅ Готово", callback_data="attach:done")],
            [InlineKeyboardButton(text="↩️ В меню", callback_data="menu:home")],
        ]
    )


def summary_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Сформировать PDF", callback_data="export:confirm")],
            [InlineKeyboardButton(text="↩️ В меню", callback_data="menu:home")],
        ]
    )


def car_filled(car: dict[str, Any]) -> bool:
    return any(str(value).strip() for value in car.values())


def is_filled(value: Any) -> bool:
    if isinstance(value, dict):
        return car_filled(value)
    return bool(str(value or "").strip())


def report_summary(report: dict[str, Any]) -> str:
    car = report["car"]
    car_name = " ".join(filter(None, [car.get("make", "").strip(), car.get("model", "").strip()]))
    lines = ["<b>📋 Сводка текущего отчёта</b>"]
    lines.append(f"🚘 Авто: {'✅ ' + html.escape(car_name) if car_name else '❌ не заполнено'}")
    for key, label in TEXT_SECTIONS.items():
        lines.append(f"{label}: {'✅' if is_filled(report.get(key)) else '❌'}")
    lines.append(f"📎 Вложения: {len(report.get('attachments', []))}")
    lines.append(f"✅ Решение: {'✅ ' + html.escape(report['decision']) if report.get('decision') else '❌ не выбрано'}")
    if report.get("urgent"):
        lines.append("\n<b>⚠️ Риски заполнены</b>")
    else:
        lines.append("\n<b>⚠️ Риски не заполнены</b>")
    return "\n".join(lines)


def short_car_card(report: dict[str, Any]) -> str:
    car = report["car"]
    values = [
        ("Марка", car.get("make")),
        ("Модель", car.get("model")),
        ("Год", car.get("year")),
        ("Пробег", car.get("mileage")),
        ("VIN", car.get("vin")),
        ("Цена", car.get("price")),
        ("Продавец/ссылка", car.get("seller")),
        ("Заказчик", car.get("customer")),
    ]
    lines = ["<b>🚘 Данные автомобиля</b>"]
    lines.extend(f"<b>{label}:</b> {html.escape(str(value or '—'))}" for label, value in values)
    return "\n".join(lines)


def parse_car_input(text: str) -> Optional[dict[str, str]]:
    parts = [part.strip() for part in text.replace("\n", ";").split(";")]
    if len(parts) != 8:
        return None
    return {
        "make": parts[0],
        "model": parts[1],
        "year": parts[2],
        "mileage": parts[3],
        "vin": parts[4].upper(),
        "price": parts[5],
        "seller": parts[6],
        "customer": parts[7],
    }


def validate_car(car: dict[str, str]) -> list[str]:
    warnings = []
    year = car.get("year", "")
    vin = car.get("vin", "")
    if year and not re.fullmatch(r"\d{4}", year):
        warnings.append("Год лучше указать четырьмя цифрами.")
    if vin and len(vin) != 17:
        warnings.append("VIN обычно состоит из 17 символов; проверьте значение.")
    return warnings


async def answer_menu(message: Message, text: str) -> None:
    report = ensure_report(str(message.chat.id))
    await message.answer(text, reply_markup=make_main_menu(report))


@dp.message(Command(commands=["start", "new"]))
async def cmd_start(message: Message) -> None:
    chat_id = str(message.chat.id)
    reports[chat_id] = new_report_template()
    await save_reports()
    await answer_menu(message, "🆕 Создан новый отчёт. Заполняйте разделы в любом порядке.")


@dp.message(Command("summary"))
async def cmd_summary(message: Message) -> None:
    report = ensure_report(str(message.chat.id))
    await message.answer(report_summary(report), reply_markup=summary_kb())


@dp.message(Command("pdf"))
async def cmd_pdf(message: Message) -> None:
    await message.answer("Формирую PDF…")
    await send_report(str(message.chat.id))


@dp.message(Command("clone"))
async def cmd_clone(message: Message) -> None:
    chat_id = str(message.chat.id)
    previous = ensure_report(chat_id)
    new_report = new_report_template()
    new_report["car"] = copy.deepcopy(previous["car"])
    reports[chat_id] = new_report
    await save_reports()
    await answer_menu(message, "🔁 Создан новый отчёт с перенесёнными данными автомобиля.")


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "<b>Команды</b>\n"
        "/new — новый отчёт\n"
        "/summary — сводка\n"
        "/pdf — экспорт PDF\n"
        "/clone — новый отчёт с данными авто из текущего\n\n"
        "Для данных авто отправьте 8 значений через <b>;</b>: \n"
        "Марка; Модель; Год; Пробег; VIN; Цена; Продавец/ссылка; Заказчик\n\n"
        "Фото и документы добавляйте через «📎 Фото / файлы»."
    )


@dp.callback_query(F.data.startswith("menu:"))
async def cb_menu(query: CallbackQuery) -> None:
    chat_id = str(query.message.chat.id)
    report = ensure_report(chat_id)
    action = query.data.split(":", 1)[1]

    if action == "home":
        report["awaiting"] = None
        await query.message.answer("Главное меню.", reply_markup=make_main_menu(report))

    elif action == "car":
        report["awaiting"] = None
        await query.message.answer(short_car_card(report), reply_markup=car_kb())

    elif action in TEXT_SECTIONS:
        report["awaiting"] = None
        current = report.get(action, "")
        text = f"<b>{TEXT_SECTIONS[action]}</b>\n"
        if current:
            text += f"Текущая запись:\n<blockquote>{html.escape(str(current))}</blockquote>"
        else:
            text += "Раздел пока не заполнен."
        await query.message.answer(text, reply_markup=section_kb(action, bool(current)))

    elif action == "attachments":
        report["awaiting"] = "attachment_tag"
        await query.message.answer("Выберите раздел для следующих фото/файлов:", reply_markup=attachment_tags_kb())

    elif action == "decision":
        report["awaiting"] = None
        current = report.get("decision") or "не выбрано"
        await query.message.answer(f"Текущее решение: <b>{html.escape(current)}</b>\nВыберите новое:", reply_markup=decision_kb())

    elif action == "summary":
        report["awaiting"] = None
        await query.message.answer(report_summary(report), reply_markup=summary_kb())

    elif action == "export":
        report["awaiting"] = None
        await query.message.answer(report_summary(report), reply_markup=summary_kb())

    elif action == "clone":
        new_report = new_report_template()
        new_report["car"] = copy.deepcopy(report["car"]) 
        reports[chat_id] = new_report
        report = new_report
        await query.message.answer("🔁 Новый отчёт создан; данные автомобиля перенесены.", reply_markup=make_main_menu(report))

    elif action == "new":
        reports[chat_id] = new_report_template()
        report = reports[chat_id]
        await query.message.answer("🆕 Создан чистый отчёт.", reply_markup=make_main_menu(report))

    touch(report)
    await save_reports()
    await query.answer()


@dp.callback_query(F.data.startswith("edit:"))
async def cb_edit(query: CallbackQuery) -> None:
    chat_id = str(query.message.chat.id)
    report = ensure_report(chat_id)
    section = query.data.split(":", 1)[1]

    if section == "car":
        report["awaiting"] = "car"
        await query.message.answer(
            "<b>Введите данные одной строкой через ;</b>\n"
            "Марка; Модель; Год; Пробег; VIN; Цена; Продавец/ссылка; Заказчик\n\n"
            "Пример:\n"
            "Toyota; Camry; 2019; 78 000 км; XW7BF4FK90S000000; 2 350 000 ₽; Avito / Иван; Алексей"
        )
    elif section in TEXT_SECTIONS:
        report["awaiting"] = section
        await query.message.answer(SECTION_PROMPTS[section])
    else:
        await query.answer("Неизвестный раздел", show_alert=True)
        return

    touch(report)
    await save_reports()
    await query.answer()


@dp.callback_query(F.data.startswith("clear:"))
async def cb_clear(query: CallbackQuery) -> None:
    chat_id = str(query.message.chat.id)
    report = ensure_report(chat_id)
    section = query.data.split(":", 1)[1]

    if section == "car":
        report["car"] = new_report_template()["car"]
        text = "Данные автомобиля очищены."
    elif section in TEXT_SECTIONS:
        report[section] = ""
        text = f"Раздел «{TEXT_SECTIONS[section]}» очищен."
    else:
        await query.answer("Неизвестный раздел", show_alert=True)
        return

    report["awaiting"] = None
    touch(report)
    await save_reports()
    await query.message.answer(text, reply_markup=make_main_menu(report))
    await query.answer()


@dp.callback_query(F.data.startswith("decision:"))
async def cb_decision(query: CallbackQuery) -> None:
    chat_id = str(query.message.chat.id)
    report = ensure_report(chat_id)
    choice = query.data.split(":", 1)[1]

    if choice == "skip_comment":
        report["decision_comment"] = ""
        report["awaiting"] = None
        touch(report)
        await save_reports()
        await query.message.answer("Решение сохранено без комментария.", reply_markup=make_main_menu(report))
        await query.answer()
        return

    choices = {
        "recommend": "✅ Рекомендую",
        "trade": "🤝 Рекомендую с торгом",
        "not_recommend": "⛔ Не рекомендую",
    }
    if choice not in choices:
        await query.answer("Неизвестное решение", show_alert=True)
        return

    report["decision"] = choices[choice]
    report["awaiting"] = "decision_comment"
    touch(report)
    await save_reports()
    await query.message.answer(
        "Введите короткий итоговый комментарий или нажмите «Пропустить».",
        reply_markup=decision_comment_kb(),
    )
    await query.answer()


@dp.callback_query(F.data.startswith("tag:"))
async def cb_tag(query: CallbackQuery) -> None:
    chat_id = str(query.message.chat.id)
    report = ensure_report(chat_id)
    tag = query.data.split(":", 1)[1]
    if tag not in ATTACHMENT_TAGS:
        await query.answer("Неизвестный раздел", show_alert=True)
        return

    report["attachment_tag"] = tag
    report["awaiting"] = "attachments"
    touch(report)
    await save_reports()
    await query.message.answer(
        f"Раздел: <b>{ATTACHMENT_TAGS[tag]}</b>\n"
        "Отправляйте фото, документы или текстовые заметки. Можно отправить несколько сообщений подряд.",
        reply_markup=attachment_mode_kb(),
    )
    await query.answer()


@dp.callback_query(F.data == "attach:done")
async def cb_attachment_done(query: CallbackQuery) -> None:
    report = ensure_report(str(query.message.chat.id))
    report["awaiting"] = None
    touch(report)
    await save_reports()
    await query.message.answer("Вложения сохранены.", reply_markup=make_main_menu(report))
    await query.answer()


@dp.callback_query(F.data == "export:confirm")
async def cb_export_confirm(query: CallbackQuery) -> None:
    report = ensure_report(str(query.message.chat.id))
    report["awaiting"] = None
    await save_reports()
    await query.message.answer("Формирую PDF…")
    await send_report(str(query.message.chat.id))
    await query.answer()


async def save_attachment(message: Message, report: dict[str, Any], chat_id: str) -> bool:
    target_dir = ATTACHMENTS_DIR / chat_id
    target_dir.mkdir(parents=True, exist_ok=True)
    tag = report.get("attachment_tag", "general")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    try:
        if message.photo:
            photo = message.photo[-1]
            filename = f"{timestamp}_{photo.file_unique_id}.jpg"
            path = target_dir / filename
            await bot.download(photo, destination=path)
            report["attachments"].append({
                "type": "photo",
                "path": str(path),
                "name": filename,
                "tag": tag,
            })
            await message.answer(f"Фото добавлено: {ATTACHMENT_TAGS.get(tag, tag)}")
            return True

        if message.document:
            document = message.document
            extension = Path(document.file_name or "").suffix[:12]
            filename = f"{timestamp}_{document.file_unique_id}{extension}"
            path = target_dir / filename
            await bot.download(document, destination=path)
            report["attachments"].append({
                "type": "document",
                "path": str(path),
                "name": document.file_name or filename,
                "tag": tag,
            })
            await message.answer(f"Файл добавлен: {ATTACHMENT_TAGS.get(tag, tag)}")
            return True
    except Exception:
        logger.exception("Failed to download attachment")
        await message.answer("Не удалось сохранить вложение.")
        return True

    return False


@dp.message()
async def handle_message(message: Message) -> None:
    chat_id = str(message.chat.id)
    report = ensure_report(chat_id)
    awaiting = report.get("awaiting")

    if awaiting == "attachments":
        if await save_attachment(message, report, chat_id):
            touch(report)
            await save_reports()
            return
        text = (message.text or "").strip()
        if text:
            report["attachments"].append({
                "type": "note",
                "text": text,
                "tag": report.get("attachment_tag", "general"),
            })
            touch(report)
            await save_reports()
            await message.answer("Заметка добавлена.", reply_markup=attachment_mode_kb())
        else:
            await message.answer("Отправьте фото, документ или текстовую заметку.", reply_markup=attachment_mode_kb())
        return

    if awaiting == "car":
        text = (message.text or "").strip()
        car = parse_car_input(text)
        if not car:
            await message.answer(
                "Нужны ровно 8 значений через <b>;</b>:\n"
                "Марка; Модель; Год; Пробег; VIN; Цена; Продавец/ссылка; Заказчик\n\n"
                "Поля, которые пока неизвестны, оставьте пустыми, но разделитель сохраните."
            )
            return
        report["car"].update(car)
        report["awaiting"] = None
        touch(report)
        await save_reports()
        warnings = validate_car(car)
        response = "✅ Данные автомобиля сохранены."
        if warnings:
            response += "\n\n⚠️ " + "\n⚠️ ".join(warnings)
        await answer_menu(message, response)
        return

    if awaiting == "decision_comment":
        report["decision_comment"] = (message.text or "").strip()
        report["awaiting"] = None
        touch(report)
        await save_reports()
        await answer_menu(message, "✅ Итоговый комментарий сохранён.")
        return

    if awaiting in TEXT_SECTIONS:
        text = (message.text or "").strip()
        if not text:
            await message.answer("Нужен текст. Отправьте описание или вернитесь в меню.", reply_markup=back_to_menu_kb())
            return
        report[awaiting] = text
        report["awaiting"] = None
        touch(report)
        await save_reports()
        await answer_menu(message, f"✅ Раздел «{TEXT_SECTIONS[awaiting]}» сохранён.")
        return

    await message.answer("Выберите раздел отчёта.", reply_markup=make_main_menu(report))


def escape_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "—"
    return html.escape(text).replace("\n", "<br>")


def render_report_html(report: dict[str, Any]) -> str:
    car = report["car"]
    created_at = report.get("created_at", "")
    generated_at = datetime.now().strftime("%d.%m.%Y %H:%M")

    # logo as data URI
    logo_path = Path("assets") / "logo.png"
    logo_data_uri = ""
    if logo_path.exists():
        try:
            data = logo_path.read_bytes()
            mime = "image/png"
            logo_data_uri = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
        except Exception:
            logger.exception("Failed to read logo image")
            logo_data_uri = ""

    section_html = ""
    for key, label in TEXT_SECTIONS.items():
        checked = "✅" if is_filled(report.get(key)) else "◻️"
        section_html += f"""
        <section>
          <h2>{label} {checked}</h2>
          <div class=\"content\">{escape_value(report.get(key))}</div>
        </section>
        """

    attachments_html = ""
    groups: dict[str, list[dict[str, Any]]] = {}
    for attachment in report.get("attachments", []):
        groups.setdefault(attachment.get("tag", "general"), []).append(attachment)

    for tag, items in groups.items():
        attachments_html += f"<h3>{html.escape(ATTACHMENT_TAGS.get(tag, tag))}</h3>"
        for item in items:
            item_type = item.get("type")
            if item_type == "note":
                attachments_html += f"<p>• {escape_value(item.get('text'))}</p>"
            elif item_type == "photo" and item.get("path") and Path(item["path"]).exists():
                uri = Path(item["path"]).resolve().as_uri()
                attachments_html += f"<figure><img src=\"{uri}\"><figcaption>{html.escape(item.get('name', 'Фото'))}</figcaption></figure>"
            else:
                attachments_html += f"<p>• 📎 {html.escape(item.get('name', 'Файл'))}</p>"

    if not attachments_html:
        attachments_html = "<p>—</p>"

    return f"""
    <!doctype html>
    <html lang=\"ru\">
    <head>
      <meta charset=\"utf-8\">
      <style>
        @page {{ size: A4; margin: 16mm 14mm; }}
        body {{ font-family: DejaVu Sans, Arial, sans-serif; color: #202124; font-size: 10.5pt; line-height: 1.35; }}
        h1 {{ margin: 0 0 4px; color: #113b64; font-size: 20pt; }}
        h2 {{ margin: 16px 0 6px; padding: 6px 8px; color: #113b64; background: #eaf3fb; font-size: 12pt; }}
        h3 {{ margin: 10px 0 5px; font-size: 10.5pt; }}
        .meta {{ color: #5f6368; margin-bottom: 14px; }}
        table {{ width: 100%; border-collapse: collapse; margin: 8px 0 12px; }}
        td {{ padding: 6px 7px; border: 1px solid #d9e0e6; vertical-align: top; }}
        td.label {{ width: 24%; background: #f7f9fb; font-weight: bold; }}
        .content {{ white-space: normal; }}
        .decision {{ padding: 10px; border: 2px solid #113b64; background: #f5faff; }}
        figure {{ margin: 8px 0 12px; page-break-inside: avoid; }}
        img {{ max-width: 100%; max-height: 170mm; object-fit: contain; }}
        figcaption {{ color: #5f6368; font-size: 8pt; }}
      </style>
    </head>
    <body>
      <div style=\"display:flex;align-items:center;gap:12px;margin-bottom:8px;\">
        {f'<img src="{logo_data_uri}" style="height:48px;">' if logo_data_uri else ''}
        <div>
          <h1>Отчёт по осмотру автомобиля</h1>
          <div class=\"meta\">Сформирован: {generated_at}{' · Создан: ' + html.escape(created_at) if created_at else ''}</div>
        </div>
      </div>

      <h2>🚘 Данные автомобиля</h2>
      <table>
        <tr><td class=\"label\">Марка / модель</td><td>{escape_value(car.get('make'))} {escape_value(car.get('model'))}</td></tr>
        <tr><td class=\"label\">Год / пробег</td><td>{escape_value(car.get('year'))} / {escape_value(car.get('mileage'))}</td></tr>
        <tr><td class=\"label\">VIN</td><td>{escape_value(car.get('vin'))}</td></tr>
        <tr><td class=\"label\">Цена</td><td>{escape_value(car.get('price'))}</td></tr>
        <tr><td class=\"label\">Продавец / ссылка</td><td>{escape_value(car.get('seller'))}</td></tr>
        <tr><td class=\"label\">Заказчик</td><td>{escape_value(car.get('customer'))}</td></tr>
      </table>

      {section_html}

      <h2>📎 Фото, файлы и заметки</h2>
      {attachments_html}

      <h2>✅ Итоговое решение</h2>
      <div class=\"decision\"><b>{escape_value(report.get('decision'))}</b><br><br>{escape_value(report.get('decision_comment'))}</div>
    </body>
    </html>
    """


async def generate_pdf_bytes(html_text: str) -> bytes:
    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise RuntimeError("Для экспорта PDF установите WeasyPrint: pip install weasyprint") from exc
    return await asyncio.to_thread(lambda: HTML(string=html_text).write_pdf())


async def send_report(chat_id: str) -> None:
    report = reports.get(chat_id)
    if not report:
        await bot.send_message(int(chat_id), "Отчёт не найден. Нажмите /new.")
        return

    try:
        pdf_bytes = await generate_pdf_bytes(render_report_html(report))
    except RuntimeError as exc:
        await bot.send_message(int(chat_id), html.escape(str(exc)))
        return
    except Exception:
        logger.exception("PDF rendering failed")
        await bot.send_message(int(chat_id), "Не удалось сформировать PDF. Подробности — в логах сервера.")
        return

    export_dir = EXPORTS_DIR / chat_id
    export_dir.mkdir(parents=True, exist_ok=True)
    car = report["car"]
    car_slug = "_".join(filter(None, [car.get("make", ""), car.get("model", "")]))
    car_slug = re.sub(r"[^\w.-]+", "_", car_slug, flags=re.UNICODE).strip("_") or "auto"
    filename = f"inspection_{car_slug}_{datetime.now():%Y%m%d_%H%M}.pdf"
    pdf_path = export_dir / filename

    try:
        await asyncio.to_thread(pdf_path.write_bytes, pdf_bytes)
        await bot.send_document(
            int(chat_id),
            FSInputFile(pdf_path, filename=filename),
            caption="📄 Отчёт по осмотру автомобиля",
        )
    except Exception:
        logger.exception("Failed to send generated PDF")
        await bot.send_message(int(chat_id), "PDF сформирован, но отправить его не удалось.")


async def main() -> None:
    load_reports()
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
