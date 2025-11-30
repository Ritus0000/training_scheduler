# training_scheduler_json_logic.py
#
# Логика:
# - хранение состояния тренировок в JSON-файле
# - частота упражнений за неделю
# - минимальный rest между сессиями
# - защита от двух high CNS тренировок подряд
# - предупреждение, если давно ничего не отмечалось
#
# Формат JSON (training_state.json):
# {
#   "metadata": {
#     "iso_year": 2025,
#     "iso_week": 48
#   },
#   "exercises": {
#     "squat": {
#       "last_done_local_date": "2025-11-29",
#       "times_completed_this_week": 2
#     },
#     ...
#   }
# }

import json
import os
from datetime import date, datetime, timedelta
from typing import Dict, Any, List, Optional

from zoneinfo import ZoneInfo

# Файл состояния (можно переопределить через переменную окружения)
TRAINING_STATE_FILE_PATH: str = os.getenv("TRAINING_STATE_FILE_PATH", "training_state.json")

# Часовой пояс пользователя (фиксируем Gdańsk/Poland)
USER_TIMEZONE: ZoneInfo = ZoneInfo("Europe/Warsaw")


# ===== 1. ОПРЕДЕЛЕНИЕ УПРАЖНЕНИЙ (СТАТИЧЕСКИЙ КОНФИГ) =====

# Ключ → словарь параметров
EXERCISE_DEFINITIONS_BY_KEY: Dict[str, Dict[str, Any]] = {
    "squat": {
        "exercise_key": "squat",
        "display_name_ru": "«Приседания»",
        "description_ru": "Любые приседания: штанга, гоблет, смит.",
        "times_per_week_target": 2,
        "minimal_rest_hours_between_sessions": 48,
        "cns_load_level": "high",
    },
    "hinge": {
        "exercise_key": "hinge",
        "display_name_ru": "«Румынская тяга / Hinge»",
        "description_ru": "Румынская тяга, гиперэкстензии, работа по задней цепи.",
        "times_per_week_target": 1,
        "minimal_rest_hours_between_sessions": 72,
        "cns_load_level": "high",
    },
    "unilateral": {
        "exercise_key": "unilateral",
        "display_name_ru": "«Одноногие упражнения»",
        "description_ru": "Болгарские приседы, выпады, шаги на тумбу.",
        "times_per_week_target": 2,
        "minimal_rest_hours_between_sessions": 48,
        "cns_load_level": "medium",
    },
    "calves": {
        "exercise_key": "calves",
        "display_name_ru": "«Икры»",
        "description_ru": "Любые подъёмы на носки: стоя, сидя, на одной ноге.",
        "times_per_week_target": 5,
        "minimal_rest_hours_between_sessions": 24,
        "cns_load_level": "low",
    },
    "ohp": {
        "exercise_key": "ohp",
        "display_name_ru": "«Жим стоя (OHP)»",
        "description_ru": "Жим штанги или гантелей вверх стоя.",
        "times_per_week_target": 2,
        "minimal_rest_hours_between_sessions": 48,
        "cns_load_level": "medium",
    },
    "horizontal_row": {
        "exercise_key": "horizontal_row",
        "display_name_ru": "«Горизонтальная тяга»",
        "description_ru": "Тяга штанги/гантели/блока к поясу.",
        "times_per_week_target": 2,
        "minimal_rest_hours_between_sessions": 48,
        "cns_load_level": "low",
    },
    "vertical_pull_volume": {
        "exercise_key": "vertical_pull_volume",
        "display_name_ru": "«Подтягивания — объём»",
        "description_ru": "Лёгкие подходы на технику и объём (День А).",
        "times_per_week_target": 1,
        "minimal_rest_hours_between_sessions": 72,
        "cns_load_level": "medium",
    },
    "vertical_pull_strength": {
        "exercise_key": "vertical_pull_strength",
        "display_name_ru": "«Подтягивания — сила»",
        "description_ru": "Тяжёлые сеты и медленные негативы (День Б).",
        "times_per_week_target": 1,
        "minimal_rest_hours_between_sessions": 72,
        "cns_load_level": "high",
    },
    "core": {
        "exercise_key": "core",
        "display_name_ru": "«Кор / пресс»",
        "description_ru": "Планки, dead bug, анти-ротация, стабилизация корпуса.",
        "times_per_week_target": 3,
        "minimal_rest_hours_between_sessions": 24,
        "cns_load_level": "low",
    },
}


# ===== 2. УТИЛИТЫ ВРЕМЕНИ =====

def get_current_local_date() -> date:
    current_datetime_utc: datetime = datetime.now(tz=ZoneInfo("UTC"))
    current_datetime_local: datetime = current_datetime_utc.astimezone(USER_TIMEZONE)
    return current_datetime_local.date()


# ===== 3. РАБОТА СО СОСТОЯНИЕМ (JSON) =====

