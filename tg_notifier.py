import os
import requests
from dotenv import load_dotenv

load_dotenv()

def send_message(report_text):

    token = os.getenv("TG_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")

    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": report_text
    }
     
    try:
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            print("Message sent successfully.");
        else:
            print(f"Failed to send message. Status code: {response.status_code}")
    except Exception as e:
        print(f"An error occurred while sending the message: {e}")

#send_message("Hello, this is a test message from the Telegram notifier.")