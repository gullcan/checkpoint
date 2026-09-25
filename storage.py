"""Read and save one user's local JSON state; never replace invalid data."""

import json
import re
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

STATE_PATH = Path(__file__).resolve().parent / "state.json"

def save_state(state: dict, path: Path | None = None) -> None:
    """Write beside the target, then replace it; assumes a single writer."""
    path = STATE_PATH if path is None else path
    json_text = json.dumps(state, ensure_ascii=False, indent=2)
    temporary_path = path.with_suffix(".tmp")

    try:
        temporary_path.write_text(json_text, encoding="utf-8")
        temporary_path.replace(path)
    except OSError as error:
        raise SystemExit(f"Durum kaydedilemedi: {error}")

def is_valid_task(task: object) -> bool:
    if not isinstance(task, dict):
        return False

    for field in ("telegram_created_update_id", "telegram_status_update_id"):
        if field in task and (type(task[field]) is not int or task[field] < 0):
            return False
    if "completed" in task and type(task["completed"]) is not bool:
        return False

    title = task.get("title")
    if not isinstance(title, str) or not title.strip():
        return False

    importance = task.get("importance")
    if type(importance) is not int or not 1 <= importance <= 5:
        return False

    minutes = task.get("estimated_minutes")
    if type(minutes) is not int or minutes <= 0:
        return False

    if task.get("cognitive_load") not in ["low", "medium", "high"]:
        return False

    if "deadline" not in task:
        return False

    deadline = task["deadline"]

    if deadline is not None:
        if not isinstance(deadline, str):
            return False

        try:
            parsed_deadline = date.fromisoformat(deadline)
        except ValueError:
            return False

        if parsed_deadline.isoformat() != deadline:
            return False
    if "blocked" in task and type(task["blocked"]) is not bool:
        return False
    if "archived" in task and type(task["archived"]) is not bool:
        return False

    next_action = task.get("next_action")

    if next_action is not None:
        if not isinstance(next_action, str) or not next_action.strip():
            return False
    if "desired_outcome" in task:
        outcome = task["desired_outcome"]

        if not isinstance(outcome, str) or not outcome.strip():
            return False
    if "context" in task:
        context = task["context"]

        if not isinstance(context, str) or not context.strip():
            return False

    checkpoints = task.get("checkpoints", [])

    if not isinstance(checkpoints, list):
        return False

    for expected_version, checkpoint in enumerate(checkpoints, start=1):
        if not isinstance(checkpoint, dict):
            return False

        version = checkpoint.get("version")
        if type(version) is not int or version != expected_version:
            return False

        action = checkpoint.get("action")
        if not isinstance(action, str) or not action.strip():
            return False

        feedback = checkpoint.get("feedback")
        if feedback not in ["done", "blocked", "continue"]:
            return False

        if "output_note" in checkpoint:
            output_note = checkpoint["output_note"]

            if feedback == "done":
                if not isinstance(output_note, str) or not output_note.strip():
                    return False
            elif output_note is not None:
                return False
        if "planned_minutes" in checkpoint:
            planned = checkpoint["planned_minutes"]

            if type(planned) is not int or planned <= 0:
                return False

        if "spent_minutes" in checkpoint:
            spent = checkpoint["spent_minutes"]

            if type(spent) is not int or spent < 0:
                return False
        blocker = checkpoint.get("blocker")
        if feedback == "blocked":
            if not isinstance(blocker, str) or not blocker.strip():
                return False
        elif blocker is not None:
            return False

        update_id = checkpoint.get("telegram_update_id")
        if update_id is not None and (type(update_id) is not int or update_id < 0):
            return False
        progress_note = checkpoint.get("progress_note")
        if progress_note is not None and (
            feedback != "continue" or not isinstance(progress_note, str)
        ):
            return False

        recorded_at = checkpoint.get("recorded_at")
        if not isinstance(recorded_at, str):
            return False

        try:
            timestamp = datetime.fromisoformat(recorded_at)
        except ValueError:
            return False

        if timestamp.utcoffset() is None:
            return False

    return True

def load_state(path: Path | None = None) -> dict:
    path = STATE_PATH if path is None else path
    try:
        json_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"tasks": [], "day": None}
    except UnicodeDecodeError:
        raise SystemExit("Kayıt dosyası UTF-8 olarak okunamadı. Dosya değiştirilmedi.") from None
    except OSError as error:
        raise SystemExit(f"Kayıt dosyası okunamadı: {error}")

    try:
        state = json.loads(json_text)
    except json.JSONDecodeError:
        raise SystemExit("state.json geçerli JSON değil. Dosya değiştirilmedi.")

    if not isinstance(state, dict):
        raise SystemExit("State bir dictionary olmalı. Dosya değiştirilmedi.")

    if not isinstance(state.get("tasks"), list):
        raise SystemExit("State içinde tasks listesi olmalı. Dosya değiştirilmedi.")

    seen_ids = []

    for position, task in enumerate(state["tasks"], start=1):
        if not is_valid_task(task):
            raise SystemExit(
                f"Kayıttaki {position}. görev geçersiz. Dosya değiştirilmedi."
            )

        if "id" not in task:
            task["id"] = str(uuid4())

        task_id = task["id"]

        if not isinstance(task_id, str) or not task_id.strip():
            raise SystemExit(
                f"Kayıttaki {position}. görev kimliği geçersiz. "
                "Dosya değiştirilmedi."
            )

        if task_id in seen_ids:
            raise SystemExit(
                f"Kayıttaki {position}. görev kimliği tekrarlanıyor. "
                "Dosya değiştirilmedi."
            )

        seen_ids.append(task_id)

    validate_day(state.get("day"))
    if "telegram" in state:
        runtime = state["telegram"]
        if not isinstance(runtime, dict):
            raise SystemExit("Telegram kaydı geçersiz. Dosya değiştirilmedi.")
        get_runtime(state, runtime.get("owner_id"))
    selection = state.get("selection")
    if selection is not None:
        if not isinstance(selection, dict):
            raise SystemExit("Plan kaydı geçersiz. Dosya değiştirilmedi.")
        ids = selection.get("active_task_ids")
        if (
            not isinstance(ids, list)
            or len(ids) > 2
            or any(not isinstance(item, str) or item not in seen_ids for item in ids)
            or len(set(ids)) != len(ids)
            or selection.get("daily_win_id") != (ids[0] if ids else None)
        ):
            raise SystemExit("Plan kaydı geçersiz. Dosya değiştirilmedi.")
        minutes = selection.get("work_minutes_by_id")
        if minutes is not None and (
            not isinstance(minutes, dict) or set(minutes) != set(ids)
            or any(type(value) is not int or value <= 0 for value in minutes.values())
        ):
            raise SystemExit("Plandaki süreler geçersiz. Dosya değiştirilmedi.")
    return state

