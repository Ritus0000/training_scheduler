# training_scheduler_bot.py
#
# Telegram-обёртка вокруг JSON-оптимизатора.
#
# Команды:
#   /start      — приветствие + ежедневное напоминание в 18:00 (Europe/Warsaw)
#   /exercises  — список упражнений
#   /today      — рекомендации на сегодня
#   /done <...> — отметить выполненное упражнение (потом «сегодня» или «вчера»)
#
# Перед запуском:
#   pip install -r requirements.txt
#   export TELEGRAM_BOT_TOKEN=...  (на Railway — Variables)
#
# Все данные хранятся в JSON (training_state.json) через training_scheduler_json_logic.py.

import logging
import os
from datetime import date, timedelta, time
from typing import List, Dict, Any, Optional

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from training_scheduler_json_logic import (
    USER_TIMEZONE,
    EXERCISE_DEFINITIONS_BY_KEY,
    get_training_recommendations_for_today,
    record_exercise_completion_for_date,
    get_current_local_date,
)

# Состояние диалога для /done
CHOOSING_DAY_FOR_DONE: int = 1

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("training_scheduler_bot")


def build_exercise_alias_mapping() -> Dict[str, str]:
    """
    Маппинг пользовательского ввода → exercise_key.
    """
    alias_to_exercise_key: Dict[str, str] = {}

    for exercise_key, definition in EXERCISE_DEFINITIONS_BY_KEY.items():
        alias_to_exercise_key[exercise_key.lower()] = exercise_key

        display_name_ru: str = definition["display_name_ru"]
        cleaned_ru: str = display_name_ru.replace("«", "").replace("»", "").lower()
        alias_to_exercise_key[cleaned_ru] = exercise_key

    # Частые алиасы
    alias_to_exercise_key.setdefault("присед", "squat")
    alias_to_exercise_key.setdefault("приседания", "squat")
    alias_to_exercise_key.setdefault("румынка", "hinge")
    alias_to_exercise_key.setdefault("румынская тяга", "hinge")
    alias_to_exercise_key.setdefault("болгарки", "unilateral")
    alias_to_exercise_key.setdefault("болгарский", "unilateral")
    alias_to_exercise_key.setdefault("икры", "calves")
    alias_to_exercise_key.setdefault("плечи", "ohp")
    alias_to_exercise_key.setdefault("жим стоя", "ohp")
    alias_to_exercise_key.setdefault("горизонтальная тяга", "horizontal_row")
    alias_to_exercise_key.setdefault("подтягивания объем", "vertical_pull_volume")
    alias_to_exercise_key.setdefault("подтягивания объём", "vertical_pull_volume")
    alias_to_exercise_key.setdefault("подтягивания сила", "vertical_pull_strength")
    alias_to_exercise_key.setdefault("кор", "core")
    alias_to_exercise_key.setdefault("пресс", "core")

    return alias_to_exercise_key


def resolve_exercise_key_from_user_text(user_input_exercise_text: str) -> Optional[str]:
    alias_mapping: Dict[str, str] = build_exercise_alias_mapping()
    normalized: str = user_input_exercise_text.strip().lower()
    return alias_mapping.get(normalized)


async def start_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_telegram_id: int = update.effective_user.id
    chat_id: int = update.effective_chat.id

    text: str = (
        "Привет. Это минималистичный планировщик тренировок.\n\n"
        "Я слежу за частотой упражнений, отдыхом и тем, чтобы не ставить тяжёлые CNS-дни подряд.\n\n"
        "Команды:\n"
        "/today — рекомендации на сегодня\n"
        "/done <упражнение> — отметить выполненное (потом выберешь: сегодня или вчера)\n"
        "/exercises — список упражнений и ключей\n\n"
        "Ежедневное напоминание приходит в 18:00 по твоему времени."
    )
    await update.message.reply_text(text)

    reminder_time_local: time = time(hour=18, minute=0, tzinfo=USER_TIMEZONE)

    job_name: str = f"daily_reminder_{user_telegram_id}"
    existing_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in existing_jobs:
        job.schedule_removal()

    context.job_queue.run_daily(
        daily_reminder_job_callback,
        time=reminder_time_local,
        chat_id=chat_id,
        name=job_name,
        data={"chat_id": chat_id},
    )


async def exercises_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines: List[str] = ["Список упражнений:\n"]

    for exercise_key, definition in EXERCISE_DEFINITIONS_BY_KEY.items():
        line: str = (
            f"- {definition['display_name_ru']} "
            f"(ключ: {exercise_key}, CNS: {definition['cns_load_level']}, "
            f"цель/нед: {definition['times_per_week_target']}, "
            f"rest: {definition['minimal_rest_hours_between_sessions']} ч)\n"
            f"  {definition['description_ru']}"
        )
        lines.append(line)

    await update.message.reply_text("\n\n".join(lines))


