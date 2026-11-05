"""No-X11 checkpoint tests: restart is explicit and never replays a prompt."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import json
import unittest

from auto_ai_scheduler.workflow import Workflow
from auto_ai_scheduler.workflow_checkpoint import CheckpointStore, graph_fingerprint
from auto_ai_scheduler.workflow_run import WorkflowRunner
from auto_ai_scheduler.proofs import ProofStore
from auto_ai_scheduler.vision import Observation


class FakeEngine:
    def __init__(self):
        self.config={'idle_seconds':8,'mode':'manual','email_on_error':False}
        self.enabled=False
        self.force_test_pending=False
        self.pending_approval=False
        self.session=SimpleNamespace(waiting_for_response=False,baseline_marker='old')
        self.sent=[]
        self.on_send=None

    def send(self, reason='manual', *, prompt_override=None):
        if self.on_send:self.on_send()
        self.sent.append((reason,prompt_override))
        self.session.waiting_for_response=True
        return True

    def _notify(self, *_):pass


def graph_for_send():
    g=Workflow(name='private scenario')
    a=g.add('event');a.params['event']='ready'
    b=g.add('state');b.params['state']='READY'
    c=g.add('send');c.params['prompt']='PRIVATE special tokens and credentials'
    g.connect(a.id,b.id);g.connect(b.id,c.id)
    return g


def ready():
    return Observation(True,True,(400,600),None,'READY','old','','synthetic')


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=CheckpointStore(Path(self.tmp.name)/'state'/'progress.json')
        self.proofs=ProofStore(Path(self.tmp.name)/'proofs')
        self.engine=FakeEngine()
        self.now=[1000.0]
        self.events=[]
        self.runner=self.make_runner()
        self.graph=graph_for_send()
        patcher=patch('auto_ai_scheduler.workflow_run.x11.user_idle_seconds',return_value=150)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_runner(self):
        return WorkflowRunner(self.engine,self.proofs,self.events.append,
                              monotonic=lambda:self.now[0],
                              wall_clock=lambda:datetime(2026,11,6,12,0),
                              checkpoints=self.store)

    def go_to_send(self,runner):
        with patch.object(runner,'_observe',return_value=(ready(),None)):
            for _ in range(4):runner.tick()
        self.assertEqual(runner.current.kind,'send')

    def test_checkpoint_is_private_without_prompt_or_ocr(self):
        self.runner.start(self.graph)
        blob=self.store.path.read_text()
        self.assertEqual(self.store.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.store.path.parent.stat().st_mode & 0o777,0o700)
        self.assertNotIn('PRIVATE',blob)
        self.assertNotIn('scenario',blob)
        self.assertNotIn('continue',blob)
        self.assertEqual(self.store.load()['workflow'],graph_fingerprint(self.graph))

    def test_restore_does_not_resume_or_call_ide(self):
        self.runner.start(self.graph)
        restarted=self.make_runner()
        with patch.object(restarted,'_observe') as observation,patch.object(self.engine,'send') as send:
            self.assertTrue(restarted.restore(self.graph))
            self.assertEqual(restarted.phase,'paused')
            restarted.tick()
            observation.assert_not_called()
            send.assert_not_called()
        self.assertEqual(restarted.current.kind,'event')

    def test_restart_at_unsent_prompt_rechecks_previous_condition(self):
        self.runner.start(self.graph)
        self.go_to_send(self.runner)
        self.assertEqual(self.store.load()['send_state'],'none')
        recovered=self.make_runner()
        recovered.restore(self.graph)
        self.assertEqual(recovered.current.kind,'state')
        self.assertEqual(recovered.phase,'paused')
        recovered.resume()
        self.assertEqual(recovered.current.kind,'state')
        self.assertFalse(recovered.evidence_at)
        self.assertEqual(self.engine.sent,[])

    def test_mark_send_attempt_before_typing_and_restore_blocks_replay(self):
        self.runner.start(self.graph)
        self.go_to_send(self.runner)
        self.engine.on_send=lambda:self.assertEqual(self.store.load()['send_state'],'attempted')
        self.runner.tick()
        self.assertEqual(self.engine.sent,[('workflow','PRIVATE special tokens and credentials')])
        self.assertEqual(self.store.load()['send_state'],'awaiting_response')
        recorded_sends=list(self.engine.sent)
        self.engine=FakeEngine()  # new app process has a fresh in-memory session
        after_crash=self.make_runner()
        after_crash.restore(self.graph)
        self.assertEqual(after_crash.phase,'paused')
        self.assertEqual(after_crash.current.kind,'send')
        self.assertTrue(after_crash.send_attempted)
        with self.assertRaisesRegex(ValueError,'Envoi incertain'):
            after_crash.resume()
        after_crash.tick()
        self.assertEqual(recorded_sends,[('workflow','PRIVATE special tokens and credentials')])
        self.assertEqual(self.engine.sent,[])
        self.assertEqual(self.store.load()['send_state'],'attempted')

    def test_crash_during_send_is_still_blocked(self):
        self.runner.start(self.graph)
        self.go_to_send(self.runner)
        def fail():raise RuntimeError('X11 unexpectedly closed')
        self.engine.on_send=fail
        self.runner.tick()
        self.assertEqual(self.runner.phase,'paused')
        self.assertEqual(self.store.load()['send_state'],'attempted')
        recovered=self.make_runner()
        recovered.restore(self.graph)
        with self.assertRaisesRegex(ValueError,'Envoi incertain'):
            recovered.resume()

    def test_checkpoint_failure_aborts_before_ide_input(self):
        self.runner.start(self.graph)
        self.go_to_send(self.runner)
        with patch.object(self.store,'save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.runner.tick()
        self.assertFalse(self.engine.sent)
        self.assertEqual(self.runner.phase,'paused')

    def test_changed_graph_cannot_inherit_a_checkpoint(self):
        self.runner.start(self.graph)
        changed=Workflow.from_dict(self.graph.to_dict())
        changed.nodes[-1].params['prompt']='another command'
        with self.assertRaisesRegex(ValueError,'a changé'):
            self.make_runner().restore(changed)
        self.assertFalse(self.engine.sent)

    def test_cannot_start_fresh_workflow_when_checkpoint_exists(self):
        self.runner.start(self.graph)
        new_runner=self.make_runner()
        with self.assertRaisesRegex(ValueError,'Checkpoint existant'):
            new_runner.start(self.graph)
        self.assertFalse(self.engine.sent)

    def test_stop_clears_checkpoint_and_user_can_rearm(self):
        self.runner.start(self.graph)
        self.assertTrue(self.store.path.exists())
        self.runner.stop()
        self.assertFalse(self.store.path.exists())
        self.make_runner().start(self.graph)
        self.assertTrue(self.store.path.exists())

    def test_stop_never_discards_a_possibly_sent_prompt(self):
        self.runner.start(self.graph)
        self.go_to_send(self.runner)
        self.runner.tick()
        self.assertTrue(self.runner.waiting_response)
        self.assertTrue(self.runner.stop())
        self.assertEqual(self.runner.phase,'stopped')
        self.assertEqual(self.store.load()['send_state'],'attempted')
        recovered=self.make_runner()
        self.engine=FakeEngine()
        recovered.engine=self.engine
        recovered.restore(self.graph)
        with self.assertRaisesRegex(ValueError,'Envoi incertain'):recovered.resume()

    def test_explicit_forget_is_needed_after_uncertain_send(self):
        self.runner.start(self.graph)
        self.go_to_send(self.runner)
        self.runner.tick()
        self.engine=FakeEngine()  # application restarted
        r=self.make_runner()
        r.restore(self.graph)
        with self.assertRaisesRegex(ValueError,'Envoi incertain'):r.resume()
        self.assertTrue(r.forget_checkpoint())
        self.assertFalse(self.store.path.exists())
        self.assertEqual(r.phase,'stopped')

    def test_refuses_malformed_and_world_readable_checkpoint(self):
        self.runner.start(self.graph)
        data=json.loads(self.store.path.read_text())
        data['prompt']='injected'
        self.store.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'structure inattendue'):self.store.load()
        self.store.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'permissions'):self.store.load()

    def test_checkpoint_malformed_phase_is_rejected_cleanly(self):
        self.runner.start(self.graph)
        data=json.loads(self.store.path.read_text())
        data['phase']=['running']
        self.store.path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'état'):self.store.load()

    def test_gui_has_explicit_restore_and_forget_no_auto_resume(self):
        source=(Path(__file__).resolve().parents[1]/'auto_ai_scheduler'/'gui.py').read_text()
        self.assertIn('↶ Restaurer',source)
        self.assertIn('✕ Effacer reprise',source)
        self.assertIn('self.runner.restore(self.workflow_store.load())',source)
        self.assertIn('self.runner.forget_checkpoint()',source)
        self.assertIn('self.runner=WorkflowRunner(',source)
        initial=source[source.index('def __init__(self):'):source.index('def log(self,message):')]
        self.assertNotIn('self.runner.restore(',initial)
        self.assertNotIn('self.runner.start(',initial)

    def test_cannot_load_symlink_checkpoint(self):
        target=Path(self.tmp.name)/'target'
        target.write_text('{}')
        self.store.path.parent.mkdir(parents=True)
        self.store.path.symlink_to(target)
        with self.assertRaisesRegex(ValueError,'symbolique'):self.store.load()

    def test_suspended_wait_resets_time_after_restart(self):
        g=Workflow()
        a=g.add('event');a.params['event']='ready'
        b=g.add('wait');b.params['seconds']='120'
        c=g.add('notify');g.connect(a.id,b.id);g.connect(b.id,c.id)
        self.runner.start(g)
        with patch.object(self.runner,'_observe',return_value=(ready(),None)):
            self.runner.tick();self.runner.tick()
        self.assertEqual(self.runner.current.kind,'wait')
        self.now[0]=1500
        recovered=self.make_runner()
        recovered.restore(g)
        self.assertEqual(recovered.phase,'paused')
        recovered.resume()
        recovered.tick()
        self.assertEqual(recovered.current.kind,'wait')
        self.now[0]=1621
        recovered.tick()
        self.assertEqual(recovered.current.kind,'notify')

    def test_complete_workflow_clears_checkpoint(self):
        g=Workflow()
        a=g.add('event');a.params['event']='ready'
        b=g.add('notify');g.connect(a.id,b.id)
        self.runner.start(g)
        with patch.object(self.runner,'_observe',return_value=(ready(),None)):
            for _ in range(3):self.runner.tick()
        self.assertEqual(self.runner.phase,'done')
        self.assertFalse(self.store.path.exists())


if __name__=='__main__':unittest.main()
