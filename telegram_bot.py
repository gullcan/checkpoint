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

from planning import select_active_tasks, is_task_blocked
from storage import load_state, save_state, is_valid_task, get_runtime
from workflow import record_checkpoint, update_task_context, daily_summary


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
        error.close()
        if error.code in (401, 409):
            raise SystemExit(
                f"Telegram bağlantısı durduruldu (HTTP {error.code}). "
                "Bot anahtarını ve aynı anda başka bot çalışmadığını kontrol et."
            ) from None
        if error.code in (400, 403):
            raise ValueError(
                f"Telegram bu isteği kabul etmedi (HTTP {error.code})."
            ) from None
        raise RuntimeError(f"Telegram şu an yanıt veremiyor (HTTP {error.code}).") from None
    except (URLError, TimeoutError):
        raise RuntimeError("Telegram bağlantısı kurulamadı.") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RuntimeError("Telegram yanıtı okunamadı.") from None

    if not isinstance(data, dict):
        raise RuntimeError("Telegram yanıtı beklenen biçimde değil.")

    if not data.get("ok"):
        raise RuntimeError("Telegram işlemi başarısız oldu.")

    if "result" not in data:
        raise RuntimeError("Telegram yanıtında sonuç bulunamadı.")
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
        lines.append("Bugün ne kadar zaman ayırabileceğini henüz bilmiyorum. /gun 40 3 gibi yazabilirsin.")

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
    if len(parts) == 1:
        day = state.get("day")
        if not isinstance(day, dict) or day.get("date") != date.today().isoformat():
            return "Önce bugün ne kadar zamanın kaldığını ve enerjini söyle. Örneğin /gun 40 3: 40 dakika, enerji 3/5."
        parts = ["/plan", str(day.get("remaining_minutes")), str(day.get("energy"))]
    if len(parts) != 3:
        return "Şu an kaç dakika ayırabilirsin, enerjin nasıl? Örneğin /plan 25 3: 25 dakika, enerji 3/5. Ya da kayıtlı bilgilerinle /plan yaz."

    try:
        minutes = int(parts[1])
        energy = int(parts[2])
    except ValueError:
        return "Dakikayı ve enerjini sayı olarak yaz: /plan 25 3 (25 dakika, enerji 3/5)."

    if minutes < 0:
        return "Süre negatif olamaz."

    if not 1 <= energy <= 5:
        return "Enerjini 1 ile 5 arasında seç: 1 çok düşük, 3 orta, 5 yüksek."

    if minutes == 0:
        return "Şu an zaman ayırmayacaksan yeni bir plan yapmayalım. Varsa önceki seçimini korudum; /devam ile görebilirsin."

    today = date.today()

    day = state.get("day")

    if not isinstance(day, dict) or day.get("date") != today.isoformat():
        return "Bugün ne kadar zamanın kaldığını henüz bilmiyorum. /gun 40 3 gibi yaz: 40 dakika, enerji 3/5."

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
        return "Şu an önerebileceğim açık bir iş yok. /ekle ile bir iş ekleyebilir veya /gorevler ile bekleyen işlerin durumuna bakabilirsin. Varsa önceki planını değiştirmedim."
    
    session["plan"] = {
        "date": today.isoformat(),
        "energy": energy,
        "task_ids": [task["id"] for task in active_tasks],
        "work_minutes_by_id": work_minutes_by_id,
    }
    session.pop("work", None)

    lines = [
        "Şimdi odaklanabileceğin işler",
        f"Şu an {minutes} dakika ayırabilirsin; enerjin {energy}/5.",
    ]

    for number, task in enumerate(active_tasks, start=1):
        label = "1. Önce bunu öneriyorum (Daily Win)" if number == 1 else "2. İstersen bu işi de seçebilirsin"
        work_minutes = work_minutes_by_id[task["id"]]

        lines.append(f"\n{label}: {task['title'][:150]}")
        lines.append(
            f"Bu işe şimdi {work_minutes} dakika ayıralım; tamamını bitirmen gerekmiyor."
        )

        lines.append(f"Senin verdiğin önem: {task['importance']}/5.")
        if task["deadline"]:
            lines.append(f"Son tarih: {task['deadline']}. Yakın tarihler seçimde öne çıkıyor.")
        if energy <= 2 and task["cognitive_load"] in ("medium", "high"):
            lines.append("Enerjin düşük olduğu için yoğun dikkat isteyen bu işe daha düşük öncelik verdim.")

    allocated_minutes = sum(work_minutes_by_id.values())
    lines.append(f"\nÖnerdiğim işlere toplam {allocated_minutes} dakika ayırdım.")
    lines.append(
        "Henüz çalışmış sayılmadın; kalan zamanından hiçbir şey düşmedim."
    )
    lines.append("Başlamak için /sec 1 yaz. AI istemezsen /sec 1 elle yaz.")
    if len(active_tasks) == 2:
        lines.append("İkinci işi tercih edersen /sec 2 yazabilirsin.")

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
    source = "Kaldığın küçük adım"

    if not action:
        if manual:
            source = "Bu kez adımı sen belirleyeceksin."
        elif not task.get("desired_outcome") or not task.get("context"):
            source = "İşin sonunda ne istediğini veya nerede kaldığını henüz bilmiyorum. Şimdilik adımı kendin yazabilirsin."
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
                source = "Şu an AI önerisi alamıyorum ama seçtiğin iş duruyor. Başlamak için AI beklemek zorunda değilsin."
            else:
                source = "Başlamak için önerim"

    work["action"] = action
    header = (
        f"Seçilen görev: {task['title'][:150]}\n"
        f"Şimdi bu işe {minutes} dakika ayıralım.\n\n"
    )

    if action is None:
        return (
            header + source + "\n"
            "Şöyle yaz: /eylem Yapacağım küçük iş ve ortaya çıkacak sonuç\n"
            "Önce bu adımı belirleyelim; çalıştıktan sonra nasıl gittiğini kaydederiz."
        )

    return (
        header + f"{source}:\n{action[:2500]}\n\n"
        "Bu adım sana ve ayırdığın zamana uyuyor mu? Uymuyorsa /eylem ile değiştirebilirsin.\n"
        "Çalıştıktan sonra örneğin /kaydet tamam 10 | Ortaya çıkan sonuç yaz. Devam ediyorsan /kaydet devam 5; engel varsa /kaydet engel 2 | Engel yaz."
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
        f"Bu işe ayırdığımız süre: {work['planned_minutes']} dakika\n"
        "Bu adımı senin için sakladım. Çalıştıktan sonra /kaydet tamam 10 | Sonuç, /kaydet devam 5 veya /kaydet engel 2 | Engel yazabilirsin."
    )


