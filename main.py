import asyncio
import copy
import html
import json
import logging
import os
import re
import shutil
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
    "testdrive": "Опишите тест-драйв: запуск, ДВС/КПП, подвеска, рулевое, тормоза, вибрации.",
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


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="🚘 Авто", callback_data="menu:car"),
            InlineKeyboardButton(text="🧾 Диагностика", callback_data="menu:diagnosis"),
        ],
        [
            InlineKeyboardButton(text="🔋 Батарея", callback_data="menu:battery"),
            InlineKeyboardButton(text="🎨 Кузов", callback_data="menu:body"),
        ],
        [
            InlineKeyboardButton(text="🪑 Салон", callback_data="menu:interior"),
            InlineKeyboardButton(text="🛞 Колёса", callback_data="menu:wheels"),
        ],
        [
            InlineKeyboardButton(text="🛣 Тест-драйв", callback_data="menu:testdrive"),
            InlineKeyboardButton(text="⚠️ Риски", callback_data="menu:urgent"),
        ],
        [
            InlineKeyboardButton(text="📎 Фото / файлы", callback_data="menu:attachments"),
            InlineKeyboardButton(text="✅ Решение", callback_data="menu:decision"),
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
    await message.answer(text, reply_markup=main_menu())


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


def cb_menu(query: CallbackQuery) -> None:
    chat_id = str(query.message.chat.id)
    report = ensure_report(chat_id)
    action = query.data.split(":", 1)[1]

    if action == "home":
        report["awaiting"] = None
        await query.message.answer("Главное меню.", reply_markup=main_menu())

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
        new_report["car"] = copy.deepcopy(report["car")
