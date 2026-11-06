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

Never commit non-approved screenshots, captured Codex conversations, exported OCR data,
mail credentials, or runtime settings. `references/`, `screenshots/` and
`captures/` are gitignored. Tests use synthetic OCR rows and the four explicitly approved, cropped UI fixtures. No screenshot is stored or emailed by default.
The local claim file at `~/.local/state/auto-ai-scheduler/claims.json`
contains only hashed opaque job identifiers (mode `0600`), not prompts.


## Compact dashboard and approved OCR references (patch 0003)

The GTK4 window now uses **two columns**, showing all current settings without
nested settings pages:

- **Left:** IDE/project/prompt and completion mode, one-shot schedule, activity log.
- **Right:** OCR/scroll/cycle/idle controls and all SMTP fields (including SSL
  port and the prompt/error notification toggles).
- **Bottom:** Save, detect, send, start, stop and approve, with a short status.

The default window size is **1160 × 700**. Its settings are kept in the
existing `~/.config/auto-ai-scheduler/settings.json`; this patch does **not**
change or reset existing schedules, prompts, or mail settings. A minimum window
size is set to preserve the side-by-side layout.

### Privacy and screenshots

Only these **four user-approved cropped UI snippets** have been added under
`tests/fixtures/codex_ui/`:

- `explorer_project.png` — Explorer title and project label.
- `edited_files.png` — edited-file summary card.
- `quota_banner.png` — usage-limit and reset time banner.
- `cycle_quota.png` — summary, quota, and empty composer together.

These files are used by OCR regression tests; **no other desktop captures are
committed by the patch**. The existing `references/`, `screenshots/`, and
`captures/` directories stay ignored. Never add real sessions, credentials,
account data or screenshots containing confidential information. The old
non-approved screenshots remain excluded and are not restored.

```bash
python3 -m unittest discover -s tests -v
python3 -m auto_ai_scheduler
```

This GUI change does not implement IMAP email approval or automatic quota reset
resume; both still require separate work. Live GTK layout should be verified on
your X11 desktop because CI/headless environments cannot render Devin.

## Compact single-page controller (patch 0004)

The UI has a **single GTK4 window with two columns**: X11 target, one-shot
schedule and read-only event history on the left; OCR monitoring and SMTP
settings on the right. A status strip turns green **only after recent OCR
verification of the exact project window** and also shows the upcoming action.
A stale observation reverts to neutral; window titles alone cannot certify a
project. The status is refreshed without taking focus or moving the pointer.

- **Play:** start/resume monitoring; keeps the existing send/cycle state.
- **Pause:** suspend all scheduled and automatic interactions, without losing
  in-memory state. Manual Send remains a deliberate, separate action.
- **Stop:** turn monitoring off; saved settings and one-shot schedule remain.
- **Previous / Next (preview):** browse the recent in-memory action log and
  the predicted upcoming action. **These buttons do not type, send, undo,
  or invoke the IDE.**
- **Send now / Approve:** retain the verified-project and quota safeguards.

Automatic continuation now requires at least one successful prompt sent by
this supervisor: an old `Edited N files` card by itself can no longer start a
new automatic chain when the application is launched. The GUI is deliberately
compact (12px content, 13px headings) and does not open extra settings pages.
It remains an X11 prototype; real GTK rendering and focus behavior still
require interactive testing on Debian/KDE. No private screens or OCR logs
are committed by this patch.

## Window-height and compact GTK layout fix (patch 0005)

On some KDE/GTK4 configurations, the previous plain `Gtk.Grid` propagated
its natural minimum height (e.g. 733px) into an allocated window height of
718px, causing repeated `Trying to measure AdwApplicationWindow ...` warnings.
This is a layout-measurement warning, not an OCR/Codex failure.

The two columns now sit in a single vertical `Gtk.ScrolledWindow` with
`propagate_natural_height=False` and a bounded minimum height. The status
strip, playback controls, and action buttons remain visible even when the
window is shorter than the full settings content. Smaller row gaps, frame
padding, and logger minimum height are intended to fit all settings at the
default 1160×700 size; an internal scrollbar appears only as needed for
font scaling, window-manager decorations, and unusually small displays.
The layout is still one page, with no extra dialogs or tabs.

This patch does not modify the Codex automation engine, time scheduling, or
any private OCR reference. For users with large fonts/display scaling, the
settings may require vertical scrolling; Gtk must never truncate buttons or
warn about an impossible minimum window height.

## Visual workflow editor and bottom timeline (patch 0006)

