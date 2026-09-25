import re
from datetime import datetime, timedelta


def configure_reminder(command, state):
    parts = command.split()

    if len(parts) != 2:
        return "Kullanım: /hatirlat 09:00 veya /hatirlat kapat"

    setting = parts[1].lower()
    runtime = state["telegram"]

    if setting == "kapat":
        runtime["reminder"] = {"enabled": False}
        return "Günlük hatırlatıcı kapatıldı."

    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", setting):
        return "Saati HH:MM biçiminde yaz. Örnek: /hatirlat 09:00"

    previous = runtime.get("reminder", {})

    runtime["reminder"] = {
        "enabled": True,
        "time": setting,
        "last_queued_date": previous.get("last_queued_date"),
    }

    return (
        f"Günlük hatırlatıcı: {setting}\n"
        "Bilgisayarın yerel saati kullanılır.\n"
        "Bilgisayar açık ve bot çalışıyor olmalı.\n"
        "Bir saatten fazla geciken hatırlatmalar gönderilmez."
    )


def queue_daily_reminder(state, owner_id, now=None):
    if now is None:
        now = datetime.now().astimezone()

    runtime = state["telegram"]
    reminder = runtime.get("reminder", {})

    if not reminder.get("enabled"):
        return False

    if runtime.get("pending_reply") is not None:
        return False

    time_text = reminder.get("time", "")

    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", time_text):
        return False

    today = now.date().isoformat()

    if reminder.get("last_queued_date") == today:
        return False

    hour, minute = map(int, time_text.split(":"))
    scheduled = now.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )

    if not scheduled <= now < scheduled + timedelta(hours=1):
        return False

    runtime["pending_reply"] = {
        "chat_id": owner_id,
        "text": (
            f"Checkpoint — günlük başlangıç ({today})\n\n"
            "Bugün ne kadar zamanın var, enerjin nasıl?\n"
            "Örnek: /gun 40 3\n\n"
            "Ardından /plan 25 3 ile küçük bir çalışma seçebilirsin.\n"
            "Bugün çalışmayacaksan /gun 0 3 yazabilirsin."
        ),
    }

    reminder["last_queued_date"] = today
    return True