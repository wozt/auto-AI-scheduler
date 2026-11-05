"""Deterministic headless tests: no real X11, mail, or IDE is contacted."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from PIL import Image

from auto_ai_scheduler.workflow import Workflow
from auto_ai_scheduler.workflow_run import WorkflowRunner, execution_path
from auto_ai_scheduler.proofs import ProofStore
from auto_ai_scheduler.vision import Observation


class DummyEngine:
    def __init__(self, *, approval=False):
        self.config = {'idle_seconds':8, 'mode':'approval' if approval else 'manual',
                       'cooldown_seconds':15, 'scroll_steps':12, 'max_cycles':10}
        self.enabled = False
        self.force_test_pending = False
        self.pending_approval = False
        self.session = SimpleNamespace(waiting_for_response=False, baseline_marker='old')
        self.sent = []
        self.scrolls = []
        self.notified = []
        self.fail_send = False

    def send(self, reason='manual', *, prompt_override=None):
        self.sent.append((reason, prompt_override))
        if self.fail_send:
            return False
        self.session.waiting_for_response = True
        return True

    def scroll(self, *, steps_override=None):
        self.scrolls.append(steps_override)
        return True

    def _notify(self, subject, body):
        self.notified.append(subject)


def obs(state, marker='worked for 9m|edited 3 files'):
    return Observation(True, True, (300, 600), (300, 320), state, marker, '', 'synthetic')


def chain(*kinds):
    g = Workflow(name='Synthetic scenario')
    previous = None
    for kind in kinds:
        node = g.add(kind)
        if previous:
            g.connect(previous.id, node.id)
        previous = node
    return g


class WorkflowRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proofs = ProofStore(Path(self.tmp.name) / 'private')
        self.engine = DummyEngine()
        self.times = [1000.0]
        self.clock = lambda: self.times[0]
        self.events = []
        self.runner = WorkflowRunner(self.engine, self.proofs, self.events.append,
                                     monotonic=self.clock,
                                     wall_clock=lambda: datetime(2026,11,7,12,0))
        self.idle = patch('auto_ai_scheduler.workflow_run.x11.user_idle_seconds', return_value=120)
        self.idle.start()
        self.addCleanup(self.idle.stop)

    def add_ocr(self, graph):
        image = Path(self.tmp.name) / 'sample.png'
        Image.new('RGB', (160, 90), 'white').save(image)
        proof_id = self.proofs.import_image(image)
        node = next(node for node in graph.nodes if node.kind == 'ocr')
        node.params.update(proof_id=proof_id, expected='Finished 1 file', roi='60,20,30,50')
        return node

    def test_validate_rejects_ambiguous_and_disconnected_graphs(self):
        g = chain('clock','state','send')
        g.nodes[0].params['at'] = '2026-11-06 10:00'
        g.add('notify')
        with self.assertRaisesRegex(ValueError,'seul bloc de départ'):
            execution_path(g, self.proofs)
        g.delete(g.nodes[-1].id)
        extra = g.add('notify')
        g.connect(g.nodes[1].id, extra.id)
        with self.assertRaisesRegex(ValueError,'une seule sortie'):
            execution_path(g, self.proofs)

    def test_validate_refuses_send_without_evidence_and_missing_proof(self):
        g=chain('clock','send')
        g.nodes[0].params['at']='2026-11-06 10:00'
        with self.assertRaisesRegex(ValueError,'condition OCR/État'):
            execution_path(g,self.proofs)
        g=chain('event','ocr','send')
        with self.assertRaises(ValueError):
            execution_path(g,self.proofs)

    def test_validate_accepts_ocr_and_wait_and_prevents_bad_parameters(self):
        g=chain('clock','wait','ocr','send')
        g.nodes[0].params['at']='2026-11-06 10:00'
        self.add_ocr(g)
        self.assertEqual(len(execution_path(g,self.proofs)),4)
        g.nodes[1].params['seconds']='-3'
        with self.assertRaises(ValueError):
            execution_path(g,self.proofs)

    def test_clock_ocr_double_check_and_one_send(self):
        g=chain('clock','ocr','send')
        g.nodes[0].params['at']='2026-11-06 10:00'
        self.add_ocr(g)
        self.runner.start(g)
        with patch.object(self.runner,'_observe',return_value=(obs('READY'),object())),\
             patch('auto_ai_scheduler.workflow_run.ocr_image',return_value='Finished 1 file'):
            self.runner.tick()  # clock
            self.assertEqual(self.runner.current.kind,'ocr')
            self.runner.tick()  # first OCR match
            self.assertEqual(self.runner.current.kind,'ocr')
            self.runner.tick()  # second OCR match
            self.assertEqual(self.runner.current.kind,'send')
            self.assertEqual(self.engine.sent,[])
            self.runner.tick()  # exactly one send
            self.assertEqual(self.engine.sent,[('workflow','continue')])
            self.runner.tick()  # response still pending, never sends again
        self.assertTrue(self.runner.waiting_response)
        self.assertEqual(len(self.engine.sent),1)

    def test_finished_event_needs_running_observed_in_this_session(self):
        g=chain('event','state','notify')
        g.nodes[0].params['event']='cycle_finished'
        self.runner.start(g)
        with patch.object(self.runner,'_observe',side_effect=[
            (obs('FINISHED'),None),(obs('FINISHED'),None),
            (obs('RUNNING',''),None),
            (obs('FINISHED'),None),(obs('FINISHED'),None)]):
            for _ in range(4):
                self.runner.tick()
                self.assertEqual(self.runner.current.kind,'event')
            self.runner.tick()
        self.assertEqual(self.runner.current.kind,'state')
        self.assertEqual(self.engine.sent,[])

    def test_ocr_bad_position_never_advances(self):
        g=chain('event','ocr','send')
        g.nodes[0].params['event']='ready'
        self.add_ocr(g)
        self.runner.start(g)
        with patch.object(self.runner,'_observe',return_value=(obs('READY'),object())),\
             patch('auto_ai_scheduler.workflow_run.ocr_image',return_value='other text'):
            self.runner.tick();self.runner.tick()
            self.assertEqual(self.runner.current.kind,'ocr')
            for _ in range(4):self.runner.tick()
        self.assertEqual(self.runner.current.kind,'ocr')
        self.assertFalse(self.engine.sent)

    def test_quota_during_response_pauses_without_resending(self):
        g=chain('event','state','send')
        g.nodes[0].params['event']='ready';g.nodes[1].params['state']='READY'
        self.runner.start(g)
        with patch.object(self.runner,'_observe',return_value=(obs('READY'),None)):
            for _ in range(5):self.runner.tick()
        self.assertEqual(self.engine.sent,[('workflow','continue')])
        with patch.object(self.runner,'_observe',return_value=(obs('QUOTA'),None)):
            self.runner.tick()
        self.assertEqual(self.runner.phase,'paused')
        self.runner.tick()
        self.assertEqual(len(self.engine.sent),1)

    def test_response_requires_changed_marker_and_two_scans(self):
        g=chain('clock','state','send')
        g.nodes[0].params['at']='2026-11-06 10:00'
        g.nodes[1].params['state']='READY'
        self.runner.start(g)
        with patch.object(self.runner,'_observe',return_value=(obs('READY'),None)):
            for _ in range(4):self.runner.tick()
        self.assertTrue(self.runner.waiting_response)
        self.times[0]=1040
        with patch.object(self.runner,'_observe',return_value=(obs('FINISHED','old'),None)):
            self.runner.tick();self.runner.tick()
        self.assertTrue(self.runner.waiting_response)
        with patch.object(self.runner,'_observe',return_value=(obs('FINISHED','new'),None)):
            self.runner.tick();self.assertTrue(self.runner.waiting_response)
            self.runner.tick()
        self.assertEqual(self.runner.phase,'done')
        self.assertFalse(self.engine.session.waiting_for_response)

    def test_approval_is_required_when_enabled(self):
        self.engine = DummyEngine(approval=True)
        self.runner.engine=self.engine
        g=chain('event','state','send');g.nodes[0].params['event']='ready';g.nodes[1].params['state']='READY'
        self.runner.start(g)
        with patch.object(self.runner,'_observe',return_value=(obs('READY'),None)):
            for _ in range(5):self.runner.tick()
        self.assertEqual(self.runner.phase,'approval')
        self.assertFalse(self.engine.sent)
        self.assertTrue(self.runner.approve())
        self.runner.tick()
        self.assertEqual(len(self.engine.sent),1)

    def test_failed_send_must_not_retry_by_itself(self):
        self.engine.fail_send=True
        g=chain('event','state','send');g.nodes[0].params['event']='ready';g.nodes[1].params['state']='READY'
        self.runner.start(g)
        with patch.object(self.runner,'_observe',return_value=(obs('READY'),None)):
            for _ in range(5):self.runner.tick()
        self.assertEqual(self.runner.phase,'paused')
        self.runner.tick()
        self.assertEqual(len(self.engine.sent),1)
        with self.assertRaisesRegex(ValueError,'Envoi incertain'):
            self.runner.resume()

    def test_active_desktop_prevents_any_action(self):
        g=chain('clock','notify');g.nodes[0].params['at']='2026-11-06 10:00'
        self.runner.start(g)
        with patch('auto_ai_scheduler.workflow_run.x11.user_idle_seconds',return_value=0),\
             patch.object(self.runner,'_observe') as visit:
            self.runner.tick()
        self.assertEqual(self.runner.current.kind,'clock')
        visit.assert_not_called()

    def test_graph_is_snapshot_after_start(self):
        g=chain('event','state','send');g.nodes[0].params['event']='ready';g.nodes[1].params['state']='READY'
        self.runner.start(g)
        g.nodes[-1].params['prompt']='changed after arming'
        self.assertEqual(self.runner.path[-1].params['prompt'],'continue')

    def test_engine_classic_and_force_test_cannot_start_parallel(self):
        g=chain('event','state','send');g.nodes[0].params['event']='ready';g.nodes[1].params['state']='READY'
        self.engine.enabled=True
        with self.assertRaises(ValueError):self.runner.start(g)
        self.engine.enabled=False
        self.engine.force_test_pending=True
        with self.assertRaises(ValueError):self.runner.start(g)

    def test_stale_ocr_or_state_proof_is_rechecked_before_send(self):
        g=chain('clock','state','send')
        g.nodes[0].params['at']='2026-11-06 10:00'
        g.nodes[1].params['state']='READY'
        self.runner.start(g)
        with patch.object(self.runner,'_observe',return_value=(obs('READY'),None)):
            for _ in range(3):self.runner.tick()  # clock + two state proofs
            self.assertEqual(self.runner.current.kind,'send')
            self.times[0]=1050  # long pause between proof and send
            self.runner.tick()
            self.assertEqual(self.runner.current.kind,'state')
            self.assertFalse(self.engine.sent)
            self.runner.tick();self.runner.tick();self.runner.tick()
        self.assertEqual(len(self.engine.sent),1)

    def test_notifier_and_scroll_do_not_send_prompt(self):
        g=chain('delay','scroll','notify')
        g.nodes[0].params['seconds']='2'
        self.runner.start(g)
        self.runner.tick()
        self.assertEqual(self.runner.current.kind,'delay')
        self.times[0]+=2
        for _ in range(3):self.runner.tick()
        self.assertEqual(self.runner.phase,'done')
        self.assertEqual(self.engine.scrolls,[12])
        self.assertEqual(self.engine.notified,['Rapport de cycle'])
        self.assertFalse(self.engine.sent)

    def test_stop_prevents_automatic_restart(self):
        g=chain('clock','notify')
        g.nodes[0].params['at']='2026-11-06 10:00'
        self.runner.start(g)
        self.assertTrue(self.runner.stop())
        self.runner.tick()
        self.assertEqual(self.runner.phase,'stopped')
        self.assertFalse(self.engine.notified)

    def test_gui_contract_has_bottom_execution_controls(self):
        source=(Path(__file__).resolve().parents[1]/'auto_ai_scheduler'/'gui.py').read_text()
        self.assertIn("'▶ Scénario'",source)
        self.assertIn("'⏸ Scénario'",source)
        self.assertIn("'■ Scénario'",source)
        self.assertIn('vertical.set_end_child(self.make_timeline())',source)
        self.assertIn('self.runner.tick()',source)
        self.assertIn('self.runner.approve()',source)

if __name__ == '__main__':
    unittest.main()