- **Launch:** `./start.sh` uses the local `.venv` when available.
- **Preferences:** change the default 1540×900 size, base font size (10–20px),
  Adwaita theme, project, OCR intervals, scheduling and SMTP in a separate window.
  Headings automatically stay 3px larger than the base font.
- **Layout:** nested `Gtk.Paned` handles let you resize the node library,
  graph canvas and inspector, plus the **full-width timeline anchored at the bottom**.
  Shrinking the window produces independent scrollable panes.
- **Nodes:** clock, delay, event, OCR proof, Codex state, send, scroll, notify
  and wait. Select, drag, edit properties, remove and connect output→input.
  Cyclic graph connections are refused.
- **Timeline:** ordered step clips show their label, parameters and (when a
  reference is attached) a small image thumbnail.
- **Proofs:** import an image from disk, automatically generate a local OCR
  test and optionally compare against the currently verified Devin window.
  The image and its text matching specification are stored under
  `~/.local/share/auto-ai-scheduler/proofs/` with restrictive permissions.
  Nothing is uploaded, emailed or committed automatically.
- **Persistence:** workflows live in the user's private data directory. No
  workflow JSON or screenshots are added to the repository.

**Important safety boundary:** This patch implements the visual scenario
**editor, preview and manual OCR tests**. It does not yet execute graph nodes
or convert them into automatic keyboard actions. Play/Pause/Stop still control
only the existing guarded Codex supervisor. A later runner must require
explicit opt-in, fresh OCR proof, verified project and quotas before it can
execute an action sequence.

### GTK settings warnings

`Unknown key gtk-modules` and `gtk-application-prefer-dark-theme is unsupported`
originate from the user's GTK4 settings, not the application window dimensions.
To remove only those obsolete GTK4 keys and back up the original configuration,
run **once**, if desired: `./start.sh --fix-gtk-config`.
The application itself uses `Adw.StyleManager` for its light/dark preference.

### Force test (explicit real Codex send)

Click **⚡ Force test** to preview and confirm the exact prompt of the selected
**Envoyer prompt** graph node; otherwise it previews the first send node in
timeline order. It invokes the verified-project/composer X11 sending pipeline
**once**, even if the graph's clock or OCR triggers have not fired. Quota,
error, busy conversation and cooldown checks are not bypassed. It changes
neither the visual node selection, the timeline, the scheduled-job claim nor
the normal cycle counter. While the forced prompt is in progress, the automatic
scheduler does not send anything; it resumes its normal behavior after the
response is observed completed. Since the scheduled job is *not consumed*,
that job may still run later at its configured time. A forced test is not a
visual workflow runner.

## Guarded workflow execution — patch 0007

The **bottom timeline** now has a separate **▶ Scénario / ⏸ Scénario / ■ Scénario** transport.
The original top **Play** continues to control the legacy Codex monitor. Only one
controller may run at once. **Force test** is still isolated and cannot execute while
a scenario is armed. Previous / Next still only browse history and never execute.

For an executable scenario, connect **one linear chain**, beginning with a clock,
delay or event trigger, then optional conditions and actions. A send MUST be
**immediately preceded by an OCR or Codex-state condition**. The workflow
runner requires two consecutive positive readings before advancing a condition,
checks the configured target project and composer again through Scheduler on
send, and invalidates proofs older than 12 seconds. An example is
`Événement (cycle_finished) → Preuve OCR → Envoyer prompt`. The event only
triggers when the runner has **observed RUNNING after arming**, followed by two
matching FINISHED observations. An existing old “Edited files” card cannot by
itself launch a new sequence. OCR tests use the ROI expressed in percentages
of the **live IDE window**; narrow ROI to the actual evidence on screen before
arming. A reference image must be imported privately first.

A prompt is attempted only once per send step. If delivery cannot be confirmed,
the scenario pauses without automatic retry, to avoid double submissions. After
a successful prompt, the next step is gated on a **new** two-times-verified
completion marker (and at least 25 seconds after dispatch). Quota and error
states pause the scenario and require manual resumption. The active clip is
highlighted in the timeline; its execution state is shown beside the controls.
The execution graph is snapshotted at Play, so edits to blocks do not silently
change an armed run. No screenshot, prompt, OCR result, or run trace is
automatically uploaded to GitHub.

**Limits of this first executor:** branching/parallel paths and loops are
rejected, execution progress is in memory and is never resumed after application
restart, and IMAP approval / automatic quota reset scheduling are not yet
implemented. The existing single-date scheduler remains separate. Use the
local approval mode for confirmed sends when you do not want unattended input.
The live GTK/X11 interaction still requires testing on your Debian desktop.

Launch and check with:

```bash
./.venv/bin/python -m unittest discover -s tests -v
./start.sh
```

## Conditional branching and quota-aware waiting (patch 0008)

The bottom timeline still displays the graph; **Scenario Play** is the only
way to arm it. The classic monitor cannot run in parallel. The new **Si / Sinon**
node has **two output ports** on the right: upper green **OUI** and lower orange
**NON**. Connect each port to a destination, and set `source` to `state` (read
Codex READY/RUNNING/FINISHED/QUOTA/ERROR) or `ocr` (reference screenshot,
`expected` text and normalized `roi` region). Each outcome needs **two stable,
recent observations**. Ambiguous, empty or unavailable OCR is **not** taken as
NO; an unexpected quota, error or in-progress response also prevents a mistaken
negative branch. Each path executes in sequence, never in parallel, and merges
are allowed in an acyclic graph. A Send node must immediately follow a positive
OCR/State condition or a positive safe branch; **NON -> Send is refused**.

The **Attendre quota** node is designed for an explicit chain such as:

    Événement (quota) -> Attendre quota -> État Codex (READY) -> Envoyer prompt

It reads an **English date with year and 12-hour time** from the Codex banner,
for example `Nov 6, 2026, 12:28 AM`, interpreted as the machine's local time.
The app **does not alter Codex quotas**. It waits until that time, checks the
quota banner has disappeared and the verified composer is READY/FINISHED on
**two consistent inspections**, then requires **local approval by default**.
`resume=auto` is an explicit opt-in and still requires the time plus the two
fresh UI checks. Missing/unreadable/past reset time causes a safety pause.
Timezone labels embedded in free-form OCR are not interpreted: verify that
Codex and Debian present local time consistently. During DST transitions, the
user should prefer the default manual approval mode. An interrupted response
is never silently re-sent after quota recovery; check it before restarting.

For Event, OCR, State, Si / Sinon and Attendre quota blocks,
`timeout_seconds` is optional (0 = no timeout, 1..86400 = bounded). A timeout
pauses the scenario and optionally sends the already configured SMTP error
notification. It never moves to the next action or sends a prompt. The current
execution state remains in RAM; after a restart, **Scenario Play** must be
activated manually and the graph starts anew. Do not use automatic resume for
critical edits without interactive verification on Devin/KDE.

No screenshot or OCR transcript is committed, stored or emailed by this patch.
The new tests use only synthetic OCR observations and throwaway local images.

## Durable workflow checkpoints (patch 0009)

The visual workflow executor now saves a **private metadata-only checkpoint**
under `~/.local/state/auto-ai-scheduler/workflow-checkpoint.json` (mode 0600,
parent directory 0700). It records only the workflow SHA-256 fingerprint,
block ID, executor phase, delivery uncertainty and update time. No prompts,
reference images, OCR transcripts, SMTP details, project names or screenshots
are included in the checkpoint. Workflows and OCR proofs remain in their
existing private local stores.

A restart **never auto-resumes**. The timeline shows an outstanding checkpoint
and offers **↶ Restaurer**, which loads the exact saved workflow *paused*.
Click ▶ Scénario to explicitly resume. Waiting timers start afresh; OCR,
Codex state, branch decisions and quota reset must be revalidated from live
observations. If the current step was an unsent prompt, restoration rewinds
to its unique incoming condition so a stale proof cannot authorize typing.

Before any live Send block, an **attempted** checkpoint is durably written.
If the application closes after a send attempt or while waiting for its
response, restoration remains blocked: the operator must inspect Codex and
**explicitly** discard the checkpoint before rearming. No automatic replay
of a potentially delivered prompt occurs. The **✕ Effacer reprise** button
requires confirmation. Completed scenarios and ordinary stops clear their checkpoint.
**Stop does not erase a checkpoint when a prompt may already have been sent**;
use the separately confirmed Effacer reprise action after inspecting Codex. This does not promise exactly-once execution after a user chooses
to discard a checkpoint and manually rearm a scenario.

Play (classic supervisor) is also refused while a workflow recovery is
pending, avoiding two automation engines racing to type in Devin. The feature
is for the currently saved default workflow; edited workflows cannot inherit
checkpoints from an older version of their graph.

Run with `./start.sh` and verify in your Debian KDE X11 desktop; headless
checks validate the checkpoint state machine but cannot verify the live GUI.

## Structured execution journal (patch 0010)

The resizable **bottom timeline** now shows a per-block status and elapsed time
below each clip. Completed steps are highlighted in green, OCR results in
amber, and blocked or timed-out steps in red. Tooltips describe the exact
status and time spent in that block. The timeline includes two horizontally
resizable journal panels: a **structured execution history** (time, block,
duration, reason) and the existing general supervisor log.

