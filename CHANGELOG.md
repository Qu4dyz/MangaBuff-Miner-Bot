# Changelog

## 11.1.0 — 2026-10-23

### Added

- Awakening reroll safety: «Оставить как есть» per card, a paused series shows paused cards, a card the site refuses to unawaken is skipped with the reason, a popular name is searched across result pages, a rerolled card returns to the Tower squad, and the card list shows the latest roll.
- Seal reforge on the Awakening page: pick awakened cards and the seals you want; the bot reforges (500 essence each) until one of them comes up, keeps attack, HP and crit untouched and never spends past your essence limit. Each seal shows how often opponents use it and whether it wins more than deck strength predicts (from 3.5k arena and cup fights).
- Reroll page: stats and seals are labelled, a finished card gets a green badge plus Windows and Telegram notifications, minimums are edited right in the running card, finished cards can be rerolled again with new minimums, and your awakened cards are listed instantly with a filter.
- Awakening reroll (its own «Пробуждение» page): pick one or several cards from tiles with their art, rank, copy number and stats, set shared minimum ATK, HP and crit with sliders; it starts right away (even with the bot stopped) and asks in its own dialog before discarding current stats; the bot awakens it, frees it again when the stats fall short (unawakening is free) and repeats with energy from arena fights until the card meets every minimum. It shows the odds from your own awakened cards, puts the card back into the active deck, waits during the evening cup and never buys energy.
- Sign-in through VK, Discord, Yandex or Google in Buffy's wizard. Buffy keeps the cookies of MangaBuff's own sign-in window; when the session dies it signs in again in a hidden window and shows it only if the provider asks for input.
- The account card shows your MangaBuff nickname, avatar and profile cover (animated ones play) instead of the email or provider label.
- Buffy takes the colours of your MangaBuff profile skin: simple skins recolour the accent, event skins (like the Halloween pumpkin lord) recolour the whole app. Switch it off in Settings → «Тема из профиля MangaBuff».

### Changed

- Evening Cup tactics v2: better choice of the enemy card to finish off and emergency healing. In fights of equal deck strength it won 64% against 47% for the old moves (tested live on the server since 20 Oct).
- Halloween candies and cauldron heat show up seconds after the bot starts instead of after its first full cycle (up to an hour).
- «За чтение сегодня» follows the site's own daily candy count, so drops from reading on the phone or before a restart count too, and the bot stops reading for candies once the site's 35 are in.
- The Russian README is now the main one (`README.md`), the English one moved to `README.en.md`. Both start with the download, social sign-in, data and problem-report sections; `.env` names now match the code (`MANGA_LOGIN`/`MANGA_PASSWORD`).

### Fixed

- Settings could be lost on Windows: a write landing while another thread was reading the file was dropped silently, and a read that failed at that moment let the next write replace the whole file with a single key. Reads and replaces now retry briefly and never write over a file they could not read.

## 11.0.12 — 2026-10-22

### Fixed

- "Report a problem" opens the GitHub issue even when the diagnostic file cannot be saved; the issue then carries the short log only.
- A title that gave real chapter progress and then hit one stale batch at its end no longer counts toward the two-stalled-titles stop.

## 11.0.11 — 2026-10-21

### Fixed

- Reading switches to another manga after two batches without daily progress. After two stalled titles, it pauses and saves the next title for the following cycle.
- Below the daily cap, the reading count reflects progress confirmed by the site. Failed submissions no longer skip ahead, and a final single chapter is submitted before switching titles.

### Added

- Full diagnostic report export from the journal and problem-report dialog, including both retained log files and the current session buffer. Reports mask credentials and preserve long lines; the issue editor asks users to attach the report file.
- Reading diagnostics record HTTP response details, safe error fields and daily counters before and after submission, without cookies, reward tokens or full HTML pages.