def record_feedback_message(command, session, update_id, state):
    for task in state["tasks"]:
        for checkpoint in task.get("checkpoints", []):
            if checkpoint.get("telegram_update_id") == update_id:
                return "Bu çalışmanı zaten kaydettim; süreni ikinci kez düşmedim."

    work = session.get("work")
    plan = session.get("plan")
    if not work or not plan:
        return "Önce birlikte bir iş seçelim. /plan dakika enerji yaz, ardından /sec 1 ile seç."
    today = date.today().isoformat()
    if plan["date"] != today:
        return "Bu plan önceki günden kalmış. Bugün için /gun dakika enerji ile başlayalım."
    if not isinstance(work.get("action"), str) or not work["action"].strip():
        return "Henüz yapacağın adımı belirlemedik. /eylem yazıp yanına küçük bir iş ve sonucunu ekle."

    header, _, note = command.partition("|")
    parts = header.split()
    if len(parts) != 3:
        return feedback_help()
    feedback = {"tamam": "done", "devam": "continue", "engel": "blocked"}.get(
        parts[1].lower(), parts[1].lower()
    )
    try:
        spent = int(parts[2])
    except ValueError:
        return "Kaç dakika çalıştığını tam sayı olarak yaz. Örneğin: /kaydet devam 5"
    day = state.get("day")
    if not isinstance(day, dict) or day.get("date") != today:
        return "Bugün ne kadar zamanın olduğunu henüz bilmiyorum. /gun 40 3 gibi bir mesajla belirt."
    task = next((task for task in state["tasks"] if task["id"] == work["task_id"]), None)
    if task is None or task.get("archived") or task.get("completed") or is_task_blocked(task):
        return "Bu görev şu an çalışmaya açık değil. /gorevler ile durumuna bakabilir veya yeni bir /plan isteyebilirsin."
    try:
        record_checkpoint(
            task, day, work["action"], feedback, work["planned_minutes"], spent,
            note=note, telegram_update_id=update_id,
        )
    except ValueError as error:
        return str(error) + "\n\n" + feedback_help()
    session.pop("work", None)
    session.pop("plan", None)
    responses = {
        "done": "Tamamladığın adımı ve ortaya çıkan sonucu kaydettim.",
        "continue": "İlerlemeni kaydettim. Aynı adımdan devam edebilirsin; bitirmek zorunda değilsin.",
        "blocked": "Engeli kaydettim. Engel kalkana kadar bu görevi yeniden önermeyeceğim.",
    }
    next_step = (
        "Engel kalktığında /ac " + task["id"][:8] + " yazabilirsin."
        if feedback == "blocked" else
        "Ana görevin de bittiyse /tamamla " + task["id"][:8] + " yaz."
        if feedback == "done" else
        "Sonraki seçimde bu adımı tekrar bulabilirsin."
    )
    if day["remaining_minutes"] > 0:
        next_step += "\nDevam etmek istersen /plan ile sıradaki kısa çalışmayı seçelim."
    else:
        next_step += "\nBugün için ayırdığın süre doldu. Burada bırakabilirsin. Zamanın değiştiyse /gun ile güncelle."
    return (
        f"{task['title'][:150]}\n{responses[feedback]}\n"
        f"Bu kez {spent} dakika çalıştığını bildirdin; bugün {day['remaining_minutes']} dakikan kaldı.\n\n"
        + next_step
    )


