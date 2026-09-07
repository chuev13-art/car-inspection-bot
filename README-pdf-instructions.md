Добавлена поддержка экспорта отчёта в PDF.

Краткая инструкция:

1) В ветке feature/pdf-export добавлены:
   - utils/pdf_utils.py — helpers для рендера шаблона и генерации PDF (weasyprint).
   - templates/report.html — Jinja2 шаблон отчёта.
   - handlers/export_pdf.py — Router/handler с командой /export_pdf для тестовой генерации.
   - requirements-pdf.txt — дополнительные Python-зависимости (weasyprint, jinja2).

2) Системные зависимости для weasyprint (Debian/Ubuntu):
   apt-get install -y libcairo2 libpango-1.0-0 libgdk-pixbuf2.0-0 libffi-dev shared-mime-info

   Рекомендуется использовать Docker-образ, в котором эти пакеты уже установлены.

3) Как протестировать локально:
   - Установите зависимости: pip install -r requirements.txt -r requirements-pdf.txt
   - Запустите бота и отправьте команду /export_pdf в чат с ботом.
   - PDF будет сохранён в exports/<chat_id>/report.pdf и отправлен в чат.

Если хотите, могу также автоматически обновить requirements.txt и изменить main.py, чтобы регистрировать роутер — подтвердите, и я сделаю это.
