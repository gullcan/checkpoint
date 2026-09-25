import json
import os
import re
import time
from datetime import date, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from openai import APIError
from llm import generate_next_action
from uuid import uuid4

from main import (
    load_state,
    save_state,
    select_active_tasks,
    calculate_priority,
    is_task_blocked,
    is_valid_task,
)


def telegram_request(token, method, payload):
    url = f"https://api.telegram.org/bot{token}/{method}"

    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=35) as response:
            data = json.load(response)
    except HTTPError as error:
        raise RuntimeError(
            f"Telegram HTTP hatası: {error.code}"
        ) from None
    except (URLError, TimeoutError):
        raise RuntimeError("Telegram bağlantısı kurulamadı.") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError("Telegram yanıtı okunamadı.") from None

    if not data.get("ok"):
        raise RuntimeError("Telegram işlemi başarısız oldu.")

    return data["result"]


def build_today_message():
    state = load_state()
    day = state.get("day")
    today = date.today().isoformat()

    lines = [f"Checkpoint — {today}"]

    if isinstance(day, dict) and day.get("date") == today:
        remaining = day.get("remaining_minutes")

        if type(remaining) is int and remaining >= 0:
            lines.append(f"Kalan süre: {remaining} dakika")
            lines.append(f"Kayıtlı enerji: {day.get('energy', 'belirtilmedi')}/5")
        else:
            lines.append("Bugünün kalan süresi henüz belirlenmedi.")
    else:
        lines.append("Bugünün zaman bütçesi henüz belirlenmedi.")

    open_tasks = [
        task for task in state["tasks"]
        if not task.get("archived", False)
        and not task.get("completed", False)
    ]

    lines.append(f"\nAçık görevler: {len(open_tasks)}")

    for number, task in enumerate(open_tasks[:10], start=1):
        lines.append(f"{number}. {task['title'][:150]}")

    if len(open_tasks) > 10:
        lines.append("İlk 10 görev gösteriliyor.")

    if not open_tasks:
        lines.append("Açık görev yok.")

    return "\n".join(lines)

def build_plan_message(command, session):
    parts = command.split()

    if len(parts) != 3:
        return "Kullanım: /plan dakika enerji\nÖrnek: /plan 25 3"

    try:
        minutes = int(parts[1])
        energy = int(parts[2])
    except ValueError:
        return "Süre ve enerji tam sayı olmalı. Örnek: /plan 25 3"

    if minutes < 0:
        return "Süre negatif olamaz."

    if not 1 <= energy <= 5:
        return "Enerji 1–5 arasında olmalı."

    if minutes == 0:
        return "Kullanılabilir süre 0 dakika; çalışma planı oluşturulmadı."

    state = load_state()
    today = date.today()

    day = state.get("day")

    if not isinstance(day, dict) or day.get("date") != today.isoformat():
        return "Önce bugünün durumunu gir: /gun 40 3"

    remaining = day.get("remaining_minutes")

    if type(remaining) is not int or remaining < 0:
        return "Kalan süre geçersiz. /gun komutuyla güncelle."

    if minutes > remaining:
        return (
            f"Kayıtlı kalan süren {remaining} dakika.\n"
            "Bu sınır içinde plan iste; zamanın arttıysa önce /gun ile düzelt."
        )

    open_tasks = [
        task for task in state["tasks"]
        if not task.get("archived", False)
        and not task.get("completed", False)
    ]

    active_tasks, work_minutes_by_id = select_active_tasks(
        open_tasks,
        minutes,
        energy,
        today,
    )

    if not active_tasks:
        return "Planlanabilecek açık ve engeli olmayan görev bulunamadı."
    
    session["plan"] = {
        "date": today.isoformat(),
        "energy": energy,
        "task_ids": [task["id"] for task in active_tasks],
        "work_minutes_by_id": work_minutes_by_id,
    }
    session.pop("work", None)

    lines = [
        "Plan önizlemesi",
        f"Süre bütçesi: {minutes} dakika | Enerji: {energy}/5",
    ]

    for number, task in enumerate(active_tasks, start=1):
        label = "Daily Win" if number == 1 else "İkinci aktif görev"
        work_minutes = work_minutes_by_id[task["id"]]
        priority = calculate_priority(task, energy, today)

        lines.append(f"\n{label}: {task['title'][:150]}")
        lines.append(
            f"Bu oturum: {work_minutes} dakika | "
            f"Öncelik puanı: {priority['score']}"
        )

        for reason in priority["reasons"]:
            lines.append(f"• {reason}")

    allocated_minutes = sum(work_minutes_by_id.values())
    lines.append(f"\nToplam ayrılan süre: {allocated_minutes} dakika")
    lines.append(
        "Bu bir önizlemedir; kayıtlı zaman bütçen ve görev durumların değişmedi."
    )
    lines.append("Görev seçmek için /sec 1 veya /sec 2 yaz.")

    return "\n".join(lines)