def feedback_help():
    return (
        "Nasıl geçti? Durumu ve bu kez kaç dakika çalıştığını birlikte yaz:\n"
        "/kaydet tamam 10 | Ortaya çıkan somut sonuç\n"
        "/kaydet devam 5 | Kaldığım yer (isteğe bağlı)\n"
        "/kaydet engel 2 | İlerlememi engelleyen şey\n\n"
        "Dakikalar yalnızca bu çalışmaya ait olsun. Bir adımı tamamlamak ana görevi kapatmaz."
    )


def set_day_message(command, session, update_id, message_date, state):
    parts = command.split()

    if len(parts) != 3:
        return "Bugün bundan sonra kaç dakika ayırabilirsin? Enerjin 1–5 arasında nasıl?\nÖrnek: /gun 40 3 → 40 dakika, orta enerji. 1 çok düşük, 5 yüksek."

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

    next_step = (
        "Bugün iş planlamayalım. Daha sonra zamanın olursa /gun ile değiştirebilirsin."
        if minutes == 0 else
        "Görevin varsa /plan yaz, yoksa /ekle ile başlayalım."
    )
    return (
        f"Tamam, bugünü buna göre düşünelim.\n"
        f"Bugün bundan sonra {minutes} dakika ayırabilirsin.\n"
        f"Enerji: {energy}/5\n\n"
        "Zamanını güncelledim; varsa önceki seçimini temizledim. " + next_step
    )

