"""Terminal interface for the same planning and progress rules as Telegram."""

from datetime import date, datetime
from uuid import uuid4

from openai import APIError
from llm import generate_next_action
from planning import calculate_priority, is_task_blocked, select_active_tasks
from storage import STATE_PATH, load_state, save_state
from workflow import record_checkpoint, update_task_context, daily_summary

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
            if task.get("archived", False):
                status = "arşivde"
            elif task.get("completed", False):
                status = "tamamlandı"
            else:
                status = "açık"
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

def review_next_action(task, work_minutes, energy):
    action = task.get("next_action")

    while True:
        print(f"\nGörev: {task['title']}")
        print(f"Bu oturumun süre sınırı: {work_minutes} dakika")

        if action:
            print(f"Eylem: {action}")
        else:
            print("Henüz bir eylem seçilmedi.")

        choice = input(
            "Kabul: k | Yeni AI önerisi: g | Kendim yaz: y | Geç: q: "
        ).strip().lower()

        if choice == "q":
            return None

        if choice == "k":
            if action:
                return action

            print("Önce bir öneri al veya kendi eylemini yaz.")
            continue

        if choice == "y":
            action = read_nonempty_text(
                "Bu sürede yapacağın eylem ve gözlenebilir sonucu: "
            )
            continue

        if choice == "g":
            completed_actions = [
                checkpoint["action"]
                for checkpoint in task.get("checkpoints", [])
                if checkpoint["feedback"] == "done"
            ]

            try:
                new_action = generate_next_action(
                    task["title"],
                    work_minutes,
                    energy,
                    completed_actions,
                    desired_outcome=task["desired_outcome"],
                    context=task["context"],
                )
            except (APIError, ValueError) as error:
                print(f"Öneri alınamadı: {error}")
                print("Kendi eylemini yazabilir veya oturumu geçebilirsin.")
            else:
                action = new_action

            continue

        print("Lütfen k, g, y veya q gir.")

def read_nonnegative_integer(prompt):
    while True:
        text = input(prompt).strip()

        try:
            value = int(text)
        except ValueError:
            print("Lütfen bir tam sayı gir.")
            continue

        if value < 0:
            print("Değer negatif olamaz.")
            continue

        return value


def read_day(state, today):
    saved_day = state.get("day")

    if (
        isinstance(saved_day, dict)
        and saved_day.get("date") == today.isoformat()
        and "remaining_minutes" in saved_day
    ):
        remaining = saved_day["remaining_minutes"]

        if type(remaining) is not int or remaining < 0:
            raise SystemExit("Kayıtlı kalan süre geçersiz. Dosya değiştirilmedi.")

        day = saved_day.copy()
        print(f"Bugünden kalan süre: {remaining} dakika")

        while True:
            choice = input(
                "Bu süreyle devam için Enter, düzeltmek için d: "
            ).strip().lower()

            if choice == "":
                return day

            if choice == "d":
                day["remaining_minutes"] = read_nonnegative_integer(
                    "Şu andan itibaren kullanılabilir süre (dakika): "
                )
                return day

            print("Lütfen Enter'a bas veya d yaz.")

    minutes = read_nonnegative_integer(
        "Bugün kullanılabilir süre (dakika): "
    )

    return {
        "date": today.isoformat(),
        "available_minutes": minutes,
        "remaining_minutes": minutes,
    }

def show_daily_summary(tasks, today):
    summary = daily_summary(tasks, today)
    print("\n--- Bugünkü ilerlemen ---")
    print(f"Tamamladığını bildirdiğin adım: {summary['counts']['done']}")
    print(f"Devam ettiğin çalışma: {summary['counts']['continue']}")
    print(f"Engelle karşılaştığın çalışma: {summary['counts']['blocked']}")
    print(f"Kaydettiğin çalışma süresi: {summary['spent_minutes']} dakika")
    if summary["missing_duration"]:
        print(f"Süresi belirtilmemiş eski kayıt: {summary['missing_duration']}")
    for _, task, checkpoint in summary["completed"]:
        print(f"\n{task['title']}: {checkpoint['action']}")
        print(f"Çıktın: {checkpoint.get('output_note') or 'Açıklama kaydedilmemiş.'}")
    print("Bu özet senin bildirdiklerine dayanıyor; çıktıları kendiliğinden doğrulamaz.")


def choose_work_task(active_tasks, work_minutes_by_id):
    if not active_tasks:
        return None

    print("\nBu oturumda hangi görev üzerinde çalışacaksın?")

    for number, task in enumerate(active_tasks, start=1):
        label = " — Daily Win" if number == 1 else ""
        minutes = work_minutes_by_id[task["id"]]

        print(f"{number}. {task['title']} | {minutes} dakika{label}")

    while True:
        answer = input(
            "Görev numarası, Daily Win için Enter, oturumu geçmek için q: "
        ).strip().lower()

        if answer == "q":
            return None

        if answer == "":
            return active_tasks[0]

        try:
            index = int(answer) - 1
        except ValueError:
            print("Lütfen görev numarası, Enter veya q kullan.")
            continue

        if not 0 <= index < len(active_tasks):
            print("Listede bulunan bir görev numarası gir.")
            continue

        return active_tasks[index]

