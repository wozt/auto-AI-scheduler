"""Pure, UI-independent canvas camera and multi-selection geometry.

The workflow graph stores WORLD positions. Zoom and pan must never change its
nodes, edges, checkpoints or execution semantics; only deliberate node drags do.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

MIN_ZOOM = 0.45
MAX_ZOOM = 2.5
NODE_W = 218
NODE_H = 88
MAX_WORLD_POSITION = 2500


@dataclass
class CanvasView:
    zoom: float = 1.0
    pan_x: float = 24.0
    pan_y: float = 20.0
    selected: set[str] = field(default_factory=set)

    def screen_to_world(self, x, y):
        return (float(x) - self.pan_x) / self.zoom, (float(y) - self.pan_y) / self.zoom

    def world_to_screen(self, x, y):
        return float(x) * self.zoom + self.pan_x, float(y) * self.zoom + self.pan_y

    def pan_by(self, dx, dy):
        if not all(math.isfinite(float(v)) for v in (dx, dy)):
            raise ValueError('Pan non fini')
        self.pan_x = max(-10000., min(10000., self.pan_x + float(dx)))
        self.pan_y = max(-10000., min(10000., self.pan_y + float(dy)))

    def zoom_at(self, zoom, sx, sy):
        zoom = float(zoom)
        if not math.isfinite(zoom) or zoom <= 0:
            raise ValueError('Zoom invalide')
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        wx, wy = self.screen_to_world(sx, sy)
        self.zoom = zoom
        self.pan_x = float(sx) - wx * self.zoom
        self.pan_y = float(sy) - wy * self.zoom
        return zoom

    def wheel(self, delta_y, sx, sy):
        return self.zoom_at(self.zoom * (1.12 ** (-max(-6., min(6., float(delta_y))))), sx, sy)

    def fit(self, graph, width, height):
        """Fit all nodes within the currently available, resizable viewport."""
        if width <= 0 or height <= 0:
            return self.zoom
        if not graph.nodes:
            return self.reset(width, height)
        low_x = min(n.x for n in graph.nodes)
        low_y = min(n.y for n in graph.nodes)
        high_x = max(n.x + NODE_W for n in graph.nodes)
        high_y = max(n.y + NODE_H for n in graph.nodes)
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, min((width - 60) / max(1., high_x-low_x),
                                                   (height - 60) / max(1., high_y-low_y))))
        self.zoom = zoom
        self.pan_x = width/2 - (low_x + high_x)/2 * zoom
        self.pan_y = height/2 - (low_y + high_y)/2 * zoom
        return zoom

    def reset(self, width=900, height=600):
        self.zoom = 1.
        self.pan_x = 24.
        self.pan_y = 20.
        return self.zoom

    def hit(self, graph, sx, sy):
        wx, wy = self.screen_to_world(sx, sy)
        for node in reversed(graph.nodes):
            if node.x <= wx <= node.x + NODE_W and node.y <= wy <= node.y + NODE_H:
                return node
        return None

    def choose(self, ident, additive=False):
        """Additive toggles an item; normal click keeps a selected drag group."""
        if ident is None:
            if not additive:
                self.selected.clear()
        elif additive:
            if ident in self.selected:
                self.selected.remove(ident)
            else:
                self.selected.add(ident)
        elif ident not in self.selected:
            self.selected = {ident}
        return set(self.selected)

    def prune(self, graph):
        self.selected.intersection_update({n.id for n in graph.nodes})

    def rectangle(self, graph, start, end, additive=False):
        """Select intersecting node rectangles (screen coords, either drag direction)."""
        x1, y1 = self.screen_to_world(*start)
        x2, y2 = self.screen_to_world(*end)
        l, r = sorted((x1, x2))
        t, b = sorted((y1, y2))
        hits = {n.id for n in graph.nodes
                if n.x <= r and n.x + NODE_W >= l and n.y <= b and n.y + NODE_H >= t}
        self.selected = (self.selected | hits) if additive else hits
        return set(self.selected)

    def move_selection(self, graph, initial, dx, dy):
        """One rigid group translation, respecting world limits and zoom."""
        items = [n for n in graph.nodes if n.id in self.selected and n.id in initial]
        if not items:
            return False
        dx = float(dx) / self.zoom
        dy = float(dy) / self.zoom
        min_x = min(initial[n.id][0] for n in items)
        min_y = min(initial[n.id][1] for n in items)
        max_x = max(initial[n.id][0] for n in items)
        max_y = max(initial[n.id][1] for n in items)
        dx = max(20 - min_x, min(MAX_WORLD_POSITION - max_x, dx))
        dy = max(20 - min_y, min(MAX_WORLD_POSITION - max_y, dy))
        changed = False
        for n in items:
            x, y = initial[n.id]
            nx, ny = round(x+dx), round(y+dy)
            changed |= (nx,ny) != (n.x,n.y)
            n.x, n.y = nx, ny
        return changed
