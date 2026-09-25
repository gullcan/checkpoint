import json
import os
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        print("Telegram token'ı eksik veya biçimi geçersiz.")
        return

    pairing_message = "/pair " + secrets.token_hex(8)

    print("Kendi Telegram botuna şu mesajı gönder:")
    print(pairing_message)
    input("Mesajı gönderdikten sonra burada Enter'a bas: ")

    offset = 0

    while True:
        url = (
            f"https://api.telegram.org/bot{token}/getUpdates"
            f"?offset={offset}&limit=100&timeout=0"
        )

        try:
            with urlopen(url, timeout=15) as response:
                data = json.load(response)
        except HTTPError as error:
            print(f"Telegram isteği reddetti. HTTP kodu: {error.code}")
            return
        except (URLError, TimeoutError):
            print("Telegram'a ulaşılamadı.")
            return
        except (json.JSONDecodeError, UnicodeDecodeError):
            print("Telegram yanıtı okunamadı.")
            return

        if not data.get("ok"):
            print("Mesajlar alınamadı.")
            return

        updates = data["result"]

        if not updates:
            print("Eşleştirme mesajı bulunamadı. Programı yeniden çalıştır.")
            return

        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message", {})

            if message.get("chat", {}).get("type") != "private":
                continue

            if message.get("text") != pairing_message:
                continue

            sender = message["from"]

            if sender.get("is_bot"):
                continue

            print("Eşleştirme mesajı bulundu.")
            print(f"Hesap adı: {sender['first_name']}")
            print(f"Telegram kullanıcı kimliği: {sender['id']}")
            return


if __name__ == "__main__":
    main()