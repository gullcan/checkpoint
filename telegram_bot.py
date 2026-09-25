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
from reminders import configure_reminder, queue_daily_reminder

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


def build_today_message(state):
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

def build_plan_message(command, session, state):
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

def select_work_message(command, session, state):
    parts = command.split()

    if len(parts) not in (2, 3) or (len(parts) == 3 and parts[2].lower() != "elle"):
        return "Kullanım: /sec 1 veya AI kullanmadan seçmek için /sec 1 elle"

    try:
        index = int(parts[1]) - 1
    except ValueError:
        return "Görev numarası tam sayı olmalı."

    manual = len(parts) == 3
    plan = session.get("plan")

    if not plan or plan["date"] != date.today().isoformat():
        return "Önce güncel bir plan oluştur: /plan 25 3"

    if not 0 <= index < len(plan["task_ids"]):
        return "Son planda bulunan bir görev numarası seç."

    task_id = plan["task_ids"][index]
    task = next((task for task in state["tasks"] if task["id"] == task_id), None)

    if (
        task is None
        or task.get("archived", False)
        or task.get("completed", False)
        or is_task_blocked(task)
    ):
        session.pop("work", None)
        return "Bu görev artık uygun değil. Yeni bir /plan oluştur."

    minutes = plan["work_minutes_by_id"][task_id]
    previous_work = session.get("work")
    action = task.get("next_action")

    if previous_work and previous_work["task_id"] == task_id:
        action = previous_work["action"]

    if manual:
        action = None

    # Görev seçimi ile eylemin hazırlanması ayrı durumlardır.
    work = {
        "task_id": task_id,
        "action": action,
        "planned_minutes": minutes,
        "energy": plan["energy"],
    }
    session["work"] = work
    source = "Kayıtlı/bekleyen eylem"

    if not action:
        if manual:
            source = "Elle eylem girişi seçildi; AI isteği gönderilmedi."
        elif not task.get("desired_outcome") or not task.get("context"):
            source = "Görevin hedef sonucu veya bağlamı eksik; AI önerisi istenmedi."
        else:
            completed_actions = [
                checkpoint["action"]
                for checkpoint in task.get("checkpoints", [])
                if checkpoint["feedback"] == "done"
            ]
            try:
                action = generate_next_action(
                    task["title"], minutes, plan["energy"], completed_actions,
                    desired_outcome=task["desired_outcome"],
                    context=task["context"],
                )
                if not isinstance(action, str) or not action.strip():
                    raise ValueError("Boş veya geçersiz eylem")
                action = action.strip()
            except (APIError, ValueError):
                action = None
                source = "AI önerisi alınamadı; görev seçimin korundu."
            else:
                source = "Yeni AI önerisi"

    work["action"] = action
    header = (
        f"Seçilen görev: {task['title'][:150]}\n"
        f"Ayrılan süre: {minutes} dakika\n\n"
    )

    if action is None:
        return (
            header + source + "\n"
            "Kendi eylemini yaz: /eylem Yapacağın küçük iş ve sonucu\n"
            "Eylem belirlenene kadar /kaydet çalışmaz."
        )

    return (
        header + f"{source}:\n{action[:2500]}\n\n"
        "Eylemin hedefinle ve süreyle uyumunu kontrol et.\n"
        "Gerekirse /eylem ile değiştir. Henüz checkpoint oluşturulmadı."
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
        "Eylem bekleyen çalışma olarak saklandı; sonucu /kaydet ile kaydedebilirsin."
    )


def record_feedback_message(command, session, update_id, state):

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

    if not isinstance(work.get("action"), str) or not work["action"].strip():
        return "Önce /eylem ile yapacağın işi yaz; henüz çalışma kaydı oluşturulmadı."

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
        return "Bugünün zaman bütçesi yok. /gun komutuyla belirle."

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

def set_day_message(command, session, update_id, message_date, state):
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

    session.pop("plan", None)
    session.pop("work", None)

    return (
        f"Günlük durum kaydedildi.\n"
        f"Şu andan itibaren kalan süre: {minutes} dakika\n"
        f"Enerji: {energy}/5\n\n"
        "Önceki plan önizlemesi temizlendi. Yeni bir /plan oluştur."
    )