This journal uses an **allowlisted set of status/reason codes**. It never saves
or displays raw OCR results, prompts, imported proof images, window titles or
exception strings. It is bounded to the latest 200 events **in memory only**;
a restart restores the safe workflow checkpoint, not the previous trace. An
OCR match requires the existing two independent observations and does not
itself send a prompt. Failed verification is displayed without bypassing any
existing X11, quota, cooldown or one-shot-send protection.

Run `./start.sh` and launch a scenario with `▶ Scénario` to see live statuses.
Resize the vertical separator to make the timeline taller, or drag the
separator between the structured and general logs.

## Visual workflow debugger (patch 0011)

The resizable **bottom timeline** contains an opt-in debugger. Click **🐞
Déboguer** while the runner is stopped, then **▶ Scénario**. Debug mode
starts **paused before the first node**. **⏭ Pas** executes one entire
node (including repeated OCR observations and waiting for a response), then
pauses **before** the following node. **▶ Continuer** runs until the next
user-defined breakpoint. While paused, the timeline and local execution trace
remain visible. Both controls retain the normal project verification, user
idle, quota, cooldown and at-most-once send checkpoints. An explicit
confirmation shows the exact prompt before stepping into an **Envoyer prompt**
node. Continue can run ordinary scheduled actions; treat it as a real run.

Select a node in the graph or timeline and click **● Point d’arrêt** to
toggle its breakpoint. Red dots are shown on the timeline. Breakpoints are
stored privately in `~/.local/share/auto-ai-scheduler/breakpoints.json`
(permissions 0600), containing **only opaque node IDs**. Debug mode is off
by default on launch; it must be explicitly selected again after restart.

Select an OCR node (or an OCR-based Si/Sinon block) and use **◉ Test OCR**
while all runners are stopped/paused. It takes a fresh, verified screenshot
of the selected Devin project, checks the selected local proof and ROI, then
reports only a positive/negative result. It **never sends**, advances the
workflow, persists a screenshot, or saves raw OCR text. An unavailable target
or active quota is a negative result, not permission to proceed.

This change extends the desktop GUI only; it does not run real X11/GTK tests
on a headless build machine. Before unattended operation, test on KDE/X11.

## Complete offline replay / dry run (patch 0012)

Expand **🧪 Simulation complète** in the bottom timeline. Choose a **hypothetical**
Codex state, event, local time, elapsed seconds, and optionally a quota-reset
deadline. Import an **observed sample screenshot** for a selected OCR or
OCR-branch node with **Importer observation**. Each imported screenshot is stored
privately in `~/.local/share/auto-ai-scheduler/proofs/`, not in the Git repo.
Alternatively enable **Référence = observation (démo)** to deliberately treat
existing reference screenshots as observation fixtures. In this demo mode, an OCR
success says nothing about the current Devin window.

Click **▶ Simuler (aucune action)** to validate and walk the graph in memory,
including YES/NO exits, time and event gates, OCR comparisons, quota gates,
notifications, scroll and prompt blocks. The simulated status of each clip
appears in the original **bottom timeline**; the left structured journal shows
only redacted decisions and predicted actions. If a screenshot, clock condition,
quota history or assumed event is missing, the preview **stops** rather than
pretending that it saw a negative result. Date/time and event inputs are
**hypotheses**, not verified observations.

**The simulator never calls X11, Scheduler, SMTP, or window automation actions.**
It never clicks, types, sends a prompt, triggers a mail, scrolls Devin, changes
workflow checkpoints, clears real breakpoints or advances the live timeline.
The existing Force test is **still real** and separate from this preview.
The preview exists only in app memory and is cleared when the graph changes
or a live scenario starts. Visual behavior requires KDE/X11 testing.

## Temporal OCR recording and offline replay (patch 0013)

A **🎞 Séquence OCR** expander is available in the bottom, resizable timeline.
Click **⏺ Enregistrer** to start a bounded observation sequence, **⏹ Terminer**
to save it, then **▶ Rejouer** to preview the graph against those successive
observations. **✕ Effacer** deletes the private recording after confirmation.
The 3–30 second sampling interval is adjustable, with a maximum of 120 frames.

The recorder inspects **only a verified Devin + Explorer project + Codex** window,
using the existing X11 focus-restoration code and respecting `idle_seconds`.
If the desktop is being used, it **skips** sampling rather than interrupting
what you are doing. The live supervisor and visual scenario executor cannot run
in parallel with a recording. A recording never clicks or types in Codex.

