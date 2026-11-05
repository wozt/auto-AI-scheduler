"""Debug-mode regression: no unintended X11 actions or private OCR artifacts."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from PIL import Image

from auto_ai_scheduler.workflow import Workflow
from auto_ai_scheduler.workflow_run import WorkflowRunner
from auto_ai_scheduler.workflow_debug import BreakpointStore, dry_run_ocr
from auto_ai_scheduler.proofs import ProofStore
from auto_ai_scheduler.vision import Observation
from auto_ai_scheduler import x11


class FakeEngine:
    def __init__(self):
        self.config = {'idle_seconds': 8, 'mode': 'manual', 'email_on_error': False}
        self.enabled = False
        self.pending_approval = False
        self.force_test_pending = False
        self.session = SimpleNamespace(waiting_for_response=False, baseline_marker='old')
        self.sent = []

    def inspect_target(self, action=None):
        obs = Observation(True, True, (400, 600), None, 'READY', '', '', '')
        if action:
            action('123', obs, (0, 0))
        return obs

    def send(self, reason='manual', *, prompt_override=None):
        self.sent.append((reason, prompt_override))
        self.session.waiting_for_response = True
        return True

    def _notify(self, *args):
        pass


def graph_for(*kinds):
    graph=Workflow()
    previous=None
    for kind in kinds:
        node=graph.add(kind)
        if previous:graph.connect(previous.id,node.id)
        previous=node
    if kinds and kinds[0]=='event':graph.nodes[0].params['event']='ready'
    if kinds and kinds[0]=='delay':graph.nodes[0].params['seconds']='1'
    return graph


class DebugTests(unittest.TestCase):
    def test_debug_starts_paused_before_any_x11(self):
        now=[100.0]
        engine=FakeEngine()
        runner=WorkflowRunner(engine,ProofStore(Path('/tmp/does-not-exist')),lambda _:None,monotonic=lambda:now[0])
        graph=graph_for('delay','wait')
        runner.configure_debug(True)
        with patch.object(x11,'user_idle_seconds') as idle:
            runner.start(graph)
            self.assertEqual(runner.phase,'paused')
            runner.tick()
            idle.assert_not_called()
        self.assertEqual(runner.current.kind,'delay')
        self.assertEqual(engine.sent,[])

    def test_step_waits_for_block_completion_and_stops_before_next(self):
        now=[100.0]
        engine=FakeEngine()
        runner=WorkflowRunner(engine,ProofStore(Path('/tmp/does-not-exist')),lambda _:None,monotonic=lambda:now[0])
        graph=graph_for('delay','wait')
        runner.configure_debug(True)
        runner.start(graph)
        runner.debug_step()
        self.assertEqual(runner.phase,'running')
        with patch.object(x11,'user_idle_seconds',return_value=200):
            runner.tick()
            self.assertEqual(runner.current.kind,'delay')
            now[0]=102
            runner.tick()
        self.assertEqual(runner.current.kind,'wait')
        self.assertEqual(runner.phase,'paused')
        self.assertTrue(runner.debug_hold)
        with self.assertRaisesRegex(ValueError,'Pas ou Continuer'):
            runner.resume()
        self.assertEqual(engine.sent,[])

    def test_continue_stops_at_breakpoint_before_action(self):
        now=[100.0]
        engine=FakeEngine()
        runner=WorkflowRunner(engine,ProofStore(Path('/tmp/does-not-exist')),lambda _:None,monotonic=lambda:now[0])
        graph=graph_for('delay','wait','notify')
        runner.configure_debug(True,{graph.nodes[1].id})
        runner.start(graph)
        runner.debug_continue()
        now[0]=102
        with patch.object(x11,'user_idle_seconds',return_value=200):
            runner.tick()
        self.assertEqual(runner.current.id,graph.nodes[1].id)
        self.assertEqual(runner.phase,'paused')
        self.assertEqual(engine.sent,[])

    def test_send_step_requires_explicit_confirmation_and_does_not_skip_guard(self):
        engine=FakeEngine()
        graph=graph_for('event','state','send')
        graph.nodes[1].params['state']='READY'
        runner=WorkflowRunner(engine,ProofStore(Path('/tmp/does-not-exist')),lambda _:None,monotonic=lambda:100)
        runner.configure_debug(True)
        runner.start(graph)
        runner.index=2
        runner.debug_hold=True
        runner.phase='paused'
        runner.evidence_at=100
        with patch.object(x11,'user_idle_seconds',return_value=200):
            with self.assertRaisesRegex(ValueError,'Confirmation'):
                runner.debug_continue()
            with self.assertRaisesRegex(ValueError,'Confirmation'):
                runner.debug_step()
            self.assertEqual(engine.sent,[])
            runner.debug_step(send_confirmed=True)
            self.assertEqual(runner.phase,'running')
            runner.tick()
        self.assertEqual(engine.sent,[('workflow','continue')])
        self.assertTrue(runner.waiting_response)

    def test_old_uncertain_send_cannot_be_unlocked_by_debugger(self):
        engine=FakeEngine()
        runner=WorkflowRunner(engine,ProofStore(Path('/tmp/does-not-exist')),lambda _:None,monotonic=lambda:100)
        runner.configure_debug(True)
        runner.start(graph_for('delay','wait'))
        runner.send_attempted=True
        with self.assertRaisesRegex(ValueError,'Envoi incertain'):
            runner.debug_step()
        self.assertEqual(runner.phase,'paused')
        self.assertEqual(engine.sent,[])

    def test_breakpoints_private_and_persistent(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'private'/'breakpoints.json'
            store=BreakpointStore(path)
            store.save({'node-one','node-two'})
            self.assertEqual(store.load(),{'node-one','node-two'})
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777,0o700)
            self.assertNotIn('prompt',path.read_text())
            with self.assertRaises(ValueError):store.save({'../../bad'})
            path.unlink()
            path.symlink_to(Path(folder)/'target')
            with self.assertRaises(ValueError):store.save({'node'})

    def test_dry_run_ocr_does_not_send_or_advance(self):
        with TemporaryDirectory() as folder:
            proof=ProofStore(Path(folder)/'proofs')
            source=Path(folder)/'crop.png'
            Image.new('RGB',(200,100),'white').save(source)
            identifier=proof.import_image(source)
            node=Workflow().add('ocr')
            node.params.update({'proof_id':identifier,'expected':'Worked for','roi':'0,0,100,100'})
            engine=FakeEngine()
            with patch.object(x11,'screenshot',return_value=(Image.new('RGB',(200,100)),(0,0))), \
                 patch('auto_ai_scheduler.workflow_debug.ocr_image',return_value='Worked for 12m'):
                result=dry_run_ocr(engine,proof,node)
            self.assertTrue(result.matched)
            self.assertEqual(engine.sent,[])
            self.assertEqual(result.description,'Preuve OCR détectée')

    def test_dry_run_refuses_quota_even_with_matching_text(self):
        with TemporaryDirectory() as folder:
            proof=ProofStore(Path(folder)/'proofs')
            source=Path(folder)/'crop.png';Image.new('RGB',(200,100)).save(source)
            node=Workflow().add('ocr')
            node.params.update({'proof_id':proof.import_image(source),'expected':'Worked for','roi':'0,0,100,100'})
            engine=FakeEngine()
            def quota(action=None):
                return Observation(True,True,(300,400),None,'QUOTA','','','')
            engine.inspect_target=quota
            with patch('auto_ai_scheduler.workflow_debug.ocr_image') as ocr,patch.object(x11,'screenshot') as screen:
                result=dry_run_ocr(engine,proof,node)
            self.assertFalse(result.matched)
            ocr.assert_not_called();screen.assert_not_called()

    def test_gui_controls_present_in_bottom_timeline(self):
        src=(Path(__file__).resolve().parents[1]/'auto_ai_scheduler'/'gui.py').read_text()
        start=src.index('        def make_timeline(self):')
        end=src.index('        def _timeline_components(self):')
        bottom=src[start:end]
        for tag in ('debug_mode','debug_step','debug_continue','breakpoint','ocr_test'):
            self.assertIn("'"+tag+"'",bottom)
        self.assertIn('self.runner.debug_step(send_confirmed=True)',src)
        self.assertIn('Adw.MessageDialog.new',src)
        self.assertIn('dry_run_ocr(self.engine,self.proof_store,node)',src)
        self.assertIn('breakpoints=lambda: self.breakpoints',src)

if __name__=='__main__':unittest.main()
