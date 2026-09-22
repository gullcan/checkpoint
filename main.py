from datetime import date, datetime

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

def select_active_tasks(tasks, available_minutes, energy, today):
    ranked_tasks = sorted(
        tasks,
        key=lambda task: calculate_priority(task, energy, today)["score"],
        reverse=True,
    )

    active_tasks = []
    remaining_minutes = available_minutes

    for task in ranked_tasks:
        if len(active_tasks) == 2:
            break

        if task["estimated_minutes"] > remaining_minutes:
            continue

        active_tasks.append(task)
        remaining_minutes -= task["estimated_minutes"]

    return active_tasks

available_minutes = read_positive_integer(
    "Bugün kullanılabilir süre (dakika): "
)

energy = read_energy()

tasks = []

while True:
    command = input("Görev eklemek için Enter, bitirmek için q: ").strip().lower()

    if command == "q":
        break

    if command != "":
        print("Lütfen Enter'a bas veya q yaz.")
        continue

    task = read_task()

    if task is not None:
        tasks.append(task)
        print("Görev listeye eklendi.")

print(f"Bugünkü süre bütçesi: {available_minutes} dakika")
print(f"Mevcut enerji: {energy}/5")
print(f"Toplam görev: {len(tasks)}")

today = date.today()
print(f"Hesaplama tarihi: {today}")

for task in tasks:
    priority = calculate_priority(task, energy, today)
    print(f"{task['title']} | Öncelik puanı: {priority['score']}")

active_tasks = select_active_tasks(tasks, available_minutes, energy, today)

if not active_tasks:
    print("Aktif görev seçilemedi: görev yok veya süreye sığan görev yok.")
else:
    print(f"Daily Win: {active_tasks[0]['title']}")

    remaining_minutes = available_minutes

    for task in active_tasks:
        priority = calculate_priority(task, energy, today)

        print(f"Aktif: {task['title']} | {task['estimated_minutes']} dakika")

        for reason in priority["reasons"]:
            print(f"  - {reason}")

        print(
            f"  Seçim: Puan sırasıyla incelenirken "
            f"kalan {remaining_minutes} dakikaya sığdı "
            f"ve iki aktif görev sınırı aşılmadı."
        )

        remaining_minutes -= task["estimated_minutes"]