from openai import APIError
from llm import generate_next_action
from uuid import uuid4
import json
from pathlib import Path
from datetime import date, datetime
from planning import calculate_work_minutes

STATE_PATH = Path(__file__).resolve().parent / "state.json"

def save_state(state):
    json_text = json.dumps(state, ensure_ascii=False, indent=2)
    temporary_path = STATE_PATH.with_suffix(".tmp")

    try:
        temporary_path.write_text(json_text, encoding="utf-8")
        temporary_path.replace(STATE_PATH)
    except OSError as error:
        raise SystemExit(f"Durum kaydedilemedi: {error}")

def is_valid_task(task):
    if not isinstance(task, dict):
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

        blocker = checkpoint.get("blocker")

        blocker = checkpoint.get("blocker")
        if feedback == "blocked":
            if not isinstance(blocker, str) or not blocker.strip():
                return False
        elif blocker is not None:
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

def load_state():
    try:
        json_text = STATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"tasks": [], "day": None}
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

    return state

def read_positive_integer(prompt):
    while True:
        text = input(prompt).strip()

        try:
            value = int(text)
        except ValueError:
            print("Lütfen bir tam sayı gir.")
            continue

        if value <= 0:
            print("Değer sıfırdan büyük olmalı.")
            continue

        return value

def read_energy():
    while True:
        energy = read_positive_integer("Mevcut enerji (1–5): ")

        if energy > 5:
            print("Enerji en fazla 5 olabilir.")
            continue

        return energy

def read_task():
    task_title = input("Görev adı: ").strip()

    if not task_title:
        print("Görev adı boş olamaz.")
        return None

    importance_text = input("Önem (1–5): ").strip()

    if importance_text not in ["1", "2", "3", "4", "5"]:
        print("Önem değeri 1–5 arasında bir tam sayı olmalı.")
        return None

    duration_text = input("Tahmini süre (dakika): ").strip()

    try:
        estimated_minutes = int(duration_text)
    except ValueError:
        print("Süreyi tam sayı olarak girmelisin.")
        return None

    if estimated_minutes <= 0:
        print("Süre sıfırdan büyük olmalı.")
        return None

    cognitive_load = input("Bilişsel yük (low/medium/high): ").strip().lower()

    if cognitive_load not in ["low", "medium", "high"]:
        print("Bilişsel yük low, medium veya high olmalı.")
        return None

    deadline_text = input("Deadline (YYYY-MM-DD, yoksa Enter): ").strip()
    deadline = None

    if deadline_text:
        try:
            parsed_deadline = datetime.strptime(deadline_text, "%Y-%m-%d").date()
        except ValueError:
            print("Geçerli bir tarih girmelisin. Örnek: 2026-09-25")
            return None

        if parsed_deadline.isoformat() != deadline_text:
            print("Tarihi YYYY-MM-DD biçiminde girmelisin.")
            return None

        deadline = parsed_deadline.isoformat()


    return {
        "id": str(uuid4()),
        "title": task_title,
        "importance": int(importance_text),
        "estimated_minutes": estimated_minutes,
        "cognitive_load": cognitive_load,
        "deadline": deadline,
    }

def days_until_deadline(deadline, today):
    if deadline is None:
        return None

    deadline_date = date.fromisoformat(deadline)
    difference = deadline_date - today

    return difference.days

def calculate_priority(task, energy, today):
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

def is_task_blocked(task):
    if "blocked" in task:
        return task["blocked"]

    checkpoints = task.get("checkpoints", [])
    return bool(checkpoints) and checkpoints[-1]["feedback"] == "blocked"


def review_blocked_tasks(tasks):
    for task in tasks:
        if not is_task_blocked(task):
            continue

        checkpoints = task.get("checkpoints", [])
        blocker = checkpoints[-1].get("blocker") if checkpoints else None

        print(f"Engelli görev: {task['title']}")
        print(f"Kaydedilen engel: {blocker or 'Belirtilmedi'}")

        while True:
            answer = input("Engel kalktı mı? (e/h): ").strip().lower()

            if answer in ["e", "h"]:
                task["blocked"] = answer == "h"
                break

            print("Lütfen e veya h gir.")