def valid_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_day(day: object) -> None:
    """Missing day is a fresh start; old records may lack remaining_minutes."""
    if day is None:
        return
    valid = isinstance(day, dict) and valid_date(day.get("date"))
    if valid:
        for field in ("available_minutes", "remaining_minutes", "telegram_settings_update_id"):
            if field in day and (type(day[field]) is not int or day[field] < 0):
                valid = False
        if "energy" in day and (type(day["energy"]) is not int or not 1 <= day["energy"] <= 5):
            valid = False
    if not valid:
        raise SystemExit("Günlük kayıt geçersiz. Dosya değiştirilmedi.")


def is_valid_reminder(reminder: object) -> bool:
    if not isinstance(reminder, dict) or type(reminder.get("enabled")) is not bool:
        return False
    if reminder["enabled"]:
        value = reminder.get("time")
        if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
            return False
    last_date = reminder.get("last_queued_date")
    return last_date is None or valid_date(last_date)


def get_runtime(state: dict, owner_id: int) -> dict:
    runtime = state.setdefault('telegram', {
        'owner_id': owner_id,
        'offset': 0,
        'session': {},
        'pending_reply': None,
    })
    valid = isinstance(runtime, dict)
    if valid:
        valid = (
            type(runtime.get('owner_id')) is int
            and runtime['owner_id'] > 0
            and runtime['owner_id'] == owner_id
            and type(runtime.get('offset')) is int
            and runtime['offset'] >= 0
            and isinstance(runtime.get('session'), dict)
        )
    if not valid:
        raise SystemExit('Telegram kaydı geçersiz veya başka hesaba ait. Dosya değiştirilmedi.')
    if "reminder" in runtime and not is_valid_reminder(runtime["reminder"]):
        raise SystemExit("Hatırlatma kaydı geçersiz. Dosya değiştirilmedi.")
    session = runtime['session']
    plan = session.get('plan')
    work = session.get('work')
    try:
        if plan is not None:
            if not (isinstance(plan, dict)):
                raise ValueError("Geçersiz oturum alanı")
            if not (isinstance(plan['date'], str)):
                raise ValueError("Geçersiz oturum alanı")
            if not (date.fromisoformat(plan['date']).isoformat() == plan['date']):
                raise ValueError("Geçersiz oturum alanı")
            if not (type(plan['energy']) is int and 1 <= plan['energy'] <= 5):
                raise ValueError("Geçersiz oturum alanı")
            ids = plan['task_ids']
            if not (isinstance(ids, list) and 1 <= len(ids) <= 2):
                raise ValueError("Geçersiz oturum alanı")
            if not (all(isinstance(item, str) and item for item in ids)):
                raise ValueError("Geçersiz oturum alanı")
            if not (len(set(ids)) == len(ids)):
                raise ValueError("Geçersiz oturum alanı")
            minutes = plan['work_minutes_by_id']
            if not (isinstance(minutes, dict) and set(minutes) == set(ids)):
                raise ValueError("Geçersiz oturum alanı")
            if not (all(type(value) is int and value > 0 for value in minutes.values())):
                raise ValueError("Geçersiz oturum alanı")
        if work is not None:
            if not (isinstance(work, dict) and plan is not None):
                raise ValueError("Geçersiz oturum alanı")
            if not (work['task_id'] in plan['task_ids']):
                raise ValueError("Geçersiz oturum alanı")
            action = work['action']
            if action is not None and not (isinstance(action, str) and action.strip()):
                raise ValueError("Geçersiz oturum alanı")
            if not (type(work['planned_minutes']) is int):
                raise ValueError("Geçersiz oturum alanı")
            if not (work['planned_minutes'] == plan['work_minutes_by_id'][work['task_id']]):
                raise ValueError("Geçersiz oturum alanı")
            if not (type(work['energy']) is int and work['energy'] == plan['energy']):
                raise ValueError("Geçersiz oturum alanı")
        pending = runtime.get('pending_reply')
        if pending is not None:
            if not (isinstance(pending, dict)):
                raise ValueError("Geçersiz oturum alanı")
            if not (type(pending['chat_id']) is int and pending['chat_id'] == owner_id):
                raise ValueError("Geçersiz oturum alanı")
            if not (isinstance(pending['text'], str) and pending['text']):
                raise ValueError("Geçersiz oturum alanı")
    except (KeyError, TypeError, ValueError):
        raise SystemExit('Saklanan Telegram oturumu geçersiz. Dosya değiştirilmedi.') from None
    return runtime
