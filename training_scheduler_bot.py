# training_scheduler_bot.py
#
# Telegram-обёртка вокруг JSON-оптимизатора.
# Управление максимально простое:
#   /start      — краткая справка + включение напоминаний
#   /today      — что лучше сделать сегодня
#   /done       — записать тренировку (упражнение + сегодня/вчера)
#   /exercises  — короткий список упражнений

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


# ===== АЛИАСЫ УПРАЖНЕНИЙ =====

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

    # Частые короткие формы
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


# ===== /start =====

async def start_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start — краткое описание и регистрация ежедневного напоминания в 18:00.
    """
    user_telegram_id: int = update.effective_user.id
    chat_id: int = update.effective_chat.id

    text: str = (
        "Планировщик тренировок.\n\n"
        "Я слежу, чтобы упражнения появлялись с нужной частотой и не шли тяжёлые дни подряд.\n\n"
        "Команды:\n"
        "/today — что лучше сделать сегодня\n"
        "/done — записать тренировку\n"
        "/exercises — краткий список упражнений\n\n"
        "Напоминание приходит каждый день в 18:00."
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


# ===== /exercises =====

async def exercises_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /exercises — компактный список упражнений без ключей и технических деталей.
    """
    lines: List[str] = ["Упражнения, которые я учитываю:\n"]

    lines.append("• Приседания — любые варианты.")
    lines.append("• Румынская тяга — всё по задней цепи.")
    lines.append("• Одноногие — болгарки, выпады, шаги на тумбу.")
    lines.append("• Икры — любые подъёмы на носки.")
    lines.append("• Жим стоя — жим штанги/гантелей вверх.")
    lines.append("• Горизонтальная тяга — тяга к поясу.")
    lines.append("• Подтягивания (объём) — лёгкие подходы.")
    lines.append("• Подтягивания (сила) — тяжёлые сеты и негативы.")
    lines.append("• Кор / пресс — планки, dead bug и т.п.")

    await update.message.reply_text("\n".join(lines))


# ===== /today =====

async def today_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /today — показать рекомендации на сегодня, максимально кратко.
    """
    recommendations: Dict[str, Any] = get_training_recommendations_for_today()

    must_do_today: List[str] = recommendations["must_do_today"]
    optional_today: List[str] = recommendations["optional_today"]
    cns_blocked_today: List[str] = recommendations["cns_blocked_today"]
    not_ready_by_rest: List[str] = recommendations["not_ready_by_rest"]
    log_gap_warning: Optional[str] = recommendations.get("log_gap_warning")

    def display_name(exercise_key: str) -> str:
        # убираем кавычки, оставляем только текст
        raw: str = EXERCISE_DEFINITIONS_BY_KEY[exercise_key]["display_name_ru"]
        return raw.replace("«", "").replace("»", "")

    def format_list(keys: List[str]) -> str:
        if not keys:
            return "—"
        return "\n".join(f"• {display_name(k)}" for k in keys)

    lines: List[str] = []

    if log_gap_warning:
        # Одно предупреждение, без лишних фраз
        lines.append("⚠ Давно не было записей. Если тренировки были, сначала добейте /done с «вчера».\n")

    lines.append("Сегодня:\n")
    lines.append("1. Приоритет:")
    lines.append(format_list(must_do_today))
    lines.append("")
    lines.append("2. Можно добавить:")
    lines.append(format_list(optional_today))
    lines.append("")
    lines.append("3. Лучше не делать (CNS):")
    lines.append(format_list(cns_blocked_today))
    lines.append("")
    lines.append("4. Ещё не восстановилось:")
    lines.append(format_list(not_ready_by_rest))

    await update.message.reply_text("\n".join(lines))


# ===== /done =====

async def done_command_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    /done или /done <упражнение>:
    - если без аргументов — показываем короткий список;
    - если с аргументом — сразу спрашиваем: сегодня / вчера.
    """
    if not context.args:
        # Список для выбора, без ключей
        lines: List[str] = [
            "Что записать?",
            "Напишите одно слово из списка:",
            "• приседания",
            "• румынская",
            "• болгарки",
            "• икры",
            "• жим стоя",
            "• горизонтальная тяга",
            "• подтягивания объём",
            "• подтягивания сила",
            "• кор / пресс",
        ]
        await update.message.reply_text("\n".join(lines))
        return ConversationHandler.END

    user_text: str = " ".join(context.args).strip().lower()
    resolved_key: Optional[str] = resolve_exercise_key_from_user_text(user_text)

    if resolved_key is None:
        await update.message.reply_text(
            "Не распознал упражнение.\n"
            "Можно так: /done приседания, /done румынская, /done икры.\n"
            "Если нужно, посмотрите список: /exercises."
        )
        return ConversationHandler.END

    context.user_data["pending_exercise_key_for_done"] = resolved_key
    display_name_ru: str = EXERCISE_DEFINITIONS_BY_KEY[resolved_key]["display_name_ru"].replace("«", "").replace("»", "")

    await update.message.reply_text(
        f"{display_name_ru}.\n"
        f"Это было сегодня или вчера?"
    )

    return CHOOSING_DAY_FOR_DONE


