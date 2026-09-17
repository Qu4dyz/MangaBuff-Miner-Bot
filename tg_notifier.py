import os
import queue
import threading
import time
import requests
from dotenv import load_dotenv
from data_management import DataManager

load_dotenv()


class TelegramNotifier:
    """Asynchronous, rate-limited, HTML-formatted Telegram Notifier."""

    def __init__(self):
        self._queue = queue.Queue()
        self._worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._worker_thread.start()

    def get_credentials(self):
        """Retrieve credentials prioritizing user_data.json, fallback to .env."""
        token = DataManager.get_setting("tg_token") or os.getenv("TG_TOKEN")
        chat_id = DataManager.get_setting("tg_chat_id") or os.getenv("TG_CHAT_ID")
        enabled = DataManager.get_setting("tg_enabled")
        if enabled is None:
            enabled = bool(token and chat_id)
        return token, chat_id, enabled

    def _process_queue(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            if len(item) == 3:
                text, parse_mode, disable_preview = item
            else:
                text, parse_mode = item
                disable_preview = True
            self._send_http(text, parse_mode, disable_preview=disable_preview)
            self._queue.task_done()
            # Slight delay to respect Telegram's rate limits (max 30/sec, safe: 2/sec)
            time.sleep(0.5)

    def _send_http(self, text, parse_mode="HTML", disable_preview=True):
        token, chat_id, enabled = self.get_credentials()
        if not enabled or not token or not chat_id:
            return False, "Telegram notifications disabled or credentials missing."

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_preview
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            response = requests.post(url, data=payload, timeout=7)
            if response.status_code == 200:
                return True, "OK"
            elif response.status_code == 429:
                try:
                    retry_after = response.json().get("parameters", {}).get("retry_after", 5)
                except Exception:
                    retry_after = 5
                time.sleep(retry_after)
                # Retry once
                requests.post(url, data=payload, timeout=7)
                return False, f"Rate limited, waited {retry_after}s"
            else:
                return False, f"HTTP {response.status_code}: {response.text[:200]}"
        except requests.RequestException as e:
            return False, str(e)

    def send_message(self, text, parse_mode="HTML", wait=False, disable_preview=True):
        """Send message asynchronously (or synchronously if wait=True)."""
        if wait:
            return self._send_http(text, parse_mode, disable_preview=disable_preview)
        else:
            self._queue.put((text, parse_mode, disable_preview))
            return True, "Queued"

    # ==========================================
    # STRUCTURED NOTIFICATIONS (Clean HTML format)
    # ==========================================
    def notify_session_start(self, email, ore=None, essence=None):
        msg = (
            "🤖 <b>MangaBuff Miner — Сессия запущена</b>\n"
            f"👤 <b>Аккаунт:</b> <code>{email}</code>\n"
        )
        if ore is not None:
            msg += f"💎 <b>Руда:</b> <code>{ore:,}</code>\n"
        if essence is not None:
            msg += f"🔮 <b>Эссенция:</b> <code>{essence:,}</code>\n"
        self.send_message(msg)

    def notify_daily_reward(self, message):
        msg = f"🎁 <b>Ежедневная награда</b>\n{message}"
        self.send_message(msg)

    def notify_quests_claimed(self, count, essence=None, energy=None):
        msg = (
            f"📋 <b>Ежедневные квесты</b>\n"
            f"✅ Успешно получено наград: <b>{count}</b>\n"
        )
        if essence:
            msg += f"🔮 Текущая эссенция: <b>{essence}</b>\n"
        if energy:
            msg += f"⚡ Энергия пробуждения: <b>{energy}</b>\n"
        self.send_message(msg)

    def notify_mining_summary(self, clicks, added_ore, total_ore):
        msg = (
            "⛏️ <b>Итоги майнинга</b>\n"
            f"🔨 Ударов: <b>{clicks}</b>\n"
            f"💎 Добыто руды: <b>+{added_ore:,}</b>\n"
            f"💰 Всего руды: <b>{total_ore:,}</b>"
        )
        self.send_message(msg)

    def notify_upgrade(self, title, level=None, cost=None):
        msg = f"⬆️ <b>Улучшение в магазине!</b>\n<b>{title}</b>\n"
        if level:
            msg += f"Новый уровень: <b>{level}</b>\n"
        if cost:
            msg += f"Потрачено: <b>{cost:,} руды</b>"
        self.send_message(msg)

    def notify_battle_summary(self, battles, wins, losses, essence_earned, daily_cap_reached=False):
        cap_str = " (достигнут лимит 1000/день)" if daily_cap_reached else ""
        msg = (
            "⚔️ <b>Итоги карточных боёв</b>\n"
            f"📊 Всего боёв: <b>{battles}</b>\n"
            f"🏆 Побед: <b>{wins}</b> | 💀 Поражений: <b>{losses}</b>\n"
            f"🔮 Заработано эссенции: <b>+{essence_earned:,}</b>{cap_str}"
        )
        self.send_message(msg)

    def notify_jackpot(self, card_rarity, card_title=None):
        msg = (
            "🔥 <b>ДЖЕКПОТ! ВЫПАЛА РЕДКАЯ КАРТА!</b> 🔥\n"
            f"🌟 Редкость: <b>{card_rarity.upper()}</b>\n"
        )
        if card_title:
            msg += f"🃏 Карта: <b>{card_title}</b>\n"
        msg += "💾 Карта сохранена в <code>jackpot_cards.json</code> и защищена от расхода!"
        self.send_message(msg)

    def notify_tower_claimed(self, diamonds, crystals, levels_gained=0):
        msg = (
            "🏰 <b>Башня Разлома — Экспедиция завершена!</b>\n"
            f"💎 Алмазы: <b>+{diamonds:,}</b>\n"
            f"🔮 Темные кристаллы: <b>+{crystals:,}</b>\n"
        )
        if levels_gained > 0:
            msg += f"⬆️ Карты получили уровней: <b>+{levels_gained}</b>\n"
        self.send_message(msg)

    def notify_tower_started(self, difficulty, duration_hours, ends_at=None):
        msg = (
            "⚔️ <b>Башня Разлома — Новая экспедиция отправлена!</b>\n"
            f"🗺️ Сложность: <b>{difficulty}</b>\n"
            f"⏱️ Длительность: <b>{duration_hours} ч.</b>\n"
        )
        if ends_at:
            msg += f"🏁 Окончание: <code>{ends_at}</code>\n"
        self.send_message(msg)

    def notify_ads_watched(self, count, diamonds):
        msg = (
            "📺 <b>Просмотр рекламы завершен!</b>\n"
            f"🎬 Просмотрено: <b>{count}/3</b>\n"
            f"💎 Получено: <b>+{diamonds} алмазов</b>"
        )
        self.send_message(msg)

    def notify_card_dropped(self, card_name, card_image=None, cards_today=None):
        msg = "🎁 <b>Найдена бонусная карта за чтение!</b>\n"
        if card_image:
            full_img = f"https://mangabuff.ru{card_image}" if card_image.startswith("/") else card_image
            msg = f'<a href="{full_img}">&#8205;</a>' + msg
        msg += f"🃏 Карта: <b>{card_name}</b>\n"
        if cards_today:
            msg += f"📦 Найдено сегодня: <b>{cards_today}/10</b>\n"
        self.send_message(msg, disable_preview=False if card_image else True)

    def notify_scroll_dropped(self, scroll_name, rank=None):
        msg = "📜 <b>Выпал свиток заточки за чтение!</b>\n"
        msg += f"✨ Свиток: <b>{scroll_name}</b>"
        if rank:
            msg += f" (Ранг {rank})"
        self.send_message(msg)

    def notify_reading_summary(self, chapters_read, total_today=None, cards_gained=0, scrolls_gained=0):
        msg = (
            "📖 <b>Чтение глав завершено!</b>\n"
            f"📚 Прочитано в сессии: <b>{chapters_read} глав</b>\n"
        )
        if total_today is not None:
            msg += f"📊 Дневной прогресс: <b>{total_today}/75</b>\n"
        if cards_gained > 0:
            msg += f"🃏 Найдено карт: <b>+{cards_gained}</b>\n"
        if scrolls_gained > 0:
            msg += f"📜 Найдено свитков: <b>+{scrolls_gained}</b>\n"
        self.send_message(msg)

    def notify_alert(self, title, detail):
        msg = f"⚠️ <b>Внимание: {title}</b>\n{detail}"
        self.send_message(msg)


# Global singleton instance
_notifier = TelegramNotifier()


def send_message(report_text, parse_mode="HTML", wait=False):
    """Module-level entry point for backward compatibility."""
    return _notifier.send_message(report_text, parse_mode=parse_mode, wait=wait)


def get_notifier():
    """Access the singleton TelegramNotifier instance."""
    return _notifier