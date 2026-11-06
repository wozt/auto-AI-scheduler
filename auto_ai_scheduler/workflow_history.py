"""In-memory, bounded graph Undo/Redo; no runner or desktop side effects."""
from copy import deepcopy
import time
from .workflow import Workflow


class GraphHistory:
    def __init__(self, graph, limit=80, clock=None):
        if not 2 <= limit <= 500:
            raise ValueError('Historique invalide')
        self.limit=limit
        self.clock=clock or time.monotonic
        self.states=[deepcopy(graph.to_dict())]
        self.cursor=0
        self.merge_tag=None
        self.last_edit=None

    @property
    def can_undo(self):return self.cursor>0

    @property
    def can_redo(self):return self.cursor+1<len(self.states)

    @property
    def depth(self):return len(self.states)

    def record(self,graph,merge_tag=None):
        snapshot=deepcopy(graph.to_dict())
        if snapshot==self.states[self.cursor]:return False
        now=self.clock()
        branched=self.can_redo
        if branched:del self.states[self.cursor+1:]
        merge=(not branched and self.cursor>0 and merge_tag is not None and
               self.merge_tag==merge_tag and self.last_edit is not None and
               0 <= now-self.last_edit <= 2.5)
        if merge:
            self.states[self.cursor]=snapshot
        else:
            self.states.append(snapshot)
            self.cursor+=1
            if len(self.states)>self.limit:
                del self.states[0]
                self.cursor-=1
        self.merge_tag=merge_tag
        self.last_edit=now
        return True

    @staticmethod
    def restore(graph,snapshot):
        # Validate first, then replace parts without changing graph identity.
        fresh=Workflow.from_dict(deepcopy(snapshot))
        graph.name=fresh.name
        graph.nodes=fresh.nodes
        graph.edges=fresh.edges

    def undo(self,graph):
        if not self.can_undo:return False
        self.restore(graph,self.states[self.cursor-1]);self.cursor-=1
        self.merge_tag=None
        return True

    def redo(self,graph):
        if not self.can_redo:return False
        self.restore(graph,self.states[self.cursor+1]);self.cursor+=1
        self.merge_tag=None
        return True
