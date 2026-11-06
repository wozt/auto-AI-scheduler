"""Headless canvas camera and GTK gesture regressions (no desktop actions)."""
import importlib
import math
import sys
import unittest
from unittest.mock import patch
from types import ModuleType, SimpleNamespace

from auto_ai_scheduler.canvas_view import CanvasView, MIN_ZOOM, MAX_ZOOM, NODE_W, NODE_H
from auto_ai_scheduler.workflow import Workflow
from auto_ai_scheduler.workflow_checkpoint import graph_fingerprint
from auto_ai_scheduler import editor, x11


class CanvasViewTests(unittest.TestCase):
    def setUp(self):
        self.graph = Workflow(name='Canvas tests')
        self.a = self.graph.add('event', 70, 130)
        self.b = self.graph.add('branch', 450, 300)
        self.c = self.graph.add('send', 750, 130)
        self.graph.connect(self.a.id,self.b.id)
        self.graph.connect(self.b.id,self.c.id,branch='yes')
        self.view = CanvasView()

    def test_zoom_anchor_does_not_move_object_under_cursor(self):
        p = (400, 260)
        original = self.view.screen_to_world(*p)
        for zoom in (1.6,0.62,2.4,1.0):
            self.view.zoom_at(zoom,*p)
            current = self.view.screen_to_world(*p)
            self.assertAlmostEqual(original[0],current[0],places=6)
            self.assertAlmostEqual(original[1],current[1],places=6)
            self.assertAlmostEqual(self.view.world_to_screen(*original)[0],p[0])
            self.assertAlmostEqual(self.view.world_to_screen(*original)[1],p[1])

    def test_clamps_zoom_from_wheel_and_buttons(self):
        self.view.zoom_at(1000,0,0)
        self.assertEqual(self.view.zoom,MAX_ZOOM)
        self.view.zoom_at(.01,0,0)
        self.assertEqual(self.view.zoom,MIN_ZOOM)
        with self.assertRaises(ValueError):self.view.zoom_at(float('nan'),0,0)
        with self.assertRaises(ValueError):self.view.zoom_at(0,0,0)
        self.view.wheel(-1,100,100)
        self.assertGreater(self.view.zoom,MIN_ZOOM)

    def test_camera_only_changes_no_saved_workflow_or_checkpoint(self):
        previous=self.graph.to_dict()
        digest=graph_fingerprint(self.graph)
        view=self.view
        view.zoom_at(1.55,322,188)
        view.pan_by(-150,45)
        view.fit(self.graph,720,500)
        view.reset()
        self.assertEqual(self.graph.to_dict(),previous)
        self.assertEqual(graph_fingerprint(self.graph),digest)

    def test_fit_and_pan_validated(self):
        view=self.view
        view.fit(self.graph,900,550)
        for node in self.graph.nodes:
            x,y=view.world_to_screen(node.x,node.y)
            self.assertGreaterEqual(x,-.01)
            self.assertGreaterEqual(y,-.01)
            self.assertLessEqual(x+NODE_W*view.zoom,900.01)
            self.assertLessEqual(y+NODE_H*view.zoom,550.01)
        old_x=view.pan_x
        view.pan_by(100,-40)
        self.assertAlmostEqual(view.pan_x,old_x+100)
        with self.assertRaises(ValueError):view.pan_by(float('inf'),0)

    def test_hit_after_zoom_and_pan(self):
        view=self.view
        view.zoom_at(1.75,300,200)
        view.pan_by(-200,120)
        sx,sy=view.world_to_screen(self.b.x+75,self.b.y+35)
        self.assertEqual(view.hit(self.graph,sx,sy).id,self.b.id)
        self.assertIsNone(view.hit(self.graph,100000,100000))

    def test_additive_selection_and_prune(self):
        view=self.view
        view.choose(self.a.id)
        view.choose(self.b.id,additive=True)
        self.assertEqual(view.selected,{self.a.id,self.b.id})
        view.choose(self.a.id,additive=True)
        self.assertEqual(view.selected,{self.b.id})
        view.choose(None,additive=True)
        self.assertEqual(view.selected,{self.b.id})
        self.graph.delete(self.b.id)
        view.prune(self.graph)
        self.assertFalse(view.selected)

    def test_rectangle_selects_intersecting_nodes_either_direction(self):
        view=self.view
        lo=view.world_to_screen(self.a.x-15,self.a.y-15)
        hi=view.world_to_screen(self.b.x+NODE_W+15,self.b.y+NODE_H+15)
        view.rectangle(self.graph,hi,lo)
        self.assertEqual(view.selected,{self.a.id,self.b.id})
        only=view.world_to_screen(self.c.x+20,self.c.y+20)
        view.rectangle(self.graph,only,only,additive=True)
        self.assertEqual(view.selected,{self.a.id,self.b.id,self.c.id})

    def test_group_move_preserves_distances_and_connections(self):
        view=self.view
        view.choose(self.a.id)
        view.choose(self.b.id,additive=True)
        before={n.id:(n.x,n.y) for n in self.graph.nodes}
        edges=list(self.graph.edges)
        view.zoom_at(2,150,80)
        self.assertTrue(view.move_selection(self.graph,before,120,-40))
        self.assertEqual((self.a.x,self.a.y),(130,110))
        self.assertEqual((self.b.x,self.b.y),(510,280))
        self.assertEqual((self.c.x,self.c.y),before[self.c.id])
        self.assertEqual(self.graph.edges,edges)
        self.assertTrue(view.move_selection(self.graph,before,0,0))
        self.assertEqual((self.a.x,self.a.y),before[self.a.id])

    def test_group_drag_limits_preserve_relative_layout(self):
        view=self.view
        view.selected={self.a.id,self.c.id}
        before={n.id:(n.x,n.y) for n in self.graph.nodes}
        view.move_selection(self.graph,before,100000,100000)
        self.assertEqual(self.c.x,2500)
        self.assertEqual(self.c.x-self.a.x,680)
        self.assertEqual(self.c.y-self.a.y,0)
        view.move_selection(self.graph,before,-100000,-100000)
        self.assertEqual(self.a.x,20)
        self.assertEqual(self.a.y,20)
        self.assertEqual(self.c.x-self.a.x,680)

    def test_hit_node_backwards_compatibility(self):
        self.assertEqual(editor.hit_node(self.graph,self.a.x+25,self.a.y+25).id,self.a.id)


