# MangaMinerBot — Project Summary & Handoff

> Automation tool (Python) for **mangabuff.ru** — battles, daily rewards, quest claiming, and ore mining via the site's Laravel-backed API.

---

## 1. Architecture

| Layer | Technology | Notes |
|-------|-------------|-------|
| HTTP | `requests` + `requests.Session` | All API calls go through one shared session (cookie jar + headers persist). |
| Session mgmt | Laravel (server-side) | Auth via CSRF token scraped from `<meta name="csrf-token">`; session = cookie jar + `csrf` + `user_agent`, cached to disk. |
| Credentials | `user_data.json` | Holds `email`, `password`, `language`. **Never** hard-code tokens. |
| Session cache | `session_cache.json` | `{cookies, csrf, agent, timestamp}`. Reused until HTTP 419 (expired) → auto re-login. |
| Engine | `MangaMinerBot` class | One class owns auth, battle loop, quest logic, card filtering. |
| GUI | `customtkinter` (`App`, `LoginDialog`) | **Currently disabled** — script runs in CLI test mode (`__main__` block). |
| Parsing | `BeautifulSoup` + `re` | HTML scraping fallback; JSON preferred when the endpoint returns it. |

**Key endpoints (base: `https://mangabuff.ru`)**
- `GET/POST /login` — CSRF meta → POST `{_token, email, password}` → `{status:true}`.
- `GET /balance` → `POST /balance/claim/{data-day}` — daily reward.
- `GET /battle` → `POST /battle/daily-quests/claim` — daily quests.
- `POST /battle/fight/start` → JSON `{redirect_url}` (or `success:false` / `400·422` = out of energy → returns `"NO_ENERGY"`).
- `GET {redirect_url}` — battle result; essence parsed from JSON (keys: `essence`, `essence_gained`, `reward`, `gain`, `added`, `essence_added`) with HTML fallback (`Эссенция +N`).
- `POST /mine/hit` `{hits:1}` → `{ore, hits_left, added}` — separate ore-mining loop.
- `POST /mine/upgrade` — auto-upgrade.

**Required headers on every POST:** `X-CSRF-TOKEN`, `X-Requested-With: XMLHttpRequest`, `Referer`, `Origin`, modern `User-Agent` (Chrome 120 string set in `__init__`).

---

## 2. Current Functionality

1. **Authentication flow** — `validate_session()` restores cached cookies; else `login_and_steal_keys()` performs real email/password login, extracts CSRF, saves session.
2. **Daily reward claiming** — `claim_daily_reward()` finds the active reward element on `/balance` and POSTs the claim (HTTP 422 = already claimed, treated as success).
3. **Quest claiming** — `check_and_claim_quests()` does a **single pass** over `<article data-daily-quest-id>`; claims any whose button text `!= "В процессе"` OR is not `disabled`. `claim_quest()` POSTs with both `user_daily_quest_id` and `id` fields.
4. **Farming loop** — `run_farm_loop()`:
   - Pre-flight `check_active_deck()` aborts if no active deck is detected.
   - Claims quests **once**, then loops battles.
   - Anti-ban: random `4.5–12.2s` between actions, `10–20s` between battles, `45–120s` micro-break every `10–15` battles.
   - Energy depletion (`"NO_ENERGY"` / `400·422` / `success:false`) → graceful break.
   - **3 consecutive `0`-essence battles** → "Daily 1000 essence limit reached" → break (guards against single jitter zeros).
   - High-value card filter: `TARGET_RARITIES = ["legendary","mythic"]` → `check_card_rarity()` preserves drops to `jackpot_cards.json` (never consumed).

---

## 3. Known Issues