async def today_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    recommendations: Dict[str, Any] = get_training_recommendations_for_today()

    must_do_today: List[str] = recommendations["must_do_today"]
    optional_today: List[str] = recommendations["optional_today"]
    cns_blocked_today: List[str] = recommendations["cns_blocked_today"]
    not_ready_by_rest: List[str] = recommendations["not_ready_by_rest"]
    log_gap_warning: Optional[str] = recommendations.get("log_gap_warning")

    def format_list(keys: List[str]) -> str:
        if not keys:
            return "—"
        return "\n".join(
            f"{EXERCISE_DEFINITIONS_BY_KEY[k]['display_name_ru']} (ключ: {k})" for k in keys
        )

    lines: List[str] = []

    if log_gap_warning:
        lines.append("⚠ " + log_gap_warning + "\n")

    lines.append("Рекомендации на сегодня:\n")
    lines.append("ОБЯЗАТЕЛЬНО СДЕЛАТЬ:")
    lines.append(format_list(must_do_today))
    lines.append("")
    lines.append("МОЖНО СДЕЛАТЬ (дополнительно):")
    lines.append(format_list(optional_today))
    lines.append("")
    lines.append("ЗАПРЕЩЕНО СЕГОДНЯ (high CNS был вчера):")
    lines.append(format_list(cns_blocked_today))
    lines.append("")
    lines.append("ЕЩЁ НЕ ВОССТАНОВИЛОСЬ (по rest):")
    lines.append(format_list(not_ready_by_rest))

    await update.message.reply_text("\n".join(lines))


async def done_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.args:
        await update.message.reply_text(
            "Нужно указать упражнение.\n"
            "Пример: /done присед  или  /done squat\n"
            "Посмотреть список: /exercises"
        )
        return ConversationHandler.END

    user_text: str = " ".join(context.args).strip().lower()
    resolved_key: Optional[str] = resolve_exercise_key_from_user_text(user_text)

    if resolved_key is None:
        await update.message.reply_text(
            f"Не понял упражнение: «{user_text}».\n"
            "Посмотри /exercises и укажи ключ (например, squat) или часть русского названия (например, присед)."
        )
        return ConversationHandler.END

    context.user_data["pending_exercise_key_for_done"] = resolved_key
    display_name_ru: str = EXERCISE_DEFINITIONS_BY_KEY[resolved_key]["display_name_ru"]

    await update.message.reply_text(
        f"Отмечаем: {display_name_ru}.\n"
        f"Напиши одним словом: «сегодня» или «вчера»."
    )

    return CHOOSING_DAY_FOR_DONE


async def done_command_choose_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_reply: str = (update.message.text or "").strip().lower()
    exercise_key: Optional[str] = context.user_data.get("pending_exercise_key_for_done")

    if exercise_key is None:
        await update.message.reply_text("Внутренняя ошибка: нет сохранённого упражнения. Ещё раз /done.")
        return ConversationHandler.END

    current_date: date = get_current_local_date()

    if user_reply in ("сегодня", "today"):
        performed_date: date = current_date
    elif user_reply in ("вчера", "yesterday"):
        performed_date = current_date - timedelta(days=1)
    else:
        await update.message.reply_text("Ответ не распознан. Напиши «сегодня» или «вчера».")
        return CHOOSING_DAY_FOR_DONE

    record_exercise_completion_for_date(
        exercise_key=exercise_key,
        performed_local_date=performed_date,
    )

    display_name_ru: str = EXERCISE_DEFINITIONS_BY_KEY[exercise_key]["display_name_ru"]
    await update.message.reply_text(
        f"Зафиксировано: {display_name_ru}, дата выполнения: {performed_date.isoformat()}."
    )

    context.user_data.pop("pending_exercise_key_for_done", None)
    return ConversationHandler.END


async def daily_reminder_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    job_data: Dict[str, Any] = context.job.data or {}
    chat_id: Optional[int] = job_data.get("chat_id")
    if chat_id is None:
        return

    recommendations: Dict[str, Any] = get_training_recommendations_for_today()
    must_do_today: List[str] = recommendations["must_do_today"]
    optional_today: List[str] = recommendations["optional_today"]

    if must_do_today:
        lines: List[str] = ["Напоминание: сегодня ОБЯЗАТЕЛЬНО сделать:"]
        for key in must_do_today:
            lines.append(f"- {EXERCISE_DEFINITIONS_BY_KEY[key]['display_name_ru']}")
        if optional_today:
            lines.append("\nДополнительно можно сделать:")
            for key in optional_today:
                lines.append(f"- {EXERCISE_DEFINITIONS_BY_KEY[key]['display_name_ru']}")
        text = "\n".join(lines)
    else:
        if optional_today:
            lines = [
                "Сегодня обязательных упражнений нет.",
                "Если хочешь — можешь сделать что-то из списка:"
            ]
            for key in optional_today:
                lines.append(f"- {EXERCISE_DEFINITIONS_BY_KEY[key]['display_name_ru']}")
            text = "\n".join(lines)
        else:
            text = (
                "Сегодня по плану нет ни обязательных, ни рекомендованных упражнений.\n"
                "Можешь отдыхать или сделать лёгкую активность по желанию."
            )

    await context.bot.send_message(chat_id=chat_id, text=text)


def main() -> None:
    """
    Точка входа. Используется локально и на Railway (через main.py).
    """
    telegram_bot_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_bot_token:
        raise RuntimeError("Нужно задать TELEGRAM_BOT_TOKEN в переменных окружения.")

    # Критичный момент: отключаем Updater, чтобы не ловить баг на Python 3.13
    application = ApplicationBuilder().token(telegram_bot_token).updater(None).build()

    application.add_handler(CommandHandler("start", start_command_handler))
    application.add_handler(CommandHandler("today", today_command_handler))
    application.add_handler(CommandHandler("exercises", exercises_command_handler))

    conversation_handler_done = ConversationHandler(
        entry_points=[CommandHandler("done", done_command_entry)],
        states={
            CHOOSING_DAY_FOR_DONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, done_command_choose_day)
            ],
        },
        fallbacks=[],
    )
    application.add_handler(conversation_handler_done)

    logger.info("Training scheduler bot started with JSON state.")
    application.run_polling()
