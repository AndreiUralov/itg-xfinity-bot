"""ITG Telegram bot entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from telegram import BotCommand, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, TypeHandler, filters

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import TELEGRAM_BOT_TOKEN, WEBHOOK_URL
from bot.handlers import (
    cmd_cancel,
    cmd_fuel,
    cmd_goal,
    cmd_help,
    cmd_invoice,
    cmd_off,
    cmd_on,
    cmd_start,
    cmd_tips,
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
    BotCommand("goal", "Цель на день и неделю в $"),
    BotCommand("today", "Работы за сегодня — изменить / удалить"),
    BotCommand("tips", "Добавить чаевые (не в план)"),
    BotCommand("fuel", "Затраты на бензин"),
    BotCommand("week", "Итог текущей недели"),
    BotCommand("invoice", "PDF инвойс ATN"),
    BotCommand("cancel", "Отменить текущую работу"),
    BotCommand("help", "Как пользоваться ботом"),
]

ALLOWED_UPDATES = ["message", "callback_query"]


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


def _build_application(*, webhook: bool) -> Application:
    scheduler_task: asyncio.Task | None = None

    async def post_init(application: Application) -> None:
        nonlocal scheduler_task
        await _register_commands(application)
        scheduler_task = asyncio.create_task(_scheduler_loop())

    async def post_shutdown(application: Application) -> None:
        nonlocal scheduler_task
        if scheduler_task and not scheduler_task.done():
            scheduler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await scheduler_task

    builder = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown)
    if webhook:
        builder = builder.updater(None)

    app = builder.build()

    app.add_handler(TypeHandler(Update, _maybe_run_scheduled_tasks), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("on", cmd_on))
    app.add_handler(CommandHandler("off", cmd_off))
    app.add_handler(CommandHandler("goal", cmd_goal))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tips", cmd_tips))
    app.add_handler(CommandHandler("fuel", cmd_fuel))
    app.add_handler(CommandHandler("invoice", cmd_invoice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))
    return app


async def _run_webhook(app: Application) -> None:
    port = int(os.environ.get("PORT", "10000"))
    webhook_path = TELEGRAM_BOT_TOKEN
    webhook_url = f"{WEBHOOK_URL.rstrip('/')}/{webhook_path}"

    async def telegram_update(request: Request) -> Response:
        await app.update_queue.put(Update.de_json(data=await request.json(), bot=app.bot))
        return Response()

    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    starlette_app = Starlette(
        routes=[
            Route(f"/{webhook_path}", telegram_update, methods=["POST"]),
            Route("/health", health, methods=["GET"]),
            Route("/", health, methods=["GET"]),
        ]
    )

    logger.info("Starting webhook server on 0.0.0.0:%s (health=/health)", port)
    webserver = uvicorn.Server(
        uvicorn.Config(
            starlette_app,
            host="0.0.0.0",
            port=port,
            log_level="info",
            use_colors=False,
        )
    )

    async with app:
        await app.bot.set_webhook(
            url=webhook_url,
            allowed_updates=ALLOWED_UPDATES,
            drop_pending_updates=True,
        )
        logger.info("Telegram webhook set: %s", webhook_url)
        await app.start()
        await webserver.serve()
        await app.stop()


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN не задан.\n"
            "Создайте .env из .env.example и укажите токен от @BotFather"
        )

    use_webhook = bool(WEBHOOK_URL)
    logger.info("ITG bot started (mode=%s)", "webhook" if use_webhook else "polling")

    if use_webhook:
        app = _build_application(webhook=True)
        asyncio.run(_run_webhook(app))
        return

    app = _build_application(webhook=False)
    app.run_polling(allowed_updates=ALLOWED_UPDATES)


if __name__ == "__main__":
    main()
