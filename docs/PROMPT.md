# Prompt: Build a "My Little Pomodoro"-style app

Build a cozy, gamified Pomodoro focus app for Android (Unity 6 + C#, or Flutter/Kotlin if preferred) with a cute character living in a decorated room.

## Core concept
The user focuses in timed sessions. While they focus, a small main character studies/works in a room; during breaks the character relaxes. Finished sessions earn currency that buys furniture, outfits and themes, so the room grows with the user's productivity.

## Features

### 1. Timer
- Pomodoro mode: configurable focus length, break length, long break, and session count (default 25/5).
- Stopwatch (count-up) mode and a "continue session" option when time ends.
- Count-down beep, start/stop/reset-session confirmations, warnings when switching focus/break.
- Persistent foreground-service notification with a live countdown or stopwatch, with a compact and an expanded layout, and a distinct break-state card.
- Keeps running reliably in the background and with the screen off; handles low-power mode.

### 2. Tasks
- Create, edit and delete tasks (with a confirm on delete).
- Attach a task to a session.
- Show a "all quests completed" state.

### 3. Focus enforcement (optional "Force mode")
- Full-screen lock view during focus, with a help dialog for how it works.
- Optional camera-based face detection (ML Kit) to check the user is present.
- Camera permission rationale dialog and graceful fallback if denied.

### 4. Character and room
- A main animated character (idle, studying, special animations).
- A room made of sections; the user can place, toggle and move furnishings.
- Style shop: buy furniture, outfits and room themes with earned coins; some premium items and themes.
- A cafe/gift feature: free menu items and gifts for friends.

### 5. Progress and stats
- Daily and monthly records: date, concentration time, break time and session count.
- Streak and daily first-activity reward, with anti-cheat checks (system time changes, timezone jumps, reward timestamps must increase, server date must match local date).

### 6. Social
- Friends list: add, request, cancel, delete, send gifts.
- Anonymous or Google sign-in, with cloud save and restore (Firebase Auth, Firestore, Storage).
- Detect local data corruption or conflicting saves and prompt the user to restore or overwrite.

### 7. Settings
- Language picker with 60+ locales, using a localization table.
- Volume, music and sound-effect controls.
- Push notification toggle with a warning when notifications are off.
- Delete account / unregister with a confirmation and info screen.
- Review prompt, with a feedback path if the user gives a low rating.

### 8. Monetization
- Optional rewarded ads (earn bonus coins), banners, an interstitial and an app-open ad, behind a consent flow (UMP/CMP) with a personalised-ads explanation.
- In-app purchases (Google Play Billing): premium characters, premium themes and a subscription that removes ads, with a restore-purchases flow and local receipt de-duplication by order ID.

## Technical requirements
- Min SDK 24+, portrait orientation, offline-first with background sync.
- Firebase: Analytics, Crashlytics, Cloud Messaging for push, Remote Config for version checks ("need update" prompt).
- Addressables or asset bundles for downloadable art, and localization string tables for every UI string, using keys like `MSG_STOP_CONFIRM`.
- Local save with integrity checks and a cloud-sync merge strategy.
- Anti-cheat on currency, time and saves.

## Look and feel
Soft pastel palette, rounded UI, chibi character art, a circular progress ring for the timer, cheerful lo-fi music, and short celebratory animations when a session completes.

## Deliverables
- Project structure and architecture overview.
- The timer service, notification and persistence code first, then the room and shop systems.
- A data model for tasks, sessions, inventory, currency and friends.
- Step-by-step setup instructions (Firebase, billing, ads, localization).
- A test plan covering background timing, time-tampering and purchase restore.

Build it incrementally: start with the timer, notification and task list, confirm it runs, then add the room and shop, then social and monetization.