def select_active_tasks(tasks, available_minutes, energy, today):
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

        if is_task_blocked(task):
            continue

        work_minutes = calculate_work_minutes(
            task["estimated_minutes"],
            remaining_minutes,
        )

        active_tasks.append(task)
        work_minutes_by_id[task["id"]] = work_minutes
        remaining_minutes -= work_minutes

    return active_tasks, work_minutes_by_id

def read_feedback():
    while True:
        feedback = input(
            "Eylemin durumu (done/blocked/continue): "
        ).strip().lower()

        if feedback in ["done", "blocked", "continue"]:
            return feedback

        print("Lütfen done, blocked veya continue gir.")


def read_nonempty_text(prompt):
    while True:
        text = input(prompt).strip()

        if text:
            return text

        print("Lütfen kısa bir açıklama yaz.")


def manage_archive(state):
    tasks = state["tasks"]

    if not tasks:
        print("Henüz görev yok.")
        return

    while True:
        for number, task in enumerate(tasks, start=1):
            status = "arşivde" if task.get("archived", False) else "açık"
            print(f"{number}. [{status}] {task['title']}")

        answer = input(
            "Arşivlemek veya geri almak için görev numarası, çıkmak için q: "
        ).strip().lower()

        if answer == "q":
            return

        try:
            index = int(answer) - 1
        except ValueError:
            print("Lütfen görev numarası veya q gir.")
            continue

        if not 0 <= index < len(tasks):
            print("Bu numarada bir görev yok.")
            continue

        task = tasks[index]
        task["archived"] = not task.get("archived", False)
        save_state(state)

        status = "arşivlendi" if task["archived"] else "geri alındı"
        print(f"{task['title']} — {status}.")

def review_next_action(suggested_action):
    if suggested_action:
        print(f"Önerilen eylem: {suggested_action}")
    else:
        print("Kullanılabilir bir öneri yok; kendi eylemini yazabilirsin.")

    while True:
        choice = input(
            "Kabul et: k | Kendim yazacağım: y | Bu oturumu geç: q: "
        ).strip().lower()

        if choice == "q":
            return None

        if choice == "y":
            return read_nonempty_text(
                "Bu sürede yapacağın tek eylem ve ortaya çıkacak sonuç: "
            )

        if choice == "k" and suggested_action:
            return suggested_action

        print("Geçerli bir seçenek gir; öneri yoksa y veya q kullan.")

state = load_state()

available_minutes = read_positive_integer(
    "Bugün kullanılabilir süre (dakika): "
)

energy = read_energy()

tasks = state["tasks"]
print(f"Kayıttan yüklenen görev: {len(tasks)}")

while True:
    command = input(
        "Görev eklemek için Enter, arşiv yönetimi için a, bitirmek için q: "
    ).strip().lower()
    if command == "q":
        break

    if command == "a":
        manage_archive(state)
        continue

    if command != "":
        print("Lütfen Enter'a bas, a veya q yaz.")
        continue

    task = read_task()

    if task is not None:
        tasks.append(task)
        print("Görev listeye eklendi.")
open_tasks = [
    task for task in tasks
    if not task.get("archived", False)
]
print(f"Bugünkü süre bütçesi: {available_minutes} dakika")
print(f"Mevcut enerji: {energy}/5")
print(f"Toplam görev: {len(tasks)}")

today = date.today()
print(f"Hesaplama tarihi: {today}")

for task in open_tasks:
    priority = calculate_priority(task, energy, today)
    print(f"{task['title']} | Öncelik puanı: {priority['score']}")

review_blocked_tasks(open_tasks)
active_tasks, work_minutes_by_id = select_active_tasks(
    open_tasks, available_minutes, energy, today
)

if not active_tasks:
    print("Aktif görev seçilemedi: uygun görev veya kullanılabilir süre yok.")
else:
    print(f"Daily Win: {active_tasks[0]['title']}")

    for task in active_tasks:
        priority = calculate_priority(task, energy, today)
        work_minutes = work_minutes_by_id[task["id"]]

        print(
            f"Aktif: {task['title']} | "
            f"Toplam tahmin: {task['estimated_minutes']} dakika | "
            f"Bu oturum: {work_minutes} dakika"
        )

        for reason in priority["reasons"]:
            print(f"  - {reason}")

        print(
            f"  Seçim: Öncelik sırasına göre {work_minutes} dakika ayrıldı. "
            "Toplam zaman bütçesi ve iki aktif görev sınırı korundu."
        )

active_task_ids = []

