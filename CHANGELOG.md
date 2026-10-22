# Changelog

## Unreleased

### Added

- Sign-in through VK, Discord, Yandex or Google in Buffy's wizard. Buffy keeps the cookies of MangaBuff's own sign-in window; when the session dies it signs in again in a hidden window and shows it only if the provider asks for input.
- The account card shows your MangaBuff nickname, avatar and profile cover (animated ones play) instead of the email or provider label.
- Buffy takes the colours of your MangaBuff profile skin: simple skins recolour the accent, event skins (like the Halloween pumpkin lord) recolour the whole app. Switch it off in Settings → «Тема из профиля MangaBuff».

### Changed

- Halloween candies and cauldron heat show up seconds after the bot starts instead of after its first full cycle (up to an hour).
- «За чтение сегодня» follows the site's own daily candy count, so drops from reading on the phone or before a restart count too, and the bot stops reading for candies once the site's 35 are in.
- The Russian README is now the main one (`README.md`), the English one moved to `README.en.md`. Both start with the download, social sign-in, data and problem-report sections; `.env` names now match the code (`MANGA_LOGIN`/`MANGA_PASSWORD`).

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