async def done_command_choose_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Второй шаг /done:
    - ответ «сегодня» / «вчера»;
    - запись в JSON.
    """
    user_reply: str = (update.message.text or "").strip().lower()
    exercise_key: Optional[str] = context.user_data.get("pending_exercise_key_for_done")

    if exercise_key is None:
        await update.message.reply_text("Не сохранил упражнение. Запустите /done ещё раз.")
        return ConversationHandler.END

    current_date: date = get_current_local_date()

    if user_reply in ("сегодня", "today"):
        performed_date: date = current_date
    elif user_reply in ("вчера", "yesterday"):
        performed_date = current_date - timedelta(days=1)
    else:
        await update.message.reply_text("Нужно одно слово: «сегодня» или «вчера».")
        return CHOOSING_DAY_FOR_DONE

    record_exercise_completion_for_date(
        exercise_key=exercise_key,
        performed_local_date=performed_date,
    )

    display_name_ru: str = EXERCISE_DEFINITIONS_BY_KEY[exercise_key]["display_name_ru"].replace("«", "").replace("»", "")
    await update.message.reply_text(
        f"Записал: {display_name_ru}, {performed_date.isoformat()}."
    )

    context.user_data.pop("pending_exercise_key_for_done", None)
    return ConversationHandler.END


# ===== ЕЖЕДНЕВНОЕ НАПОМИНАНИЕ =====

async def daily_reminder_job_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    job_data: Dict[str, Any] = context.job.data or {}
    chat_id: Optional[int] = job_data.get("chat_id")
    if chat_id is None:
        return

    recommendations: Dict[str, Any] = get_training_recommendations_for_today()
    must_do_today: List[str] = recommendations["must_do_today"]
    optional_today: List[str] = recommendations["optional_today"]

    def display_name(exercise_key: str) -> str:
        raw: str = EXERCISE_DEFINITIONS_BY_KEY[exercise_key]["display_name_ru"]
        return raw.replace("«", "").replace("»", "")

    if must_do_today:
        lines: List[str] = ["Напоминание на сегодня:"]
        lines.append("")
        lines.append("Приоритет:")
        for key in must_do_today:
            lines.append(f"• {display_name(key)}")
        if optional_today:
            lines.append("")
            lines.append("Можно добавить:")
            for key in optional_today:
                lines.append(f"• {display_name(key)}")
        text = "\n".join(lines)
    else:
        if optional_today:
            lines = ["Сегодня можно по желанию:"]
            for key in optional_today:
                lines.append(f"• {display_name(key)}")
            text = "\n".join(lines)
        else:
            text = "Сегодня по плану можно отдыхать."

    await context.bot.send_message(chat_id=chat_id, text=text)


# ===== MAIN =====

def main() -> None:
    """
    Точка входа.
    Используется локально и на Railway (через main.py).
    """
    telegram_bot_token: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
    if not telegram_bot_token:
        raise RuntimeError("Нужно задать TELEGRAM_BOT_TOKEN в переменных окружения.")

    application = ApplicationBuilder().token(telegram_bot_token).build()

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
