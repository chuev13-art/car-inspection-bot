import os
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import FSInputFile

from utils.pdf_utils import create_export

router = Router()

@router.message(Command(commands=["export_pdf"]))
async def export_pdf_handler(message: types.Message):
    """Simple /export_pdf command handler that generates a test PDF and sends it.

    This handler builds a small report using placeholder data. In your app
    you'll want to pass the actual inspection data instead.
    """
    chat_id = message.chat.id
    output_dir = os.path.join("exports", str(chat_id))
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "report.pdf")

    # Example report data — replace with real report fields from your bot.
    report_data = {
        "report_title": "Отчёт по осмотру автомобиля",
        "date": message.date.strftime("%Y-%m-%d %H:%M") if hasattr(message, 'date') else None,
        "client": message.from_user.full_name if message.from_user else 'Клиент',
        "sections": [
            {"title": "Общая информация", "items": [
                {"label": "Внешний вид", "checked": True, "note": "Без повреждений"},
                {"label": "Кузов", "checked": False, "note": "Не проверен"},
            ]},
            {"title": "Двигатель", "items": [
                {"label": "Утечки", "checked": True},
                {"label": "Шумы", "checked": False},
            ]},
        ],
        "comments": "Тестовая генерация PDF. Замените данными осмотра."
    }

    template_path = "report.html"  # relative to templates/
    try:
        create_export(report_data, template_path, output_path)
    except Exception as e:
        await message.answer(f"Ошибка при генерации PDF: {e}")
        return

    if os.path.exists(output_path):
        await message.answer_document(FSInputFile(output_path))
    else:
        await message.answer("Не удалось создать PDF (файл не найден)")


def register_export_handlers(dp):
    """Register the router in main Dispatcher (aiogram v3).

    Call register_export_handlers(dp) from your main.py after Dispatcher exists.
    """
    try:
        dp.include_router(router)
    except Exception as e:
        # Fallback: some projects keep different setup. Print warning only.
        print("Failed to include router for export_pdf:", e)