**Privacy:** Only local timestamps, Codex state, whether the composer was seen,
hashes of cycle markers, quota reset deadlines (if explicitly recognized), and
per-node OCR values **true / false / unknown** are stored. Screenshots are used
only in memory for OCR and are **never** saved or committed. Neither the
recognized text nor prompt content is recorded. The single local recording
is stored as `~/.local/share/auto-ai-scheduler/recordings/last.json`, with
`0700` on its parent and `0600` on the file. It is not in the Git tree.

Replay is deterministic and **entirely offline**: it uses the actual sequence
of recorded states instead of applying one global state to every workflow block.
Two consecutive confirmations are required for event, OCR, state and branch
transitions; unavailable OCR stays **unknown** rather than implying a NO
branch. Clock and delay nodes use the recorded timestamps. Prompt, scroll and
mail nodes display **would execute** without ever dispatching an action.
A changed scenario fingerprint invalidates the recording rather than replaying
it against a different prompt. This is a simulation of a captured session,
**not proof that an action would succeed in a future live session**.

The original static simulation controls and the existing execution/debug
checkpoint system are untouched. A quota-wait that requires local approval
also requires the explicit **Approbation fictive** checkbox when replaying.

## Navigable recorded-state timeline and A/B comparison (patch 0014)

Expand **🎞 Séquence OCR** in the **bottom resizable timeline**. A new
**TRAMES OCR** track shows the sequence as colored *state markers*, not image
thumbnails: patch 0013 intentionally never stores screenshots. Move the
scrubber, click an observation on the ruler or use **◀ / ▶** to inspect one
recorded observation at a time. **Zoom 1×–8×** changes the visible time span
around the selected frame without altering the file. The current state, local
timestamp, composer presence, quota-deadline presence and each OCR node's
YES / NO / unknown value are shown in read-only, scrollable panels. Never
interpret unknown OCR as negative proof.

Click **📌 Épingler référence** and confirm to save a second private recording
`~/.local/share/auto-ai-scheduler/recordings/reference.json`. The existing
`last.json` remains independent, so you can capture a second session without
overwriting the comparison reference. Both are local JSON files with mode
0600; the directory is 0700. Click **⇄ Comparer** after opening the new last
recording to compare the selected frame against the nearest frame by *relative
elapsed time*, rather than by index or absolute clock. The reference must
belong to the exact same workflow fingerprint; otherwise comparison refuses
without attempting to reinterpret its OCR results.

Scrubbing also shows the **offline replay outcome up to that frame** on the
existing scenario clips, never changing the live runner or its checkpoint.
Nothing in inspection, replay or comparison calls X11, Scheduler.send, SMTP,
or external services. Existing **Force test** remains a separate, real-send
button; the inspection controls do not dispatch any Codex command.

The GTK layout is designed to stay compact on 1080p screens: the new
information panels scroll inside the expandable sequence area. Headless
unit tests cover time alignment, input validation, redaction and zero desktop
side-effects; actual KDE/GTK behavior still needs live confirmation.

## Fluid multi-select canvas and zoom (patch 0015)

The **central visual editor** now uses a viewport-sized Cairo drawing area,
not a fixed 1600×1050 canvas that forces scrollbars on smaller panes. The
**bottom timeline stays pinned and vertically resizable** as before. Its
separate playback, recording, OCR and debugger functions are unchanged.

- **Mouse wheel**: zoom under the pointer (45–250%); **− / +** zoom around
  the viewport center. **100 %** resets zoom/pan; **Cadrer les blocs** fits
  the whole workflow to the available pane size. Zoom/pan are non-persistent
  camera settings and do not alter any node coordinates or workflow content.
- **Middle-button drag** or **Space + left drag**: pan the canvas.
- **Shift/Ctrl + click**: add or remove blocks from the selection; **Ctrl+A**
  selects all, **Escape** abandons an unfinished connection. Drag the empty
  canvas to rectangle-select several nodes, then drag any selected node to
  move the group without breaking relative positions. The graph is saved once
  after the gesture, not once per intermediate mouse movement.
- **Connection wires**: stronger colored cubic curves, directional arrows and
  separately colored OUI/NON branches. The original output-port-to-input-port
  gesture is preserved. Invalid loops are still rejected by `Workflow.connect`.

The pure-Python viewport model is tested headlessly (coordinates, anchor zoom,
lasso, group movement, non-mutation of graph during camera changes). GTK events
are wired on the desktop at runtime; check wheel/pan behavior on your KDE/X11
setup after applying the patch. No screenshots, prompts, runtime settings or
OCR transcripts are included in the patch.
