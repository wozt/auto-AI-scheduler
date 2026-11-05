"""Fail-closed, single-path visual workflow runner.

Only the existing Scheduler may interact with X11/Codex. Starting a workflow
requires an explicit GUI action. Never evaluate OCR content as instructions.
No automated execution resumes on application launch or after a crash.
"""
from __future__ import annotations

from datetime import datetime
import copy
import time

from . import schedule, x11
from .proofs import match_text, ocr_image, parse_roi
from .workflow import Workflow

TRIGGERS = {'clock', 'delay', 'event'}
CONDITIONS = {'ocr', 'state'}
ACTIONS = {'send', 'scroll', 'notify', 'wait'}
EVENTS = {'cycle_finished', 'quota', 'error', 'ready'}
STATES = {'READY', 'RUNNING', 'FINISHED', 'QUOTA', 'ERROR'}


def _number(value, minimum, maximum, name):
    try:
        n = int(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'{name}: entier requis') from exc
    if not minimum <= n <= maximum:
        raise ValueError(f'{name}: valeur hors limites ({minimum}..{maximum})')
    return n


def execution_path(graph: Workflow, proofs):
    """Validate a strictly linear, connected path before ANY desktop action.

    Branching/parallelism and automatic loops are deliberately unsupported
    until the executor can guarantee exactly-once delivery on every branch.
    """
    if not graph.nodes or len(graph.nodes) > 50 or graph.has_cycle():
        raise ValueError('Scénario vide, trop long ou cyclique')
    node_map = {n.id: n for n in graph.nodes}
    if len(node_map) != len(graph.nodes):
        raise ValueError('Identifiants de blocs dupliqués')
    out, indegree = {}, {n.id: 0 for n in graph.nodes}
    for src, dst in graph.edges:
        if src not in node_map or dst not in node_map or src in out:
            raise ValueError('Branchement ambigu ou invalide : une seule sortie par bloc')
        out[src] = dst
        indegree[dst] += 1
        if indegree[dst] > 1:
            raise ValueError('Fusion de branches non prise en charge')
    roots = [n.id for n in graph.nodes if indegree[n.id] == 0]
    if len(roots) != 1:
        raise ValueError('Le scénario doit avoir un seul bloc de départ connecté')
    path, at = [], roots[0]
    while at:
        path.append(node_map[at])
        at = out.get(at)
    if len(path) != len(graph.nodes) or path[0].kind not in TRIGGERS:
        raise ValueError('Le premier bloc doit être un déclencheur, tous les blocs reliés')
    evidence_since_send = False
    for i, node in enumerate(path):
        p = node.params
        if node.kind in ('clock', 'event') and i != 0:
            raise ValueError('Déclencheur utilisable uniquement au début du scénario')
        if node.kind == 'clock':
            schedule.parse_local(p.get('at', ''))
        elif node.kind in ('delay', 'wait'):
            _number(p.get('seconds'), 1, 86400, node.kind)
        elif node.kind == 'event':
            if p.get('event') not in EVENTS:
                raise ValueError('Événement non pris en charge')
        elif node.kind == 'state':
            if p.get('state') not in STATES:
                raise ValueError('État Codex non pris en charge')
            evidence_since_send = True
        elif node.kind == 'ocr':
            proof_id = p.get('proof_id', '')
            expected = p.get('expected', '').strip()
            if not expected or len(expected) > 240:
                raise ValueError('Bloc OCR : texte attendu manquant ou trop long')
            parse_roi(p.get('roi', ''))
            # The reference must be a previously imported, locally stored image;
            # checking the extension alone is never sufficient.
            if not proofs._file(proof_id).is_file():
                raise ValueError('Bloc OCR : référence locale introuvable')
            evidence_since_send = True
        elif node.kind == 'send':
            prompt = p.get('prompt', '')
            if not prompt.strip() or '\n' in prompt or len(prompt) > 4000:
                raise ValueError('Bloc prompt : texte manquant ou invalide')
            if not evidence_since_send or i == 0 or path[i-1].kind not in CONDITIONS:
                raise ValueError('Placez une condition OCR/État immédiatement avant chaque envoi')
            evidence_since_send = False
        elif node.kind == 'scroll':
            _number(p.get('steps'), 1, 80, 'scroll')
        elif node.kind == 'notify':
            if not (1 <= len(p.get('subject', '')) <= 200):
                raise ValueError('Sujet de notification invalide')
        else:
            raise ValueError(f'Bloc non pris en charge : {node.kind}')
    return path


