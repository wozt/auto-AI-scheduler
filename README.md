# Auto AI Scheduler — GTK4 prototype for Devin Desktop / Codex

An **opt-in X11 desktop automation skeleton**. Its default mode is manual.
It does not use private Codex APIs, bypass quotas, or modify project files.

## Design contract

- **No Devin:** `xdotool search --onlyvisible --name Devin` returns no windows; nothing is clicked, typed or scrolled.
- **Correct project only:** for each candidate Devin window, activate by ID and OCR the **Explorer header and project name just below it**. `metroidvania` in chat content is not sufficient. Require Codex UI indicators in that same window as well.
- **No blind Alt+Tab:** activate a known window ID, test it, and restore the previous active window and the exact mouse coordinates **immediately after each candidate/action**. A `finally` block handles normal and error exits. The user's clipboard is never touched.
- **Do not steal control for every frame:** OCR discovery currently activates each candidate window briefly; for users who are actively typing, use only when unattended. A future backend should use reliable offscreen window captures and avoid focus changes during observation.
- **No unsafe automatic sending:** `Edited N files` + `Worked for` is only a potential completion. Require repeat observations, a different marker after an actual send, a cooldown, a cycle limit, a visible composer, and no quota/error. OCR cannot trigger shell commands.
- **Quota:** no sends while quota is visible. When quota disappears, manual approval is required; email-only approval is not implemented.
- **Periodic scroll:** every minute when monitoring, only if project and Codex are found; it also restores the mouse and focus.
- **Mail:** SMTP notifications on prompt/error/completion are optional. `AUTO_AI_SMTP_USER` and `AUTO_AI_SMTP_PASSWORD` hold credentials. Email confirmation (IMAP) is future work; currently the "Approuver" button is local.

**Known limitations:** The Codex pane might be hidden, the Explorer collapsed, another window obscuring Devin, X11 security prompts or screen lock can defeat OCR. This prototype only types one-line prompts. A single date/time can be scheduled persistently, but the GUI must remain open and Devin must be reachable. It does not yet automatically restart on login or read replies by IMAP. Do not leave it unattended for critical changes until live tests on your workstation pass.

## Debian 13 install and run

```bash
sudo apt update
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
  xdotool xprintidle tesseract-ocr tesseract-ocr-eng python3-pil python3-pytesseract
cd ~/dev/auto-AI-scheduler
python3 -m unittest discover -s tests -v
python3 -m auto_ai_scheduler
```

If you are still using the previous `codex-guard` service, stop it before launching this prototype to avoid two automation processes fighting for the mouse.

## UI

- **Projet cible:** the precise folder name in the Explorer (example: `metroidvania`).
- **Titre de fenêtre IDE:** regex matched by xdotool (default `Devin`).
- **Prompt:** a single-line message (default `continue`).
- **Modes:** manual / automatic / local approval. Automatic is explicit opt-in; do not start without checking the target.
- **Tester la détection:** visit visible Devin windows, OCR, restore your original window/pointer.
- **Envoyer maintenant:** single guarded send with a visible composer.
- **Démarrer / Arrêter:** enable or disable the periodic monitor.

## Future milestones

1. Offscreen X11 capture and in-focus user activity protection.
2. Region detection and confidence score for multiple IDE layouts.
3. Service/systemd integration, time-zone-aware recurring schedules, job queue.
4. OCR reset-date parsing and resumed cycle handling, including DST.
5. SMTP reports, signed IMAP approvals, redacted attachments, and audit log.
6. GUI queue of multiple projects/prompts, stop-on-failure policies, screenshots and tests.

## One-shot scheduling and safe focus (patch 0002)

In the GUI, enable **Activer un envoi unique à heure fixe**, enter a local date/time
such as `2026-11-06 03:00` and click **Enregistrer**, then **Démarrer**. The app
must stay running in the user's X11 desktop session; it is not a daemon yet.
If no verified Devin project and Codex composer is available at the requested time,
the pending job remains queued until the IDE becomes available and the user is idle.

By default the monitor waits for **8 seconds of user inactivity** (`xprintidle`)
before touching a window. All window focus and pointer movements performed by the
scheduler are restored after each interaction. Manual actions are explicit and
not idle-gated. The schedule is marked as *attempted* on disk **before** typing:
this prevents accidental duplicate sends on restart, but a mid-send crash may
require manually re-arming the job (disable/enable the schedule and save).
An already-sent job is automatically switched off in settings.

## Privacy

Never commit screenshots, captured Codex conversations, exported OCR data,
mail credentials, or runtime settings. `references/`, `screenshots/` and
`captures/` are gitignored. Tests use **synthetic OCR rows**, not private
screenshots. No screenshot is stored or emailed by default.
The local claim file at `~/.local/state/auto-ai-scheduler/claims.json`
contains only hashed opaque job identifiers (mode `0600`), not prompts.