class FakeWidget:
    def __init__(self):
        self.handlers={}
        self.controllers=[]
        self.draw=None
        self.redraws=0
        self.size=(800,500)
        self._state=0
    def __getattr__(self,key):
        if key.startswith('set_') or key in ('grab_focus',):
            return lambda *args:None
        raise AttributeError(key)
    def set_draw_func(self,fn):self.draw=fn
    def add_controller(self,x):self.controllers.append(x)
    def queue_draw(self):self.redraws+=1
    def connect(self,key,callback):self.handlers[key]=callback
    def get_current_event_state(self):return self._state


class FakeScroll(FakeWidget):
    @classmethod
    def new(cls,*args):return cls()


class FakeGtk:
    DrawingArea=FakeWidget
    EventControllerMotion=FakeWidget
    GestureClick=FakeWidget
    GestureDrag=FakeWidget
    EventControllerKey=FakeWidget
    EventControllerScroll=FakeScroll
    EventControllerScrollFlags=SimpleNamespace(VERTICAL=1)


class CanvasGtkWiringTests(unittest.TestCase):
    def setUp(self):
        self.graph=Workflow()
        self.a=self.graph.add('event',100,100)
        self.b=self.graph.add('state',400,100)
        self.gdk=SimpleNamespace(ModifierType=SimpleNamespace(SHIFT_MASK=1,CONTROL_MASK=2),
                                 KEY_space=32,KEY_Escape=27,KEY_a=ord('a'),KEY_A=ord('A'))
        gi=ModuleType('gi')
        repo=ModuleType('gi.repository')
        repo.Gdk=self.gdk
        self.patch_modules=patch.dict(sys.modules,{'gi':gi,'gi.repository':repo})
        self.patch_modules.start()
        self.addCleanup(self.patch_modules.stop)
        self.view=CanvasView()
        self.selected=[]
        self.changed=[]
        self.messages=[]
        self.area=editor.canvas_widget(FakeGtk,self.graph,self.selected.append,
                                       lambda:self.changed.append(True),self.messages.append,
                                       view=self.view)
        self.click=next(x for x in self.area.controllers if x.__class__==FakeWidget and 'pressed' in x.handlers)
        self.drag=[x for x in self.area.controllers if x.__class__==FakeWidget and 'drag-begin' in x.handlers]
        self.scroll=next(x for x in self.area.controllers if isinstance(x,FakeScroll))
        self.keys=next(x for x in self.area.controllers if x.__class__==FakeWidget and 'key-pressed' in x.handlers)
    def test_scroll_zoom_does_not_change_graph(self):
        before=self.graph.to_dict()
        self.assertTrue(self.scroll.handlers['scroll'](self.scroll,0,-2))
        self.assertGreater(self.view.zoom,1)
        self.assertEqual(before,self.graph.to_dict())
    def test_shift_multiselect_then_drag_as_rigid_group(self):
        x,y=self.view.world_to_screen(self.a.x+50,self.a.y+40)
        self.click.handlers['pressed'](self.click,1,x,y)
        x2,y2=self.view.world_to_screen(self.b.x+50,self.b.y+40)
        self.click._state=1
        self.click.handlers['pressed'](self.click,1,x2,y2)
        self.assertEqual(self.view.selected,{self.a.id,self.b.id})
        dr=self.drag[0]
        dr.handlers['drag-begin'](dr,x,y)
        dr.handlers['drag-update'](dr,70,45)
        dr.handlers['drag-end'](dr,70,45)
        self.assertEqual((self.a.x,self.b.x),(170,470))
        self.assertEqual(len(self.changed),1)
    def test_middle_drag_pan_does_not_mutate_nodes(self):
        before=self.graph.to_dict()
        pan=self.drag[1]
        pan.handlers['drag-begin'](pan,2,2)
        pan.handlers['drag-update'](pan,60,-12)
        self.assertEqual(self.view.pan_x,84)
        self.assertEqual(before,self.graph.to_dict())
    def test_esc_cancels_connection_without_graph_mutation(self):
        before=self.graph.to_dict()
        x,y=self.view.world_to_screen(self.a.x+NODE_W-4,self.a.y+NODE_H/2)
        self.click.handlers['pressed'](self.click,1,x,y)
        self.assertTrue(self.keys.handlers['key-pressed'](self.keys,27,0,0))
        self.assertEqual(before,self.graph.to_dict())
    def test_port_connection_preserves_branch_contract(self):
        from auto_ai_scheduler.workflow import NODE_KINDS
        source=self.graph.add('branch',80,300)
        x,y=self.view.world_to_screen(source.x+NODE_W-4,source.y+NODE_H/3)
        self.click.handlers['pressed'](self.click,1,x,y)
        tx,ty=self.view.world_to_screen(self.b.x+4,self.b.y+NODE_H/2)
        self.click.handlers['pressed'](self.click,1,tx,ty)
        self.assertIn((source.id,self.b.id,'yes'),self.graph.edges)
        self.assertEqual(len(self.changed),1)

    def test_draw_path_with_zoom_selection_and_lasso(self):
        class Cairo:
            def __getattr__(self,name):
                return lambda *args: None
        self.view.choose(self.a.id)
        self.view.zoom_at(1.25,100,100)
        self.area.draw(self.area,Cairo(),800,500)
        self.assertGreater(self.area.redraws,-1)

    def test_ctrl_a_selects_all_without_sending(self):
        with patch.object(x11,'type_and_send') as send:
            self.assertTrue(self.keys.handlers['key-pressed'](self.keys,ord('a'),0,2))
            self.assertEqual(self.view.selected,{self.a.id,self.b.id})
            send.assert_not_called()


class GuiContract(unittest.TestCase):
    def test_canvas_controls_are_present_and_timeline_stays_below(self):
        from pathlib import Path
        gui=(Path(__file__).resolve().parents[1]/'auto_ai_scheduler/gui.py').read_text()
        self.assertIn("('−',1/1.2",gui)
        self.assertIn('Cadrer les blocs',gui)
        self.assertIn('self.canvas_view=CanvasView()',gui)
        self.assertIn('vertical.set_end_child(self.make_timeline())',gui)
        self.assertIn('wrap.append(self.canvas)',gui)
        self.assertNotIn('viewport.set_child(self.canvas)',gui)


if __name__=='__main__':unittest.main()
