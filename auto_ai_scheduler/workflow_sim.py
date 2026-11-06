"""Entirely offline, deterministic preview of a visual workflow.

This module calls no X11/SMTP/Scheduler action. The graph validator is shared
with the live runner, but this preview never invokes a runner or desktop API.
It does NOT move the live cursor or persist any observed OCR result.
Only already-imported private ProofStore images can be replayed as OCR samples.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from . import schedule
from .proofs import match_text, ocr_image
from .workflow import Workflow
from .workflow_run import execution_plan, EVENTS, STATES, WAITABLE


@dataclass(frozen=True)
class SimulationInputs:
    codex_state: str = 'UNKNOWN'
    event: str = ''
    at: datetime | None = None
    elapsed_seconds: int = 0
    saw_running: bool = False
    use_reference_images: bool = False
    observed: Mapping[str, str] = field(default_factory=dict)
    quota_was_seen: bool = False
    quota_deadline: datetime | None = None
    quota_approved: bool = False


@dataclass(frozen=True)
class SimStep:
    node_id: str
    kind: str
    title: str
    status: str  # ok, yes, no, would, waiting, blocked
    reason: str  # predeclared safe, redacted description
    route: str = 'next'

    @property
    def caption(self):
        icon = {'ok':'✓', 'yes':'✓', 'no':'↪', 'would':'◇',
                'waiting':'◷', 'blocked':'!'}[self.status]
        return icon + ' ' + self.reason


@dataclass(frozen=True)
class SimulationResult:
    steps: tuple[SimStep, ...]
    outcome: str  # complete, waiting, blocked
    next_node_id: str | None

    @property
    def latest(self):
        return {step.node_id:step for step in self.steps}

    def lines(self):
        header = ('SIMULATION HORS LIGNE — aucun envoi / clic / scroll / mail')
        result = [header]
        for index, step in enumerate(self.steps, 1):
            result.append(f'{index:02d}  {step.title}  {step.caption}')
        result.append('Résultat : ' + {
            'complete':'Parcours simulé terminé (aucune action réelle)',
            'waiting':'Attente simulée : modifier les hypothèses',
            'blocked':'Simulation bloquée : preuve ou hypothèse manquante',
        }[self.outcome])
        return result


def _sample_text(proofs, node, cfg: SimulationInputs):
    """Return (matched, source) or (None, source); never expose OCR text."""
    proof_id = cfg.observed.get(node.id)
    origin = 'capture locale choisie'
    if proof_id is None and cfg.use_reference_images:
        proof_id = node.params.get('proof_id')
        origin = 'référence (démo seulement)'
    if proof_id is None:
        return None, 'aucune capture d’observation sélectionnée'
    try:
        if not proofs._file(proof_id).is_file():
            return None, 'capture locale introuvable'
        sample = proofs.get_image(proof_id)
        text = ocr_image(sample, node.params.get('roi','0,0,100,100'))
    except Exception:
        # OCR wrappers may raise engine-specific exceptions; fail closed and
        # never expose their messages or local file paths in the UI.
        return None, 'lecture OCR impossible'
    if not text.strip():
        return None, 'aucun texte OCR exploitable'
    return match_text(text, node.params.get('expected', '')), origin


def simulate(graph: Workflow, proofs, inputs: SimulationInputs | None = None):
    """Compile then walk one path; actions are ALWAYS represented as descriptions.

    A missing observation is unknown, not a failed OCR condition; therefore it
    cannot incorrectly select the NO exit. Quota reset cannot be assumed.
    """
    cfg = inputs or SimulationInputs()
    if cfg.codex_state not in STATES | {'UNKNOWN'}:
        raise ValueError('État simulé invalide')
    if cfg.event not in EVENTS | {''}:
        raise ValueError('Événement simulé invalide')
    if not isinstance(cfg.elapsed_seconds, int) or not 0 <= cfg.elapsed_seconds <= 86400:
        raise ValueError('Durée simulée invalide')
    if cfg.at is not None and not isinstance(cfg.at, datetime):
        raise ValueError('Date simulée invalide')
    if cfg.quota_deadline is not None and not isinstance(cfg.quota_deadline, datetime):
        raise ValueError('Échéance simulée invalide')
    if not isinstance(cfg.observed, Mapping):
        raise ValueError('Captures simulées invalides')
    # Reuse exactly the live graph validator, but never instantiate its runner.
    # Also snap a copy: even caller mutations do not affect an in-progress walk.
    frozen = Workflow.from_dict(graph.to_dict())
    path, links = execution_plan(frozen, proofs)
    nodes = {node.id: node for node in path}
    current = path[0].id
    steps = []
    evidence = False
    now = cfg.at or datetime.now()

    for _ in range(len(path)):
        node = nodes[current]
        p = node.params
        status, reason, route = 'ok', 'Bloc validé (hypothèse)', 'next'
        kind = node.kind
        # Live runner pauses BEFORE handling a timed-out waitable condition.
        # The preview must not show a green send after the same timeout.
        timeout = int(p.get('timeout_seconds','0')) if kind in WAITABLE else 0
        if timeout and cfg.elapsed_seconds >= timeout:
            status, reason = 'blocked', 'Timeout simulé : condition non validée'
            steps.append(SimStep(node.id, kind, node.label, status, reason, route))
            return SimulationResult(tuple(steps),status,node.id)
        if kind == 'clock':
            if now < schedule.parse_local(p['at']):
                status, reason = 'waiting', 'Heure programmée non atteinte'
            else:
                reason = 'Horaire atteint (horloge simulée)'
        elif kind in ('delay','wait'):
            if cfg.elapsed_seconds < int(p['seconds']):
                status, reason = 'waiting', 'Durée simulée insuffisante'
            else:
                reason = 'Délai écoulé (durée simulée)'
        elif kind == 'event':
            if cfg.event != p['event']:
                status, reason = 'waiting', 'Événement simulé absent'
            elif p['event'] == 'cycle_finished' and not (
                    cfg.saw_running and cfg.codex_state == 'FINISHED'):
                status, reason = 'waiting', 'Fin de cycle non prouvée par un RUNNING préalable'
            else:
                reason = 'Événement fourni par les hypothèses'
        elif kind == 'state':
            if cfg.codex_state == 'UNKNOWN':
                status, reason = 'blocked', 'État Codex simulé inconnu'
            elif cfg.codex_state != p['state']:
                status, reason = 'waiting', 'État Codex simulé différent'
            else:
                evidence = True
                reason = 'État déclaré conforme (simulation)'
        elif kind == 'ocr':
            matched, source = _sample_text(proofs, node, cfg)
            if matched is None:
                status, reason = 'blocked', source
            elif not matched:
                status, reason = 'waiting', 'OCR absent dans ' + source
            else:
                evidence = True
                reason = 'OCR positif : ' + source
        elif kind == 'branch':
            if p['source'] == 'state':
                if cfg.codex_state == 'UNKNOWN':
                    status, reason = 'blocked', 'État simulé inconnu : aucune branche choisie'
                else:
                    decision = cfg.codex_state == p['state']
                    reason = 'Comparaison avec état Codex déclaré'
            else:
                matched, source = _sample_text(proofs, node, cfg)
                if matched is None:
                    status, reason = 'blocked', source + ' : aucune branche choisie'
                else:
                    decision = matched
                    reason = 'Branchement OCR : ' + source
            if status == 'ok':
                route = 'yes' if decision else 'no'
                status = route
                reason += ' → ' + ('OUI' if decision else 'NON')
                evidence = decision
        elif kind == 'quota_wait':
            if not cfg.quota_was_seen or cfg.quota_deadline is None:
                status, reason = 'blocked', 'Quota et échéance non attestés dans les hypothèses'
            elif now < cfg.quota_deadline:
                status, reason = 'waiting', 'Heure de réinitialisation non atteinte'
            elif cfg.codex_state not in ('READY','FINISHED'):
                status, reason = 'waiting', 'Disparition du quota non confirmée'
            elif p.get('resume','manual') == 'manual' and not cfg.quota_approved:
                status, reason = 'waiting', 'Approbation locale simulée requise'
            else:
                reason = 'Reprise hypothétique ; contrôle réel requis'
        elif kind == 'send':
            if not evidence or cfg.codex_state not in ('READY','FINISHED'):
                status, reason = 'blocked', 'Envoi non autorisé par la preuve et l’état simulés'
            else:
                status, reason = 'would', 'Envoi de prompt prévu — NON ENVOYÉ'
                evidence = False
        elif kind == 'scroll':
            status, reason = 'would', 'Défilement prévu — NON EXÉCUTÉ'
        elif kind == 'notify':
            status, reason = 'would', 'Mail prévu — NON ENVOYÉ'
        else:
            raise ValueError('Type de bloc non simulable')
        steps.append(SimStep(node.id, kind, node.label, status, reason, route))
        if status in ('waiting','blocked'):
            return SimulationResult(tuple(steps),status,node.id)
        current = links[node.id].get(route)
        if current is None:
            return SimulationResult(tuple(steps),'complete',None)
    raise ValueError('Simulation interrompue : parcours trop long')