def initialize_default_training_state() -> Dict[str, Any]:
    """
    Создаёт начальное состояние для всех упражнений.
    """
    current_local_date: date = get_current_local_date()
    iso_year, iso_week, _ = current_local_date.isocalendar()

    exercises_state: Dict[str, Dict[str, Any]] = {}
    for exercise_key in EXERCISE_DEFINITIONS_BY_KEY.keys():
        exercises_state[exercise_key] = {
            "last_done_local_date": None,           # строка 'YYYY-MM-DD' или None
            "times_completed_this_week": 0,
        }

    training_state: Dict[str, Any] = {
        "metadata": {
            "iso_year": iso_year,
            "iso_week": iso_week,
        },
        "exercises": exercises_state,
    }
    return training_state


def ensure_all_exercises_present(training_state: Dict[str, Any]) -> None:
    """
    Гарантирует, что в состоянии есть все упражнения, определённые в EXERCISE_DEFINITIONS_BY_KEY.
    Если добавишь новое упражнение в конфиг, состояние автоматически его подхватит.
    """
    exercises_state: Dict[str, Any] = training_state.setdefault("exercises", {})

    for exercise_key in EXERCISE_DEFINITIONS_BY_KEY.keys():
        if exercise_key not in exercises_state:
            exercises_state[exercise_key] = {
                "last_done_local_date": None,
                "times_completed_this_week": 0,
            }


def perform_week_rollover_if_needed(training_state: Dict[str, Any]) -> None:
    """
    Если началась новая ISO-неделя (по локальному времени) — обнуляем weekly-счётчики.
    """
    metadata: Dict[str, Any] = training_state.setdefault("metadata", {})
    current_local_date: date = get_current_local_date()
    current_iso_year, current_iso_week, _ = current_local_date.isocalendar()

    stored_iso_year: int = metadata.get("iso_year", current_iso_year)
    stored_iso_week: int = metadata.get("iso_week", current_iso_week)

    if (stored_iso_year, stored_iso_week) != (current_iso_year, current_iso_week):
        # Новая неделя — обнуляем weekly-счётчики
        exercises_state: Dict[str, Any] = training_state.setdefault("exercises", {})
        for exercise_state in exercises_state.values():
            exercise_state["times_completed_this_week"] = 0

        metadata["iso_year"] = current_iso_year
        metadata["iso_week"] = current_iso_week


def load_training_state() -> Dict[str, Any]:
    """
    Загружает состояние из JSON-файла.
    При отсутствии файла — создаёт дефолтное состояние.
    При смене недели — автоматически обнуляет weekly-счётчики.
    """
    if not os.path.exists(TRAINING_STATE_FILE_PATH):
        training_state: Dict[str, Any] = initialize_default_training_state()
        save_training_state(training_state)
        return training_state

    with open(TRAINING_STATE_FILE_PATH, "r", encoding="utf-8") as file_handle:
        training_state: Dict[str, Any] = json.load(file_handle)

    ensure_all_exercises_present(training_state)
    perform_week_rollover_if_needed(training_state)
    return training_state


def save_training_state(training_state: Dict[str, Any]) -> None:
    """
    Сохраняет состояние в JSON-файл.
    """
    with open(TRAINING_STATE_FILE_PATH, "w", encoding="utf-8") as file_handle:
        json.dump(training_state, file_handle, ensure_ascii=False, indent=2)


# ===== 4. ОБНОВЛЕНИЕ СОСТОЯНИЯ ПРИ ВЫПОЛНЕНИИ УПРАЖНЕНИЯ =====

def record_exercise_completion_for_date(exercise_key: str, performed_local_date: date) -> None:
    """
    Обновляет состояние: отмечает, что упражнение выполнено в указанный день.
    - last_done_local_date = performed_local_date
    - times_completed_this_week += 1
    """
    if exercise_key not in EXERCISE_DEFINITIONS_BY_KEY:
        raise ValueError(f"Неизвестное упражнение: {exercise_key}")

    training_state: Dict[str, Any] = load_training_state()
    exercises_state: Dict[str, Any] = training_state["exercises"]
    exercise_runtime_state: Dict[str, Any] = exercises_state[exercise_key]

    exercise_runtime_state["last_done_local_date"] = performed_local_date.isoformat()
    exercise_runtime_state["times_completed_this_week"] = (
        exercise_runtime_state.get("times_completed_this_week", 0) + 1
    )

    save_training_state(training_state)


# ===== 5. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ОПТИМИЗАТОРА =====

def compute_last_any_training_local_date(training_state: Dict[str, Any]) -> Optional[date]:
    """
    Находит максимальную (последнюю) дату выполнения среди всех упражнений.
    Используется для предупреждения о "дырках" в журнале.
    """
    exercises_state: Dict[str, Any] = training_state.get("exercises", {})
    latest_date: Optional[date] = None

    for exercise_runtime_state in exercises_state.values():
        last_done_local_date_str: Optional[str] = exercise_runtime_state.get("last_done_local_date")
        if last_done_local_date_str is None:
            continue
        try:
            parsed_date: date = date.fromisoformat(last_done_local_date_str)
        except ValueError:
            continue

        if latest_date is None or parsed_date > latest_date:
            latest_date = parsed_date

    return latest_date