def select_work_message(command, session):
    parts = command.split()

    if len(parts) != 2:
        return "Kullanım: /sec 1 veya /sec 2"

    try:
        index = int(parts[1]) - 1
    except ValueError:
        return "Görev numarası tam sayı olmalı."

    plan = session.get("plan")

    if not plan or plan["date"] != date.today().isoformat():
        return "Önce güncel bir plan oluştur: /plan 25 3"

    if not 0 <= index < len(plan["task_ids"]):
        return "Son planda bulunan bir görev numarası seç."

    state = load_state()
    task_id = plan["task_ids"][index]

    task = next(
        (task for task in state["tasks"] if task["id"] == task_id),
        None,
    )

    if (
        task is None
        or task.get("archived", False)
        or task.get("completed", False)
        or is_task_blocked(task)
    ):
        session.pop("work", None)
        return "Bu görev artık uygun değil. Yeni bir /plan oluştur."

    minutes = plan["work_minutes_by_id"][task_id]
    action = task.get("next_action")

    # Aynı görevi tekrar seçince üretilen öneriyi tekrar kullan.
    previous_work = session.get("work")
    if previous_work and previous_work["task_id"] == task_id:
        action = previous_work["action"]

    source = "Kayıtlı/bekleyen eylem"

    if not action:
        if not task.get("desired_outcome") or not task.get("context"):
            return (
                "Bu görevin hedef sonucu veya bağlamı eksik. "
                "Şimdilik CLI üzerinden tamamlayıp yeniden /plan oluştur."
            )

        completed_actions = [
            checkpoint["action"]
            for checkpoint in task.get("checkpoints", [])
            if checkpoint["feedback"] == "done"
        ]

        try:
            action = generate_next_action(
                task["title"],
                minutes,
                plan["energy"],
                completed_actions,
                desired_outcome=task["desired_outcome"],
                context=task["context"],
            )
        except (APIError, ValueError):
            session.pop("work", None)
            return "AI önerisi alınamadı. Biraz sonra tekrar deneyebilirsin."

        source = "Yeni AI önerisi"

    session["work"] = {
        "task_id": task_id,
        "action": action,
        "planned_minutes": minutes,
        "energy": plan["energy"],
    }

    return (
        f"Seçilen görev: {task['title'][:150]}\n"
        f"Ayrılan süre: {minutes} dakika\n\n"
        f"{source}:\n{action[:2500]}\n\n"
        "Eylemin hedefinle ve süreyle uyumunu kontrol et.\n"
        "Henüz checkpoint oluşturulmadı ve süre düşülmedi."
    )

def set_action_message(command, session):
    work = session.get("work")
    plan = session.get("plan")

    if not work or not plan:
        return "Önce /plan ve /sec ile bir görev seç."

    if plan["date"] != date.today().isoformat():
        return "Plan önceki güne ait. Yeni bir /plan oluştur."

    parts = command.split(maxsplit=1)

    if len(parts) != 2 or not parts[1].strip():
        return "Kullanım: /eylem Yapacağın küçük eylem"

    work["action"] = parts[1].strip()

    return (
        f"Çalışacağın eylem:\n{work['action'][:2500]}\n\n"
        f"Planlanan süre: {work['planned_minutes']} dakika\n"
        "Bu eylem henüz botun belleğinde; sonucu /kaydet ile kaydedebilirsin."
    )


