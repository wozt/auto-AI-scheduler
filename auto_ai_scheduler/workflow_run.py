"""Fail-closed, single-path visual workflow runner.

Only the existing Scheduler may interact with X11/Codex. Starting a workflow
requires an explicit GUI action. Never evaluate OCR content as instructions.
No automated execution resumes on application launch or after a crash.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import copy
import hashlib
import re
import time

from . import schedule, x11
from .proofs import match_text, ocr_image, parse_roi
from .workflow import Workflow

TRIGGERS = {'clock', 'delay', 'event'}
CONDITIONS = {'ocr', 'state'}
ACTIONS = {'send', 'scroll', 'notify', 'wait'}
EVENTS = {'cycle_finished', 'quota', 'error', 'ready'}
STATES = {'READY', 'RUNNING', 'FINISHED', 'QUOTA', 'ERROR'}
WAITABLE = {'event', 'state', 'ocr', 'branch', 'quota_wait'}


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


def parse_quota_reset(text):
    """Read an explicit English Codex local-time deadline; never guess one.

    e.g. "Your rate limit resets on Nov 6, 2026, 12:28 AM".
    A missing year, time, AM/PM, or malformed date fails closed.
    """
    if not isinstance(text, str):
        return None
    match = re.search(
        r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+'
        r'(\d{1,2}),?\s+(20\d{2}),?\s+(\d{1,2}):(\d{2})\s*(AM|PM)\b',
        text, re.I)
    if not match:
        return None
    month, day, year, hour, minute, meridian = match.groups()
    months = dict(zip(('jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'),range(1,13)))
    h, minute = int(hour), int(minute)
    if not (1 <= h <= 12 and 0 <= minute < 60):
        return None
    h = h % 12 + (12 if meridian.upper() == 'PM' else 0)
    try:
        # Do not use strptime(%b): French LC_TIME would reject English "Nov".
        return datetime(int(year), months[month.lower()[:3]], int(day), h, minute)
    except ValueError:
        return None


def execution_plan(graph: Workflow, proofs):
    """Compile a single active path through an acyclic graph with optional YES/NO.

    There is no concurrency or implicit repetition. A join is permitted because
    only one route is active at a time; a second branch cannot replay a send.
    """
    if not any(n.kind in ('branch', 'quota_wait') for n in graph.nodes):
        path = execution_path(graph, proofs)
        return path, {node.id: {'next': path[i+1].id if i+1 < len(path) else None}
                      for i, node in enumerate(path)}
    if not graph.nodes or len(graph.nodes) > 50 or graph.has_cycle():
        raise ValueError('Scénario vide, trop long ou cyclique')
    nodes = {n.id: n for n in graph.nodes}
    if len(nodes) != len(graph.nodes):
        raise ValueError('Identifiants de blocs dupliqués')
    outgoing = {ident: {} for ident in nodes}
    parents = {ident: [] for ident in nodes}
    for edge in graph.edges:
        if not isinstance(edge, (list, tuple)) or len(edge) not in (2, 3):
            raise ValueError('Connexion invalide')
        src, dst = edge[:2]
        if src not in nodes or dst not in nodes or src == dst:
            raise ValueError('Connexion vers un bloc introuvable')
        slot = edge[2] if len(edge) == 3 else 'next'
        if nodes[src].kind == 'branch':
            if slot not in ('yes', 'no'):
                raise ValueError('Chaque sortie du branchement doit être yes ou no')
        elif slot != 'next':
            raise ValueError('Sortie conditionnelle sur un bloc non conditionnel')
        if slot in outgoing[src]:
            raise ValueError('Une sortie ne peut alimenter deux blocs')
        outgoing[src][slot] = dst
        parents[dst].append((src, slot))
    roots = [ident for ident in nodes if not parents[ident]]
    if len(roots) != 1 or nodes[roots[0]].kind not in TRIGGERS:
        raise ValueError('Le scénario doit avoir un seul bloc déclencheur au départ')
    discovered = set()
    pending = [roots[0]]
    while pending:
        ident = pending.pop()
        if ident in discovered:
            continue
        discovered.add(ident)
        pending.extend(outgoing[ident].values())
    if len(discovered) != len(nodes):
        raise ValueError('Tous les blocs doivent être reliés au départ')
    for node in graph.nodes:
        p = node.params
        kind = node.kind
        slots = outgoing[node.id]
        if kind == 'branch':
            if set(slots) != {'yes', 'no'}:
                raise ValueError('Le bloc Si / Sinon exige deux sorties OUI et NON')
            if p.get('source') not in ('state', 'ocr'):
                raise ValueError('Branchement : source doit être state ou ocr')
            if p['source'] == 'state':
                if p.get('state') not in STATES:
                    raise ValueError('Branchement : état Codex non reconnu')
            else:
                if not p.get('expected','').strip() or len(p['expected']) > 240:
                    raise ValueError('Branchement OCR : texte attendu requis')
                parse_roi(p.get('roi',''))
                if not proofs._file(p.get('proof_id','')).is_file():
                    raise ValueError('Branchement OCR : capture locale introuvable')
        elif len(slots) > 1:
            raise ValueError('Seul le bloc Si / Sinon peut avoir deux sorties')
        if kind == 'clock':
            if parents[node.id]:
                raise ValueError('Horloge uniquement en début de chaîne')
            schedule.parse_local(p.get('at',''))
        elif kind == 'event':
            if parents[node.id] or p.get('event') not in EVENTS:
                raise ValueError('Événement invalide ou placé après le départ')
        elif kind in ('delay', 'wait'):
            _number(p.get('seconds'), 1, 86400, kind)
        elif kind == 'state':
            if p.get('state') not in STATES:
                raise ValueError('État Codex inconnu')
        elif kind == 'ocr':
            if not p.get('expected','').strip() or len(p['expected']) > 240:
                raise ValueError('Preuve OCR manquante')
            parse_roi(p.get('roi',''))
            if not proofs._file(p.get('proof_id','')).is_file():
                raise ValueError('Image de preuve OCR locale introuvable')
        elif kind == 'send':
            prompt = p.get('prompt','')
            if not prompt.strip() or '\n' in prompt or len(prompt) > 4000:
                raise ValueError('Prompt manquant ou invalide')
            if not parents[node.id] or any(
                nodes[src].kind not in ('state','ocr','branch') or
                (nodes[src].kind == 'branch' and (slot != 'yes' or
                 (nodes[src].params.get('source') == 'state' and
                  nodes[src].params.get('state') in ('QUOTA','ERROR'))))
                for src, slot in parents[node.id]
            ):
                raise ValueError('Chaque entrée d’un envoi doit être une preuve OCR/État ou Si/OUI')
        elif kind == 'scroll':
            _number(p.get('steps'),1,80,'scroll')
        elif kind == 'notify':
            if not (1 <= len(p.get('subject','')) <= 200):
                raise ValueError('Sujet de rapport invalide')
        elif kind == 'quota_wait':
            if p.get('resume','manual') not in ('manual', 'auto'):
                raise ValueError('Reprise quota : manual ou auto uniquement')
        elif kind != 'branch':
            raise ValueError('Bloc non pris en charge : ' + kind)
        if kind in WAITABLE:
            _number(p.get('timeout_seconds','0'),0,86400,'timeout_seconds')
    return graph.ordered(), outgoing


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
        self.links = {}
        self.positions = {}
        self.quota_seen = False
        self.quota_deadline = None
        self.quota_confirmation = 0

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
        path, links = execution_plan(frozen, self.proofs)
        self.path = path
        self.links = links
        self.positions = {node.id: i for i, node in enumerate(path)}
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
        self.quota_seen = False
        self.quota_deadline = None
        self.quota_confirmation = 0
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

    def _advance(self, route='next'):
        old = self.current
        destination = self.links.get(old.id, {}).get(route)
        self.index = self.positions[destination] if destination is not None else len(self.path)
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

    def _branch_tick(self, node, now):
        p = node.params
        obs, image = self._observe(require_image=p.get('source') == 'ocr')
        if obs is None:
            self._confirmed(None)
            return
        # An unexpected quota, error or still-busy response is NOT a reliable
        # negative result. Stay in place instead of entering the wrong branch.
        if obs.state in ('QUOTA','ERROR') and (
                p.get('source') != 'state' or obs.state != p.get('state')):
            self._confirmed(None)
            return
        if obs.state == 'RUNNING' and p.get('state') != 'RUNNING':
            self._confirmed(None)
            return
        if p['source'] == 'state':
            direction = 'yes' if obs.state == p['state'] else 'no'
            if obs.state not in ('READY', 'RUNNING', 'FINISHED', 'QUOTA', 'ERROR'):
                self._confirmed(None)
                return
            marker = 'state:' + obs.state
        else:
            if image is None or obs.state not in ('READY','FINISHED'):
                self._confirmed(None)
                return
            try:
                actual = ocr_image(image, p['roi'])
                if not actual.strip():
                    self._confirmed(None)
                    return
                direction = 'yes' if match_text(actual,p['expected']) else 'no'
                marker = hashlib.sha256(actual.casefold().encode()).hexdigest()[:16]
            except Exception:
                self._confirmed(None)
                return
        if self._confirmed('branch:' + direction + ':' + marker):
            # Negative proof cannot authorize a direct send. Positive evidence
            # still needs a fresh verification at the destination Send block.
            self.evidence_at = now if direction == 'yes' else None
            self.log('Condition ' + node.label + ' → ' + ('OUI' if direction == 'yes' else 'NON'))
            self._advance(route=direction)

    def _quota_tick(self, node, now):
        obs, _ = self._observe()
        if obs is None:
            self._confirmed(None)
            return
        wall_now = self.wall_clock()
        if obs.state == 'QUOTA':
            deadline = parse_quota_reset(obs.reset_text)
            # A deadline in the past suggests stale/misread OCR. No guessing.
            if deadline is None or deadline + timedelta(minutes=5) < wall_now:
                self.pause('Quota : heure OCR absente ou incohérente ; vérification manuelle')
                return
            if self._confirmed('quota-reset:' + deadline.isoformat()):
                if not self.quota_seen:
                    self.log('Limite détectée · reprise pas avant ' + deadline.strftime('%Y-%m-%d %H:%M'))
                self.quota_seen = True
                self.quota_deadline = deadline
            return
        if not self.quota_seen or self.quota_deadline is None:
            # A stale READY card does not prove there was ever a quota.
            self._confirmed(None)
            return
        if wall_now < self.quota_deadline:
            self._confirmed(None)
            return
        if obs.state not in ('READY','FINISHED') or obs.composer is None:
            self._confirmed(None)
            return
        if self._confirmed('quota-cleared:' + obs.state):
            if node.params.get('resume','manual') == 'manual' and not self.approved:
                self.phase = 'approval'
                self.log('Quota écoulé et disparu · approbation locale requise')
                return
            self.log('Quota disparu et heure dépassée · suite du scénario autorisée')
            self.quota_seen = False
            self.quota_deadline = None
            self._advance()

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
        if kind in WAITABLE:
            timeout = _number(p.get('timeout_seconds','0'),0,86400,'timeout_seconds')
            if timeout and now - self.entered_at >= timeout:
                self.pause(f'Timeout du bloc {node.label} après {timeout}s · aucune action exécutée')
                if self.engine.config.get('email_on_error'):
                    self.engine._notify('Scénario en timeout',f'Bloc : {node.label} · scénario suspendu')
                return
        if kind == 'branch':
            self._branch_tick(node, now)
        elif kind == 'quota_wait':
            self._quota_tick(node, now)
        elif kind == 'clock':
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