def compute_last_high_cns_training_local_date(training_state: Dict[str, Any]) -> Optional[date]:
    """
    Находит последнюю дату high CNS тренировки (по локальному времени).
    Используется для правила: "не делать high CNS два дня подряд".
    """
    exercises_state: Dict[str, Any] = training_state.get("exercises", {})
    latest_high_cns_date: Optional[date] = None

    for exercise_key, exercise_definition in EXERCISE_DEFINITIONS_BY_KEY.items():
        if exercise_definition["cns_load_level"] != "high":
            continue

        exercise_runtime_state: Dict[str, Any] = exercises_state.get(exercise_key, {})
        last_done_local_date_str: Optional[str] = exercise_runtime_state.get("last_done_local_date")
        if last_done_local_date_str is None:
            continue

        try:
            parsed_date: date = date.fromisoformat(last_done_local_date_str)
        except ValueError:
            continue

        if latest_high_cns_date is None or parsed_date > latest_high_cns_date:
            latest_high_cns_date = parsed_date

    return latest_high_cns_date


# ===== 6. ГЛАВНЫЙ ОПТИМИЗАТОР: РЕКОМЕНДАЦИИ НА СЕГОДНЯ =====

def get_training_recommendations_for_today() -> Dict[str, Any]:
    """
    Возвращает:
    {
      "must_do_today": [exercise_key, ...],
      "optional_today": [...],
      "cns_blocked_today": [...],
      "not_ready_by_rest": [...],
      "log_gap_warning": Optional[str]
    }
    """
    training_state: Dict[str, Any] = load_training_state()
    current_local_date: date = get_current_local_date()

    # 1) Детектор "дырок" в журнале
    last_any_training_local_date: Optional[date] = compute_last_any_training_local_date(training_state)
    log_gap_warning: Optional[str] = None
    if last_any_training_local_date is not None:
        days_since_last_log: int = (current_local_date - last_any_training_local_date).days
        if days_since_last_log >= 2:
            log_gap_warning = (
                f"Внимание: ты последний раз отмечал тренировки {last_any_training_local_date.isoformat()} "
                f"(это было {days_since_last_log} дней назад).\n"
                f"Если ты тренировался в эти дни, но не отмечал, рекомендации могут быть неточными. "
                f"Сначала дозаполни пропуски через /done с выбором «вчера»."
            )

    # 2) high CNS правило
    last_high_cns_training_local_date: Optional[date] = compute_last_high_cns_training_local_date(training_state)
    yesterday_local_date: date = current_local_date - timedelta(days=1)
    was_high_cns_yesterday: bool = last_high_cns_training_local_date == yesterday_local_date

    must_do_today: List[str] = []
    optional_today: List[str] = []
    cns_blocked_today: List[str] = []
    not_ready_by_rest: List[str] = []

    exercises_state: Dict[str, Any] = training_state.get("exercises", {})

    for exercise_key, exercise_definition in EXERCISE_DEFINITIONS_BY_KEY.items():
        exercise_runtime_state: Dict[str, Any] = exercises_state.get(exercise_key, {
            "last_done_local_date": None,
            "times_completed_this_week": 0,
        })

        last_done_local_date_str: Optional[str] = exercise_runtime_state.get("last_done_local_date")
        times_completed_this_week: int = exercise_runtime_state.get("times_completed_this_week", 0)

        if last_done_local_date_str is None:
            hours_since_last_done: float = 1e6  # условно "очень давно"
        else:
            try:
                last_done_local_date: date = date.fromisoformat(last_done_local_date_str)
                days_since_last_done: int = (current_local_date - last_done_local_date).days
                hours_since_last_done = days_since_last_done * 24.0
            except ValueError:
                hours_since_last_done = 1e6

        is_rest_satisfied: bool = (
            hours_since_last_done >= exercise_definition["minimal_rest_hours_between_sessions"]
        )

        remaining_times_this_week: int = max(
            0,
            exercise_definition["times_per_week_target"] - times_completed_this_week,
        )

        is_high_cns_exercise: bool = exercise_definition["cns_load_level"] == "high"
        if is_high_cns_exercise and was_high_cns_yesterday:
            cns_blocked_today.append(exercise_key)
            continue

        if not is_rest_satisfied:
            not_ready_by_rest.append(exercise_key)
            continue

        if remaining_times_this_week > 0:
            must_do_today.append(exercise_key)
        else:
            optional_today.append(exercise_key)

    return {
        "must_do_today": must_do_today,
        "optional_today": optional_today,
        "cns_blocked_today": cns_blocked_today,
        "not_ready_by_rest": not_ready_by_rest,
        "log_gap_warning": log_gap_warning,
    }
