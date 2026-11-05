"""Local, non-executable visual scenario documents.

A graph only describes automation. It is never evaluated into desktop actions
by this module. The existing Scheduler remains the sole X11 actuator.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

WORKFLOW_DIR = Path.home() / '.local' / 'share' / 'auto-ai-scheduler' / 'workflows'

NODE_KINDS = {
    'clock': ('Horloge', 'Déclencheur', {'at': ''}, 'À la date/heure locale'),
    'delay': ('Délai', 'Déclencheur', {'seconds': '60'}, 'Après un délai en secondes'),
    'event': ('Événement', 'Déclencheur', {'event': 'cycle_finished'}, 'Fin de cycle, quota ou erreur'),
    'ocr': ('Preuve OCR', 'Condition', {'proof_id': '', 'expected': '', 'roi': '0,0,100,100'}, 'Texte reconnu dans une zone'),
    'state': ('État Codex', 'Condition', {'state': 'FINISHED'}, 'Confirmer l’état lu dans Codex'),
    'send': ('Envoyer prompt', 'Action', {'prompt': 'continue'}, 'Saisie dans le projet confirmé'),
    'scroll': ('Défiler', 'Action', {'steps': '12'}, 'Défiler jusqu’au bas du chat'),
    'notify': ('Rapport mail', 'Action', {'subject': 'Rapport de cycle'}, 'Avertissement par courriel'),
    'wait': ('Attendre', 'Action', {'seconds': '60'}, 'Suspendre la suite du scénario'),
    'branch': ('Si / Sinon', 'Condition', {'source': 'state', 'state': 'FINISHED', 'proof_id': '', 'expected': '', 'roi': '0,0,100,100', 'timeout_seconds': '0'}, 'Deux sorties OUI (port haut) / NON (port bas), basées sur un état Codex ou une preuve OCR'),
    'quota_wait': ('Attendre quota', 'Condition', {'resume': 'manual', 'timeout_seconds': '0'}, 'Attend une limite OCR lisible, son échéance, puis deux contrôles sans quota. Reprise manuelle par défaut.'),
}


@dataclass
class Node:
    id: str
    kind: str
    x: int
    y: int
    params: dict[str, str] = field(default_factory=dict)

    @property
    def label(self):
        return NODE_KINDS[self.kind][0]


class Workflow:
    def __init__(self, nodes=None, edges=None, name='Nouveau scénario'):
        self.name = name
        self.nodes = list(nodes or [])
        self.edges = [tuple(e) for e in (edges or [])]

    def add(self, kind, x=100, y=100):
        if kind not in NODE_KINDS:
            raise ValueError('Unknown node type')
        node = Node(uuid.uuid4().hex[:12], kind, int(x), int(y), dict(NODE_KINDS[kind][2]))
        self.nodes.append(node)
        return node

    def find(self, ident):
        return next((n for n in self.nodes if n.id == ident), None)

    def delete(self, ident):
        self.nodes = [n for n in self.nodes if n.id != ident]
        self.edges = [edge for edge in self.edges if ident not in edge[:2]]

    def connect(self, a, b, branch=None):
        if a == b or not self.find(a) or not self.find(b):
            raise ValueError('Two distinct existing nodes are required')
        source = self.find(a)
        if source.kind == 'branch':
            if branch not in ('yes', 'no'):
                raise ValueError('Un branchement nécessite la sortie yes ou no')
            if any(e[0] == a and len(e) == 3 and e[2] == branch for e in self.edges):
                raise ValueError('Cette sortie du branchement est déjà reliée')
            edge = (a, b, branch)
        else:
            if branch is not None:
                raise ValueError('Seul le bloc Si / Sinon possède deux sorties')
            edge = (a, b)
        if edge in self.edges:
            return
        self.edges.append(edge)
        if self.has_cycle():
            self.edges.pop()
            raise ValueError('Connections cannot create an infinite loop')

    def has_cycle(self):
        incoming = {n.id: 0 for n in self.nodes}
        graph = {n.id: [] for n in self.nodes}
        for edge in self.edges:
            a, b = edge[:2]
            if a not in graph or b not in graph:
                return True
            graph[a].append(b)
            incoming[b] += 1
        queue = [n for n,degree in incoming.items() if degree == 0]
        done = 0
        while queue:
            a = queue.pop()
            done += 1
            for b in graph[a]:
                incoming[b] -= 1
                if incoming[b] == 0:
                    queue.append(b)
        return done != len(self.nodes)

    def ordered(self):
        """Stable topological order used in the read-only timeline."""
        incoming = {n.id: 0 for n in self.nodes}
        adj = {n.id: [] for n in self.nodes}
        for edge in self.edges:
            a, b = edge[:2]
            incoming[b] += 1
            adj[a].append(b)
        # Nodes with no dependency are ordered by their position in the
        # editor's node list. This keeps the timeline stable for newly added
        # standalone nodes instead of placing them before existing chains.
        index = {node.id: idx for idx,node in enumerate(self.nodes)}
        queue = [n.id for n in self.nodes if incoming[n.id] == 0]
        output=[]
        while queue:
            queue.sort(key=index.__getitem__)
            a=queue.pop(0)
            output.append(self.find(a))
            for b in adj[a]:
                incoming[b] -= 1
                if not incoming[b]:
                    queue.append(b)
        if len(output) != len(self.nodes):
            raise ValueError('Workflow contains a cycle')
        return output

    def to_dict(self):
        return {'schema':1, 'name':self.name,
                'nodes':[dict(id=n.id, kind=n.kind, x=n.x, y=n.y, params=n.params) for n in self.nodes],
                'edges':[list(e) for e in self.edges]}

    @classmethod
    def from_dict(cls, data):
        if data.get('schema') != 1:
            raise ValueError('Unsupported workflow format')
        nodes=[]
        ids=set()
        for item in data['nodes']:
            if item['kind'] not in NODE_KINDS or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',item['id']):
                raise ValueError('Unsupported node')
            if item['id'] in ids:
                raise ValueError('Duplicate node id')
            ids.add(item['id'])
            params=item.get('params', {})
            if not isinstance(params,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in params.items()):
                raise ValueError('Invalid node parameters')
            nodes.append(Node(item['id'], item['kind'],int(item['x']),int(item['y']),params))
        edges = data.get('edges', [])
        if not isinstance(edges, list):
            raise ValueError('Invalid scenario connections')
        for edge in edges:
            if (not isinstance(edge, (list, tuple)) or len(edge) not in (2, 3)
                    or any(not isinstance(v, str) for v in edge)):
                raise ValueError('Invalid scenario edge')
            if len(edge) == 3 and edge[2] not in ('yes', 'no'):
                raise ValueError('Invalid conditional exit')
        graph=cls(nodes, edges, str(data.get('name','Scénario')))
        if graph.has_cycle():
            raise ValueError('Invalid scenario connections')
        return graph

    @classmethod
    def starter(cls):
        graph=cls(name='Surveillance Codex (exemple)')
        a=graph.add('event', 70, 150)
        b=graph.add('ocr', 330, 150)
        c=graph.add('send', 600, 150)
        graph.connect(a.id,b.id)
        graph.connect(b.id,c.id)
        return graph


class WorkflowStore:
    """Save locally only, never in the repository and never upload proof images."""
    def __init__(self, directory=WORKFLOW_DIR):
        self.directory=Path(directory)

    def _path(self, name):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', name):
            raise ValueError('Invalid scenario identifier')
        return self.directory / (name+'.json')

    def save(self, graph, name='default'):
        path=self._path(name)
        self.directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.directory.chmod(0o700)
        payload=json.dumps(graph.to_dict(), ensure_ascii=False, indent=2).encode('utf-8')
        handle,temp=tempfile.mkstemp(dir=self.directory,prefix='.workflow-')
        try:
            with os.fdopen(handle,'wb') as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(temp,0o600)
            os.replace(temp,path)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        return path

    def load(self,name='default'):
        path=self._path(name)
        if not path.exists():return Workflow.starter()
        return Workflow.from_dict(json.loads(path.read_text(encoding='utf-8')))


def planned_send(graph, selected_id=None):
    """Choose a send action for explicit live testing, without graph mutations.

    A selected Send node takes priority. Otherwise use the earliest Send node
    in topological/timeline order. There is no automated timeline cursor yet.
    """
    chosen = graph.find(selected_id) if selected_id else None
    if chosen is not None and chosen.kind == 'send':
        return chosen
    return next((node for node in graph.ordered() if node.kind == 'send'), None)