def add_task_message(command, session, update_id, state):
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


    for existing_task in state["tasks"]:
        if existing_task.get("telegram_created_update_id") == update_id:
            return "Bu mesajdaki görev daha önce kaydedildi."

    state["tasks"].append(task)

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

def list_tasks_message(state):
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


def change_task_status_message(command, session, update_id, state):
    parts = command.split()

    if len(parts) != 2:
        return "Önce /gorevler yaz. Örnek kullanım: /arsiv 1234abcd"

    operation, short_id = parts

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

    session.pop("plan", None)
    session.pop("work", None)

    return (
        f"{task['title'][:150]} — {result}.\n"
        "Geçmiş kayıtlar korundu. Yeni seçim için /plan oluştur."
    )


def help_message():
    return (
        "Checkpoint komutları\n\n"
        "/gun 40 3 — Kalan süreyi 40, enerjiyi 3 olarak ayarla\n"
        "/bugun — Günlük durum\n"
        "/plan 25 3 — Plan önizlemesi\n"
        "/sec 1 — Plandaki görevi seç\n"
        "/sec 1 elle — AI isteği yapmadan seç, ardından /eylem yaz\n"
        "/devam — Saklanan planı ve bekleyen eylemi göster\n"
        "/eylem metin — Seçili görev için kendi eylemini yaz\n"
        "/kaydet continue 5 — 5 dakika çalıştım, devam edeceğim\n"
        "/kaydet done 10 | çıktı — Adım tamamlandı\n"
        "/kaydet blocked 2 | engel — Engeli kaydet\n\n"
        "/ekle görev | önem | dakika | yük | deadline | hedef | bağlam\n"
        "Yük: low/medium/high; deadline yoksa -\n\n"
        "/gorevler — Görev kimlikleri ve durumları\n"
        "/arsiv kimlik — Arşivle\n"
        "/ac kimlik — Yeniden aç, engeli kaldır\n"
        "/tamamla kimlik — Ana görevi tamamlandı olarak işaretle\n\n"
        "Süre kendi bildirimindir. /gun süre eklemez, kalan süreyi değiştirir.\n"
        "Yeni plan, görev veya günlük ayar bekleyen seçimi temizler.\n"
        "/duzenle kimlik | hedef | bağlam — Görev bilgilerini güncelle; aynı alan için -\n"
        "/ozet — Bugünün çıktıları ve kaydedilmiş çalışma süresi\n"
        "/hatirlat 09:00 — Günlük başlangıç hatırlatıcısı\n"
        "/hatirlat kapat — Hatırlatıcıyı kapat\n"
    )


def resume_message(session, state):
    plan = session.get('plan')
    if not plan:
        return 'Bekleyen plan yok. /plan dakika enerji ile başlayabilirsin.'
    if plan['date'] != date.today().isoformat():
        return 'Saklanan plan önceki güne ait. /gun ve /plan ile bugünü başlat.'
    tasks = {task['id']: task for task in state['tasks']}
    lines = ['Saklanan plan:']
    for number, task_id in enumerate(plan['task_ids'], start=1):
        task = tasks.get(task_id)
        title = task['title'][:150] if task else 'Artık bulunmayan görev'
        lines.append(f"{number}. {title} | {plan['work_minutes_by_id'][task_id]} dakika")
    work = session.get('work')
    if not work:
        lines.append('Henüz çalışma seçilmedi. /sec 1 veya /sec 2 yaz.')
    else:
        task = tasks.get(work['task_id'])
        if not task or task.get('archived') or task.get('completed') or is_task_blocked(task):
            lines.append('Bekleyen görev artık uygun değil; yeni bir /plan oluştur.')
        else:
            lines.append(f"\nSeçili görev: {task['title'][:150]}")
            lines.append(f"Planlanan süre: {work['planned_minutes']} dakika")
            if work['action'] is None:
                lines.append('Eylem henüz belirlenmedi. /eylem ile kendi eylemini yaz.')
            else:
                lines.append(f"Eylem: {work['action'][:2200]}")
                lines.append('Sonucu /kaydet ile bildirebilirsin.')
    return '\n'.join(lines)


