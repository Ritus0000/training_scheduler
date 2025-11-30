# training_scheduler_json_logic.py
#
# Вся логика планировщика:
# - хранение состояния тренировок в JSON-файле;
# - недельная частота упражнений;
# - минимальный rest между сессиями;
# - защита от двух high-CNS дней подряд;
# - предупреждение, если давно ничего не логировалось.

import json
import os
from datetime import date, datetime, timedelta
from typing import Dict, Any, List, Optional

from zoneinfo import ZoneInfo

# Путь к JSON-файлу (можно переопределить через переменную окружения)
TRAINING_STATE_FILE_PATH: str = os.getenv("TRAINING_STATE_FILE_PATH", "training_state.json")
def ensure_training_state_directory_exists() -> None:
    """
    Гарантирует, что директория для TRAINING_STATE_FILE_PATH существует.
    Если путь без директории (просто 'training_state.json'), ничего не делаем.
    """
    directory_path: str = os.path.dirname(TRAINING_STATE_FILE_PATH)
    if directory_path:
        os.makedirs(directory_path, exist_ok=True)

# Часовой пояс пользователя
USER_TIMEZONE: ZoneInfo = ZoneInfo("Europe/Warsaw")


# ===== 1. ОПРЕДЕЛЕНИЕ УПРАЖНЕНИЙ =====

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
        "description_ru": "Румынская тяга, гиперэкстензии, задняя цепь.",
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


# ===== 2. ВРЕМЯ =====

def get_current_local_date() -> date:
    now_utc: datetime = datetime.now(tz=ZoneInfo("UTC"))
    now_local: datetime = now_utc.astimezone(USER_TIMEZONE)
    return now_local.date()


# ===== 3. СОСТОЯНИЕ (JSON) =====

def initialize_default_training_state() -> Dict[str, Any]:
    current_local_date: date = get_current_local_date()
    iso_year, iso_week, _ = current_local_date.isocalendar()

    exercises_state: Dict[str, Dict[str, Any]] = {}
    for exercise_key in EXERCISE_DEFINITIONS_BY_KEY.keys():
        exercises_state[exercise_key] = {
            "last_done_local_date": None,
            "times_completed_this_week": 0,
        }

    return {
        "metadata": {
            "iso_year": iso_year,
            "iso_week": iso_week,
        },
        "exercises": exercises_state,
    }


def ensure_all_exercises_present(training_state: Dict[str, Any]) -> None:
    exercises_state: Dict[str, Any] = training_state.setdefault("exercises", {})
    for exercise_key in EXERCISE_DEFINITIONS_BY_KEY.keys():
        if exercise_key not in exercises_state:
            exercises_state[exercise_key] = {
                "last_done_local_date": None,
                "times_completed_this_week": 0,
            }


def perform_week_rollover_if_needed(training_state: Dict[str, Any]) -> None:
    metadata: Dict[str, Any] = training_state.setdefault("metadata", {})
    current_local_date: date = get_current_local_date()
    current_iso_year, current_iso_week, _ = current_local_date.isocalendar()

    stored_iso_year: int = metadata.get("iso_year", current_iso_year)
    stored_iso_week: int = metadata.get("iso_week", current_iso_week)

    if (stored_iso_year, stored_iso_week) != (current_iso_year, current_iso_week):
        exercises_state: Dict[str, Any] = training_state.setdefault("exercises", {})
        for exercise_state in exercises_state.values():
            exercise_state["times_completed_this_week"] = 0

        metadata["iso_year"] = current_iso_year
        metadata["iso_week"] = current_iso_week


def load_training_state() -> Dict[str, Any]:
    if not os.path.exists(TRAINING_STATE_FILE_PATH):
        state: Dict[str, Any] = initialize_default_training_state()
        save_training_state(state)
        return state

    with open(TRAINING_STATE_FILE_PATH, "r", encoding="utf-8") as f:
        state: Dict[str, Any] = json.load(f)

    ensure_all_exercises_present(state)
    perform_week_rollover_if_needed(state)
    return state


