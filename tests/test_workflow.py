import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from PIL import Image

from auto_ai_scheduler.workflow import Workflow,WorkflowStore,NODE_KINDS
from auto_ai_scheduler.proofs import ProofStore,normalize,match_text,parse_roi,crop_roi
from auto_ai_scheduler.display import validate,css
from auto_ai_scheduler import editor


class WorkflowTests(unittest.TestCase):
    def test_starter_graph_is_connected(self):
        g=Workflow.starter()
        self.assertEqual([n.kind for n in g.ordered()],['event','ocr','send'])
        self.assertEqual(len(g.edges),2)

    def test_cycle_is_prevented(self):
        g=Workflow.starter()
        a,b,c=[n.id for n in g.nodes]
        with self.assertRaisesRegex(ValueError,'loop'):
            g.connect(c,a)
        self.assertEqual(len(g.edges),2)

    def test_invalid_graph_rejected(self):
        g=Workflow.starter()
        data=g.to_dict()
        data['nodes'][1]['kind']='injected_python'
        with self.assertRaises(ValueError):Workflow.from_dict(data)

    def test_persistent_local_storage_and_permissions(self):
        with TemporaryDirectory() as d:
            store=WorkflowStore(Path(d)/'private')
            g=Workflow.starter()
            g.nodes[-1].params['prompt']='private instruction'
            path=store.save(g)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777,0o700)
            self.assertEqual(store.load().nodes[-1].params['prompt'],'private instruction')
            with self.assertRaises(ValueError):store.load('../evil')

    def test_canvas_node_hit_test(self):
        g=Workflow.starter()
        n=g.nodes[0]
        self.assertEqual(editor.hit_node(g,n.x+20,n.y+20).id,n.id)
        self.assertIsNone(editor.hit_node(g,1,1))


class ProofTests(unittest.TestCase):
    def test_roi_and_text_matching(self):
        self.assertEqual(parse_roi('25,10,50,60'),(25.,10.,50.,60.))
        for value in ('-1,0,10,10','50,90,60,11','bad','1,2,3,0'):
            with self.subTest(value=value),self.assertRaises(ValueError):parse_roi(value)
        self.assertTrue(match_text('Edited 16\nFiles','edited 16 files'))
        self.assertFalse(match_text('anything',''))
        img=Image.new('RGB',(200,100))
        self.assertEqual(crop_roi(img,'10,10,50,50').size,(100,50))

    def test_import_is_local_private(self):
        with TemporaryDirectory() as d:
            source=Path(d)/'input.png'
            Image.new('RGB',(180,90),'white').save(source)
            store=ProofStore(Path(d)/'private')
            proof_id=store.import_image(source)
            self.assertEqual(store._file(proof_id).stat().st_mode & 0o777,0o600)
            self.assertEqual(store.directory.stat().st_mode & 0o777,0o700)
            self.assertEqual(store.get_image(proof_id).size,(180,90))
            with self.assertRaises(ValueError):store._file('../../etc/passwd')

    def test_generated_test_is_local_and_deterministic(self):
        with TemporaryDirectory() as d:
            source=Path(d)/'input.png'
            Image.new('RGB',(180,90),'white').save(source)
            store=ProofStore(Path(d)/'private')
            proof_id=store.import_image(source)
            with patch('auto_ai_scheduler.proofs.ocr_image',return_value='Worked for 14m'):
                spec,_=store.generate_test(proof_id)
                passed,_=store.test(proof_id)
            self.assertTrue(passed)
            self.assertEqual(spec.expected,'Worked for 14m')
            self.assertEqual(store.manifest.stat().st_mode & 0o777,0o600)
            self.assertNotIn('input.png',store.manifest.read_text())


class DisplayTests(unittest.TestCase):
    def test_1080p_default_and_relative_fonts(self):
        cfg=validate({})
        self.assertEqual((cfg['window_width'],cfg['window_height']),(1540,900))
        self.assertIn(b'font-size:15px',css(12))
        self.assertIn(b'font-size:18px',css(15))

    def test_invalid_values_are_refused(self):
        for field,value in [('window_width',100),('window_height',2500),('font_size',5),('theme','unsafe')]:
            with self.subTest(field=field),self.assertRaises(ValueError):validate({field:value})


if __name__=='__main__':unittest.main()


