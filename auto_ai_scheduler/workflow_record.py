"""Opt-in, privacy-minimal Codex observation sequences and offline replay.

Live capture reads a verified window through Scheduler; it NEVER dispatches an
X11 action, sends a prompt, or saves a screenshot / raw OCR text. Only an
explicit GUI recording session writes bounded, boolean proof results to a
private state file. Replay is strictly offline and NEVER calls Scheduler.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time

from . import x11, schedule
from .proofs import match_text, ocr_image, parse_roi
from .workflow import Workflow
from .workflow_checkpoint import graph_fingerprint
from .workflow_run import STATES, execution_plan, parse_quota_reset
from .proofs import ProofStore
from .workflow_sim import SimStep, SimulationResult

RECORDING_PATH = Path.home() / '.local' / 'share' / 'auto-ai-scheduler' / 'recordings' / 'last.json'
MAX_FRAMES = 120
MAX_BYTES = 160_000
_NODE_ID = re.compile(r'[a-zA-Z0-9_-]{1,64}\Z')
_DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def _local_date(s):
    if not isinstance(s, str) or len(s) > 40:
        raise ValueError('Date d’enregistrement invalide')
    try:
        result = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError('Date d’enregistrement invalide') from exc
    if result.tzinfo is not None:
        raise ValueError('Heure locale sans fuseau requise')
    return result


def validate_recording(document):
    """Validate an untrusted local recording before using any recorded input."""
    if not isinstance(document, dict) or set(document) != {'schema','workflow','frames'}:
        raise ValueError('Enregistrement : format invalide')
    if document['schema'] != 1 or not isinstance(document['workflow'], str) or not _DIGEST.fullmatch(document['workflow']):
        raise ValueError('Enregistrement : empreinte invalide')
    frames = document['frames']
    if not isinstance(frames,list) or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError('Enregistrement : nombre de mesures invalide')
    previous = -1.0
    for frame in frames:
        required={'seconds','local_at','state','composer','marker','matches','quota_reset'}
        if not isinstance(frame,dict) or set(frame) != required:
            raise ValueError('Enregistrement : trame invalide')
        s=frame['seconds']
        if isinstance(s,bool) or not isinstance(s,(int,float)) or not 0 <= s <= 36000 or s <= previous:
            raise ValueError('Enregistrement : temps non monotone')
        previous=s
        # Local clock can move backwards during a DST change; seconds is monotonic.
        _local_date(frame['local_at'])
        if not isinstance(frame['state'],str) or frame['state'] not in STATES | {'UNKNOWN'} or not isinstance(frame['composer'],bool):
            raise ValueError('Enregistrement : état invalide')
        if not isinstance(frame['marker'],str) or (frame['marker'] and not _DIGEST.fullmatch(frame['marker'])):
            raise ValueError('Enregistrement : marqueur invalide')
        reset=frame['quota_reset']
        if reset is not None:
            _local_date(reset)
        matches=frame['matches']
        if not isinstance(matches,dict) or len(matches)>50 or any(
            not isinstance(k,str) or not _NODE_ID.fullmatch(k) or type(v) not in (bool,type(None))
            for k,v in matches.items()
        ):
            raise ValueError('Enregistrement : résultats OCR invalides')
    return document


class RecordingStore:
    """One private recording, never inside Git, with atomic 0600 persistence."""
    def __init__(self, path=RECORDING_PATH):
        self.path=Path(path)

    def save(self, document):
        validate_recording(document)
        data=(json.dumps(document,ensure_ascii=False,separators=(',',':'))+'\n').encode()
        if len(data)>MAX_BYTES:
            raise ValueError('Enregistrement trop volumineux')
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError('Lien symbolique refusé')
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path.parent.chmod(0o700)
        fd,temp=tempfile.mkstemp(prefix='.record-',dir=self.path.parent)
        try:
            os.fchmod(fd,0o600)
            with os.fdopen(fd,'wb') as out:
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp,self.path)
            dfd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(dfd)
            finally:os.close(dfd)
        finally:
            if os.path.exists(temp):os.unlink(temp)

    def load(self):
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError('Lien symbolique refusé')
        if not self.path.exists():return None
        if not self.path.is_file() or self.path.stat().st_size>MAX_BYTES or self.path.stat().st_mode&0o077:
            raise ValueError('Enregistrement inattendu ou permissions trop ouvertes')
        try:
            data=json.loads(self.path.read_text(encoding='utf-8'))
        except (UnicodeError,json.JSONDecodeError) as exc:
            raise ValueError('Enregistrement illisible') from exc
        return validate_recording(data)

    def clear(self):
        if self.path.is_symlink() or self.path.parent.is_symlink():raise ValueError('Lien symbolique refusé')
        if self.path.exists():
            self.path.unlink()
            return True
        return False


class SequenceRecorder:
    """Explicit capture: no recording while the desktop is active."""
    def __init__(self, store=None, clock=None, local_clock=None):
        self.store=store or RecordingStore()
        self.clock=clock or time.monotonic
        self.local_clock=local_clock or datetime.now
        self.active=False
        self.graph=None
        self.digest=None
        self.frames=[]
        self.started=0.0
        self.interval=3
        self.last_sample=None
        self.skipped=0

    def start(self, graph, interval=3):
        if self.active:raise ValueError('Enregistrement déjà actif')
        if isinstance(interval,bool) or not isinstance(interval,int) or not 3<=interval<=30:
            raise ValueError('Intervalle de capture : 3 à 30 secondes')
        frozen=Workflow.from_dict(graph.to_dict())
        if not 1 <= len(frozen.nodes) <= 50:
            raise ValueError('Séquence : scénario vide ou trop de blocs (maximum 50)')
        self.graph=frozen
        self.digest=graph_fingerprint(frozen)
        self.frames=[]
        self.started=self.clock()
        self.last_sample=None
        self.interval=interval
        self.skipped=0
        self.active=True

    def capture(self, engine):
        if not self.active:return False
        now=self.clock()
        if self.last_sample is not None and now-self.last_sample < self.interval:return False
        if len(self.frames)>=MAX_FRAMES:
            self.active=False
            return False
        # Idle check MUST be performed before inspect_target activates X11.
        if x11.user_idle_seconds() < engine.config['idle_seconds']:
            self.skipped+=1
            return False
        image=[]
        candidates=[node for node in self.graph.nodes
                    if node.kind=='ocr' or (node.kind=='branch' and node.params.get('source')=='ocr')]

        def acquire(wid,obs,offset):
            if candidates:
                shot,_=x11.screenshot(wid)
                image.append(shot)

        obs=engine.inspect_target(acquire if candidates else None)
        if obs is None:
            self.skipped+=1
            return False
        matches={}
        for node in candidates:
            decision=None
            if image and obs.state not in ('QUOTA','ERROR','RUNNING'):
                try:
                    parse_roi(node.params.get('roi','0,0,100,100'))
                    actual=ocr_image(image[0],node.params['roi'])
                    if actual.strip():
                        decision=match_text(actual,node.params.get('expected',''))
                except Exception:
                    pass
            matches[node.id]=decision
        reset=parse_quota_reset(obs.reset_text) if obs.state=='QUOTA' else None
        frame={
            'seconds':round(now-self.started,3),
            'local_at':self.local_clock().isoformat(timespec='seconds'),
            'state':obs.state if obs.state in STATES else 'UNKNOWN',
            'composer':obs.composer is not None,
            'marker':hashlib.sha256(obs.cycle_marker.encode()).hexdigest() if obs.cycle_marker else '',
            'matches':matches,
            'quota_reset':reset.isoformat(timespec='seconds') if reset else None,
        }
        self.frames.append(frame)
        self.last_sample=now
        if len(self.frames)>=MAX_FRAMES:self.active=False
        return True

    def stop(self, save=True):
        if not self.active and not self.frames:
            raise ValueError('Aucune mesure enregistrée')
        self.active=False
        if not self.frames:
            raise ValueError('Aucune fenêtre confirmée pendant la capture')
        document={'schema':1,'workflow':self.digest,'frames':self.frames}
        if save:self.store.save(document)
        return document


def replay(graph, recording, proofs=None, *, quota_approved=False):
    """Offline temporal walk. One transition per frame, no desktop or network API."""
    data=validate_recording(recording)
    frozen=Workflow.from_dict(graph.to_dict())
    if data['workflow']!=graph_fingerprint(frozen):
        raise ValueError('Le scénario a changé : replay refusé')
    path, links=execution_plan(frozen, proofs or ProofStore())
    nodes={node.id:node for node in path}
    roots=[n.id for n in path if not any(n.id in x.values() for x in links.values())]
    if len(roots)!=1:raise ValueError('Graphe ambigu')
    current=roots[0]
    entered=data['frames'][0]['seconds']
    stable_key=None
    stable_count=0
    saw_running=False
    seen_running_marker=''
    evidence_at=None
    quota_at=None
    quota_seen=0
    steps=[]
    for frame in data['frames']:
        if current is None:break
        node=nodes[current]
        kind=node.kind
        p=node.params
        now=frame['seconds']
        state=frame['state']
        route='next'
        status='waiting'
        reason='Attente d’une observation suivante'
        key=None
        success=False
        if now<entered:continue
        timeout=int(p.get('timeout_seconds','0')) if kind in ('ocr','state','branch','event','quota_wait') else 0
        if timeout and now-entered>=timeout:
            steps.append(SimStep(node.id,kind,node.label,'blocked','Timeout observé dans la séquence'))
            return SimulationResult(tuple(steps),'blocked',node.id)
        if kind=='clock':
            success=datetime.fromisoformat(frame['local_at'])>=schedule.parse_local(p['at'])
            reason='Horaire enregistré atteint' if success else 'Horaire non atteint'
        elif kind in ('delay','wait'):
            success=now-entered>=int(p['seconds'])
            reason='Délai observé écoulé' if success else 'Délai encore actif'
        elif kind=='event':
            event=p['event']
            if event=='cycle_finished':
                if state=='RUNNING':
                    saw_running=True
                    seen_running_marker=frame['marker']
                key='cycle:'+frame['marker'] if saw_running and state=='FINISHED' and frame['composer'] and frame['marker'] and frame['marker']!=seen_running_marker else None
                reason='Fin de cycle observée' if key else 'Attente RUNNING puis FINISHED distinct'
            else:
                expected={'ready':'READY','quota':'QUOTA','error':'ERROR'}[event]
                key='event:'+expected if state==expected else None
                reason='Événement confirmé' if key else 'Événement absent'
        elif kind=='state':
            target=p['state']
            key='state:'+state if state==target and (state not in ('READY','FINISHED') or frame['composer']) else None
            reason='État Codex vérifié' if key else 'État Codex non confirmé'
        elif kind=='ocr':
            match=frame['matches'].get(node.id)
            key='ocr:yes' if match is True and state in ('READY','FINISHED') and frame['composer'] else None
            reason='Preuve OCR positive (valeur enregistrée)' if key else 'Preuve OCR absente ou inconnue'
        elif kind=='branch':
            if p['source']=='state':
                if state in ('UNKNOWN',) or (state in ('QUOTA','ERROR') and state!=p['state']):
                    match=None
                else:match=state==p['state']
            else:match=frame['matches'].get(node.id) if state in ('READY','FINISHED') and frame['composer'] else None
            if match is not None:
                route='yes' if match else 'no'
                key='branch:'+route+':'+state
            reason='Branche '+route.upper()+' confirmée' if key else 'Branche indéterminée'
        elif kind=='quota_wait':
            deadline=frame['quota_reset']
            if state=='QUOTA' and deadline:
                quota_seen=quota_seen+1 if deadline==quota_at else 1
                quota_at=deadline
            key='quota:clear' if quota_seen>=2 and quota_at and datetime.fromisoformat(frame['local_at'])>=datetime.fromisoformat(quota_at) and state in ('READY','FINISHED') and frame['composer'] and (p.get('resume','manual')=='auto' or quota_approved) else None
            reason='Quota dépassé et disparu (simulation)' if key else 'Quota, échéance ou accord non confirmés'
        elif kind=='send':
            if evidence_at is None or now-evidence_at>12 or state not in ('READY','FINISHED') or not frame['composer']:
                steps.append(SimStep(node.id,kind,node.label,'blocked','Preuve récente et champ Codex requis'))
                return SimulationResult(tuple(steps),'blocked',node.id)
            status='would'
            reason='Prompt prévu — AUCUN ENVOI'
            success=True
            evidence_at=None
        elif kind=='scroll':
            status='would';reason='Défilement prévu — NON EXÉCUTÉ';success=True
        elif kind=='notify':
            status='would';reason='Rapport prévu — NON ENVOYÉ';success=True
        else:
            raise ValueError('Bloc non pris en charge dans le replay')
        if key is not None:
            stable_count=stable_count+1 if key==stable_key else 1
            stable_key=key
            success=stable_count>=2
        elif kind not in ('clock','delay','wait','send','scroll','notify'):
            stable_count=0;stable_key=None
        if not success:continue
        if status!='would':status=route if kind=='branch' else 'ok'
        steps.append(SimStep(node.id,kind,node.label,status,f't+{now:.1f}s · '+reason,route))
        if kind in ('ocr','state') or (kind=='branch' and route=='yes'):
            evidence_at=now
        current=links[node.id].get(route)
        entered=now
        stable_key=None;stable_count=0
        saw_running=False;seen_running_marker=''
        quota_at=None;quota_seen=0
        if current is None:
            return SimulationResult(tuple(steps),'complete',None)
    if current is None:
        return SimulationResult(tuple(steps),'complete',None)
    node=nodes[current]
    steps.append(SimStep(node.id,node.kind,node.label,'waiting','Séquence terminée avant validation'))
    return SimulationResult(tuple(steps),'waiting',node.id)