def save_training_state(training_state: Dict[str, Any]) -> None:
    with open(TRAINING_STATE_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(training_state, f, ensure_ascii=False, indent=2)


# ===== 4. ОБНОВЛЕНИЕ СОСТОЯНИЯ =====

def record_exercise_completion_for_date(exercise_key: str, performed_local_date: date) -> None:
    if exercise_key not in EXERCISE_DEFINITIONS_BY_KEY:
        raise ValueError(f"Неизвестное упражнение: {exercise_key}")

    training_state: Dict[str, Any] = load_training_state()
    exercises_state: Dict[str, Any] = training_state["exercises"]
    runtime_state: Dict[str, Any] = exercises_state[exercise_key]

    runtime_state["last_done_local_date"] = performed_local_date.isoformat()
    runtime_state["times_completed_this_week"] = runtime_state.get("times_completed_this_week", 0) + 1

    save_training_state(training_state)


# ===== 5. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====

def compute_last_any_training_local_date(training_state: Dict[str, Any]) -> Optional[date]:
    exercises_state: Dict[str, Any] = training_state.get("exercises", {})
    latest: Optional[date] = None

    for runtime_state in exercises_state.values():
        last_str: Optional[str] = runtime_state.get("last_done_local_date")
        if not last_str:
            continue
        try:
            d = date.fromisoformat(last_str)
        except ValueError:
            continue
        if latest is None or d > latest:
            latest = d
    return latest


def compute_last_high_cns_training_local_date(training_state: Dict[str, Any]) -> Optional[date]:
    exercises_state: Dict[str, Any] = training_state.get("exercises", {})
    latest: Optional[date] = None

    for exercise_key, definition in EXERCISE_DEFINITIONS_BY_KEY.items():
        if definition["cns_load_level"] != "high":
            continue

        runtime_state: Dict[str, Any] = exercises_state.get(exercise_key, {})
        last_str: Optional[str] = runtime_state.get("last_done_local_date")
        if not last_str:
            continue
        try:
            d = date.fromisoformat(last_str)
        except ValueError:
            continue

        if latest is None or d > latest:
            latest = d
    return latest


# ===== 6. ОПТИМИЗАТОР НА СЕГОДНЯ =====

def get_training_recommendations_for_today() -> Dict[str, Any]:
    training_state: Dict[str, Any] = load_training_state()
    current_date: date = get_current_local_date()

    # 1) дырки в логах
    last_any: Optional[date] = compute_last_any_training_local_date(training_state)
    log_gap_warning: Optional[str] = None
    if last_any is not None:
        days_since = (current_date - last_any).days
        if days_since >= 2:
            log_gap_warning = (
                f"Внимание: последние отмеченные тренировки были {last_any.isoformat()} "
                f"(это {days_since} дней назад).\n"
                f"Если ты тренировался, но не логировал, сначала дозаполни пропуски через /done с выбором «вчера»."
            )

    # 2) high CNS два дня подряд
    last_high: Optional[date] = compute_last_high_cns_training_local_date(training_state)
    yesterday: date = current_date - timedelta(days=1)
    was_high_yesterday: bool = last_high == yesterday

    must_do_today: List[str] = []
    optional_today: List[str] = []
    cns_blocked_today: List[str] = []
    not_ready_by_rest: List[str] = []

    exercises_state: Dict[str, Any] = training_state.get("exercises", {})

    for exercise_key, definition in EXERCISE_DEFINITIONS_BY_KEY.items():
        runtime_state: Dict[str, Any] = exercises_state.get(
            exercise_key,
            {"last_done_local_date": None, "times_completed_this_week": 0},
        )

        last_str: Optional[str] = runtime_state.get("last_done_local_date")
        times_done: int = runtime_state.get("times_completed_this_week", 0)

        if last_str is None:
            hours_since_last = 1e6
        else:
            try:
                last_date = date.fromisoformat(last_str)
                days_since_last = (current_date - last_date).days
                hours_since_last = days_since_last * 24.0
            except ValueError:
                hours_since_last = 1e6

        rest_ok: bool = hours_since_last >= definition["minimal_rest_hours_between_sessions"]
        remaining_this_week: int = max(0, definition["times_per_week_target"] - times_done)
        is_high_cns: bool = definition["cns_load_level"] == "high"

        if is_high_cns and was_high_yesterday:
            cns_blocked_today.append(exercise_key)
            continue

        if not rest_ok:
            not_ready_by_rest.append(exercise_key)
            continue

        if remaining_this_week > 0:
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