def get_runtime(state, owner_id):
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
            and runtime['owner_id'] == owner_id
            and type(runtime.get('offset')) is int
            and runtime['offset'] >= 0
            and isinstance(runtime.get('session'), dict)
        )
    if not valid:
        raise SystemExit('Telegram kaydı geçersiz veya başka hesaba ait. Dosya değiştirilmedi.')
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
    except (AssertionError, KeyError, TypeError, ValueError):
        raise SystemExit('Saklanan Telegram oturumu geçersiz. Dosya değiştirilmedi.') from None
    return runtime

def edit_task_message(command, session, state):
    parts = command.split(maxsplit=1)

    if len(parts) != 2:
        return "Kullanım: /duzenle kimlik | hedef sonuç | bağlam"

    fields = [field.strip() for field in parts[1].split("|")]

    if len(fields) != 3 or not all(fields):
        return (
            "Üç alan gerekli: kimlik | hedef sonuç | bağlam\n"
            "Aynı kalacak alan için - yaz."
        )

    short_id, outcome, context = fields

    matches = [
        task for task in state["tasks"]
        if task["id"].startswith(short_id)
    ]

    if len(short_id) < 8 or len(matches) != 1:
        return "Görev kimliği bulunamadı veya belirsiz. /gorevler yaz."

    task = matches[0]
    changed = False

    if outcome != "-" and outcome != task.get("desired_outcome"):
        task["desired_outcome"] = outcome
        changed = True

    if context != "-" and context != task.get("context"):
        task["context"] = context
        changed = True

    if not changed:
        return "Bilgiler aynı; görev değiştirilmedi."

    task["next_action"] = None
    session.pop("plan", None)
    session.pop("work", None)

    return (
        f"Görev güncellendi: {task['title'][:150]}\n"
        "Eski eylem ve bekleyen plan temizlendi; checkpoint geçmişi korundu.\n"
        "Yeni bir /plan oluşturabilirsin."
    )

def build_summary_message(state):
    today = date.today()
    records = []

    for task in state["tasks"]:
        for checkpoint in task.get("checkpoints", []):
            timestamp = datetime.fromisoformat(
                checkpoint["recorded_at"]
            ).astimezone()

            if timestamp.date() == today:
                records.append((timestamp, task, checkpoint))

    records.sort(key=lambda item: item[0])

    counts = {"done": 0, "continue": 0, "blocked": 0}
    spent_minutes = 0
    missing_duration = 0

    for _, _, checkpoint in records:
        counts[checkpoint["feedback"]] += 1

        if "spent_minutes" in checkpoint:
            spent_minutes += checkpoint["spent_minutes"]
        else:
            missing_duration += 1

    lines = [
        f"Bugünün özeti — {today.isoformat()}",
        f"Tamamlandı bildirimi: {counts['done']}",
        f"Devam bildirimi: {counts['continue']}",
        f"Engel bildirimi: {counts['blocked']}",
        f"Kaydedilmiş çalışma süresi: {spent_minutes} dakika",
    ]

    if missing_duration:
        lines.append(
            f"Süre bilgisi olmayan eski kayıt: {missing_duration}"
        )

    completed = [
        (task, checkpoint)
        for _, task, checkpoint in records
        if checkpoint["feedback"] == "done"
    ]

    if not completed:
        lines.append("\nBugün henüz tamamlandı bildirimi yok.")
    else:
        lines.append("\nSon tamamlanan adımlar:")

        for task, checkpoint in completed[-5:]:
            output = checkpoint.get("output_note")
            if not output:
                output = "Çıktı açıklaması kaydedilmemiş."

            lines.append(
                f"\n• {task['title'][:100]}\n"
                f"Eylem: {checkpoint['action'][:180]}\n"
                f"Çıktı: {output[:250]}"
            )

        if len(completed) > 5:
            lines.append("\nSon 5 tamamlanma kaydı gösteriliyor.")

    lines.append(
        "\nBu özet kendi bildirimlerine dayanır; test kayıtları da dahildir."
    )

    return "\n".join(lines)