for task in active_tasks:
    active_task_ids.append(task["id"])

daily_win_id = None

if active_task_ids:
    daily_win_id = active_task_ids[0]

state = {
    "tasks": tasks,
    "day": {
        "date": today.isoformat(),
        "available_minutes": available_minutes,
        "energy": energy,
    },
    "selection": {
        "active_task_ids": active_task_ids,
        "daily_win_id": daily_win_id,
        "work_minutes_by_id": work_minutes_by_id,
    },
}

save_state(state)
print(f"Durum kaydedildi: {STATE_PATH}")

if active_tasks:
    daily_win = active_tasks[0]
    if not daily_win.get("desired_outcome"):
        print(f"Görev: {daily_win['title']}")
        daily_win["desired_outcome"] = read_nonempty_text(
            "Bu görev bittiğinde elinde somut olarak ne olmalı? "
        )
        save_state(state)

    print(f"Hedef sonuç: {daily_win['desired_outcome']}")
    if not daily_win.get("context"):
        daily_win["context"] = read_nonempty_text(
            "Şu an hangi aşamadasın, sıradaki ihtiyaç ve sınırlar neler? "
        )
        save_state(state)
    print(f"Mevcut bağlam: {daily_win['context']}")

    while True:
        choice = input(
            "Hedefi/bağlamı düzenlemek için d, devam etmek için Enter: "
        ).strip().lower()

        if choice == "":
            break

        if choice != "d":
            print("Lütfen d yaz veya Enter'a bas.")
            continue

        new_outcome = input(
            "Yeni hedef sonuç (aynı kalacaksa Enter): "
        ).strip()

        new_context = input(
            "Yeni bağlam (aynı kalacaksa Enter): "
        ).strip()

        if new_outcome:
            daily_win["desired_outcome"] = new_outcome

        if new_context:
            daily_win["context"] = new_context

        save_state(state)
        print("Görev bilgileri kaydedildi.")
        break

    
    work_minutes = work_minutes_by_id[daily_win["id"]]

    try:
        action = daily_win.get("next_action")

        if action:
            print(f"Kayıtlı eylem: {action}")

            while True:
                answer = input(
                    f"Bu eylem hedef sonuca ve {work_minutes} dakikalık "
                    "bütçeye uygun mu? (e/h): "
                ).strip().lower()

                if answer in ["e", "h"]:
                    break

                print("Lütfen e veya h gir.")

            if answer == "h":
                action = None

        if action:
            print(f"Kayıtlı eylemle devam: {action}")
        else:
            completed_actions = []

            for checkpoint in daily_win.get("checkpoints", []):
                if checkpoint["feedback"] == "done":
                    completed_actions.append(checkpoint["action"])

            action = generate_next_action(
                daily_win["title"],
                work_minutes,
                energy,
                completed_actions,
                desired_outcome=daily_win["desired_outcome"],
                context=daily_win["context"],
            )

            daily_win["next_action"] = action
            save_state(state)

            print(f"Daily Win için önerilen eylem: {action}")
            print("Eylem kaydedildi.")

    except (APIError, ValueError) as error:
        print(f"Sonraki eylem hazırlanamadı: {error}")
        print("Görevler ve seçim dosyada korunuyor.")
        action = None

    action = review_next_action(action)

    if action is not None:
        daily_win["next_action"] = action
        save_state(state)
        print(f"Çalışacağın eylem: {action}")

        feedback = read_feedback()
        output_note = None

        if feedback == "done":
            output_note = read_nonempty_text(
                "Somut olarak ne ürettin veya neyi değiştirdin? "
            )
        blocker = None

        if feedback == "blocked":
            while True:
                blocker = input("İlerlemeyi ne engelliyor? ").strip()

                if blocker:
                    break

                print("Lütfen engeli kısaca belirt.")

        checkpoints = daily_win.setdefault("checkpoints", [])

        checkpoint = {
            "version": len(checkpoints) + 1,
            "action": action,
            "feedback": feedback,
            "blocker": blocker,
            "recorded_at": datetime.now().astimezone().isoformat(),
            "output_note": output_note,
        }

        checkpoints.append(checkpoint)
        daily_win["blocked"] = feedback == "blocked"

        if feedback == "done":
            daily_win["next_action"] = None

        save_state(state)

        print(
            f"Checkpoint v{checkpoint['version']} kaydedildi: {feedback}"
        )