"""Headless graph edit history, minimap mapping and GTK event safety checks."""
from pathlib import Path
import math
import unittest
from unittest.mock import patch

from auto_ai_scheduler.workflow import Workflow
from auto_ai_scheduler.workflow_history import GraphHistory
from auto_ai_scheduler.canvas_view import CanvasView
from auto_ai_scheduler.workflow_minimap import compute_minimap, minimap_widget
from auto_ai_scheduler.workflow_checkpoint import graph_fingerprint
from auto_ai_scheduler import x11, editor


class GraphHistoryTests(unittest.TestCase):
    def setUp(self):
        self.graph=Workflow()
        self.a=self.graph.add('event',100,100)
        self.b=self.graph.add('state',400,100)
        self.clock=[10.]
        self.history=GraphHistory(self.graph,clock=lambda:self.clock[0])

    def test_undo_node_creation_and_redo(self):
        initial=self.graph.to_dict()
        self.graph.add('send',800,100)
        self.assertTrue(self.history.record(self.graph))
        self.assertTrue(self.history.undo(self.graph))
        self.assertEqual(self.graph.to_dict(),initial)
        self.assertTrue(self.history.redo(self.graph))
        self.assertEqual(len(self.graph.nodes),3)

    def test_graph_object_identity_is_stable(self):
        identity=id(self.graph)
        self.graph.connect(self.a.id,self.b.id)
        self.history.record(self.graph)
        self.history.undo(self.graph)
        self.assertEqual(id(self.graph),identity)
        self.history.redo(self.graph)
        self.assertEqual(id(self.graph),identity)

    def test_connections_restore_exactly(self):
        self.graph.connect(self.a.id,self.b.id)
        self.history.record(self.graph)
        self.assertTrue(self.history.undo(self.graph))
        self.assertEqual(self.graph.edges,[])
        self.assertTrue(self.history.redo(self.graph))
        self.assertEqual(self.graph.edges,[(self.a.id,self.b.id)])

    def test_group_drag_is_one_step(self):
        before=self.graph.to_dict()
        self.a.x+=70;self.b.x+=70
        self.history.record(self.graph)
        self.history.undo(self.graph)
        self.assertEqual(self.graph.to_dict(),before)

    def test_property_edits_are_coalesced_within_short_window(self):
        self.a.params['event']='ready'
        self.history.record(self.graph,merge_tag=f'param:{self.a.id}:event')
        self.clock[0]+=1
        self.a.params['event']='error'
        self.history.record(self.graph,merge_tag=f'param:{self.a.id}:event')
        self.assertEqual(self.history.depth,2)
        self.history.undo(self.graph)
        self.assertEqual(self.graph.find(self.a.id).params['event'],'cycle_finished')
        self.history.redo(self.graph)
        self.assertEqual(self.graph.find(self.a.id).params['event'],'error')

    def test_property_merges_end_on_new_key(self):
        self.a.params['event']='ready'
        self.history.record(self.graph,merge_tag='one')
        self.a.params['event']='error'
        self.history.record(self.graph,merge_tag='different')
        self.assertEqual(self.history.depth,3)

    def test_property_merge_expires(self):
        self.a.params['event']='ready'
        self.history.record(self.graph,merge_tag='one')
        self.clock[0]+=5
        self.a.params['event']='error'
        self.history.record(self.graph,merge_tag='one')
        self.assertEqual(self.history.depth,3)

    def test_redo_invalidated_after_divergent_edit(self):
        self.graph.connect(self.a.id,self.b.id)
        self.history.record(self.graph)
        self.history.undo(self.graph)
        self.assertTrue(self.history.can_redo)
        self.graph.add('wait',650,200)
        self.history.record(self.graph)
        self.assertFalse(self.history.can_redo)
        self.assertFalse(self.history.redo(self.graph))
        self.assertEqual(len(self.graph.edges),0)

    def test_bounded_history(self):
        h=GraphHistory(self.graph,limit=3,clock=lambda:100)
        for value in range(6):
            self.graph.find(self.a.id).x=150+value
            h.record(self.graph)
        self.assertEqual(h.depth,3)
        self.assertTrue(h.undo(self.graph))
        self.assertTrue(h.undo(self.graph))
        self.assertFalse(h.undo(self.graph))

    def test_nothing_to_undo_or_redo_and_unchanged_record(self):
        self.assertFalse(self.history.undo(self.graph))
        self.assertFalse(self.history.redo(self.graph))
        self.assertFalse(self.history.record(self.graph))

    def test_invalid_history_does_not_damage_graph(self):
        self.graph.add('send')
        self.history.record(self.graph)
        before=self.graph.to_dict()
        self.history.states[0]['nodes'][0]['kind']='unknown'
        with self.assertRaises(ValueError):self.history.undo(self.graph)
        self.assertEqual(self.graph.to_dict(),before)

    def test_camera_only_edits_not_recorded(self):
        v=CanvasView()
        before=graph_fingerprint(self.graph)
        v.zoom_at(1.5,200,150);v.pan_by(-20,70)
        self.assertEqual(graph_fingerprint(self.graph),before)
        self.assertFalse(self.history.can_undo)