def manage_tasks(state, today):
    while True:
        command = input(
            "Görev ekle: Enter | Arşiv: a | Günlük özet: o | Planla: q: "
        ).strip().lower()
        if command == "q":
            break

        if command == "a":
            manage_archive(state)
            continue
        if command == "o":
            show_daily_summary(state["tasks"], today)
            continue

        if command != "":
            print("Lütfen Enter'a bas veya a, o, q seçeneklerinden birini yaz.")
            continue

        task = read_task()

        if task is not None:
            state["tasks"].append(task)
            save_state(state)
            print("Görev listeye eklendi.")


def review_task_context(work_task, state):
    if not work_task.get("desired_outcome"):
        print(f"Görev: {work_task['title']}")
        outcome = read_nonempty_text(
            "Bu görev bittiğinde elinde somut olarak ne olmalı? "
        )
        update_task_context(work_task, outcome, None)
        save_state(state)

    print(f"Hedef sonuç: {work_task['desired_outcome']}")
    if not work_task.get("context"):
        context = read_nonempty_text(
            "Şu an hangi aşamadasın, sıradaki ihtiyaç ve sınırlar neler? "
        )
        update_task_context(work_task, None, context)
        save_state(state)
    print(f"Mevcut bağlam: {work_task['context']}")

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

        changed = update_task_context(work_task, new_outcome or None, new_context or None)
        if changed:
            print("Hedef veya bağlam değişti. Buna uygun bir sonraki adımı yeniden seçelim.")

        save_state(state)
        print("Görev bilgileri kaydedildi.")
        break


def collect_feedback(work_task, day, action, work_minutes, state):
    feedback = read_feedback()
    spent_minutes = read_nonnegative_integer(
        "Bu adım üzerinde bu kez kaç dakika çalıştın? "
    )
    output_note = None

    if feedback == "done":
        output_note = read_nonempty_text(
            "Somut olarak ne ürettin veya neyi değiştirdin? "
        )

        while True:
            answer = input(
                "Ana görevin hedef sonucu da tamamen gerçekleşti mi? (e/h): "
            ).strip().lower()

            if answer in ["e", "h"]:
                work_task["completed"] = answer == "e"
                break

            print("Lütfen e veya h gir.")
    blocker = None

    if feedback == "blocked":
        while True:
            blocker = input("İlerlemeyi ne engelliyor? ").strip()

            if blocker:
                break

            print("Lütfen engeli kısaca belirt.")

    checkpoint = record_checkpoint(
        work_task, day, action, feedback, work_minutes, spent_minutes,
        note=output_note if feedback == "done" else blocker,
    )

    save_state(state)

    print(
        f"Checkpoint v{checkpoint['version']} kaydedildi: {feedback}"
    )
    print(
        f"Kalan süre: {day['remaining_minutes']} dakika"
        )


def main():

    state = load_state()

    today = date.today()
    day = read_day(state, today)
    available_minutes = day["remaining_minutes"]

    energy = read_energy()
    day["energy"] = energy
    state["day"] = day

    tasks = state["tasks"]
    print(f"Kayıttan yüklenen görev: {len(tasks)}")

    manage_tasks(state, today)
    open_tasks = [
        task for task in tasks
        if not task.get("archived", False)
        and not task.get("completed", False)
    ]
    print(f"Şu an kullanılabilir süre: {available_minutes} dakika")
    print(f"Mevcut enerji: {energy}/5")
    print(f"Toplam görev: {len(tasks)}")

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
                "Ayırabileceğin süre aşılmadı; en fazla iki görev önerildi."
            )

    active_task_ids = []

    for task in active_tasks:
        active_task_ids.append(task["id"])

    daily_win_id = None

    if active_task_ids:
        daily_win_id = active_task_ids[0]

    state.update({
        "tasks": tasks,
        "day": day,
        "selection": {
            "active_task_ids": active_task_ids,
            "daily_win_id": daily_win_id,
            "work_minutes_by_id": work_minutes_by_id,
        },
    })

    save_state(state)
    print(f"Durum kaydedildi: {STATE_PATH}")

    work_task = choose_work_task(active_tasks, work_minutes_by_id)

    if work_task is not None:
        review_task_context(work_task, state)

        work_minutes = work_minutes_by_id[work_task["id"]]

        action = review_next_action(work_task, work_minutes, energy)

        if action is not None:
            work_task["next_action"] = action
            save_state(state)
            print(f"Çalışacağın eylem: {action}")

            collect_feedback(work_task, day, action, work_minutes, state)

    show_daily_summary(tasks, today)


if __name__ == "__main__":
    main()