| Issue | Status | Detail |
|-------|--------|--------|
| **422 on quest claim** | *Mitigated, not fully verified* | Root cause was missing/incorrect id payload + CSRF. Current code sends `{_token, user_daily_quest_id, id}` + `X-CSRF-TOKEN`. Still depends on guessing the site's exact field name — **confirm against the real network-tab request**. |
| **"0 essence" handling** | *Fixed* | Was terminating on a single `0`. Now requires **3 consecutive** zeros before declaring the daily cap. Edge case: a brand-new/empty deck yields constant `0` — mitigated by `check_active_deck()` pre-flight, but the empty-deck text markers are heuristic (RU/UA/EN substrings) and may miss site copy changes. |
| **Essence JSON key guessing** | *Open* | `check_battle_result()` tries 6 candidate keys then falls back to HTML scrape. If the real key differs, essence is misread as `0`. |
| **Deck detection heuristic** | *Open* | `check_active_deck()` scans page text for empty-deck phrases; fragile to UI text changes. |

---

## 4. Future Roadmap

### Phase 2 — Stability
- [ ] **Harden quest-claim payload**: capture the exact request from the browser Network tab; lock the correct field name (`user_daily_quest_id` vs `id`) instead of sending both.
- [ ] **`check_active_deck()` robustness**: parse the actual deck/ready-slot DOM (e.g., a `.deck--empty` class or missing card element) instead of substring matching; surface a clear CLI/GUI warning.
- [ ] **Retry/backoff policy**: bounded retries on transient `5xx`/timeouts distinct from permanent `422`.
- [ ] **Config-driven constants**: move delays, rarity tiers, and thresholds into `CONFIG` rather than literals.

### Phase 3 — Intelligence
- [ ] **Analytics module** (`analytics.py`): parse card stats (attack/defense/rarity/level) from battle-result JSON into a structured model.
- [ ] **Awakening strategy**: define rules for which cards to awaken vs. preserve (currently all legendary/mythic are preserved blindly).
- [ ] **Market optimization export**: dump card + essence + ore data to CSV/JSON for external pricing analysis.
- [ ] **Adaptive anti-ban**: learn per-account delay windows; randomize micro-break cadence further.

---

## 5. Successor Prompt (paste-ready for a new AI agent)

```markdown
# ONBOARDING PROMPT — MangaMinerBot

## Project
MangaMinerBot is a Python `requests`-based automation tool for mangabuff.ru
(Laravel backend). It logs in with email/password, claims the daily
balance reward + daily battle quests, and farms battles for essence
(1000/day cap) with anti-ban humanization. Ore mining (/mine/hit)
and auto-upgrade also exist. Credentials live in user_data.json; the
session is cached in session_cache.json and reused until HTTP 419.

## Core coding principles
1. KEEP IT MODULAR — one method per responsibility inside MangaMinerBot.
2. PRIORITIZE ANTI-BAN — every loop needs randomized human-like delays;
   never hammer endpoints. Respect 419 (re-login) and 403/422 (energy/out-of-state).
3. USE CLEAN JSON PARSING — prefer response.json(); only fall back to
   BeautifulSoup/re scraping when JSON is truly unavailable.
4. NEVER hard-code tokens — CSRF is scraped from the login meta tag at runtime.
5. Every POST must carry X-CSRF-TOKEN + X-Requested-With + Referer + User-Agent.

## High-priority TODO
- [ ] Confirm quest-claim payload against the real Network-tab request
      (field is user_daily_quest_id and/or id); stop sending both blindly.
- [ ] Make check_active_deck() parse real deck DOM, not text substrings.
- [ ] Verify the essence JSON key name from a live battle response.
- [ ] Phase 3: analytics module for card stats + awakening strategy + market export.

## How to handle future requests
- ALWAYS check the browser Network tab before writing any POST: copy the
  exact URL, headers, and request body the site actually sends.
- If a 422 appears, suspect a missing/renamed field or stale CSRF BEFORE
  changing logic — re-scrape the token and re-read the payload shape.
- Preserve existing anti-ban delays; extend them, don't remove them.
- Keep the GUI (customtkinter) code intact even though it's currently
  commented out in the __main__ CLI test block.
- After any edit, run `python -m py_compile main.py` to confirm it parses.