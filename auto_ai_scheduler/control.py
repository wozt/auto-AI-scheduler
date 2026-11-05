"""UI-independent run controls, action history and next-action preview.

Prev/next are navigation only. They must never perform desktop automation.
"""
from __future__ import annotations

from datetime import datetime
import time

from . import schedule


class ActionHistory:
    def __init__(self, limit=100):
        self.limit = limit
        self.items: list[str] = []
        self.cursor = 0  # index len(items) means 'upcoming action'

    def add(self, event: str) -> None:
        self.items.append(event)
        if len(self.items) > self.limit:
            del self.items[:len(self.items)-self.limit]
        self.cursor = len(self.items)

    def previous(self) -> str | None:
        if self.cursor > 0:
            self.cursor -= 1
        return self.selected()

    def next(self) -> str | None:
        if self.cursor < len(self.items):
            self.cursor += 1
        return self.selected()

    def selected(self) -> str | None:
        return self.items[self.cursor] if self.cursor < len(self.items) else None

    @property
    def can_previous(self):
        return self.cursor > 0

    @property
    def can_next(self):
        return self.cursor < len(self.items)


class RunControls:
    """Pause keeps all in-memory cycle markers; stop disables the monitor.

    Saving settings does not arm/run the monitor; only Play does that.
    """
    def __init__(self, engine):
        self.engine = engine
        self.phase = 'stopped'
        self.history = ActionHistory()

    def play(self):
        if self.phase != 'running':
            self.phase = 'running'
            self.engine.enabled = True
            return True
        return False

    def pause(self):
        if self.phase == 'running':
            self.phase = 'paused'
            self.engine.enabled = False
            return True
        return False

    def stop(self):
        if self.phase != 'stopped':
            self.phase = 'stopped'
            self.engine.enabled = False
            return True
        return False


def next_action(engine, phase, now=None):
    """Non-executable description based only on committed configuration/state."""
    if phase == 'stopped':
        return 'Appuyer sur Play pour commencer la surveillance'
    if phase == 'paused':
        return 'En pause · Play pour reprendre sans perdre le cycle'
    if engine.session.cycles >= engine.config['max_cycles']:
        return 'Limite de cycles atteinte · Stop ou ajuster la limite'
    if engine.pending_approval:
        return 'Attendre approbation locale'
    if engine.session.quota_seen or getattr(engine, 'last_ui_state', '') == 'QUOTA':
        return 'Quota Codex · attendre la fin de la limite puis valider'
    if engine.config.get('schedule_enabled'):
        when = engine.config.get('scheduled_at', '')
        if schedule.due(engine.config, now):
            return 'Prompt planifié prêt · attente du bureau libre et de Devin'
        return f'Attendre le prompt planifié : {when}'
    if engine.session.waiting_for_response:
        return 'Surveiller la réponse et identifier la fin du cycle'
    if engine.config['mode'] == 'approval':
        return 'Surveiller puis demander validation de chaque cycle'
    if engine.config['mode'] == 'auto':
        if engine.session.cycles == 0:
            return 'Attendre un premier envoi manuel ou planifié'
        return 'Observer Codex · poursuivre après fin vérifiée'
    if getattr(engine, 'last_ui_state', '') == 'ERROR':
        return 'Erreur détectée · vérifier Codex avant de reprendre'
    return 'Surveiller Codex sans envoi automatique'


def recent_target(engine, now=None, timeout=None):
    """Fresh window discovery only; a stale past scan is NOT a green status."""
    target = getattr(engine, 'last_target', None)
    if target is None:
        return None
    if timeout is None:
        timeout = max(35, min(120, engine.config['poll_seconds'] * 3))
    current = time.monotonic() if now is None else now
    if current - target.seen_at < 0 or current - target.seen_at > timeout:
        return None
    return target
