import requests
from data_management import DataManager
from languages import tr
from bs4 import BeautifulSoup
from config import CONFIG, TARGET_RARITIES, BASE_DIR
import re
import json
from utils import (
    parse_smart_number,
    safe_cookies_to_dict,
    normalize_proxy_url,
    mask_proxy_url,
    parse_card_cooldown_seconds,
    get_seconds_until_midnight_msk
)
import os
import random
import time
from tg_notifier import get_notifier


# ==========================================
# BOT ENGINE
# ==========================================
class MangaMinerBot:
    def __init__(self, log_callback, progress_callback, stats_callback, headless=True, proxy=None):
        self.log = log_callback       
        self.auth_log_buffer = []
        self.update_progress = progress_callback
        self.update_stats = stats_callback
        self.headless = headless
        self.running = False
        self.API_URL = "https://mangabuff.ru/mine/hit"
        self.notifier = get_notifier()
        self.session = requests.Session()

        # Configure proxy if provided or found in settings/environment
        raw_proxy = proxy or os.getenv("MANGABUFF_PROXY") or os.getenv("PROXY") or DataManager.get_setting("proxy")
        self.proxy = normalize_proxy_url(raw_proxy)
        if self.proxy:
            self.session.proxies.update({
                "http": self.proxy,
                "https": self.proxy
            })
            masked = mask_proxy_url(self.proxy)
            self.auth_log_buffer.append(f"🌐 Прокси подключен: {masked}")

        # Default modern browser UA (overridden after login if a saved one exists)
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        })
        self.email, self.password = DataManager.get_credentials()
        self.csrf_token = None
        self.user_agent = None
        self.current_balance = 0

    def validate_session(self):
        data = DataManager.load_session()
        if not data or "cookies" not in data or "csrf" not in data:
            return False

        self.session.cookies.update(data["cookies"])
        self.csrf_token = data["csrf"]
        self.user_agent = data["agent"]
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "X-CSRF-TOKEN": self.csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://mangabuff.ru/mine",
            "Origin": "https://mangabuff.ru",
            "Content-Type": "application/json"
        })
        self.auth_log_buffer.append(tr("log_session_valid"))
        return True

    def login_and_steal_keys(self):
        self.auth_log_buffer.append("Logging in via API...")
        self.session.cookies.clear()
        try:
            self.user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            self.session.headers.update({"User-Agent": self.user_agent})

            # 1. Initial GET to retrieve the login page (and session cookie)
            res = self.session.get(CONFIG["urls"]["login"], timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")

            # Extract CSRF token from the meta tag
            meta = soup.find("meta", attrs={"name": "csrf-token"})
            if not meta or not meta.get("content"):
                self.auth_log_buffer.append(tr("log_login_fail_csrf"))
                return False
            self.csrf_token = meta.get("content")

            # 2. POST login (standard form submit)
            post_res = self.session.post(
                CONFIG["urls"]["login"],
                data={"_token": self.csrf_token, "email": self.email, "password": self.password},
                headers={
                    "Referer": "https://mangabuff.ru/login",
                    "Origin": "https://mangabuff.ru",
                    "Content-Type": "application/x-www-form-urlencoded"
                },
                timeout=10,
                allow_redirects=True
            )

            # 3. Validation: parse JSON response ({"status": true} on success)
            try:
                response_data = post_res.json()
            except ValueError:
                self.auth_log_buffer.append(tr("log_login_fail_page"))
                return False

            if response_data.get("status") is True:
                # 4. Configure API headers for subsequent requests
                self.session.headers.update({
                    "User-Agent": self.user_agent,
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/mine",
                    "Origin": "https://mangabuff.ru",
                    "Content-Type": "application/json"
                })

                cookies_dict = safe_cookies_to_dict(self.session)
                DataManager.save_session(cookies_dict, self.csrf_token, self.user_agent)
                self.auth_log_buffer.append(tr("log_login_ok"))
                return True
            else:
                self.auth_log_buffer.append(tr("log_login_fail_page"))
                return False

        except Exception as e:
            self.auth_log_buffer.append(tr("log_login_crash", e=e))
            return False

    def claim_daily_reward(self):
        """Claim the daily login reward from /balance page."""
        self.auth_log_buffer.append("🎁 Checking for daily reward...")
        try:
            claim_url = self._find_active_claim_url()
            if not claim_url:
                self.auth_log_buffer.append("ℹ️ No available daily reward to claim.")
                return

            self.auth_log_buffer.append(f"🎁 Claiming reward: {claim_url}")
            claim_res = self.session.post(
                claim_url,
                data="",
                headers={
                    "Accept": "*/*",
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/balance"
                },
                timeout=10
            )

            if claim_res.status_code == 422:               
                self.auth_log_buffer.append("ℹ️ Daily reward already claimed or not available yet.")
            elif claim_res.status_code == 200:                    
                msg = "✅ Daily reward claimed successfully!"
                self.auth_log_buffer.append(msg)
                self.notifier.notify_daily_reward(msg)
            else:
                self.auth_log_buffer.append(f"⚠️ Claim returned status {claim_res.status_code}")

        except Exception as e:
            self.auth_log_buffer.append(f"⚠️ Daily reward error: {e}")

    def watch_daily_ads(self):
        """Watch up to 3 daily ads on /balance page for +7 diamonds each (+21 diamonds total)."""
        if not DataManager.get_setting("ads_enabled", True):
            return
        self.log(tr("log_ads_check"))
        try:
            res = self.session.get("https://mangabuff.ru/balance", timeout=10)
            if res.status_code != 200:
                self.log(f"⚠️ Failed to open /balance for ads: {res.status_code}")
                return

            soup = BeautifulSoup(res.text, "html.parser")
            meta = soup.find("meta", attrs={"name": "csrf-token"})
            if meta and meta.get("content"):
                self.csrf_token = meta.get("content")

            btn = soup.find(class_=lambda c: c and "user-quest__watch-ads-btn" in c)
            if not btn:
                self.log("ℹ️ Daily ads button not found on /balance.")
                return

            count = int(btn.get("data-count", 0))
            if count >= 3:
                self.log(tr("log_ads_already"))
                return

            needed = 3 - count
            watched = 0
            diamonds = 0
            for i in range(needed):
                if not self.running:
                    break

                # If this is not the first ad in this session, wait for video ad duration (cooldown ~20s)
                if i > 0:
                    delay = random.uniform(18.0, 22.0)
                    for _ in range(int(delay * 4)):
                        if not self.running:
                            break
                        time.sleep(0.25)
                else:
                    time.sleep(random.uniform(1.0, 2.0))

                if not self.running:
                    break

                ad_res = self.session.post(
                    "https://mangabuff.ru/balance/ads",
                    data="",
                    headers={
                        "Accept": "*/*",
                        "X-CSRF-TOKEN": self.csrf_token,
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": "https://mangabuff.ru/balance"
                    },
                    timeout=10
                )

                # Handle 429 rate limit cooldown
                if ad_res.status_code == 429:
                    for _ in range(int(20 * 4)):
                        if not self.running:
                            break
                        time.sleep(0.25)
                    if not self.running:
                        break
                    ad_res = self.session.post(
                        "https://mangabuff.ru/balance/ads",
                        data="",
                        headers={
                            "Accept": "*/*",
                            "X-CSRF-TOKEN": self.csrf_token,
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": "https://mangabuff.ru/balance"
                        },
                        timeout=10
                    )

                if ad_res.status_code == 200:
                    watched += 1
                    diamonds += 7
                else:
                    self.log(f"⚠️ Ad request failed with status {ad_res.status_code}")
                    break

            if watched > 0:
                self.log(tr("log_ads_watched", count=(count + watched), diamonds=diamonds))
                self.notifier.notify_ads_watched(count + watched, diamonds)
        except Exception as e:
            self.log(f"⚠️ watch_daily_ads error: {e}")

    def get_reading_stats(self, soup=None):
        """Parse current daily chapter reading progress and card drops from /balance."""
        try:
            if soup is None:
                res = self.session.get("https://mangabuff.ru/balance", timeout=10)
                if res.status_code != 200:
                    return None
                soup = BeautifulSoup(res.text, "html.parser")

            chapters_read = 0
            chapters_max = 75
            for span in soup.find_all(string=lambda t: t and "Главы" in t):
                parent = span.parent
                grand = parent.parent if parent else None
                text = grand.get_text(strip=True) if grand else ""
                m = re.search(r"(\d+)\s*/\s*(\d+)", text)
                if m:
                    chapters_read = int(m.group(1))
                    chapters_max = int(m.group(2))
                    break

            cards_found = 0
            cards_max = 10
            card_ready = True
            card_cooldown = None

            drop_container = soup.find(class_=lambda c: c and "wallet-panel__drop" in c)
            if drop_container:
                tooltip = drop_container.get("data-tooltip", "")
                m_cd = re.search(r"через\s*([^.]+)", tooltip)
                if m_cd:
                    card_cooldown = m_cd.group(1).strip()

                wait_state = drop_container.find(class_=lambda c: c and "drop-state--wait" in c)
                if wait_state or card_cooldown:
                    card_ready = False

            drop_div = soup.find(class_="wallet-panel__drop-text")
            if drop_div:
                m = re.search(r"(\d+)\s*из\s*(\d+)", drop_div.get_text())
                if m:
                    cards_found = int(m.group(1))
                    cards_max = int(m.group(2))

            return {
                "chapters_read": chapters_read,
                "chapters_max": chapters_max,
                "cards_found": cards_found,
                "cards_max": cards_max,
                "card_ready": card_ready,
                "card_cooldown": card_cooldown
            }
        except Exception as e:
            print(f"get_reading_stats exception: {e}")
            return None

    def read_manga_chapters(self, target_count=None):
        """Read manga chapters to farm bonus cards, sharpening scrolls, and 75-chapter daily quest."""
        if not DataManager.get_setting("reading_enabled", True):
            return

        if not self.running:
            return

        self.log(tr("log_reading_check"))
        stats = self.get_reading_stats()
        if stats:
            if stats["chapters_read"] >= stats["chapters_max"] and stats["cards_found"] >= stats["cards_max"]:
                self.log(tr("log_reading_limit_reached", chapters=stats["chapters_read"], cards=stats["cards_found"]))
                return

            wait_card_cd = DataManager.get_setting("reading_wait_card_cooldown", False)
            if wait_card_cd and not stats.get("card_ready", True):
                cd_msg = stats.get("card_cooldown") or "ожидание"
                self.log(f"⏳ Бонусные карты на кулдауне ({cd_msg}). Пропуск чтения глав (включен режим ожидания кулдауна).")
                return

        limit_setting = int(DataManager.get_setting("reading_chapters_limit", 20))
        if target_count is None:
            target_count = limit_setting

        remaining_today = max(0, stats["chapters_max"] - stats["chapters_read"]) if stats else 75
        target_count = min(target_count, remaining_today)
        if target_count <= 0:
            if stats:
                self.log(tr("log_reading_limit_reached", chapters=stats["chapters_read"], cards=stats["cards_found"]))
            return

        delay_min = float(DataManager.get_setting("reading_delay_min", 18.0))
        delay_max = float(DataManager.get_setting("reading_delay_max", 32.0))

        state = DataManager.load_reading_state() or {}
        read_history = set(state.get("history", []))
        progress_map = state.get("progress", {})

        manga_list = state.get("manga_list") or [
            "slabeishii-geroi",
            "mech-razyashchego-groma",
            "geroi-vernulsya",
            "plamya-beschislennyh-nevzgod",
            "tochka-zreniya-vsevedushchego-chitatelya",
            "nachalo-posle-konca",
            "podnyatie-urovnya-v-odinochku",
            "ubijca-drakonov",
            "svinarnik",
            "eleceed",
            "vozvrashenie-velikogo-mudreca-posle-4000-let",
            "oruzheinyi-baron",
            "ya-obnovil-svoi-navyki-do-maksimuma",
            "ubijca-geroev",
            "mag-kotoryi-poglyotil-drakona",
            "voshozhdenie-v-tenyax",
            "vysshii-mag",
            "magicheskii-imperator",
            "pirat-sudby",
            "doktor-drevnih-vremen",
            "master-magii",
            "reinkarnaciya-bezrabotnogo",
            "chernyi-klever",
            "chelovek-benzopila",
            "klinok-rassekajushij-demonov",
            "magicheskaya-bitva",
            "semya-shpiona",
            "adskiy-ray",
            "monstr-nomer-vosem",
            "kaiju-no-8",
            "bluelock"
        ]
        manga_idx = state.get("manga_idx", 0)

        chapters_read_session = 0
        cards_gained_session = 0
        scrolls_gained_session = 0
        buffer = []

        try:
            attempts = 0
            while chapters_read_session < target_count and self.running and attempts < len(manga_list):
                current_slug = manga_list[manga_idx % len(manga_list)]
                attempts += 1

                title_url = f"https://mangabuff.ru/manga/{current_slug}"
                res_title = self.session.get(title_url, timeout=10)
                if res_title.status_code != 200:
                    manga_idx += 1
                    continue

                soup_title = BeautifulSoup(res_title.text, "html.parser")
                ch_links = []
                pattern = re.compile(rf"/manga/{re.escape(current_slug)}/(\d+)/(\d+)")
                for a in soup_title.find_all("a", href=True):
                    href = a["href"]
                    m = pattern.search(href)
                    if m:
                        vol = int(m.group(1))
                        ch = int(m.group(2))
                        full_url = href if href.startswith("http") else f"https://mangabuff.ru{href}"
                        ch_links.append((vol, ch, full_url))

                # Deduplicate and sort chronologically by (volume, chapter)
                ch_links = sorted(list(set(ch_links)), key=lambda x: (x[0], x[1]))

                # Check saved sequential progress for this manga
                prog = progress_map.get(current_slug, {})
                last_v = prog.get("vol", 0)
                last_c = prog.get("ch", 0)

                # Prioritize chapters strictly after our last read chapter
                unread_chapters = [
                    item for item in ch_links
                    if item[2] not in read_history and (item[0] > last_v or (item[0] == last_v and item[1] > last_c))
                ]
                # Fallback to any unread chapter in this title
                if not unread_chapters:
                    unread_chapters = [item for item in ch_links if item[2] not in read_history]

                if not unread_chapters:
                    # All chapters of this manga completed, advance to next title
                    manga_idx += 1
                    continue

                for vol, ch, ch_url in unread_chapters:
                    if chapters_read_session >= target_count or not self.running:
                        break

                    ch_res = self.session.get(ch_url, timeout=10)
                    if ch_res.status_code != 200:
                        continue

                    m_ch = re.search(r"window\.current_chapter\s*=\s*({[^;]+});", ch_res.text)
                    if not m_ch:
                        continue

                    try:
                        ch_data = json.loads(m_ch.group(1))
                        manga_id = ch_data.get("id")
                        chapter_id = ch_data.get("chapter_id")
                    except Exception:
                        continue

                    if not manga_id or not chapter_id:
                        continue

                    meta_csrf = BeautifulSoup(ch_res.text, "html.parser").find("meta", attrs={"name": "csrf-token"})
                    if meta_csrf and meta_csrf.get("content"):
                        self.csrf_token = meta_csrf.get("content")

                    # Humanized reading delay (simulates page-turning and reading time)
                    delay = random.uniform(delay_min, delay_max)
                    self.log(f"📖 Читаем «{current_slug}» (т.{vol} гл.{ch})... имитация чтения ({delay:.0f}с)")
                    for _ in range(int(delay * 4)):
                        if not self.running:
                            break
                        time.sleep(0.25)

                    if not self.running:
                        break

                    buffer.append({
                        "manga_id": manga_id,
                        "chapter_id": chapter_id,
                        "url": ch_url,
                        "vol": vol,
                        "ch": ch,
                        "slug": current_slug
                    })

                    # Batch submit (every 1-2 chapters)
                    if len(buffer) >= 2 or (chapters_read_session + len(buffer)) >= target_count:
                        payload = {}
                        for bi, b_item in enumerate(buffer):
                            payload[f"items[{bi}][manga_id]"] = b_item["manga_id"]
                            payload[f"items[{bi}][chapter_id]"] = b_item["chapter_id"]

                        headers = {
                            "Accept": "*/*",
                            "X-CSRF-TOKEN": self.csrf_token,
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": buffer[-1]["url"]
                        }

                        post_res = self.session.post(
                            "https://mangabuff.ru/addHistory?r=702",
                            data=payload,
                            headers=headers,
                            timeout=10
                        )

                        # Handle rate limit (429) backoff
                        if post_res.status_code == 429:
                            retry_after = int(post_res.headers.get("Retry-After", 4))
                            time.sleep(retry_after + 1)
                            post_res = self.session.post(
                                "https://mangabuff.ru/addHistory?r=702",
                                data=payload,
                                headers=headers,
                                timeout=10
                            )

                        if post_res.status_code == 200:
                            chapters_read_session += len(buffer)
                            for b in buffer:
                                read_history.add(b["url"])
                                progress_map[b["slug"]] = {"vol": b["vol"], "ch": b["ch"]}

                            state["manga_idx"] = manga_idx
                            state["progress"] = progress_map
                            state["history"] = list(read_history)[-2000:]
                            DataManager.save_reading_state(state)

                            # Parse drop responses
                            try:
                                resp_data = post_res.json()
                                if isinstance(resp_data, dict) and resp_data.get("name"):
                                    card_name = resp_data.get("name")
                                    card_img = resp_data.get("image")
                                    cards_gained_session += 1
                                    current_cards = (stats["cards_found"] if stats else 0) + cards_gained_session
                                    self.log(f"🃏 ВЫПАЛА КАРТА: «{card_name}»! ({current_cards}/10 сегодня)")
                                    self.notifier.notify_card_dropped(card_name, card_img, current_cards)
                                    wait_card_cd = DataManager.get_setting("reading_wait_card_cooldown", True)
                                    if wait_card_cd:
                                        self.log("🎁 Бонусная карта получена! Кулдаун активирован (~45–60 мин). Остановка чтения для экономии глав.")
                                        buffer.clear()
                                        return True

                                if isinstance(resp_data, dict) and resp_data.get("scroll"):
                                    scroll_name = resp_data.get("scroll", "Свиток заточки")
                                    scrolls_gained_session += 1
                                    self.log(f"📜 ВЫПАЛ СВИТОК: «{scroll_name}»!")
                                    self.notifier.notify_scroll_dropped(scroll_name, resp_data.get("rank"))
                            except Exception:
                                pass

                            self.log(tr("log_reading_batch", count=chapters_read_session, current=chapters_read_session, total=target_count))

                        buffer.clear()

            state["manga_idx"] = manga_idx
            state["progress"] = progress_map
            state["history"] = list(read_history)[-2000:]
            DataManager.save_reading_state(state)

            if chapters_read_session > 0:
                final_stats = self.get_reading_stats()
                final_today = final_stats["chapters_read"] if final_stats else None
                self.log(tr("log_reading_summary", chapters=chapters_read_session, cards=cards_gained_session))
                if cards_gained_session > 0 or scrolls_gained_session > 0:
                    self.notifier.notify_reading_summary(
                        chapters_read_session,
                        total_today=final_today,
                        cards_gained=cards_gained_session,
                        scrolls_gained=scrolls_gained_session
                    )
            return False
        except Exception as e:
            self.log(f"⚠️ read_manga_chapters error: {e}")
            return False

    def check_and_claim_quests(self):
        """Parse all daily quests on /battle and claim any completed unclaimed quests."""
        try:
            res = self.session.get("https://mangabuff.ru/battle", timeout=10)
            if res.status_code != 200:
                return
            soup = BeautifulSoup(res.text, "html.parser")

            root = soup.find(class_="battle-home")
            claim_url = root.get("data-daily-claim-url") if root else "https://mangabuff.ru/battle/daily-quests/claim"

            claimed_count = 0
            last_essence = None
            last_energy = None

            for article in soup.find_all("article"):
                qid = article.get("data-daily-quest-id")
                if not qid:
                    continue
                classes = article.get("class", [])
                completed = "battle-home__quest--completed" in classes
                claimed = "battle-home__quest--claimed" in classes

                if completed and not claimed:
                    data = self.claim_quest(qid, claim_url)
                    if data and data.get("success"):
                        claimed_count += 1
                        last_essence = data.get("essence")
                        last_energy = data.get("awakening_energy")
                        time.sleep(random.uniform(1.0, 2.0))

            if claimed_count > 0:
                self.log(tr("log_quests_claimed", count=claimed_count))
                self.notifier.notify_quests_claimed(claimed_count, last_essence, last_energy)
        except Exception as e:
            print(f"check_and_claim_quests exception: {e}")

    def claim_quest(self, quest_id, claim_url="https://mangabuff.ru/battle/daily-quests/claim"):
        """Claim a single completed daily quest using application/x-www-form-urlencoded."""
        if not quest_id:
            return None
        try:
            payload = {
                "_token": self.csrf_token,
                "user_daily_quest_id": quest_id
            }
            headers = {
                "X-CSRF-TOKEN": self.csrf_token,
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://mangabuff.ru/battle",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
            }
            res = self.session.post(claim_url, data=payload, headers=headers, timeout=10)
            if res.status_code == 200:
                try:
                    return res.json()
                except ValueError:
                    return None
            return None
        except Exception as e:
            print(f"claim_quest({quest_id}) exception: {e}")
            return None

    def auto_upgrade_pickaxe(self):
        """Automatically check and buy pickaxe upgrade and strong hit if ore balance allows."""
        if not DataManager.get_setting("auto_upgrade", True):
            return False

        try:
            res = self.session.get(CONFIG["urls"]["game"], timeout=10)
            if res.status_code != 200:
                return False
            html = res.text

            # 1. Check for "Сильный удар" (Strong Hit, 50k ore)
            if 'mine-shop__hit-power-btn' in html and self.current_balance >= 50000:
                buy_res = self.session.post(
                    "https://mangabuff.ru/mine/buy-strong-hit",
                    data={},
                    headers={"X-CSRF-TOKEN": self.csrf_token, "X-Requested-With": "XMLHttpRequest"},
                    timeout=10
                )
                if buy_res.status_code == 200:
                    self.log(tr("log_strong_hit_ok"))
                    self.notifier.notify_upgrade("Сильный удар (25 ударов/клик)", cost=50000)
                    self.current_balance -= 50000
                    self.update_stats(balance=self.current_balance)

            # 2. Check for Pickaxe Upgrade (max lvl 15)
            lvl_match = re.search(r'mine-shop__card--upgrade.*?Текущий уровень:\s*<b>?(\d+)</b>?', html, re.DOTALL | re.IGNORECASE)
            current_lvl = int(lvl_match.group(1)) if lvl_match else None

            if 'mine-shop__upgrade-btn' in html and (current_lvl is None or current_lvl < 15):
                cost = (2000 + current_lvl * 1000) if current_lvl is not None else None
                price_match = re.search(r'mine-shop__card--upgrade.*?mine-shop__price.*?>Цена:\s*([\d\s]+)', html, re.DOTALL | re.IGNORECASE)
                if price_match:
                    parsed_cost = parse_smart_number(price_match.group(1))
                    if parsed_cost > 0:
                        cost = parsed_cost

                if cost and self.current_balance >= cost:
                    self.log(tr("log_buy_upgrade", price=f"{cost:,}"))
                    upg_res = self.session.post(
                        "https://mangabuff.ru/mine/upgrade",
                        data={},
                        headers={"X-CSRF-TOKEN": self.csrf_token, "X-Requested-With": "XMLHttpRequest"},
                        timeout=10
                    )
                    if upg_res.status_code == 200:
                        try:
                            upg_data = upg_res.json()
                            new_lvl = upg_data.get("pickaxe_level", current_lvl + 1 if current_lvl else "?")
                            new_ore = upg_data.get("ore", self.current_balance - cost)
                            self.current_balance = new_ore
                            self.update_stats(balance=self.current_balance)
                            self.log(tr("log_upgrade_ok"))
                            self.notifier.notify_upgrade("Кирка", level=new_lvl, cost=cost)
                            return True
                        except Exception:
                            pass
        except Exception as e:
            print(f"auto_upgrade_pickaxe exception: {e}")
        return False

    def check_tower_expedition(self):
        """Check Abyss Tower (/tower), claim completed expeditions, and start a new one if idle."""
        if not DataManager.get_setting("tower_enabled", True):
            return None

        self.log(tr("log_tower_check"))
        try:
            res = self.session.get("https://mangabuff.ru/tower", timeout=12)
            if res.status_code != 200:
                self.log(f"⚠️ Tower page status: {res.status_code}")
                return None

            soup = BeautifulSoup(res.text, "html.parser")
            root = soup.find(class_="mbf-abyss-tower") or soup.find(attrs={"data-tower-root": True})
            if not root:
                root = soup.find(attrs={"data-claim-url": True})

            if not root:
                self.log("⚠️ Could not locate Tower container.")
                return None

            claim_url = root.get("data-claim-url", "https://mangabuff.ru/tower/expedition/claim")
            start_url = root.get("data-start-url", "https://mangabuff.ru/tower/expedition/start")
            is_active = str(root.get("data-expedition-active", "0")) == "1"
            is_complete = str(root.get("data-expedition-complete", "0")) == "1"

            timer_el = soup.find(attrs={"data-tower-timer": True}) or soup.find(class_="mbf-abyss-tower__timer-value")
            if timer_el and timer_el.get("data-remaining-seconds"):
                remaining_seconds = int(timer_el.get("data-remaining-seconds"))
                time_str = timer_el.get_text(strip=True)
            else:
                remaining_seconds = int(root.get("data-remaining-seconds", 0) or 0)
                hours = remaining_seconds // 3600
                minutes = (remaining_seconds % 3600) // 60
                time_str = f"{hours}ч {minutes}м" if hours > 0 else f"{minutes}м"

            # 1. Claim completed rewards
            if is_complete:
                claim_res = self.session.post(
                    claim_url,
                    json={},
                    headers={
                        "Content-Type": "application/json",
                        "X-CSRF-TOKEN": self.csrf_token,
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": "https://mangabuff.ru/tower"
                    },
                    timeout=10
                )
                if claim_res.status_code == 200:
                    try:
                        c_data = claim_res.json()
                        rewards = c_data.get("data", {}).get("rewards", {})
                        diamonds = rewards.get("diamonds", 0)
                        crystals = rewards.get("dark_crystals", 0)
                        lvl_ups = len(c_data.get("data", {}).get("level_ups", []))
                        self.log(tr("log_tower_claimed", diamonds=diamonds, crystals=crystals))
                        self.notifier.notify_tower_claimed(diamonds, crystals, lvl_ups)
                        is_active = False
                    except Exception as e:
                        self.log(f"⚠️ Tower claim parse error: {e}")
                else:
                    self.log(f"⚠️ Tower claim failed with status {claim_res.status_code}")

            # 2. Start a new expedition if idle
            if not is_active:
                raw_ids = root.get("data-selected-card-ids")
                card_ids = []
                if raw_ids:
                    try:
                        parsed = json.loads(raw_ids) if isinstance(raw_ids, str) else raw_ids
                        if isinstance(parsed, list):
                            card_ids = [int(x) for x in parsed if x]
                    except Exception:
                        pass

                if not card_ids:
                    saved_squad = DataManager.get_setting("tower_squad")
                    if isinstance(saved_squad, list) and len(saved_squad) > 0:
                        card_ids = [int(x) for x in saved_squad]

                if not card_ids:
                    slot_cards = soup.select(".mbf-tower-slot[data-card-user-id]")
                    for sc in slot_cards:
                        cid = sc.get("data-card-user-id")
                        if cid and int(cid) not in card_ids:
                            card_ids.append(int(cid))

                if card_ids:
                    DataManager.set_setting("tower_squad", card_ids)

                duration_mins = int(root.get("data-selected-duration") or DataManager.get_setting("tower_duration") or 720)
                difficulty = root.get("data-selected-difficulty") or DataManager.get_setting("tower_difficulty") or "nightmare"
                route = root.get("data-selected-route") or "depths"
                order = root.get("data-selected-order") or "attack"

                if card_ids:
                    payload = {
                        "card_user_ids": card_ids,
                        "duration_minutes": duration_mins,
                        "route_type": route,
                        "difficulty_type": difficulty,
                        "order_type": order,
                        "resonance_offer_token": "",
                        "resonance_accept": 0
                    }
                    start_res = self.session.post(
                        start_url,
                        json=payload,
                        headers={
                            "Content-Type": "application/json",
                            "X-CSRF-TOKEN": self.csrf_token,
                            "X-Requested-With": "XMLHttpRequest",
                            "Referer": "https://mangabuff.ru/tower"
                        },
                        timeout=10
                    )
                    if start_res.status_code in (200, 201):
                        try:
                            s_data = start_res.json()
                            ends_at = s_data.get("data", {}).get("ends_at", "")
                            dur_hours = duration_mins // 60
                            self.log(tr("log_tower_started", diff=difficulty.capitalize(), dur=dur_hours))
                            self.notifier.notify_tower_started(difficulty.capitalize(), dur_hours, ends_at)
                            return duration_mins * 60
                        except Exception:
                            return duration_mins * 60
                    else:
                        self.log(f"⚠️ Tower start failed with status {start_res.status_code}")
                        return 3600
                else:
                    self.log("ℹ️ No cards selected for Tower expedition. Please select squad in browser once.")
                    return 3600
            else:
                self.log(tr("log_tower_running", left=time_str))
                return remaining_seconds

        except Exception as e:
            self.log(f"⚠️ Tower error: {e}")
            return 3600

    def start_battle(self):
        """POST to the matchmaking endpoint; return redirect_url or error code."""
        try:
            res = self.session.post(
                "https://mangabuff.ru/battle/fight/start",
                headers={
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/battle"
                },
                timeout=10
            )

            # Check for Cloudflare Turnstile captcha
            if "turnstile" in res.text.lower() or "requestactioncaptcha" in res.text.lower():
                self.log(tr("log_turnstile_detected"))
                self.notifier.notify_alert("Капча Cloudflare Turnstile", "Бот обнаружил проверку капчи на сайте. Пожалуйста, пройдите её в браузере.")
                return "CAPTCHA"

            if res.status_code == 419:
                if self._handle_session_expired():
                    return self.start_battle()
                return None

            if res.status_code in (400, 422):
                return None

            try:
                data = res.json()
            except ValueError:
                return None

            if data.get("success") is False or data.get("status") is False:
                if data.get("energy_left") == 0 or data.get("energy") == 0:
                    return "NO_ENERGY"
                return None

            redirect_url = data.get("redirect_url")
            return redirect_url
        except Exception:
            return None

    def check_battle_result(self, match_url):
        """Return a tuple (earned_essence, is_win) and detect jackpot card drops."""
        try:
            if not match_url:
                return None

            res = self.session.get(
                match_url if match_url.startswith("http") else "https://mangabuff.ru" + match_url,
                headers={
                    "X-CSRF-TOKEN": self.csrf_token,
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://mangabuff.ru/battle"
                },
                timeout=10
            )

            if res.status_code != 200:
                return None

            # 1. Parse earned essence
            m = re.search(r'"essence"\s*:\s*(\d+)', res.text)
            if not m:
                return None
            earned_essence = int(m.group(1))

            # 2. Parse win status
            is_win = None
            mw = re.search(r'"is_win"\s*:\s*(true|false)', res.text)
            if mw:
                is_win = (mw.group(1).lower() == "true")

            # 3. Detect high-value card drops in battle result
            card_match = re.search(r'"card"\s*:\s*(\{.+?\})[,}]', res.text)
            if card_match:
                try:
                    card_dict = json.loads(card_match.group(1))
                    self.check_card_rarity({"card": card_dict})
                except Exception:
                    pass
            else:
                rarity_match = re.search(r'"potential_rarity"\s*:\s*"([^"]+)"', res.text, re.IGNORECASE)
                name_match = re.search(r'"(?:name|title)"\s*:\s*"([^"]+)"', res.text, re.IGNORECASE)
                if rarity_match:
                    r_val = rarity_match.group(1).lower()
                    c_name = name_match.group(1) if name_match else "Card"
                    self.check_card_rarity({"card": {"potential_rarity": r_val, "name": c_name}})

            return (earned_essence, is_win)
        except Exception as e:
            print(f"check_battle_result exception: {e}")
            return None

    def check_card_rarity(self, data):
        """Detect high-value card drops and preserve them in jackpot_cards.json."""
        card = data.get("card") if isinstance(data, dict) else None
        if not isinstance(card, dict):
            return
        rarity = card.get("potential_rarity")
        if rarity and str(rarity).lower() in TARGET_RARITIES:
            card_name = card.get("name") or card.get("title") or "Карточка"
            print(f"🔥 JACKPOT: Dropped a card with tier: {rarity}! ({card_name})")
            self.log(tr("log_jackpot", rarity=str(rarity).upper()))
            self.notifier.notify_jackpot(str(rarity), card_name)
            self._preserve_card(card)

    def _preserve_card(self, card):
        """Save a jackpot card to disk so it is never consumed or modified."""
        try:
            path = os.path.join(BASE_DIR, "jackpot_cards.json")
            existing = DataManager.load_json(path)
            if not isinstance(existing, list):
                existing = []
            existing.append(card)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=4, ensure_ascii=False)
            print(f"💾 Preserved jackpot card -> {path}")
        except Exception as e:
            print(f"Preserve card error: {e}")

    def check_active_deck(self):
        """Check for active deck marker before battling."""
        try:
            res = self.session.get("https://mangabuff.ru/battle", timeout=10)
            if res.status_code != 200:
                return True
            soup = BeautifulSoup(res.text, "html.parser")
            text = soup.get_text(" ", strip=True).lower()
            empty_markers = [
                "нет активной колод", "колода пуст", "no active deck",
                "deck is empty", "установите колод", "set a deck"
            ]
            if any(m in text for m in empty_markers):
                print("⚠️ Possible empty deck detected — proceeding with battles anyway.")
            return True
        except Exception:
            return True

    def _handle_session_expired(self):
        """Re-authenticate when the server rejects the session."""
        self.log("♻️ Session expired. Re-logging in...")
        if self.login_and_steal_keys():
            self.log("✅ Re-login successful! Resuming...")
            return True
        self.log("❌ Re-login failed. Stopping.")
        return False

    def _find_active_claim_url(self):
        """Parse /balance HTML for the currently CLAIMABLE daily-reward button."""
        try:
            res = self.session.get("https://mangabuff.ru/balance", timeout=10)
            if res.status_code != 200:
                return None
            soup = BeautifulSoup(res.text, "html.parser")

            meta = soup.find("meta", attrs={"name": "csrf-token"})
            if meta and meta.get("content"):
                self.csrf_token = meta.get("content")

            claim_buttons = []
            for el in soup.find_all(string=lambda t: t and "Забрать" in t):
                node = el.parent
                if node:
                    claim_buttons.append(node)

            for btn in claim_buttons:
                link = None
                for ancestor in [btn] + list(btn.find_parents()):
                    if ancestor is None:
                        continue
                    name = getattr(ancestor, "name", None)
                    if name in ("a", "button", "form") or ancestor.get("href") or ancestor.get("data-day"):
                        link = ancestor
                        break
                if link is None:
                    link = btn

                if link.get("disabled") is not None:
                    continue
                classes = " ".join(link.get("class", []))
                if "disabled" in classes.lower():
                    continue

                parent = link.find_parent("div", class_="daily-rewards-item")
                if parent is None:
                    parent = link.parent
                if parent is not None:
                    pclasses = " ".join(parent.get("class", [])).lower()
                    if any(m in pclasses for m in ("completed", "claimed", "locked", "inactive", "disabled", "taken")):
                        continue

                href = link.get("href") or link.get("action")
                if href:
                    return href if href.startswith("http") else ("https://mangabuff.ru" + href)

                day = link.get("data-day")
                if day is None and parent is not None:
                    day = parent.get("data-day")
                if day:
                    return f"https://mangabuff.ru/balance/claim/{day}"

        except Exception as e:
            print(f"_find_active_claim_url exception: {e}")
        return None

    def run_farm_loop(self):
        """Continuously grind battles until the daily essence limit is reached (3 consecutive +0)."""
        if not self.check_active_deck():
            return

        # Claim daily quests before battling
        self.check_and_claim_quests()

        self.log(tr("log_farm_start"))

        battles_done = 0
        total_essence_ = 0 
        wins = 0
        losses = 0
        consecutive_zero_essence = 0
        consecutive_unknown = 0
        next_micro_break = random.randint(10, 15)
        cap_reached = False

        while self.running:
            time.sleep(random.uniform(4.5, 12.2))

            match_url = self.start_battle()
            if match_url == "NO_ENERGY":
                print("Out of battle energy.")
                break
            elif match_url == "CAPTCHA":
                break
            elif not match_url:
                print("Matchmaking transient — backing off 25s, will retry.")
                time.sleep(25)
                continue

            result = self.check_battle_result(match_url)
            if result is None:
                consecutive_unknown += 1
                if consecutive_unknown >= 3:
                    self.log("⚠️ 3 consecutive unparseable battle results — stopping.")
                    break
                time.sleep(random.uniform(10, 15))
                continue

            consecutive_unknown = 0
            earned_essence, is_win = result

            # STOP ON FIRST 0 ESSENCE BATTLE: Daily essence limit reached
            if earned_essence == 0:
                outcome = "Win" if is_win else "Loss"
                print(f"[BATTLE] Result: {outcome} | Earned: +0. Daily cap reached, stopping.")
                self.log("Daily essence limit reached (+0)")
                cap_reached = True
                break

            battles_done += 1
            total_essence_ += earned_essence
            if is_win:
                wins += 1
            else:
                losses += 1

            outcome = "Win" if is_win else "Loss"
            print(f"[BATTLE] #{battles_done} {outcome} | +{earned_essence} essence (Total: +{total_essence_})")

            # Micro-break
            if battles_done >= next_micro_break:
                brk = random.uniform(35, 75)
                print(f"😴 Micro-break ({brk:.0f}s)...")
                time.sleep(brk)
                next_micro_break = battles_done + random.randint(10, 15)

            delay = random.uniform(10, 18)
            time.sleep(delay)

        # Summary
        if battles_done > 0:
            summary = tr("log_battle_summary", done=battles_done, wins=wins, losses=losses, essence=total_essence_)
            self.log(summary)
            self.notifier.notify_battle_summary(battles_done, wins, losses, total_essence_, cap_reached)
        else:
            self.log("No battles completed during this session.")

        # Claim any quests completed DURING the battles
        self.check_and_claim_quests()

    def stop(self):
        self.running = False
        self.log(tr("log_stopping"))

    def check_status_only(self):
        self.auth_log_buffer.append(tr("log_session_load"))
        if not self.validate_session():
            if not self.login_and_steal_keys():
                return

        self.auth_log_buffer.append(tr("log_source_check"))
        try:
            res = self.session.get(CONFIG["urls"]["game"], timeout=10)
            if res.status_code == 200:
                html = res.text
                ore_raw, hits_raw, cost_raw = "0", "0", None
                b_cls = CONFIG["selectors"]["balance_class"]
                ore_match = re.search(f'class="[^"]*{b_cls}[^"]*">\\s*([\\d\\.\\s,kKmM]+)\\s*<', html)
                if ore_match: ore_raw = ore_match.group(1)

                e_cls = CONFIG["selectors"]["energy_class"]
                hits_match = re.search(f'class="[^"]*{e_cls}[^"]*">\\s*([\\d\\s]+)\\s*<', html)
                if hits_match: hits_raw = hits_match.group(1)

                price_match = re.search(r'class="[^"]*cost[^"]*">\s*([\d\.\s,kKmM]+)\s*<', html)
                if not price_match:
                    price_match = re.search(r'upgrade-btn.*?<span>([\d\.\s,kKmM]+)</span>', html, re.IGNORECASE)
                if price_match:
                    cost_raw = price_match.group(1)

                ore = parse_smart_number(ore_raw)
                hits = parse_smart_number(hits_raw)

                self.current_balance = ore
                self.update_stats(energy=hits, balance=ore)
                self.auth_log_buffer.append(tr("log_stat_energy", val=hits))
                self.auth_log_buffer.append(tr("log_stat_balance", val=f"{ore:,}"))

                if cost_raw:
                    self.auth_log_buffer.append(tr("log_upgrade_cost", cost=cost_raw))
                else:
                    self.auth_log_buffer.append(tr("log_upgrade_status_max"))

            else:
                self.log(f"⚠️ API Error: {res.status_code}")
        except Exception as e:
            self.log(tr("log_error_generic", e=e))

        if self.auth_log_buffer:
            header = tr("log_startup_header")
            body = "🔸 " + "\n🔸 ".join(self.auth_log_buffer)
            self.log(header + body)
            self.auth_log_buffer.clear()

        # Check and report Tower status
        self.check_tower_expedition()

        # Check and report Daily Ads & Reading stats
        try:
            res_bal = self.session.get("https://mangabuff.ru/balance", timeout=10)
            if res_bal.status_code == 200:
                soup_bal = BeautifulSoup(res_bal.text, "html.parser")
                ads_btn = soup_bal.find(class_=lambda c: c and "user-quest__watch-ads-btn" in c)
                if ads_btn:
                    ads_cnt = ads_btn.get("data-count", "0")
                    self.log(f"📺 Реклама: {ads_cnt}/3 просмотрено")
                r_stats = self.get_reading_stats(soup=soup_bal)
                if r_stats:
                    cd_info = ""
                    if r_stats.get("card_ready"):
                        cd_info = " (✅ Карта готова к дропу)"
                    elif r_stats.get("card_cooldown"):
                        cd_info = f" (⏳ Кулдаун карты: {r_stats.get('card_cooldown')})"
                    self.log(f"📖 Чтение глав: {r_stats['chapters_read']}/{r_stats['chapters_max']} глав | {r_stats['cards_found']}/{r_stats['cards_max']} бонусных карт{cd_info}")
        except Exception:
            pass

        self.log(tr("log_done"))

    def _run_mining(self):
        """Phase 1: grind ore via /mine/hit until energy is depleted."""
        self.log(tr("log_mining_start"))

        clicks = 0
        total_added = 0
        consecutive_errors = 0
        initial_hits = None

        while self.running:
            try:
                response = self.session.post(self.API_URL, json={"hits": 1}, timeout=5)

                if response.status_code == 200:
                    try:
                        data = response.json()
                        if data.get('status') is False:
                            self.log(f"⚠️ Mine rejected: {data}")
                            time.sleep(2)
                            continue

                        ore = data.get('ore', 0)
                        hits_left = data.get('hits_left', 0)
                        added = data.get('added', 0)

                        if initial_hits is None:
                            initial_hits = max(hits_left, 100)

                        self.current_balance = ore
                        clicks += 1
                        total_added += added
                        consecutive_errors = 0

                        self.update_stats(energy=hits_left, balance=ore)
                        prog = 1.0 - (hits_left / float(initial_hits))
                        self.update_progress(max(0.0, min(1.0, prog)))

                        if hits_left <= 0:                           
                            self.update_stats(energy=0, balance=ore)
                            break

                        time.sleep(random.uniform(0.30, 0.50))

                    except Exception as e:
                        self.log(f"⚠️ JSON Error: {e}")
                        time.sleep(2)
                        continue

                else:
                    if response.status_code == 403:
                        self.log("⚡ Ore energy depleted (403). Moving to battles.")
                        self.update_stats(energy=0)
                        break
                    elif response.status_code == 419:
                        self.log("♻️ Session expired (419). Auto-refreshing...")
                        if self.login_and_steal_keys():
                            self.log("✅ Re-login successful! Resuming...")
                            consecutive_errors = 0
                            time.sleep(1)
                            continue
                        else:
                            self.log("❌ Re-login failed. Stopping.")
                            break
                    elif response.status_code == 429:
                        self.log("⚠️ Rate limited (429). Backing off 30s...")
                        time.sleep(30)
                        consecutive_errors = 0
                        continue
                    else:
                        self.log(f"⚠️ Server: {response.status_code}")
                        consecutive_errors += 1
                        time.sleep(2)
                        if consecutive_errors > 3:
                            self.log(tr("log_session_restart"))
                            if self.login_and_steal_keys():
                                consecutive_errors = 0
                                continue
                            break

            except requests.exceptions.Timeout:
                self.log(tr("log_timeout"))
            except Exception as e:
                self.log(tr("log_error_generic", e=e))
                time.sleep(1)

        if clicks > 0:
            self.log(tr("log_mining_summary", clicks=clicks, added=total_added))
            self.log(tr("log_stat_balance", val=f"{self.current_balance:,}"))
            self.notifier.notify_mining_summary(clicks, total_added, self.current_balance)

        # Check and purchase pickaxe upgrades with newly mined ore
        self.auto_upgrade_pickaxe()

    def run(self):
        """Main execution flow: claim daily, Tower expedition, mine ore, auto-upgrade, and farm battles."""
        self.running = True
        self.auth_log_buffer.append(tr("log_session_load"))

        # Step 1: Load saved session
        if not self.validate_session():
            self.auth_log_buffer.append(tr("log_session_expired"))
            if not self.login_and_steal_keys():
                self.running = False
                return

        # Step 2: Validate session with server
        if not self._validate_session_with_server():
            self.auth_log_buffer.append(tr("log_session_expired"))
            if not self.login_and_steal_keys():
                self.running = False
                return

        # Notify Telegram about start
        self.notifier.notify_session_start(self.email, ore=self.current_balance)

        # Claim daily reward
        self.claim_daily_reward()

        if self.auth_log_buffer:
            header = tr("log_startup_header")
            body = "🔸 " + "\n🔸 ".join(self.auth_log_buffer)
            self.log(header + body)
            self.auth_log_buffer.clear()

        # Watch daily ads (3x7 💎)
        self.watch_daily_ads()

        # Check Tower of Rift expeditions (claim & start)
        self.check_tower_expedition()

        # Phase 1: Ore mining & auto-upgrade
        self._run_mining()

        # Phase 2: Battle farming
        if self.running:
            self.run_farm_loop()

        # Phase 3: Manga chapter reading (bonus cards & quest)
        if self.running:
            self.read_manga_chapters()

        # Claim completed daily quests
        self.check_and_claim_quests()

        # Re-check Tower expedition status
        self.check_tower_expedition()

        self.running = False
        self.log(tr("log_mining_finish"))

    def run_daemon(self):
        """Smart event-driven scheduler daemon. Runs 24/7, sleeping until the earliest event."""
        self.running = True
        self.log("🚀 MangaBuff Miner — Запуск автономного Smart Daemon (24/7)")

        # Validate session at start
        if not self.validate_session() or not self._validate_session_with_server():
            if not self.login_and_steal_keys():
                self.log("❌ Первоначальная авторизация не удалась. Остановка демона.")
                self.running = False
                return

        if self.auth_log_buffer:
            header = tr("log_startup_header")
            body = "🔸 " + "\n🔸 ".join(self.auth_log_buffer)
            self.log(header + body)
            self.auth_log_buffer.clear()

        # Claim daily login reward if ready
        self.claim_daily_reward()

        while self.running:
            try:
                # 1. Mining & Energy check
                energy = 0
                res_game = self.session.get(CONFIG["urls"]["game"], timeout=10)
                if res_game.status_code == 200:
                    e_cls = CONFIG["selectors"]["energy_class"]
                    hits_match = re.search(f'class="[^"]*{e_cls}[^"]*">\\s*([\\d\\s]+)\\s*<', res_game.text)
                    if hits_match:
                        energy = parse_smart_number(hits_match.group(1))
                elif res_game.status_code in (401, 419):
                    self.login_and_steal_keys()

                if energy >= 15:
                    self.log(f"⚡ Накопилась энергия ({energy}). Запуск добычи руды и боев...")
                    self._run_mining()
                    if self.running:
                        self.run_farm_loop()
                    self.auto_upgrade_pickaxe()
                    self.check_and_claim_quests()

                # 2. Daily Ads check (3x7 💎)
                if self.running and DataManager.get_setting("ads_enabled", True):
                    self.watch_daily_ads()

                # 3. Abyss Tower check (claim rewards & restart 12h)
                tower_left_sec = 86400
                if self.running and DataManager.get_setting("tower_enabled", True):
                    t_sec = self.check_tower_expedition()
                    if t_sec is not None:
                        tower_left_sec = max(60, t_sec)

                # 4. Manga Reading check (cards & 75-chapter quest)
                card_wait_sec = 60 * 45
                if self.running and DataManager.get_setting("reading_enabled", True):
                    reading_stats = self.get_reading_stats()
                    if reading_stats:
                        cards_found = reading_stats.get("cards_found", 0)
                        cards_max = reading_stats.get("cards_max", 10)
                        card_ready = reading_stats.get("card_ready", False)
                        cd_str = reading_stats.get("card_cooldown") or ""

                        if cards_found >= cards_max:
                            # 10/10 cards reached for today! Wait until midnight MSK
                            card_wait_sec = get_seconds_until_midnight_msk()
                        elif card_ready:
                            self.log(f"🃏 Бонусная карта готова к дропу ({cards_found}/{cards_max})! Читаем главы...")
                            self.read_manga_chapters(target_count=10)
                            # After reading, re-evaluate cooldown
                            fresh_r = self.get_reading_stats()
                            if fresh_r:
                                if not fresh_r.get("card_ready", True):
                                    card_wait_sec = parse_card_cooldown_seconds(fresh_r.get("card_cooldown", "")) or (45 * 60)
                                else:
                                    card_wait_sec = 60
                        else:
                            card_wait_sec = parse_card_cooldown_seconds(cd_str) or (45 * 60)

                # 5. Energy recovery wait estimation (1 energy per ~3.5 min, target 15 = ~50 min)
                mine_wait_sec = 60 * 50 if energy < 15 else 60

                # 6. Ads wait (resets at midnight MSK)
                ads_wait_sec = get_seconds_until_midnight_msk()

                candidates = [
                    ("Карта", card_wait_sec),
                    ("Шахта", mine_wait_sec),
                    ("Башня", tower_left_sec),
                    ("Реклама", ads_wait_sec)
                ]
                positive = [(name, s) for name, s in candidates if s > 0]
                if not positive:
                    next_name, sleep_sec = "Повторная проверка", 60
                else:
                    next_name, sleep_sec = min(positive, key=lambda x: x[1])

                # Bounded sleep: at least 60s, max 7200s (2h)
                sleep_sec = max(60, min(sleep_sec, 7200))
                # Add human jitter (+15..45s)
                sleep_sec += random.randint(15, 45)

                w_m = sleep_sec // 60
                w_s = sleep_sec % 60
                self.log(f"💤 Все задачи проверены. Ближайшее действие: {next_name} (~{w_m} мин {w_s} сек). Ухожу в сон.")

                # Heartbeat notification to Telegram once per cycle
                t_h = tower_left_sec // 3600
                t_m = (tower_left_sec % 3600) // 60
                tower_str = f"{t_h}ч {t_m}м" if t_h > 0 else f"{t_m}м"
                cards_cnt_str = f"{reading_stats.get('cards_found', 0)}/{reading_stats.get('cards_max', 10)}" if reading_stats else "?/10"
                ads_cnt = 0
                try:
                    res_bal_check = self.session.get("https://mangabuff.ru/balance", timeout=8)
                    if res_bal_check.status_code == 200:
                        btn = BeautifulSoup(res_bal_check.text, "html.parser").find(class_=lambda c: c and "user-quest__watch-ads-btn" in c)
                        if btn:
                            ads_cnt = int(btn.get("data-count", 0))
                except Exception:
                    pass

                self.notifier.notify_cycle_heartbeat(
                    energy=energy,
                    balance=self.current_balance,
                    ads_count=ads_cnt,
                    cards_count=cards_cnt_str,
                    tower_str=tower_str,
                    next_action=next_name,
                    next_wait_min=w_m
                )

                # Non-blocking sleep: checks self.running every 1 sec
                for _ in range(int(sleep_sec)):
                    if not self.running:
                        break
                    time.sleep(1)

            except Exception as e:
                self.log(f"⚠️ Ошибка в Daemon цикле: {e}")
                for _ in range(30):
                    if not self.running:
                        break
                    time.sleep(1)

    def _validate_session_with_server(self):
        """Make a lightweight GET request to verify the session is still valid."""
        try:
            res = self.session.get(CONFIG["urls"]["game"], timeout=10)
            if res.status_code == 200:
                if "/login" in res.url.lower() or "auth-form" in res.text.lower():
                    self.auth_log_buffer.append("⚠️ Session validation failed: redirected to login")
                    return False
                self.auth_log_buffer.append(tr("log_session_valid"))
                return True
            elif res.status_code in (401, 419, 403):
                self.auth_log_buffer.append(f"⚠️ Session validation failed with status {res.status_code}")
                return False
            else:
                self.auth_log_buffer.append(f"⚠️ Session validation returned status {res.status_code}")
                return False
        except Exception as e:
            self.auth_log_buffer.append(f"⚠️ Session validation error: {e}")
            return False
