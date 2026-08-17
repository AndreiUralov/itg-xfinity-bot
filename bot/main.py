"""ITG Telegram bot entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from telegram import BotCommand, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import TELEGRAM_BOT_TOKEN, WEBHOOK_URL
from bot.handlers import (
    cmd_cancel,
    cmd_goal,
    cmd_help,
    cmd_invoice,
    cmd_off,
    cmd_on,
    cmd_start,
    cmd_today,
    cmd_week,
    handle_callback,
    handle_photo,
    handle_text,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("itg.bot")

BOT_COMMANDS = [
    BotCommand("start", "Сводка за день и неделю"),
    BotCommand("on", "На работе сегодня"),
    BotCommand("off", "Выходной сегодня"),
    BotCommand("goal", "Цель на неделю в $"),
    BotCommand("today", "Работы за сегодня — изменить / удалить"),
    BotCommand("week", "Итог текущей недели"),
    BotCommand("invoice", "PDF инвойс ATN"),
    BotCommand("cancel", "Отменить текущую работу"),
    BotCommand("help", "Как пользоваться ботом"),
]


async def _register_commands(application: Application) -> None:
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
        logger.info("Bot command menu registered")
    except Exception as exc:
        logger.warning("Could not register bot commands (non-fatal): %s", exc)


async def _scheduler_loop() -> None:
    while True:
        await asyncio.sleep(1800)
        try:
            from bot.scheduler_runner import run_due_tasks

            ran = run_due_tasks()
            if ran:
                logger.info("Scheduled tasks ran: %s", ", ".join(ran))
        except Exception as exc:
            logger.warning("Scheduler tick failed: %s", exc)


async def _maybe_run_scheduled_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        from bot.scheduler_runner import run_due_tasks

        run_due_tasks()
    except Exception:
        pass


async def _post_init(application: Application) -> None:
    await _register_commands(application)
    asyncio.create_task(_scheduler_loop())


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN не задан.\n"
            "Создайте .env из .env.example и укажите токен от @BotFather"
        )

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(TypeHandler(Update, _maybe_run_scheduled_tasks), group=-1)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("on", cmd_on))
    app.add_handler(CommandHandler("off", cmd_off))
    app.add_handler(CommandHandler("goal", cmd_goal))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("invoice", cmd_invoice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    use_webhook = bool(WEBHOOK_URL)
    logger.info("ITG bot started (mode=%s)", "webhook" if use_webhook else "polling")

    if use_webhook:
        port = int(os.environ.get("PORT", "10000"))
        path = TELEGRAM_BOT_TOKEN
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=path,
            webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{path}",
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
    else:
        app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