def record_feedback_message(command, session, update_id):
    state = load_state()

    # Telegram aynı mesajı yeniden iletirse ikinci kez kayıt oluşturma.
    for task in state["tasks"]:
        for checkpoint in task.get("checkpoints", []):
            if checkpoint.get("telegram_update_id") == update_id:
                return "Bu mesajın checkpoint'i daha önce kaydedildi."

    work = session.get("work")
    plan = session.get("plan")

    if not work or not plan:
        return "Önce /plan ve /sec ile bir görev seç."

    today = date.today().isoformat()

    if plan["date"] != today:
        return "Plan önceki güne ait. Yeni bir /plan oluştur."

    header, separator, note = command.partition("|")
    parts = header.split()

    if len(parts) != 3:
        return (
            "Örnekler:\n"
            "/kaydet continue 5\n"
            "/kaydet done 10 | İki paragrafın özetini yazdım.\n"
            "/kaydet blocked 2 | Defter yanımda değil."
        )

    feedback = parts[1].lower()

    if feedback not in ["done", "blocked", "continue"]:
        return "Durum done, blocked veya continue olmalı."

    try:
        spent_minutes = int(parts[2])
    except ValueError:
        return "Harcanan süre tam sayı olmalı."

    if spent_minutes < 0:
        return "Harcanan süre negatif olamaz."

    note = note.strip()

    if feedback in ["done", "blocked"] and not note:
        return "Çıktıyı veya engeli | işaretinden sonra yaz."

    day = state.get("day")

    if not isinstance(day, dict) or day.get("date") != today:
        return "Bugünün zaman bütçesi yok. Şimdilik CLI üzerinden belirle."

    remaining = day.get("remaining_minutes")

    if type(remaining) is not int or remaining < 0:
        return "Kayıtlı kalan süre geçersiz; kayıt yapılmadı."

    task = next(
        (task for task in state["tasks"] if task["id"] == work["task_id"]),
        None,
    )

    if (
        task is None
        or task.get("archived", False)
        or task.get("completed", False)
        or is_task_blocked(task)
    ):
        return "Seçilen görev artık çalışmaya uygun değil. Yeni plan oluştur."

    checkpoints = task.setdefault("checkpoints", [])

    checkpoint = {
        "version": len(checkpoints) + 1,
        "action": work["action"],
        "feedback": feedback,
        "blocker": note if feedback == "blocked" else None,
        "output_note": note if feedback == "done" else None,
        "progress_note": note if feedback == "continue" else None,
        "recorded_at": datetime.now().astimezone().isoformat(),
        "planned_minutes": work["planned_minutes"],
        "spent_minutes": spent_minutes,
        "telegram_update_id": update_id,
    }

    checkpoints.append(checkpoint)
    task["blocked"] = feedback == "blocked"
    task["next_action"] = None if feedback == "done" else work["action"]
    day["remaining_minutes"] = max(0, remaining - spent_minutes)

    save_state(state)

    # Aynı çalışma için yanlışlıkla tekrar feedback girilmesini önle.
    session.pop("work", None)
    session.pop("plan", None)

    return (
        f"Görev: {task['title'][:150]}\n"
        f"Checkpoint v{checkpoint['version']} kaydedildi: {feedback}\n"
        f"Kaydedilen çalışma: {spent_minutes} dakika\n"
        f"Kalan süre: {day['remaining_minutes']} dakika\n\n"
        "Bir sonraki çalışma için yeni bir /plan oluştur."
    )