class WorkflowRunner:
    """Explicitly armed runner; one transition or action per tick.

    A cycle-finished EVENT needs a RUNNING observation witnessed AFTER arming.
    An outgoing prompt requires two consistent OCR/state observations and
    Scheduler's own fresh project/composer/idle/cooldown validations.
    """
    def __init__(self, engine, proof_store, log, monotonic=None, wall_clock=None):
        self.engine = engine
        self.proofs = proof_store
        self.log = log
        self.monotonic = monotonic or time.monotonic
        self.wall_clock = wall_clock or datetime.now
        self.phase = 'stopped'
        self.path = []
        self.index = 0
        self.entered_at = 0.0
        self.stable = 0
        self.last_match = None
        self.saw_running = False
        self.waiting_response = False
        self.sent_at = 0.0
        self.sent_marker = ''
        self.approved = False
        self.last_reason = ''
        self.send_attempted = False
        self.evidence_at = None

    @property
    def current(self):
        return self.path[self.index] if self.index < len(self.path) else None

    def start(self, graph):
        if self.phase not in ('stopped', 'done', 'error'):
            raise ValueError('Scénario déjà armé ; utilisez Reprendre ou Stop')
        if self.engine.enabled or self.engine.session.waiting_for_response or self.engine.force_test_pending:
            raise ValueError('Arrêtez la surveillance ou attendez la réponse Codex avant de démarrer')
        # Snapshot to prevent GUI edits from changing an armed automation.
        frozen = Workflow.from_dict(copy.deepcopy(graph.to_dict()))
        path = execution_path(frozen, self.proofs)
        self.path = path
        self.index = 0
        self.entered_at = self.monotonic()
        self.stable = 0
        self.last_match = None
        self.saw_running = False
        self.waiting_response = False
        self.approved = False
        self.last_reason = ''
        self.send_attempted = False
        self.evidence_at = None
        self.phase = 'running'
        self.log(f'Scénario armé : {len(path)} blocs · aucune action sans vérification')

    def pause(self, reason='Pause demandée'):
        if self.phase in ('running', 'approval'):
            self.phase = 'paused'
            self.last_reason = reason
            self.log(reason)
            return True
        return False

    def resume(self):
        if self.phase != 'paused':
            return False
        if self.send_attempted and not self.waiting_response:
            raise ValueError('Envoi incertain : Stop puis vérifiez Codex avant de réarmer le scénario')
        self.phase = 'approval' if (self.current is not None and self.current.kind == 'send'
                                    and self.engine.config['mode'] == 'approval' and not self.approved) else 'running'
        self.log('Scénario repris · contrôles de sécurité conservés')
        return True

    def stop(self):
        if self.phase == 'stopped':
            return False
        self.phase = 'stopped'
        self.log('Scénario arrêté · aucun redémarrage automatique')
        return True

    def approve(self):
        if self.phase != 'approval':
            return False
        self.approved = True
        self.phase = 'running'
        self.log('Prochain envoi du scénario approuvé localement')
        return True

    def _advance(self):
        old = self.current
        self.index += 1
        self.entered_at = self.monotonic()
        self.stable = 0
        self.last_match = None
        self.saw_running = False
        self.approved = False
        self.send_attempted = False
        if self.current is None:
            self.phase = 'done'
            self.log('Scénario terminé · aucune reprise automatique')
        else:
            self.log(f'Bloc {old.label} terminé → {self.current.label}')

    def _confirmed(self, identifier):
        if identifier is None:
            self.stable = 0
            self.last_match = None
            return False
        self.stable = self.stable + 1 if identifier == self.last_match else 1
        self.last_match = identifier
        return self.stable >= 2

    def _observe(self, require_image=False):
        image = []
        def action(wid, obs, offset):
            if require_image:
                current, _ = x11.screenshot(wid)
                image.append(current)
        obs = self.engine.inspect_target(action if require_image else None)
        return obs, (image[0] if image else None)

    def _response_tick(self, now):
        obs, _ = self._observe()
        if obs is None:
            return
        if obs.state in ('QUOTA', 'ERROR'):
            self.pause('Réponse interrompue : ' + obs.state + ' · reprise manuelle requise')
            return
        if now - self.sent_at < 25:
            return
        marker = obs.cycle_marker
        if (obs.state == 'FINISHED' and obs.composer is not None and marker and
                marker != self.sent_marker and self._confirmed('response:' + marker)):
            self.waiting_response = False
            self.engine.session.waiting_for_response = False
            self.log('Réponse Codex terminée et vérifiée deux fois')
            self._advance()
        elif obs.state != 'FINISHED' or not marker or marker == self.sent_marker:
            self._confirmed(None)

    def tick(self):
        if self.phase != 'running':
            return
        # Fail closed: never move windows if the user is actively using them.
        try:
            if x11.user_idle_seconds() < self.engine.config['idle_seconds']:
                return
        except Exception as exc:
            self.pause('Détection d’inactivité indisponible ; scénario suspendu')
            return
        now = self.monotonic()
        if self.waiting_response:
            self._response_tick(now)
            return
        node = self.current
        if node is None:
            self.phase = 'done'
            return
        p = node.params
        kind = node.kind
        if kind == 'clock':
            if self.wall_clock() >= schedule.parse_local(p['at']):
                self._advance()
        elif kind in ('delay', 'wait'):
            if now - self.entered_at >= _number(p['seconds'], 1, 86400, kind):
                self._advance()
        elif kind in ('event', 'state', 'ocr'):
            obs, image = self._observe(require_image=kind == 'ocr')
            if obs is None:
                self._confirmed(None)
                return
            if kind == 'event':
                event = p['event']
                if event == 'cycle_finished':
                    if obs.state == 'RUNNING':
                        self.saw_running = True
                        self._confirmed(None)
                        return
                    key = ('finished:' + obs.cycle_marker if self.saw_running and
                           obs.state == 'FINISHED' and obs.cycle_marker and obs.composer else None)
                else:
                    want = {'quota': 'QUOTA', 'error': 'ERROR', 'ready': 'READY'}[event]
                    key = 'event:' + want if obs.state == want else None
            elif kind == 'state':
                key = 'state:' + p['state'] if obs.state == p['state'] else None
            else:
                if image is None or obs.state in ('QUOTA','ERROR','RUNNING'):
                    key = None
                else:
                    try:
                        actual = ocr_image(image, p['roi'])
                        key = 'ocr:' + node.id if match_text(actual, p['expected']) else None
                    except Exception:
                        key = None
            if self._confirmed(key):
                if kind in CONDITIONS:
                    self.evidence_at = now
                self._advance()
        elif kind == 'send':
            # A long pause, approval dialog, or desktop interruption can make
            # a prior proof obsolete. Recheck it before a live send.
            if self.evidence_at is None or now - self.evidence_at > 12:
                self.index -= 1
                self.stable = 0
                self.last_match = None
                self.entered_at = now
                self.log('Preuve périmée : nouvelle vérification avant envoi')
                return
            if self.engine.config['mode'] == 'approval' and not self.approved:
                self.phase = 'approval'
                self.log('Confirmation locale requise pour envoyer le prochain prompt')
                return
            if self.engine.pending_approval:
                self.pause('Approbation Codex en attente : scénario suspendu')
                return
            # Only Scheduler may type, after live target verification.
            # Mark the attempt before interacting with X11. A partial xdotool
            # failure must never result in a second automatic send on next tick.
            if self.send_attempted:
                self.pause('Envoi déjà tenté : intervention manuelle nécessaire')
                return
            self.send_attempted = True
            try:
                delivered = self.engine.send('workflow', prompt_override=p['prompt'])
            except Exception:
                delivered = False
            if not delivered:
                self.pause('Envoi non confirmé : arrêt de sécurité, vérifiez Codex')
                return
            self.waiting_response = True
            self.sent_at = self.monotonic()
            self.sent_marker = self.engine.session.baseline_marker
            self.stable = 0
            self.last_match = None
            self.approved = False
            self.log('Prompt du scénario envoyé · attente d’une nouvelle fin vérifiée')
        elif kind == 'scroll':
            # Scheduler rechecks project and restores focus and pointer.
            if self.engine.scroll(steps_override=_number(p['steps'], 1, 80, 'scroll')):
                self._advance()
        elif kind == 'notify':
            self.engine._notify(p['subject'], 'Événement du scénario pour le projet configuré.')
            self._advance()
        else:
            self.pause('Type de bloc inconnu ; scénario suspendu')
