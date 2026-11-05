"""Headless structured timeline trace tests: no real window or external API."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from PIL import Image

from auto_ai_scheduler.workflow import Workflow
from auto_ai_scheduler.workflow_run import WorkflowRunner
from auto_ai_scheduler.workflow_checkpoint import CheckpointStore
from auto_ai_scheduler.workflow_trace import TraceJournal, duration_label, REASONS
from auto_ai_scheduler.proofs import ProofStore
from auto_ai_scheduler.vision import Observation
from auto_ai_scheduler import editor, display


class StubEngine:
    def __init__(self):
        self.config = {'idle_seconds':8, 'mode':'manual', 'email_on_error':False}
        self.enabled=False
        self.force_test_pending=False
        self.pending_approval=False
        self.session=SimpleNamespace(waiting_for_response=False,baseline_marker='old')
        self.sent=[]
        self.fail=False

    def send(self, reason='manual', *, prompt_override=None):
        self.sent.append((reason,prompt_override))
        if self.fail:
            return False
        self.session.waiting_for_response=True
        return True

    def _notify(self,*_):pass


def make_graph(*kinds):
    graph=Workflow(name='private-special-workflow')
    previous=None
    for kind in kinds:
        node=graph.add(kind)
        if previous:graph.connect(previous.id,node.id)
        previous=node
    if kinds and kinds[0]=='event':graph.nodes[0].params['event']='ready'
    return graph


def ready():
    return Observation(True,True,(200,500),None,'READY','old','','SECRET RAW OCR TOKEN')


class TraceTests(unittest.TestCase):
    def test_durations_are_bounded_and_formatted(self):
        self.assertEqual(duration_label(-5),'00:00')
        self.assertEqual(duration_label(67),'01:07')
        self.assertEqual(duration_label(3723),'1h 02m 03s')
        node=Workflow().add('ocr')
        current=[100.0]
        trace=TraceJournal(lambda:current[0])
        trace.enter(node)
        current[0]=134.6
        self.assertIn('00:34',trace.summary(node.id)[0])
        trace.record(node,'done','ocr_confirmed')
        current[0]=190
        self.assertIn('00:34',trace.summary(node.id)[0])

    def test_codes_are_allowlisted_and_raw_ocr_refused(self):
        node=Workflow().add('ocr')
        trace=TraceJournal(lambda:10)
        with self.assertRaises(ValueError):trace.record(node,'done','SECRET OCR text')
        with self.assertRaises(ValueError):trace.record(node,'not-a-status','complete')
        trace.enter(node)
        trace.record(node,'ocr_no','ocr_missing')
        self.assertNotIn('SECRET',repr(trace.events))
        self.assertEqual(len(trace.lines()),2)
        self.assertEqual(REASONS['ocr_missing'],'Texte attendu non détecté dans la zone OCR')

    def test_duplicate_observations_do_not_flood_the_log(self):
        node=Workflow().add('ocr')
        trace=TraceJournal(lambda:200)
        trace.enter(node)
        for i in range(50):trace.record(node,'ocr_no','ocr_missing')
        self.assertEqual(len(trace.events),2)
        self.assertEqual(trace.revision,2)

    def test_log_bound_capped_and_reset(self):
        node=Workflow().add('event')
        trace=TraceJournal(lambda:200,limit=10)
        for i in range(50):trace.record(node,'active' if i%2 else 'waiting','event_wait')
        self.assertEqual(len(trace.events),10)
        trace.reset()
        self.assertEqual(trace.events,[])
        self.assertIsNone(trace.latest.get(node.id))

    def test_css_and_editor_offer_per_clip_status(self):
        src=(Path(__file__).resolve().parents[1]/'auto_ai_scheduler'/'editor.py').read_text()
        gui=(Path(__file__).resolve().parents[1]/'auto_ai_scheduler'/'gui.py').read_text()
        self.assertIn('def update_trace():',src)
        self.assertIn('trace.summary(ident)',src)
        self.assertIn('Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)',gui)
        self.assertIn('trace=self.runner.trace',gui)
        self.assertIn('trace_widget.get_buffer().set_text',gui)
        self.assertIn(b'.scheduler-trace-timeout',display.css(12))


class RunnerTraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proofs=ProofStore(Path(self.tmp.name)/'proofs')
        self.engine=StubEngine()
        self.now=[1000.0]
        self.runner=WorkflowRunner(self.engine,self.proofs,lambda _:None,
                                   monotonic=lambda:self.now[0],
                                   wall_clock=lambda:datetime(2026,11,7,12,0))
        patcher=patch('auto_ai_scheduler.workflow_run.x11.user_idle_seconds',return_value=100)
        patcher.start();self.addCleanup(patcher.stop)

    def test_no_send_log_on_start_and_actual_progress(self):
        graph=make_graph('event','state','send')
        graph.nodes[1].params['state']='READY'
        graph.nodes[2].params['prompt']='SUPER SECRET PROMPT'
        self.runner.start(graph)
        self.assertEqual(len(self.runner.trace.events),1)
        with patch.object(self.runner,'_observe',return_value=(ready(),None)):
            self.runner.tick();self.now[0]+=3
            self.runner.tick()
            self.runner.tick()
            self.runner.tick()
            self.runner.tick()
        self.assertEqual(self.engine.sent,[('workflow','SUPER SECRET PROMPT')])
        self.assertEqual(self.runner.trace.latest[graph.nodes[0].id].status,'done')
        self.assertEqual(self.runner.trace.latest[graph.nodes[1].id].status,'done')
        self.assertEqual(self.runner.trace.latest[graph.nodes[2].id].status,'response')
        self.assertNotIn('SUPER SECRET PROMPT','\n'.join(self.runner.trace.lines()))
        self.assertNotIn('SECRET RAW OCR TOKEN','\n'.join(self.runner.trace.lines()))

    def test_ocr_missing_then_detected_twice(self):
        graph=make_graph('event','ocr','send')
        picture=Path(self.tmp.name)/'proof.png'
        Image.new('RGB',(140,90),'white').save(picture)
        proof_id=self.proofs.import_image(picture)
        graph.nodes[1].params.update(proof_id=proof_id,expected='SUCCEEDED',roi='0,0,100,100')
        self.runner.start(graph)
        with patch.object(self.runner,'_observe',return_value=(ready(),object())):
            self.runner.tick();self.runner.tick()
            with patch('auto_ai_scheduler.workflow_run.ocr_image',return_value='OTHER TOKEN SECRET'):
                self.runner.tick()
            self.assertEqual(self.runner.trace.latest[graph.nodes[1].id].reason,'ocr_missing')
            self.assertEqual(self.runner.current.kind,'ocr')
            with patch('auto_ai_scheduler.workflow_run.ocr_image',return_value='SUCCEEDED SECRET'):
                self.runner.tick()
                self.assertEqual(self.runner.trace.latest[graph.nodes[1].id].reason,'ocr_first')
                self.runner.tick()
        self.assertEqual(self.runner.current.kind,'send')
        self.assertEqual(self.runner.trace.latest[graph.nodes[1].id].reason,'ocr_confirmed')
        self.assertFalse(self.engine.sent)
        self.assertNotIn('OTHER TOKEN SECRET','\n'.join(self.runner.trace.lines()))

    def test_timeout_records_reason_without_actuating(self):
        graph=make_graph('event','state','send')
        graph.nodes[0].params['event']='ready'
        graph.nodes[1].params['timeout_seconds']='5'
        self.runner.start(graph)
        with patch.object(self.runner,'_observe',return_value=(ready(),None)):
            self.runner.tick();self.runner.tick()
        self.assertEqual(self.runner.current.kind,'state')
        self.now[0]+=10
        with patch.object(self.runner,'_observe') as observed:
            self.runner.tick()
            observed.assert_not_called()
        self.assertEqual(self.runner.phase,'paused')
        self.assertEqual(self.runner.trace.latest[graph.nodes[1].id].status,'timeout')
        self.assertEqual(self.engine.sent,[])

    def test_failed_send_is_traced_and_not_retried(self):
        graph=make_graph('event','state','send')
        graph.nodes[1].params['state']='READY'
        self.engine.fail=True
        self.runner.start(graph)
        with patch.object(self.runner,'_observe',return_value=(ready(),None)):
            for _ in range(7):self.runner.tick()
        self.assertEqual(len(self.engine.sent),1)
        self.assertEqual(self.runner.phase,'paused')
        self.assertEqual(self.runner.trace.latest[graph.nodes[2].id].reason,'send_unconfirmed')

    def test_restore_trace_is_paused_and_does_not_replay(self):
        graph=make_graph('event','state','send')
        storage=CheckpointStore(Path(self.tmp.name)/'state'/'checkpoint.json')
        self.runner.checkpoints=storage
        self.runner.start(graph)
        second=WorkflowRunner(self.engine,self.proofs,lambda _:None,
                              monotonic=lambda:self.now[0],
                              wall_clock=lambda:datetime(2026,11,7,12,0),
                              checkpoints=storage)
        with patch.object(second,'_observe') as obs:
            second.restore(graph)
            second.tick()
            obs.assert_not_called()
        self.assertEqual(second.phase,'paused')
        self.assertEqual(second.trace.latest[graph.nodes[0].id].status,'restored')
        self.assertEqual(self.engine.sent,[])

    def test_branch_yes_trace_is_reached_without_double_send(self):
        graph=Workflow()
        a=graph.add('event');a.params['event']='ready'
        b=graph.add('branch');b.params.update(source='state',state='READY')
        c=graph.add('notify')
        d=graph.add('notify')
        graph.connect(a.id,b.id)
        graph.connect(b.id,c.id,branch='yes')
        graph.connect(b.id,d.id,branch='no')
        self.runner.start(graph)
        with patch.object(self.runner,'_observe',return_value=(ready(),None)):
            for _ in range(6):self.runner.tick()
        self.assertEqual(self.runner.phase,'done')
        self.assertEqual(self.runner.trace.latest[b.id].reason,'branch_yes')
        self.assertIsNone(self.runner.trace.latest.get(d.id))
        self.assertEqual(self.engine.sent,[])

    def test_pause_resume_does_not_send_and_trace_is_in_memory(self):
        graph=make_graph('event','state','send')
        self.runner.start(graph)
        self.assertTrue(self.runner.pause())
        self.assertEqual(self.runner.trace.latest[graph.nodes[0].id].reason,'manual_pause')
        self.assertTrue(self.runner.resume())
        self.assertEqual(self.runner.trace.latest[graph.nodes[0].id].reason,'resumed')
        self.assertEqual(self.engine.sent,[])


if __name__=='__main__':unittest.main()