class ForceTestTests(unittest.TestCase):
    def test_selected_send_node_takes_priority_without_mutation(self):
        from auto_ai_scheduler.workflow import planned_send
        graph=Workflow.starter()
        first=graph.nodes[-1]
        second=graph.add('send', 450, 400)
        second.params['prompt']='second command'
        before=graph.to_dict()
        self.assertIs(planned_send(graph, second.id), second)
        self.assertIs(planned_send(graph, graph.nodes[0].id), first)
        self.assertEqual(graph.to_dict(), before)

    def test_force_test_sends_real_prompt_once_without_advancing_state(self):
        from auto_ai_scheduler.engine import Scheduler
        from auto_ai_scheduler.config import validate as config_validate
        from auto_ai_scheduler.vision import Observation
        from auto_ai_scheduler import x11
        cfg=config_validate({'schedule_enabled':True,'scheduled_at':'2026-11-06 03:00',
                             'schedule_id':'still-armed', 'mode':'auto'})
        engine=Scheduler(cfg,lambda _:None)
        obs=Observation(True,True,(200,400),None,'READY','old marker','','')
        def inspect(action=None):
            if action:action('123',obs,(10,20))
            return obs
        with patch.object(engine,'inspect_target',side_effect=inspect), \
             patch.object(x11,'type_and_send') as send, \
             patch('auto_ai_scheduler.engine.time.monotonic',return_value=1000):
            self.assertTrue(engine.force_test('test a real prompt'))
            send.assert_called_once_with('123',(210,420),'test a real prompt')
            self.assertTrue(engine.force_test_pending)
            self.assertEqual(engine.session.cycles,0)
            self.assertFalse(engine.session.waiting_for_response)
            self.assertTrue(cfg['schedule_enabled'])
            self.assertEqual(cfg['schedule_id'],'still-armed')

    def test_force_test_refuses_quota_and_busy(self):
        from auto_ai_scheduler.engine import Scheduler
        from auto_ai_scheduler.config import validate as config_validate
        from auto_ai_scheduler.vision import Observation
        from auto_ai_scheduler import x11
        engine=Scheduler(config_validate({}),lambda _:None)
        obs=Observation(True,True,(200,400),None,'QUOTA','','','')
        def inspect(action=None):
            if action:action('123',obs,(0,0))
            return obs
        with patch.object(engine,'inspect_target',side_effect=inspect), \
             patch.object(x11,'type_and_send') as send, \
             patch('auto_ai_scheduler.engine.time.monotonic',return_value=1000):
            self.assertFalse(engine.force_test('continue'))
            engine.session.waiting_for_response=True
            self.assertFalse(engine.force_test('continue'))
            send.assert_not_called()

    def test_force_test_blocks_automatic_chain_until_response(self):
        from auto_ai_scheduler.engine import Scheduler
        from auto_ai_scheduler.config import validate as config_validate
        from auto_ai_scheduler.vision import Observation
        from auto_ai_scheduler import x11
        engine=Scheduler(config_validate({'mode':'auto','scroll_seconds':0}),lambda _:None)
        engine.enabled=True
        engine.session.cycles=2
        engine.force_test_pending=True
        engine.force_test_marker='old'
        engine.force_test_at=1000
        obs=Observation(True,True,(200,400),None,'FINISHED','new','','new response')
        with patch.object(x11,'user_idle_seconds',return_value=100), \
             patch.object(engine,'inspect_target',return_value=obs), \
             patch.object(engine,'send') as send, \
             patch('auto_ai_scheduler.engine.time.monotonic',return_value=1001):
            engine.tick()
            engine.tick()
            send.assert_not_called()
            self.assertTrue(engine.force_test_pending)


class ForceTestGUIContract(unittest.TestCase):
    def test_button_and_confirmation_are_present(self):
        source=(Path(__file__).resolve().parents[1]/'auto_ai_scheduler'/'gui.py').read_text()
        self.assertIn("'force_test','⚡ Force test'",source)
        self.assertIn('Adw.MessageDialog.new',source)
        self.assertIn('self.engine.force_test(prompt)',source)

    def test_regular_send_is_blocked_while_forced_test_is_in_progress(self):
        from auto_ai_scheduler.engine import Scheduler
        from auto_ai_scheduler.config import validate as config_validate
        from auto_ai_scheduler import x11
        engine=Scheduler(config_validate({}),lambda _:None)
        engine.force_test_pending=True
        with patch.object(x11,'windows') as scan:
            self.assertFalse(engine.send('manual'))
            scan.assert_not_called()
