# 🌱 Pomo Nook

A cozy, ad-free, open-source Pomodoro timer with a little room that grows as you focus.
Mochi, a small sprout character, studies while you focus and relaxes during breaks.
Finished sessions earn coins you spend on furniture, outfits and room themes.

Everything is in one file, `index.html`. There is no build step, no server and no tracking;
all data stays in your browser's `localStorage`.

## Run it

Open `index.html` in a browser, or serve the folder:

```sh
python3 -m http.server 8000   # then visit http://localhost:8000
```

To host it for free, enable GitHub Pages for this repository (Settings → Pages → deploy from branch, root folder).

## Features

- **Timer**: Pomodoro (configurable focus / short break / long break / sessions before a long break, default 25/5/15/4) and stopwatch mode. "Keep going" bonus focus when a session ends, 3-second countdown beep, confirmations for stop / reset / switching phases. Timestamp-based, so it stays accurate in background tabs; a Web Worker ticker avoids timer throttling.
- **Quests**: add, edit, delete (with confirm), attach a quest to the timer, focus time tracked per quest, "all quests completed" banner.
- **Force mode**: full-screen lock view with no pause button, counts tab switches as distractions, help dialog.
- **Room & shop**: animated character (idle / studying / relaxing / celebrating), 9 furniture items you can place, hide and drag to arrange, 6 outfits, 5 themes, plus a café with one free drink a day.
- **Stats**: today, last 7 days chart, and a monthly table of focus time, break time and sessions. Streaks and a daily first-session reward.
- **Anti-cheat**: checksummed save with automatic backup and corruption recovery, detects the clock going backwards, timezone jumps and server/local time mismatch, and pauses rewards when the clock looks wrong.
- **Settings**: English and Hindi (all UI strings are keyed, e.g. `MSG_STOP_CONFIRM`), volume, procedural lo-fi music, sound effects, notifications with a blocked warning, keep-screen-on (Wake Lock), export / import / delete all data.
- Single active tab guard, reduced-motion support, keyboard shortcut (Space to start/pause).

## Adding a language

Add an entry to `LANGS` and a matching key table to `I18N` in `index.html`. Missing keys fall back to English.

## Roadmap

`docs/PROMPT.md` holds the full spec for a native Android version (foreground-service notification,
ML Kit face check, Firebase sign-in and cloud save, friends and gifts, 60+ locales, ads and billing).
This web version covers the offline core of that spec; the rest is future work.

## License

MIT, see `LICENSE`.