def set_day_message(command, session, update_id, message_date):
    parts = command.split()

    if len(parts) != 3:
        return "Kullanım: /gun kalan_dakika enerji\nÖrnek: /gun 40 3"

    try:
        minutes = int(parts[1])
        energy = int(parts[2])
    except ValueError:
        return "Süre ve enerji tam sayı olmalı."

    if minutes < 0 or not 1 <= energy <= 5:
        return "Süre en az 0, enerji 1–5 arasında olmalı."

    today = date.today()

    sent_day = datetime.fromtimestamp(message_date).astimezone().date()
    if sent_day != today:
        return "Bu günlük ayar mesajı önceki güne ait. Yeniden /gun gönder."

    state = load_state()
    saved_day = state.get("day")

    if (
        isinstance(saved_day, dict)
        and saved_day.get("date") == today.isoformat()
    ):
        day = saved_day.copy()

        if update_id <= day.get("telegram_settings_update_id", -1):
            return "Bu günlük ayar mesajı daha önce işlendi."
    else:
        day = {
            "date": today.isoformat(),
            "available_minutes": minutes,
        }

    day["remaining_minutes"] = minutes
    day["energy"] = energy
    day["telegram_settings_update_id"] = update_id

    state["day"] = day
    save_state(state)

    session.pop("plan", None)
    session.pop("work", None)

    return (
        f"Günlük durum kaydedildi.\n"
        f"Şu andan itibaren kalan süre: {minutes} dakika\n"
        f"Enerji: {energy}/5\n\n"
        "Önceki plan önizlemesi temizlendi. Yeni bir /plan oluştur."
    )

def add_task_message(command, session, update_id):
    usage = (
        "Kullanım:\n"
        "/ekle görev | önem | dakika | yük | deadline | hedef | bağlam\n\n"
        "Önem: 1–5\n"
        "Dakika: pozitif tam sayı\n"
        "Yük: low, medium veya high\n"
        "Deadline: YYYY-MM-DD veya -\n"
        "Alanların içinde | kullanma."
    )

    command_parts = command.split(maxsplit=1)

    if len(command_parts) != 2:
        return usage

    fields = [field.strip() for field in command_parts[1].split("|")]

    if len(fields) != 7 or not all(fields):
        return usage

    title, importance_text, minutes_text, load, deadline, outcome, context = fields

    try:
        importance = int(importance_text)
        minutes = int(minutes_text)
    except ValueError:
        return "Önem ve dakika tam sayı olmalı."

    task = {
        "id": str(uuid4()),
        "title": title,
        "importance": importance,
        "estimated_minutes": minutes,
        "cognitive_load": load.lower(),
        "deadline": None if deadline == "-" else deadline,
        "desired_outcome": outcome,
        "context": context,
        "telegram_created_update_id": update_id,
    }

    if not is_valid_task(task):
        return (
            "Görev bilgileri geçersiz; hiçbir şey kaydedilmedi.\n\n"
            + usage
        )

    state = load_state()

    for existing_task in state["tasks"]:
        if existing_task.get("telegram_created_update_id") == update_id:
            return "Bu mesajdaki görev daha önce kaydedildi."

    state["tasks"].append(task)
    save_state(state)

    session.pop("plan", None)
    session.pop("work", None)

    return (
        f"Görev kaydedildi: {title[:150]}\n"
        f"Önem: {importance}/5\n"
        f"Toplam tahmin: {minutes} dakika\n"
        f"Hedef: {outcome[:500]}\n\n"
        "Görev listesi değiştiği için önceki plan temizlendi. "
        "Yeni bir /plan oluştur."
    )

def list_tasks_message():
    state = load_state()
    lines = ["Görevler — kimlik | durum | ad"]

    for task in state["tasks"]:
        if task.get("archived", False):
            status = "arşivde"
        elif task.get("completed", False):
            status = "tamamlandı"
        elif is_task_blocked(task):
            status = "engelli"
        else:
            status = "açık"

        line = f"{task['id'][:8]} | {status} | {task['title'][:100]}"

        if len("\n".join(lines)) + len(line) > 3500:
            lines.append("Liste mesaj sınırına ulaştı; kalan görevler gösterilmedi.")
            break

        lines.append(line)

    if not state["tasks"]:
        lines.append("Henüz görev yok.")

    lines.append(
        "\nÖrnek: /arsiv görev_kimliği\n"
        "/tamamla görev_kimliği\n"
        "/ac görev_kimliği"
    )

    return "\n".join(lines)


