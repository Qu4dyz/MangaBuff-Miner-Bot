from datetime import datetime, timezone, timedelta
import json
import os
import queue
import threading
import time
import requests
from dotenv import load_dotenv
from data_management import DataManager

load_dotenv()


class TelegramNotifier:
    """Asynchronous, rate-limited, HTML-formatted Telegram Notifier with Silent & Night Mode."""

    def __init__(self):
        self._queue = queue.Queue()
        self._worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self._worker_thread.start()
        self._last_heartbeat_time = 0

    def get_credentials(self):
        """Retrieve credentials prioritizing user_data.json, fallback to .env."""
        token = DataManager.get_setting("tg_token") or os.getenv("TG_TOKEN")
        chat_id = DataManager.get_setting("tg_chat_id") or os.getenv("TG_CHAT_ID")
        enabled = DataManager.get_setting("tg_enabled")
        if enabled is None:
            enabled = bool(token and chat_id)
        return token, chat_id, enabled

    def is_silent_all_enabled(self):
        """Retrieve whether all notifications should be delivered silently."""
        val = DataManager.get_setting("tg_silent_all")
        if val is not None:
            return bool(val)
        return os.getenv("TG_SILENT_ALL", "0").lower() in ("1", "true", "yes")

    def is_night_time(self):
        """
        Check if current time falls within configured quiet hours (default: 23:00 - 08:00 MSK).
        During night time, routine status updates are suppressed and important
        notifications (e.g. card drops) are sent strictly silently (disable_notification=True).
        """
        night_mode = DataManager.get_setting("tg_night_mode")
        if night_mode is None:
            night_mode = os.getenv("TG_NIGHT_MODE", "1").lower() in ("1", "true", "yes")
        if not night_mode:
            return False

        try:
            tz_offset = int(DataManager.get_setting("tg_night_tz_offset") or os.getenv("TG_NIGHT_TZ_OFFSET", "3"))
            start_hour = int(DataManager.get_setting("tg_night_start") or os.getenv("TG_NIGHT_START", "23"))
            end_hour = int(DataManager.get_setting("tg_night_end") or os.getenv("TG_NIGHT_END", "8"))

            tz = timezone(timedelta(hours=tz_offset))
            current_hour = datetime.now(tz).hour

            if start_hour > end_hour:
                return current_hour >= start_hour or current_hour < end_hour
            elif start_hour < end_hour:
                return start_hour <= current_hour < end_hour
            else:
                return False
        except Exception:
            return False

    def should_silence(self, silent=None):
        """
        Determine if a notification should be delivered without sound or vibration.
        - If silent=True: always silent.
        - If global silent mode (tg_silent_all) is active: always silent.
        - If night quiet hours are active: always silent.
        - Otherwise, honor the passed silent parameter (default False).
        """
        if silent is True:
            return True
        if self.is_silent_all_enabled():
            return True
        if self.is_night_time():
            return True
        return bool(silent)

    def _process_queue(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            try:
                if isinstance(item, tuple) and len(item) > 0 and item[0] == "__PHOTO__":
                    # Format: ("__PHOTO__", photo_bytes, caption, parse_mode, disable_notification)
                    photo_bytes = item[1]
                    caption = item[2] if len(item) > 2 else None
                    parse_mode = item[3] if len(item) > 3 else "HTML"
                    disable_notification = item[4] if len(item) > 4 else False
                    self._send_photo_http(
                        photo_bytes,
                        caption=caption,
                        parse_mode=parse_mode,
                        disable_notification=disable_notification
                    )
                elif isinstance(item, tuple) and len(item) > 0 and item[0] == "__MEDIA_GROUP__":
                    # Format: ("__MEDIA_GROUP__", [photo_bytes...], caption, parse_mode, disable_notification)
                    photos = item[1] if len(item) > 1 else []
                    caption = item[2] if len(item) > 2 else None
                    parse_mode = item[3] if len(item) > 3 else "HTML"
                    disable_notification = item[4] if len(item) > 4 else False
                    self._send_media_group_http(
                        photos,
                        caption=caption,
                        parse_mode=parse_mode,
                        disable_notification=disable_notification
                    )
                else:
                    text = item[0]
                    parse_mode = item[1] if len(item) > 1 else "HTML"
                    disable_preview = item[2] if len(item) > 2 else True
                    disable_notification = item[3] if len(item) > 3 else False
                    self._send_http(
                        text,
                        parse_mode=parse_mode,
                        disable_preview=disable_preview,
                        disable_notification=disable_notification
                    )
            except Exception as e:
                print(f"Telegram queue error: {e}")
            self._queue.task_done()
            # Slight delay to respect Telegram's rate limits
            time.sleep(0.5)

    def _send_http(self, text, parse_mode="HTML", disable_preview=True, disable_notification=False):
        token, chat_id, enabled = self.get_credentials()
        if not enabled or not token or not chat_id:
            return False, "Telegram notifications disabled or credentials missing."

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_preview,
            "disable_notification": bool(disable_notification)
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

    def _send_photo_http(self, photo_bytes, caption=None, parse_mode="HTML", disable_notification=False):
        token, chat_id, enabled = self.get_credentials()
        if not enabled or not token or not chat_id:
            return False, "Telegram notifications disabled or credentials missing."

        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        payload = {
            "chat_id": chat_id,
            "disable_notification": bool(disable_notification)
        }
        if caption:
            payload["caption"] = caption
        if parse_mode:
            payload["parse_mode"] = parse_mode

        files = {"photo": ("card.png", photo_bytes, "image/png")}
        try:
            response = requests.post(url, data=payload, files=files, timeout=15)
            if response.status_code == 200:
                return True, "OK"
            elif response.status_code == 429:
                try:
                    retry_after = response.json().get("parameters", {}).get("retry_after", 5)
                except Exception:
                    retry_after = 5
                time.sleep(retry_after)
                requests.post(url, data=payload, files=files, timeout=15)
                return False, f"Rate limited, waited {retry_after}s"
            else:
                return False, f"HTTP {response.status_code}: {response.text[:200]}"
        except requests.RequestException as e:
            return False, str(e)

    def _send_media_group_http(self, photos, caption=None, parse_mode="HTML", disable_notification=False):
        """Send 2–10 photos as a Telegram album. Caption attaches to the first photo."""
        token, chat_id, enabled = self.get_credentials()
        if not enabled or not token or not chat_id:
            return False, "Telegram notifications disabled or credentials missing."

        photos = [p for p in (photos or []) if p]
        if not photos:
            return False, "No photos"
        if len(photos) == 1:
            return self._send_photo_http(
                photos[0],
                caption=caption,
                parse_mode=parse_mode,
                disable_notification=disable_notification
            )

        # Telegram album limit is 10
        photos = photos[:10]
        url = f"https://api.telegram.org/bot{token}/sendMediaGroup"
        media = []
        files = {}
        for i, photo_bytes in enumerate(photos):
            attach_name = f"photo{i}"
            item = {"type": "photo", "media": f"attach://{attach_name}"}
            if i == 0 and caption:
                # Telegram caption hard limit ~1024
                item["caption"] = caption[:1024]
                if parse_mode:
                    item["parse_mode"] = parse_mode
            media.append(item)
            files[attach_name] = (f"card_{i}.png", photo_bytes, "image/png")

        payload = {
            "chat_id": chat_id,
            "media": json.dumps(media),
            "disable_notification": str(bool(disable_notification)).lower()
        }
        try:
            response = requests.post(url, data=payload, files=files, timeout=30)
            if response.status_code == 200:
                return True, "OK"
            elif response.status_code == 429:
                try:
                    retry_after = response.json().get("parameters", {}).get("retry_after", 5)
                except Exception:
                    retry_after = 5
                time.sleep(retry_after)
                requests.post(url, data=payload, files=files, timeout=30)
                return False, f"Rate limited, waited {retry_after}s"
            else:
                return False, f"HTTP {response.status_code}: {response.text[:200]}"
        except requests.RequestException as e:
            return False, str(e)

    def send_message(self, text, parse_mode="HTML", wait=False, disable_preview=True, silent=None):
        """Send message asynchronously (or synchronously if wait=True)."""
        disable_notif = self.should_silence(silent)
        if wait:
            return self._send_http(
                text,
                parse_mode=parse_mode,
                disable_preview=disable_preview,
                disable_notification=disable_notif
            )
        else:
            self._queue.put((text, parse_mode, disable_preview, disable_notif))
            return True, "Queued"

    def send_photo(self, photo_bytes, caption=None, parse_mode="HTML", wait=False, silent=None):
        """Send a photo asynchronously (or synchronously if wait=True)."""
        disable_notif = self.should_silence(silent)
        if wait:
            return self._send_photo_http(
                photo_bytes,
                caption=caption,
                parse_mode=parse_mode,
                disable_notification=disable_notif
            )
        else:
            self._queue.put(("__PHOTO__", photo_bytes, caption, parse_mode, disable_notif))
            return True, "Queued"

    def send_media_group(self, photos, caption=None, parse_mode="HTML", wait=False, silent=None):
        """Send multiple photos as one Telegram album (async by default)."""
        disable_notif = self.should_silence(silent)
        photos = [p for p in (photos or []) if p]
        if not photos:
            return False, "No photos"
        if len(photos) == 1:
            return self.send_photo(
                photos[0],
                caption=caption,
                parse_mode=parse_mode,
                wait=wait,
                silent=silent
            )
        if wait:
            return self._send_media_group_http(
                photos,
                caption=caption,
                parse_mode=parse_mode,
                disable_notification=disable_notif
            )
        self._queue.put(("__MEDIA_GROUP__", photos, caption, parse_mode, disable_notif))
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
        self.send_message(msg, silent=True)

    def notify_daily_reward(self, message):
        msg = f"🎁 <b>Ежедневная награда</b>\n{message}"
        self.send_message(msg, silent=True)

    def notify_quests_claimed(self, count, essence=None, energy=None):
        msg = (
            f"📋 <b>Ежедневные квесты</b>\n"
            f"✅ Успешно получено наград: <b>{count}</b>\n"
        )
        if essence:
            msg += f"🔮 Текущая эссенция: <b>{essence}</b>\n"
        if energy:
            msg += f"⚡ Энергия пробуждения: <b>{energy}</b>\n"
        self.send_message(msg, silent=True)

    def notify_mining_summary(self, clicks, added_ore, total_ore):
        msg = (
            "⛏️ <b>Итоги майнинга</b>\n"
            f"🔨 Ударов: <b>{clicks}</b>\n"
            f"💎 Добыто руды: <b>+{added_ore:,}</b>\n"
            f"💰 Всего руды: <b>{total_ore:,}</b>"
        )
        self.send_message(msg, silent=True)

    def notify_upgrade(self, title, level=None, cost=None):
        msg = f"⬆️ <b>Улучшение в магазине!</b>\n<b>{title}</b>\n"
        if level:
            msg += f"Новый уровень: <b>{level}</b>\n"
        if cost:
            msg += f"Потрачено: <b>{cost:,} руды</b>"
        self.send_message(msg, silent=True)

    def notify_battle_summary(self, battles, wins, losses, essence_earned, daily_cap_reached=False):
        cap_str = " (достигнут лимит 1000/день)" if daily_cap_reached else ""
        msg = (
            "⚔️ <b>Итоги карточных боёв</b>\n"
            f"📊 Всего боёв: <b>{battles}</b>\n"
            f"🏆 Побед: <b>{wins}</b> | 💀 Поражений: <b>{losses}</b>\n"
            f"🔮 Заработано эссенции: <b>+{essence_earned:,}</b>{cap_str}"
        )
        self.send_message(msg, silent=True)

    def notify_trade_offer(
        self,
        trade_id,
        user_name="Пользователь",
        receive_cards=None,
        give_cards=None,
        photo_bytes=None,
        receive_photos=None,
        give_photos=None,
    ):
        """
        Notify about a trade offer with clear visual separation:
        1) text summary + link
        2) album/photo «📥 ВЫ ПОЛУЧИТЕ»
        3) album/photo «📤 ВЫ ОТДАДИТЕ»
        """
        recv_photos = [p for p in (receive_photos or []) if p]
        give_photos_list = [p for p in (give_photos or []) if p]
        if not recv_photos and photo_bytes:
            recv_photos = [photo_bytes]

        header = "🤝 <b>Новое предложение обмена на MangaBuff!</b>\n"
        if user_name:
            header += f"👤 <b>Пользователь:</b> <code>{user_name}</code>\n"
        if receive_cards:
            cards_str = "\n• " + "\n• ".join(receive_cards)
            header += f"📥 <b>Вы получите ({len(receive_cards)}):</b>{cards_str}\n"
        if give_cards:
            cards_str = "\n• " + "\n• ".join(give_cards)
            header += f"📤 <b>Вы отдадите ({len(give_cards)}):</b>{cards_str}\n"
        header += f'🔗 <a href="https://mangabuff.ru/trades/{trade_id}">Открыть обмен на сайте</a>'

        # Always send text first so sides stay readable even without photos
        self.send_message(header, disable_preview=True)

        if recv_photos:
            recv_cap = f"📥 <b>ВЫ ПОЛУЧИТЕ</b> ({len(recv_photos)})"
            if len(recv_photos) == 1:
                self.send_photo(recv_photos[0], caption=recv_cap, parse_mode="HTML")
            else:
                self.send_media_group(recv_photos[:10], caption=recv_cap, parse_mode="HTML")

        if give_photos_list:
            give_cap = f"📤 <b>ВЫ ОТДАДИТЕ</b> ({len(give_photos_list)})"
            if len(give_photos_list) == 1:
                self.send_photo(give_photos_list[0], caption=give_cap, parse_mode="HTML")
            else:
                self.send_media_group(give_photos_list[:10], caption=give_cap, parse_mode="HTML")

    def notify_trade_status(self, trade_id, user_name="Пользователь", status="canceled"):
        if status == "canceled":
            msg = (
                f"❌ <b>Обмен #{trade_id} отменён</b>\n"
                f"👤 Пользователь <b>{user_name}</b> отозвал предложение обмена."
            )
        elif status == "accepted":
            msg = (
                f"🎉 <b>Обмен #{trade_id} успешно завершён!</b>\n"
                f"Обмен с пользователем <b>{user_name}</b> принят."
            )
        elif status == "rejected":
            msg = (
                f"🚫 <b>Обмен #{trade_id} отклонён</b>\n"
                f"Обмен с пользователем <b>{user_name}</b> отклонён."
            )
        else:
            msg = f"ℹ️ <b>Статус обмена #{trade_id} изменён:</b> <code>{status}</code> ({user_name})"
        self.send_message(msg, silent=True)

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
        self.send_message(msg, silent=True)

    def notify_ads_watched(self, count, diamonds):
        msg = (
            "📺 <b>Просмотр рекламы завершен!</b>\n"
            f"🎬 Просмотрено: <b>{count}/3</b>\n"
            f"💎 Получено: <b>+{diamonds} алмазов</b>"
        )
        self.send_message(msg, silent=True)

    def notify_card_dropped(self, card_name, card_image=None, cards_today=None, photo_bytes=None, copy_info=None, user_id=None, card_id=None):
        import urllib.parse
        market_url = f"https://mangabuff.ru/market?q={urllib.parse.quote_plus(card_name)}"
        inventory_url = f"https://mangabuff.ru/users/{user_id}/cards?sort=new" if user_id else None

        msg = "🎁 <b>Найдена бонусная карта за чтение!</b>\n"
        msg += f'🃏 Карта: <a href="{market_url}"><b>{card_name}</b></a>\n'
        if copy_info and copy_info.get("copy_number"):
            num = copy_info["copy_number"]
            title = copy_info.get("title", "")
            is_special = copy_info.get("is_special", False)
            formatted_num = f"#{num:06d}" if num < 1000000 else f"#{num}"
            special_fire = " 🔥" if is_special else ""
            msg += f"🔢 <b>Экземпляр:</b> <code>{formatted_num}</code> (<b>{title}</b>{special_fire})\n"
        if cards_today:
            msg += f"📦 Найдено сегодня: <b>{cards_today}/10</b>\n"

        action_links = [f'💰 <b><a href="{market_url}">Цены на Маркете</a></b>']
        if inventory_url:
            action_links.append(f'🎒 <b><a href="{inventory_url}">В инвентаре</a></b>')
        msg += " | ".join(action_links) + "\n"

        if photo_bytes:
            self.send_photo(photo_bytes, caption=msg, parse_mode="HTML")
        else:
            if card_image:
                full_img = f"https://mangabuff.ru{card_image}" if card_image.startswith("/") else card_image
                msg = f'<a href="{full_img}">&#8205;</a>' + msg
            self.send_message(msg, disable_preview=False if card_image else True)

    def notify_scroll_dropped(self, scroll_name, rank=None):
        msg = "📜 <b>Выпал свиток заточки за чтение!</b>\n"
        msg += f"✨ Свиток: <b>{scroll_name}</b>"
        if rank:
            msg += f" (Ранг {rank})"
        self.send_message(msg, silent=True)

    def notify_reading_summary(self, chapters_read, total_today=None, cards_gained=0, scrolls_gained=0):
        # In night mode, suppress routine reading progress completely if no drops occurred
        if self.is_night_time() and cards_gained == 0 and scrolls_gained == 0:
            return

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
        self.send_message(msg, silent=True)

    def notify_alert(self, title, detail):
        msg = f"⚠️ <b>Внимание: {title}</b>\n{detail}"
        self.send_message(msg)

    def notify_cycle_heartbeat(self, energy, balance, ads_count, cards_count, tower_str, next_action, next_wait_min, diamonds=None, chapters_str=None, force=False):
        """
        Send periodic cycle status report.
        - Suppressed entirely during night quiet hours (23:00 - 08:00 MSK) unless force=True.
        - Throttled to at most once every 4 hours (configurable via tg_heartbeat_interval_hours) unless force=True.
        - Always delivered silently (disable_notification=True).
        """
        # 1. Suppress during night hours
        if self.is_night_time() and not force:
            return

        # 2. Check throttle interval
        interval_hours = DataManager.get_setting("tg_heartbeat_interval_hours")
        if interval_hours is None:
            try:
                interval_hours = float(os.getenv("TG_HEARTBEAT_INTERVAL_HOURS", "4.0"))
            except (ValueError, TypeError):
                interval_hours = 4.0

        interval_sec = max(900, int(float(interval_hours) * 3600))  # At least 15 min
        now = time.time()
        if not force and (now - self._last_heartbeat_time < interval_sec):
            return

        self._last_heartbeat_time = now

        ore_dia = (balance // 100) if balance else 0
        dia_part = f" | 💎 Баланс: <b>{diamonds:,}</b>" if diamonds is not None else ""

        msg = "🤖 <b>Отчет цикла MangaBuff</b>\n"
        msg += f"⛏️ Руда: <b>{balance:,}</b> (≈{ore_dia:,} 💎){dia_part}\n"

        if diamonds is not None:
            total_dia = diamonds + ore_dia
            msg += f"💰 Всего капитал: <b>~{total_dia:,} 💎</b> | ⚡ Энергия: <b>{energy}</b>\n"
        else:
            msg += f"⚡ Энергия: <b>{energy}</b>\n"

        ch_part = f"📖 Главы: <b>{chapters_str}</b> | " if chapters_str else ""
        msg += f"{ch_part}📺 Реклама: <b>{ads_count}/3</b> | 🃏 Карты: <b>{cards_count}</b>\n"
        msg += f"🏰 Башня: <b>{tower_str}</b>\n"
        msg += f"💤 Следующее: <b>{next_action}</b> (~{next_wait_min} мин)"
        self.send_message(msg, silent=True)


# Global singleton instance
_notifier = TelegramNotifier()


def send_message(report_text, parse_mode="HTML", wait=False, silent=None):
    """Module-level entry point for backward compatibility."""
    return _notifier.send_message(report_text, parse_mode=parse_mode, wait=wait, silent=silent)


def get_notifier():
    """Access the singleton TelegramNotifier instance."""
    return _notifier