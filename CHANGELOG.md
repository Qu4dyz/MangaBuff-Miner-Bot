# Changelog

## 11.0.11 — 2026-10-21

### Fixed

- Reading switches to another manga after two batches without daily progress. After two stalled titles, it pauses and saves the next title for the following cycle.
- Below the daily cap, the reading count reflects progress confirmed by the site. Failed submissions no longer skip ahead, and a final single chapter is submitted before switching titles.

### Added

- Full diagnostic report export from the journal and problem-report dialog, including both retained log files and the current session buffer. Reports mask credentials and preserve long lines; the issue editor asks users to attach the report file.
- Reading diagnostics record HTTP response details, safe error fields and daily counters before and after submission, without cookies, reward tokens or full HTML pages.