def dispatch_message(text, session, state, update_id, message_date):
    command = text.split(maxsplit=1)[0] if text else ''
    if command in ['/start', '/yardim']:
        return help_message()
    if command == '/bugun':
        return build_today_message(state)
    if command == "/ozet":
        return build_summary_message(state)
    if command == '/gorevler':
        return list_tasks_message(state)
    if command == '/devam':
        return resume_message(session, state)
    sent_day = datetime.fromtimestamp(message_date).astimezone().date()
    if sent_day != date.today():
        return 'Bu komut önceki güne ait; işlenmedi. Güncel komutunu yeniden gönder.'
    if command == '/gun':
        return set_day_message(text, session, update_id, message_date, state)
    if command == "/hatirlat":
        return configure_reminder(text, state)
    if command == '/plan':
        return build_plan_message(text, session, state)
    if command == '/sec':
        return select_work_message(text, session, state)
    if command == '/eylem':
        return set_action_message(text, session)
    if command == '/kaydet':
        return record_feedback_message(text, session, update_id, state)
    if command == '/ekle':
        return add_task_message(text, session, update_id, state)
    if command == "/duzenle":
        return edit_task_message(text, session, state)
    if command in ['/arsiv', '/tamamla', '/ac']:
        return change_task_status_message(text, session, update_id, state)
    return 'Komutları görmek için /yardim yaz.'


def process_update(update, owner_id):
    # Tek yükleme + tek atomik kayıt: görev, oturum ve mesaj konumu birlikte saklanır.
    state = load_state()
    runtime = get_runtime(state, owner_id)
    update_id = update['update_id']
    if runtime.get('pending_reply') is not None:
        raise RuntimeError('Önce bekleyen yanıt gönderilmeli.')
    if update_id < runtime['offset']:
        return False
    message = update.get('message', {})
    sender_id = message.get('from', {}).get('id')
    chat = message.get('chat', {})
    if sender_id == owner_id and chat.get('type') == 'private' and chat.get('id') == owner_id:
        reply = dispatch_message(
            message.get('text', '').strip(), runtime['session'], state,
            update_id, message['date'],
        )
        runtime['pending_reply'] = {'chat_id': owner_id, 'text': reply}
    runtime['offset'] = update_id + 1
    save_state(state)
    return True


def flush_pending_reply(token, owner_id):
    state = load_state()
    runtime = get_runtime(state, owner_id)
    pending = runtime.get('pending_reply')
    if pending is None:
        return
    telegram_request(token, 'sendMessage', pending)
    runtime['pending_reply'] = None
    save_state(state)


def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    owner_text = os.environ.get('TELEGRAM_ALLOWED_USER_ID', '').strip()
    if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
        print("Telegram token'ı eksik veya biçimi geçersiz.")
        return
    if not re.fullmatch(r'[0-9]+', owner_text) or int(owner_text) <= 0:
        print('TELEGRAM_ALLOWED_USER_ID geçerli bir kullanıcı kimliği olmalı.')
        return
    owner_id = int(owner_text)
    get_runtime(load_state(), owner_id)
    print('Bot çalışıyor. /yardim komutları, /devam bekleyen çalışmayı gösterir.')
    print('Aynı anda yalnızca bir bot çalıştır. CLI ile eşzamanlı kullanma. Ctrl+C ile durdur.')
    while True:
        try:
            flush_pending_reply(token, owner_id)

            state = load_state()
            get_runtime(state, owner_id)

            if queue_daily_reminder(state, owner_id):
                save_state(state)
                flush_pending_reply(token, owner_id)

            runtime = get_runtime(load_state(), owner_id)
            updates = telegram_request(token, 'getUpdates', {
                'offset': runtime['offset'], 'timeout': 25,
                'allowed_updates': ['message'],
            })
            for update in updates:
                process_update(update, owner_id)
                flush_pending_reply(token, owner_id)
        except RuntimeError as error:
            print(error)
            print('5 saniye sonra yeniden denenecek; bekleyen yanıt korunuyor.')
            time.sleep(5)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nBot durduruldu.')

