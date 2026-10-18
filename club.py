# -*- coding: utf-8 -*-
"""Club automation mixin: personal crystal contributions and card donation alerts."""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup
from data_management import DataManager


def _msk_today():
    return datetime.now(timezone(timedelta(hours=3))).date().isoformat()


class ClubAutomationMixin:
    """Mixin for MangaMinerBot providing automated club contributions."""

    def club_check_interval(self) -> int:
        """Seconds until the next club visit: crystals are a daily 10/10, the wanted card rotates."""
        today = _msk_today()
        if DataManager.get_setting("club_none_date") == today:
            return 6 * 3600  # not in a club: looked once today
        if DataManager.get_setting("club_crystals_date") == today:
            return 3 * 3600  # crystals done: only the wanted-card alert is left
        return 30 * 60

    def get_club_slug(self) -> str:
        """Detect and return user's club slug, cached in memory and settings."""
        cached = getattr(self, "_club_slug", None)
        if cached:
            return cached

        saved = DataManager.get_setting("club_slug")
        if saved:
            self._club_slug = saved
            return saved

        # Detect from /balance navigation
        self._club_lookup_failed = False
        try:
            r = self.session.get("https://mangabuff.ru/balance", timeout=12)
            # A login page (expired session) says nothing about club membership
            logged_out = "/login" in str(getattr(r, "url", "")) or 'name="password"' in r.text
            if r.status_code == 200 and not logged_out:
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    text = a.get_text(strip=True).lower()
                    if "мой клуб" in text or "клуб" in text:
                        m = re.search(r"/clubs/([a-zA-Z0-9_\-]+)", href)
                        if m and m.group(1) not in ("create", "my", "all"):
                            slug = m.group(1)
                            self._club_slug = slug
                            DataManager.set_setting("club_slug", slug)
                            return slug
            else:
                self._club_lookup_failed = True
        except Exception as e:
            self._club_lookup_failed = True
            self.log(f"⚠️ Ошибка определения клуба: {e}")

        # Not in a club (or the page changed): skip, retry detection next cycle
        return None

    def run_club_cycle(self):
        """Execute regular club checks: personal crystal contributions & card alerts."""
        if not DataManager.get_setting("club_enabled", True):
            return

        today = _msk_today()
        if DataManager.get_setting("club_none_date") == today:
            return
        slug = self.get_club_slug()
        if not slug and getattr(self, "_club_lookup_failed", False):
            return  # /balance did not load: try again next cycle instead of skipping the day
        if not slug:
            DataManager.set_setting("club_none_date", today)
            self.log("ℹ️ Клуб не найден на /balance — вклады пропущены.")
            return
        boost_url = f"https://mangabuff.ru/clubs/{slug}/boost"

        try:
            r = self.session.get(boost_url, timeout=15)
            if r.status_code != 200:
                self.log(f"⚠️ Страница вкладов клуба ({slug}) вернула код {r.status_code}")
                return
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            self.log(f"⚠️ Ошибка загрузки страницы клуба: {e}")
            return

        # 1. Personal crystal contribution (10 a day)
        if DataManager.get_setting("club_crystals_date") != today:
            if self._process_crystal_contributions(soup, boost_url):
                DataManager.set_setting("club_crystals_date", today)

        # 2. Check rotating card contribution
        self._process_card_contribution_alert(soup, boost_url)

    def _process_crystal_contributions(self, soup, boost_url: str):
        """Contribute crystals up to 10/10; True once today's limit is reached."""
        cryst_box = soup.select_one(".club-crystals")
        if not cryst_box:
            return False

        contribute_url = cryst_box.get("data-contribute-url")
        if not contribute_url:
            return False
        finished = False

        contributed_count = 0
        total_exp = 0
        total_points = 0

        # Maximum 10 contributions per day/cycle
        for _ in range(10):
            progress_el = cryst_box.select_one(".club-crystals__progress")
            progress_text = progress_el.get_text(strip=True) if progress_el else ""
            if "10 / 10" in progress_text or "10/10" in progress_text:
                finished = True
                break
            if cryst_box.select_one(".club-crystals__complete"):
                finished = True
                break

            submit_btn = cryst_box.select_one(".club-crystals__submit")
            if not submit_btn:
                break

            if submit_btn.has_attr("disabled"):
                # Crystal not in inventory
                break

            item_id = submit_btn.get("data-item-id")
            version = submit_btn.get("data-version")
            if not item_id or not version:
                break

            try:
                csrf = getattr(self, "csrf_token", "") or (soup.select_one('meta[name="csrf-token"]') or {}).get("content", "")
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-CSRF-TOKEN": csrf,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": boost_url,
                }
                payload = {
                    "item_id": item_id,
                    "version": version,
                }
                resp = self.session.post(contribute_url, json=payload, headers=headers, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    contributed_count += 1
                    total_exp += int(data.get("exp") or 0)
                    total_points += int(data.get("points") or 0)
                    new_html = data.get("content")
                    if new_html:
                        parsed = BeautifulSoup(new_html, "html.parser")
                        cryst_box = parsed.select_one(".club-crystals") or parsed
                    else:
                        break
                    time.sleep(1.5)
                elif resp.status_code == 409:
                    break
                else:
                    break
            except Exception as e:
                self.log(f"⚠️ Ошибка персонального вклада кристаллов: {e}")
                break

        if contributed_count > 0:
            self.log(f"💎 Клуб: внесено {contributed_count} кристаллов (+{total_exp} опыта, +{total_points} очков)!")
            self.notifier.send_message(
                f"💎 <b>Персональный вклад в клуб</b>\n"
                f"Внесено кристаллов: <b>{contributed_count}</b>\n"
                f"Получено опыта клубу: <b>+{total_exp}</b>",
                silent=True,
            )

        return finished

    def _process_card_contribution_alert(self, soup, boost_url: str):
        """Check rotating card requested by club; alert user via TG if available in collection."""
        boost_box = soup.select_one(".club-boost--content")
        if not boost_box:
            return

        card_id = None
        for a in boost_box.select("a[href*='/cards/']"):
            m = re.search(r"/cards/(\d+)", a.get("href", ""))
            if m:
                card_id = m.group(1)
                break

        if not card_id:
            return

        box_text = boost_box.get_text().lower()
        if "у вас нет этой карты" in box_text or "карты ни у кого" in box_text:
            return

        has_donate_btn = False
        for btn in boost_box.find_all("button"):
            b_class = " ".join(btn.get("class", []))
            b_text = btn.get_text(strip=True).lower()
            if "replace" in b_class or "buy" in b_class:
                continue
            if "пожертвовать" in b_text or "внести" in b_text or "donate" in b_class:
                has_donate_btn = True
                break

        if not has_donate_btn and "пожертвовать" not in box_text:
            return

        # Do not spam for the same card ID
        last_alerted = getattr(self, "_last_club_card_alerted_id", None)
        if str(last_alerted) == str(card_id):
            return

        img_el = boost_box.select_one("img[src*='/img/cards/']")
        img_url = img_el["src"] if img_el else None
        photo_bytes = None
        if img_url:
            if not img_url.startswith("http"):
                img_url = "https://mangabuff.ru" + img_url
            try:
                img_resp = self.session.get(img_url, timeout=10)
                if img_resp.status_code == 200:
                    photo_bytes = img_resp.content
            except Exception:
                pass

        self._last_club_card_alerted_id = str(card_id)
        self.log(f"🃏 Клубный вклад: в вашей коллекции есть карта #{card_id}! Отправлено уведомление в Telegram.")
        self.notifier.notify_club_card_available(card_id=card_id, boost_url=boost_url, photo_bytes=photo_bytes)
