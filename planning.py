"""Explicit priority rules and short work sessions; no API or file access."""

from datetime import date

def calculate_work_minutes(
    estimated_minutes: int,
    available_minutes: int,
    session_limit: int = 15,
) -> int:
    if available_minutes <= 0:
        return 0

    return min(
        estimated_minutes,
        available_minutes,
        session_limit,
    )

def days_until_deadline(deadline: str | None, today: date) -> int | None:
    if deadline is None:
        return None

    deadline_date = date.fromisoformat(deadline)
    difference = deadline_date - today

    return difference.days

def calculate_priority(task: dict, energy: int, today: date) -> dict:
    score = task["importance"] * 2
    reasons = [f"Önem katkısı: {task['importance']} × 2 = {score}."]

    days_left = days_until_deadline(task["deadline"], today)

    if days_left is None:
        reasons.append("Deadline belirtilmedi: +0.")
    elif days_left <= 0:
        score += 4
        reasons.append("Deadline bugün veya geçmiş: +4.")
    elif days_left == 1:
        score += 3
        reasons.append("Deadline yarın: +3.")
    elif days_left <= 3:
        score += 1
        reasons.append("Deadline 2–3 gün içinde: +1.")
    else:
        reasons.append("Deadline 3 günden daha uzakta: +0.")

    penalty = 0

    if energy <= 2:
        if task["cognitive_load"] == "high":
            penalty = 3
        elif task["cognitive_load"] == "medium":
            penalty = 1

    score -= penalty
    reasons.append(
        f"Enerji {energy}/5, bilişsel yük {task['cognitive_load']}: "
        f"kesinti {penalty}."
    )

    return {"score": score, "reasons": reasons}

def is_task_blocked(task: dict) -> bool:
    if "blocked" in task:
        return task["blocked"]

    checkpoints = task.get("checkpoints", [])
    return bool(checkpoints) and checkpoints[-1]["feedback"] == "blocked"


def select_active_tasks(
    tasks: list[dict], available_minutes: int, energy: int, today: date,
) -> tuple[list[dict], dict[str, int]]:
    ranked_tasks = sorted(
        tasks,
        key=lambda task: calculate_priority(task, energy, today)["score"],
        reverse=True,
    )

    active_tasks = []
    work_minutes_by_id = {}
    remaining_minutes = available_minutes

    for task in ranked_tasks:
        if len(active_tasks) == 2 or remaining_minutes <= 0:
            break

        if task.get("archived") or task.get("completed") or is_task_blocked(task):
            continue

        work_minutes = calculate_work_minutes(
            task["estimated_minutes"],
            remaining_minutes,
        )

        active_tasks.append(task)
        work_minutes_by_id[task["id"]] = work_minutes
        remaining_minutes -= work_minutes

    return active_tasks, work_minutes_by_id