def change_task_status_message(command, session, update_id):
    parts = command.split()

    if len(parts) != 2:
        return "Önce /gorevler yaz. Örnek kullanım: /arsiv 1234abcd"

    operation, short_id = parts
    state = load_state()

    matches = [
        task for task in state["tasks"]
        if task["id"].startswith(short_id)
    ]

    if len(matches) != 1 or len(short_id) < 8:
        return "Görev kimliği bulunamadı veya belirsiz. /gorevler listesini kontrol et."

    task = matches[0]

    if update_id <= task.get("telegram_status_update_id", -1):
        return "Bu durum değişikliği daha önce işlendi."

    if operation == "/arsiv":
        task["archived"] = True
        result = "arşivlendi"
    elif operation == "/tamamla":
        task["completed"] = True
        task["blocked"] = False
        task["next_action"] = None
        result = "tamamlandı olarak işaretlendi"
    elif operation == "/ac":
        task["archived"] = False
        task["completed"] = False
        task["blocked"] = False
        result = "yeniden açıldı"
    else:
        return "Geçersiz durum komutu."

    task["telegram_status_update_id"] = update_id
    save_state(state)

    session.pop("plan", None)
    session.pop("work", None)

    return (
        f"{task['title'][:150]} — {result}.\n"
        "Geçmiş kayıtlar korundu. Yeni seçim için /plan oluştur."
    )

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    owner_text = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "").strip()

    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        print("Telegram token'ı eksik veya biçimi geçersiz.")
        return

    if not re.fullmatch(r"[0-9]+", owner_text) or int(owner_text) <= 0:
        print("TELEGRAM_ALLOWED_USER_ID geçerli bir kullanıcı kimliği olmalı.")
        return

    owner_id = int(owner_text)
    offset = 0
    session = {}

    print("Bot çalışıyor. Telegram'dan /bugun gönder.")
    print("Durdurmak için Ctrl+C.")

    while True:
        try:
            updates = telegram_request(
                token,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 25,
                    "allowed_updates": ["message"],
                },
            )

            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message", {})
                sender_id = message.get("from", {}).get("id")
                chat = message.get("chat", {})

                if sender_id != owner_id or chat.get("type") != "private":
                    continue

                text = message.get("text", "").strip()

                command = text.split(maxsplit=1)[0] if text else ""

                try:
                    if command == "/start":
                        reply = (
                            "Checkpoint hazır.\n"
                            "/bugun — Kalan süre ve açık görevler\n"
                            "/plan 25 3 — 25 dakika, enerji 3 için plan önizlemesi"
                        )
                    elif command == "/bugun":
                        reply = build_today_message()
                    elif command == "/gun":
                        reply = set_day_message(
                            text,
                            session,
                            update["update_id"],
                            message["date"],
                        )
                    elif command == "/plan":
                        reply = build_plan_message(text, session)
                    elif command == "/sec":
                        reply = select_work_message(text, session)
                    elif command == "/eylem":
                        reply = set_action_message(text, session)
                    elif command == "/kaydet":
                        reply = record_feedback_message(
                            text, session, update["update_id"]
                        )
                    elif command == "/ekle":
                        reply = add_task_message(
                            text, session, update["update_id"]
                        )
                    elif command == "/gorevler":
                        reply = list_tasks_message()
                    elif command in ["/arsiv", "/tamamla", "/ac"]:
                        reply = change_task_status_message(
                            text, session, update["update_id"]
                        )
                    else:
                        reply = (
                            "Durum için /bugun yaz.\n"
                            "Plan için örnek: /plan 25 3"
                        )
                except SystemExit:
                    reply = (
                        "Kayıt dosyası okunamadı, doğrulanamadı veya yazılamadı. "
                        "İşlem tamamlanamadı; dosyayı kontrol et."
                    )

                telegram_request(
                    token,
                    "sendMessage",
                    {"chat_id": chat["id"], "text": reply},
                )

        except RuntimeError as error:
            print(error)
            print("5 saniye sonra bağlantı yeniden denenecek.")
            time.sleep(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot durduruldu.")