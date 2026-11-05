"""Bounded, in-memory execution evidence for the visual timeline.

The journal accepts only predeclared status/reason codes, never arbitrary OCR
results, window titles, prompts, paths or exception strings. It is not saved
in workflow checkpoints or uploaded anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
import time

from .workflow import NODE_KINDS

STATUS = {
    'active': ('●', 'En cours'),
    'waiting': ('◷', 'En attente'),
    'ocr_yes': ('◐', 'OCR positif (1/2)'),
    'ocr_no': ('○', 'OCR absent'),
    'done': ('✓', 'Terminé'),
    'paused': ('Ⅱ', 'En pause'),
    'blocked': ('!', 'Bloqué'),
    'timeout': ('⌛', 'Timeout'),
    'attempt': ('!', 'Envoi engagé'),
    'response': ('◷', 'Réponse Codex'),
    'restored': ('↶', 'Restauré'),
}
REASONS = {
    'entered': 'Entrée dans le bloc',
    'resumed': 'Exécution reprise par l’utilisateur',
    'restored': 'Reprise en pause ; anciennes preuves invalidées',
    'complete': 'Étape franchie',
    'ocr_first': 'Texte attendu détecté : seconde lecture requise',
    'ocr_missing': 'Texte attendu non détecté dans la zone OCR',
    'ocr_confirmed': 'Texte attendu confirmé deux fois',
    'ocr_unavailable': 'Image OCR non accessible',
    'ocr_error': 'Lecture OCR impossible',
    'codex_busy': 'Codex occupé, quota atteint ou erreur UI',
    'state_first': 'État attendu détecté : seconde lecture requise',
    'state_missing': 'État Codex différent de celui attendu',
    'state_confirmed': 'État Codex confirmé deux fois',
    'no_target': 'Fenêtre Devin et projet non vérifiés',
    'event_wait': 'Événement attendu non encore confirmé',
    'branch_yes': 'Condition validée : sortie OUI',
    'branch_no': 'Condition validée : sortie NON',
    'quota_active': 'Quota actif : heure de réinitialisation attendue',
    'quota_unverified': 'Réinitialisation non confirmée par OCR',
    'quota_confirmed': 'Fin du quota vérifiée',
    'approval': 'Approbation locale requise',
    'timeout': 'Délai maximal écoulé sans preuve suffisante',
    'send_attempt': 'Envoi engagé : protection anti-doublon active',
    'send_unconfirmed': 'Envoi non confirmé : vérifier Codex manuellement',
    'response_wait': 'Attente de la nouvelle réponse Codex',
    'response_confirmed': 'Nouvelle réponse Codex terminée et confirmée',
    'response_blocked': 'Réponse interrompue par quota ou erreur',
    'idle_unavailable': 'Contrôle d’inactivité indisponible',
    'debug_breakpoint': 'Débogueur : arrêt avant le bloc, utiliser Pas ou Continuer',
    'manual_pause': 'Pause demandée par l’utilisateur',
    'manual_stop': 'Arrêt demandé par l’utilisateur',
    'safety_block': 'Suspension par sécurité',
    'send_done': 'Prompt envoyé ; réponse vérifiée',
}


@dataclass(frozen=True)
class TraceRecord:
    node_id: str
    kind: str
    status: str
    reason: str
    duration: float
    clock: str

    @property
    def caption(self):
        return f'{STATUS[self.status][0]} {STATUS[self.status][1]}'

    @property
    def explanation(self):
        return REASONS[self.reason]


def duration_label(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f'{seconds // 3600}h {seconds % 3600 // 60:02d}m {seconds % 60:02d}s'
    return f'{seconds // 60:02d}:{seconds % 60:02d}'


class TraceJournal:
    def __init__(self, monotonic=None, limit=200):
        self.monotonic = monotonic or time.monotonic
        self.limit = max(10, min(1000, int(limit)))
        self.events: list[TraceRecord] = []
        self.latest: dict[str, TraceRecord] = {}
        self.started: dict[str, float] = {}
        self.revision = 0

    def reset(self):
        self.events.clear()
        self.latest.clear()
        self.started.clear()
        self.revision += 1

    def enter(self, node, at=None):
        at = self.monotonic() if at is None else at
        self.started[node.id] = at
        return self.record(node, 'active', 'entered', at=at)

    def record(self, node, status, reason, *, at=None):
        if (status not in STATUS or reason not in REASONS or
                node.kind not in NODE_KINDS or
                not isinstance(node.id, str) or
                not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', node.id)):
            raise ValueError('Invalid structured workflow trace')
        previous = self.latest.get(node.id)
        if previous is not None and previous.status == status and previous.reason == reason:
            return previous
        at = self.monotonic() if at is None else at
        elapsed = max(0.0, at - self.started.get(node.id, at))
        entry = TraceRecord(node.id, node.kind, status, reason, elapsed,
                            datetime.now().strftime('%H:%M:%S'))
        self.events.append(entry)
        if len(self.events) > self.limit:
            del self.events[:len(self.events) - self.limit]
        self.latest[node.id] = entry
        self.revision += 1
        return entry

    def summary(self, node_id, now=None):
        record = self.latest.get(node_id)
        if record is None:
            return '· Non exécuté', 'Aucune observation pour ce bloc'
        elapsed = record.duration
        if record.status in ('active', 'waiting', 'ocr_yes', 'ocr_no', 'response'):
            at = self.monotonic() if now is None else now
            elapsed = max(0, at - self.started.get(node_id, at))
        label = f'{record.caption} · {duration_label(elapsed)}'
        tooltip = f'{record.explanation}\nDurée dans le bloc : {duration_label(elapsed)}'
        return label, tooltip

    def lines(self, node_names=None):
        """Chronological, redacted summary: labels only, never node params/OCR."""
        names = node_names or {}
        return [f'{r.clock}  {names.get(r.node_id, r.kind)}  '
                f'{r.caption}  {duration_label(r.duration)}  —  {r.explanation}'
                for r in self.events]