class MiniMapTests(unittest.TestCase):
    def setUp(self):
        self.graph=Workflow()
        self.a=self.graph.add('event',100,80)
        self.b=self.graph.add('state',720,450)
        self.graph.connect(self.a.id,self.b.id)
        self.view=CanvasView()

    def test_map_and_inverse(self):
        g=compute_minimap(self.graph,self.view,850,580)
        for point in ((0,0),(100,80),(720,450),(1000,800)):
            px,py=g.project(*point)
            wx,wy=g.unproject(px,py)
            self.assertAlmostEqual(wx,point[0],places=6)
            self.assertAlmostEqual(wy,point[1],places=6)

    def test_viewport_box_is_positive(self):
        g=compute_minimap(self.graph,self.view,850,580)
        x,y,w,h=g.viewport(self.view,850,580)
        self.assertGreater(w,0);self.assertGreater(h,0)
        self.assertLess(x,198);self.assertLess(y,135)

    def test_centering_moves_only_the_camera(self):
        prior=self.graph.to_dict()
        g=compute_minimap(self.graph,self.view,850,580)
        point=g.project(810,505)
        g.recenter(self.view,*point,850,580)
        sx,sy=self.view.world_to_screen(810,505)
        self.assertAlmostEqual(sx,425)
        self.assertAlmostEqual(sy,290)
        self.assertEqual(self.graph.to_dict(),prior)

    def test_recenter_at_zoom_2_works(self):
        self.view.zoom_at(2,200,160)
        g=compute_minimap(self.graph,self.view,700,500)
        g.recenter(self.view,*g.project(200,180),700,500)
        self.assertEqual(self.view.zoom,2)
        px,py=self.view.world_to_screen(200,180)
        self.assertAlmostEqual(px,350)
        self.assertAlmostEqual(py,250)

    def test_empty_graph_and_invalid_size(self):
        empty=Workflow()
        g=compute_minimap(empty,self.view,0,0)
        self.assertGreater(g.scale,0)
        self.assertEqual(g.viewport(self.view,0,0),(0.,0.,0.,0.))
        with self.assertRaises(ValueError):
            compute_minimap(self.graph,self.view,700,500,10,10)

    def test_no_desktop_calls_during_minimap_navigation(self):
        from auto_ai_scheduler import x11
        with patch.object(x11,'type_and_send') as send,patch.object(x11,'windows') as windows:
            g=compute_minimap(self.graph,self.view,800,500)
            g.recenter(self.view,*g.project(500,220),800,500)
            send.assert_not_called();windows.assert_not_called()

    def test_gtk_minimap_click_drag_and_draw(self):
        class Dummy:
            def __init__(self):self.handlers={};self.controllers=[];self.draw=None
            def __getattr__(self,name):
                if name.startswith('set_') or name=='add_css_class':return lambda *args:None
                raise AttributeError(name)
            def set_draw_func(self,f):self.draw=f
            def connect(self,name,callback):self.handlers[name]=callback
            def add_controller(self,c):self.controllers.append(c)
            def get_width(self):return 198
            def get_height(self):return 135
            def queue_draw(self):pass
        class Gtk:
            DrawingArea=Dummy
            GestureClick=Dummy
            GestureDrag=Dummy
            class Align:
                END=1
        state=[0]
        area=minimap_widget(Gtk,self.graph,self.view,lambda:(800,500),lambda:state.__setitem__(0,state[0]+1))
        class Cairo:
            def __getattr__(self,key):return lambda *args:None
        area.draw(area,Cairo(),198,135)
        original=(self.view.pan_x,self.view.pan_y)
        area.controllers[0].handlers['pressed'](None,1,140,100)
        self.assertNotEqual((self.view.pan_x,self.view.pan_y),original)
        area.controllers[1].handlers['drag-begin'](None,90,80)
        area.controllers[1].handlers['drag-update'](None,20,10)
        self.assertEqual(state[0],2)


class GuiWiringTests(unittest.TestCase):
    def test_timeline_bottom_minimap_overlay_and_shortcuts_present(self):
        root=Path(__file__).resolve().parents[1]
        gui=(root/'auto_ai_scheduler/gui.py').read_text()
        code=(root/'auto_ai_scheduler/editor.py').read_text()
        self.assertIn('overlay.set_child(self.canvas)',gui)
        self.assertIn('overlay.add_overlay(self.minimap)',gui)
        self.assertIn('vertical.set_end_child(self.make_timeline())',gui)
        self.assertIn('on_undo=self.graph_undo,on_redo=self.graph_redo',gui)
        self.assertIn('self.graph_changed(record=False)',gui)
        self.assertIn('Gdk.ModifierType.CONTROL_MASK',code)
        self.assertIn("if mode == 'nodes' and on_view_changed",code)


if __name__=='__main__':unittest.main()