def add_task_message(command, session, update_id, state):
    usage = (
        "Kullanım:\n"
        "/ekle görev | önem | dakika | yük | deadline | hedef | bağlam\n\n"
        "Önem: 1–5\n"
        "Dakika: pozitif tam sayı\n"
        "Yük: low, medium veya high\n"
        "Deadline: YYYY-MM-DD veya -\n"
        "Hedef: iş bitince elinde ne olacak? Bağlam: şu an nerede kaldın?\n"
        "low: az dikkat, medium: orta, high: yoğun dikkat.\n\n"
        "Örnek (kendi işine göre değiştir):\n"
        "/ekle CV güncelle | 4 | 60 | medium | - | Başvuruya hazır CV | Projeler bölümünü henüz yazmadım\n\n"
        "60, bütün iş için tahminin; şimdi tamamını yapman gerekmiyor. Alanları | ile ayır."
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
        "İşini kaydettim. Varsa önceki seçimini temizledim. "
        "Hazırsan /plan ile küçük bir başlangıç seçelim."
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


def start_message():
    return (
        "Merhaba. Aklındaki bütün işleri aynı anda çözmeye çalışmadan, şimdi yapabileceğin küçük bir adım seçelim.\n\n"
        "1. Önce bugün ne kadar zamanın kaldığını ve enerjini söyle:\n"
        "/gun 40 3\n"
        "Bu, 40 dakikan var ve enerjin 5 üzerinden 3 demek. 1 çok düşük, 5 yüksek.\n\n"
        "2. Henüz görev eklemediysen /ekle yaz; bir örnek göstereyim.\n"
        "3. /plan yaz. Açık görevlerinden en fazla ikisini önereceğim.\n\n"
        "Sen çalıştıktan sonra sonucu birlikte kaydedeceğiz. Öneriyi değiştirebilir ya da ara verebilirsin.\n"
        "Tüm seçenekler: /yardim"
    )


def help_message():
    return (
        "Birlikte küçük bir adım seçelim\n\n"
        "/gun 40 3 — Bugün 40 dakikam var, enerjim 5 üzerinden 3\n"
        "/bugun — Kalan zamanım ve açık işlerim\n"
        "/ekle — Görev eklemek için açıklama ve örnek\n"
        "/plan — Kalan zamanım ve kayıtlı enerjimle iş öner\n"
        "/plan 25 3 — Şu an 25 dakika ayırabilirim, enerjim 3\n"
        "/sec 1 — İlk öneriyi seç\n"
        "/sec 1 elle — AI kullanmadan seç\n"
        "/eylem metin — Yapacağım küçük adımı kendim yaz\n"
        "/devam — Yarım kalan seçimimi ve adımımı göster\n\n"
        + feedback_help()
        + "\n\n/gorevler — Görevlerimi ve kısa kimliklerini göster\n"
        "/duzenle kimlik | hedef | bağlam — Neyi amaçladığımı veya nerede kaldığımı değiştir; aynı alan için -\n"
        "/arsiv kimlik — Bu işi şimdilik önerme\n"
        "/ac kimlik — İşi yeniden aç, varsa engeli kaldır\n"
        "/tamamla kimlik — Ana görevin tamamı bitti\n"
        "/ozet — Bugün kaydettiğim ilerlemeyi göster\n"
        "/hatirlat 09:00 — Bot açıkken sabah bir hatırlatma gönder\n"
        "/hatirlat kapat — Hatırlatmayı kapat\n\n"
        "Yeni plan veya görev değişikliği bekleyen seçimi temizler. "
        "Önce çalışmanı kaydet. /gun süre eklemez; kalan zamanını verdiğin sayıyla değiştirir."
    )


def resume_message(session, state):
    plan = session.get('plan')
    if not plan:
        return 'Bekleyen plan yok. /plan dakika enerji ile başlayabilirsin.'
    if plan['date'] != date.today().isoformat():
        return 'Saklanan plan önceki güne ait. /gun ve /plan ile bugünü başlat.'
    tasks = {task['id']: task for task in state['tasks']}
    lines = ['Kaldığın yer:']
    for number, task_id in enumerate(plan['task_ids'], start=1):
        task = tasks.get(task_id)
        title = task['title'][:150] if task else 'Artık bulunmayan görev'
        lines.append(f"{number}. {title} | {plan['work_minutes_by_id'][task_id]} dakika")
    work = session.get('work')
    if not work:
        lines.append('Henüz bir iş seçmedin. İlk öneriyle başlamak için /sec 1 yaz.')
    else:
        task = tasks.get(work['task_id'])
        if not task or task.get('archived') or task.get('completed') or is_task_blocked(task):
            lines.append('Bekleyen görev artık uygun değil; yeni bir /plan oluştur.')
        else:
            lines.append(f"\nSeçili görev: {task['title'][:150]}")
            lines.append(f"Bu işe ayırdığımız süre: {work['planned_minutes']} dakika")
            if work['action'] is None:
                lines.append('Eylem henüz belirlenmedi. /eylem ile kendi eylemini yaz.')
            else:
                lines.append(f"Eylem: {work['action'][:2200]}")
                lines.append('Çalıştıktan sonra /kaydet tamam 10 | Sonuç veya /kaydet devam 5 yazabilirsin.')
    return '\n'.join(lines)
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
    changed = update_task_context(
        task, None if outcome == "-" else outcome, None if context == "-" else context,
    )
    if not changed:
        return "Hedef ve bağlam aynı kaldı; mevcut adımını korudum."

    session.pop("plan", None)
    session.pop("work", None)

    return (
        f"İşin hedefini ve nerede kaldığını güncelledim: {task['title'][:150]}\n"
        "Hedef veya bağlam değiştiği için önceki öneriyi kaldırdım. Geçmiş ilerlemen duruyor.\n"
        "Yeni bir adım seçmek için /plan yazabilirsin."
    )

def build_summary_message(state):
    summary = daily_summary(state["tasks"], date.today())
    counts = summary["counts"]
    lines = [
        "Bugünkü ilerlemen",
        f"Tamamladığını bildirdiğin adım: {counts['done']}",
        f"Devam ettiğin çalışma: {counts['continue']}",
        f"Engelle karşılaştığın çalışma: {counts['blocked']}",
        f"Kaydettiğin çalışma süresi: {summary['spent_minutes']} dakika",
    ]
    if summary["missing_duration"]:
        lines.append(f"Süresi belirtilmemiş eski kayıt: {summary['missing_duration']}")
    if not summary["completed"]:
        lines.append("\nHenüz tamamladığını bildirdiğin bir adım yok. Bu özet yalnızca kaydettiğin çalışmaları gösterir.")
    for _, task, checkpoint in summary["completed"][-5:]:
        lines.append(
            f"\n• {task['title'][:100]}\n"
            f"Yaptığın adım: {checkpoint['action'][:160]}\n"
            f"Sonuç: {(checkpoint.get('output_note') or 'Açıklama yok.')[:200]}"
        )
    if len(summary["completed"]) > 5:
        lines.append("\nSon 5 tamamlanan adımı gösteriyorum; diğer kayıtlar saklanıyor.")
    lines.append("\nBu özet senin bildirdiklerine dayanıyor; çıktıları kendiliğinden doğrulamaz.")
    return "\n".join(lines)


def dispatch_message(text, session, state, update_id, message_date):
    command = text.split(maxsplit=1)[0] if text else ''
    if command == '/start':
        return start_message()
    if command == '/yardim':
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


def shorten_message(text: str) -> str:
    """Stay below Telegram's limit, including text containing emoji."""
    encoded = text.encode("utf-16-le")
    if len(encoded) <= 3800 * 2:
        return text
    return (
        encoded[:3500 * 2].decode("utf-16-le", errors="ignore").rstrip()
        + "\n\n… Mesajı kısalttım. Kaydedilmiş görev ve ilerleme bilgilerin değişmedi."
    )


def flush_pending_reply(token, owner_id):
    state = load_state()
    runtime = get_runtime(state, owner_id)
    pending = runtime.get("pending_reply")
    if pending is None:
        return
    payload = {"chat_id": pending["chat_id"], "text": shorten_message(pending["text"])}
    try:
        telegram_request(token, "sendMessage", payload)
    except ValueError as error:
        # A permanently rejected reply must not prevent later commands.
        # Work and offset were already saved together in process_update.
        print(f"{error} Bu yanıt gönderilemedi. Çalışma kayıtların korundu; yeni komutlar alınacak.")
    runtime["pending_reply"] = None
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
        except ValueError as error:
            raise SystemExit(f"{error} Botu durdurup bağlantı ayarlarını kontrol et.") from None
        except RuntimeError as error:
            print(error)
            print('5 saniye sonra yeniden denenecek; bekleyen yanıt korunuyor.')
            time.sleep(5)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nBot durduruldu.')

