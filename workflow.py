"""Shared progress rules. Callers collect input and save after a successful change."""

from datetime import date, datetime


def update_task_context(task: dict, outcome: str | None, context: str | None) -> bool:
    """Changed goals invalidate a pending action, but never erase history."""
    changes = {}
    for field, value in (("desired_outcome", outcome), ("context", context)):
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Hedef ve bağlam boş olamaz.")
            if value.strip() != task.get(field):
                changes[field] = value.strip()
    if not changes:
        return False
    task.update(changes)
    task["next_action"] = None
    return True


def record_checkpoint(
    task: dict,
    day: dict,
    action: str,
    feedback: str,
    planned_minutes: int,
    spent_minutes: int,
    note: str | None = None,
    *,
    now: datetime | None = None,
    telegram_update_id: int | None = None,
) -> dict:
    """Record one self-reported work session; this does not finish the parent task."""
    if feedback not in ("done", "continue", "blocked"):
        raise ValueError("Durum done, continue veya blocked olmalı.")
    if not isinstance(action, str) or not action.strip():
        raise ValueError("Önce yapacağın adımı belirt.")
    if type(planned_minutes) is not int or planned_minutes <= 0:
        raise ValueError("Planlanan süre sıfırdan büyük olmalı.")
    if type(spent_minutes) is not int or spent_minutes < 0:
        raise ValueError("Harcanan süre negatif olamaz.")
    remaining = day.get("remaining_minutes")
    if type(remaining) is not int or remaining < 0:
        raise ValueError("Kalan süre geçersiz.")
    if note is not None and not isinstance(note, str):
        raise ValueError("Açıklama metin olmalı.")
    note = note.strip() if note else None
    if feedback in ("done", "blocked") and not note:
        raise ValueError("Tamamladığın çıktıyı veya karşılaştığın engeli belirt.")
    if telegram_update_id is not None and (
        type(telegram_update_id) is not int or telegram_update_id < 0
    ):
        raise ValueError("Mesaj kimliği geçersiz.")
    now = datetime.now().astimezone() if now is None else now
    if now.utcoffset() is None:
        raise ValueError("Kayıt zamanı saat dilimi içermeli.")

    checkpoints = task.setdefault("checkpoints", [])
    checkpoint = {
        "version": len(checkpoints) + 1,
        "action": action.strip(),
        "feedback": feedback,
        "blocker": note if feedback == "blocked" else None,
        "output_note": note if feedback == "done" else None,
        "progress_note": note if feedback == "continue" else None,
        "recorded_at": now.isoformat(),
        "planned_minutes": planned_minutes,
        "spent_minutes": spent_minutes,
    }
    if telegram_update_id is not None:
        checkpoint["telegram_update_id"] = telegram_update_id
    checkpoints.append(checkpoint)
    task["blocked"] = feedback == "blocked"
    task["next_action"] = None if feedback == "done" else action.strip()
    day["remaining_minutes"] = max(0, remaining - spent_minutes)
    return checkpoint


def daily_summary(tasks: list[dict], today: date) -> dict:
    """Summarize reported work, not measured productivity or verified outputs."""
    records = []
    for task in tasks:
        for checkpoint in task.get("checkpoints", []):
            timestamp = datetime.fromisoformat(checkpoint["recorded_at"]).astimezone()
            if timestamp.date() == today:
                records.append((timestamp, task, checkpoint))
    records.sort(key=lambda record: record[0])
    counts = {"done": 0, "continue": 0, "blocked": 0}
    spent_minutes = 0
    missing_duration = 0
    for _, _, checkpoint in records:
        counts[checkpoint["feedback"]] += 1
        if "spent_minutes" in checkpoint:
            spent_minutes += checkpoint["spent_minutes"]
        else:
            missing_duration += 1
    return {
        "counts": counts,
        "spent_minutes": spent_minutes,
        "missing_duration": missing_duration,
        "completed": [record for record in records if record[2]["feedback"] == "done"],
    }
