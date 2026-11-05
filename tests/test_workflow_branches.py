"""Fail-closed scenario branching and OCR quota reset regression tests (no GUI/X11)."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from PIL import Image
from auto_ai_scheduler.proofs import ProofStore
from auto_ai_scheduler.vision import Observation
from auto_ai_scheduler.workflow import Workflow
from auto_ai_scheduler.workflow_run import WorkflowRunner, execution_plan, parse_quota_reset


class Engine:
    def __init__(self):
        self.config = {'idle_seconds':8,'mode':'manual','email_on_error':False}
        self.enabled=False
        self.force_test_pending=False
        self.pending_approval=False
        self.session=SimpleNamespace(waiting_for_response=False,baseline_marker='old')
        self.sent=[]
        self.notified=[]

    def send(self, reason='manual', *, prompt_override=None):
        self.sent.append((reason, prompt_override))
        self.session.waiting_for_response=True
        return True

    def _notify(self, subject, body):
        self.notified.append(subject)


def observation(state, reset=''):
    return Observation(True,True,(350,680),None,state,'worked for 1m|edited 2 files',reset,'fake OCR')


class BranchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proofs=ProofStore(Path(self.tmp.name)/'private')
        self.engine=Engine()
        self.now=[1000.0]
        self.wall=[datetime(2026,11,6,0,20)]
        self.logs=[]
        self.runner=WorkflowRunner(self.engine,self.proofs,self.logs.append,
                                   monotonic=lambda:self.now[0],wall_clock=lambda:self.wall[0])
        self.patcher=patch('auto_ai_scheduler.workflow_run.x11.user_idle_seconds',return_value=300)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def graph(self):
        g=Workflow(name='Branches')
        trigger=g.add('clock')
        trigger.params['at']='2026-11-05 23:00'
        decision=g.add('branch')
        yes=g.add('state')
        yes.params['state']='FINISHED'
        sent=g.add('send')
        no=g.add('notify')
        g.connect(trigger.id,decision.id)
        g.connect(decision.id,yes.id,branch='yes')
        g.connect(yes.id,sent.id)
        g.connect(decision.id,no.id,branch='no')
        return g

    def test_both_exits_persist_and_cycles_are_rejected(self):
        g=self.graph()
        self.assertIn('yes',[e[2] for e in g.edges if len(e)==3])
        restored=Workflow.from_dict(g.to_dict())
        self.assertEqual(restored.edges,g.edges)
        with self.assertRaisesRegex(ValueError,'déjà reliée'):
            g.connect(g.nodes[1].id,g.nodes[-1].id,branch='yes')
        with self.assertRaisesRegex(ValueError,'infinite loop'):
            g.connect(g.nodes[-1].id,g.nodes[0].id)

    def test_yes_goes_to_state_then_send_once(self):
        g=self.graph()
        path, links=execution_plan(g,self.proofs)
        self.assertEqual(set(links[g.nodes[1].id]),{'yes','no'})
        self.runner.start(g)
        with patch.object(self.runner,'_observe',return_value=(observation('FINISHED'),None)):
            for _ in range(7):self.runner.tick()
        self.assertEqual(self.engine.sent,[('workflow','continue')])
        self.assertTrue(self.runner.waiting_response)
        self.assertEqual(len(self.engine.sent),1)

    def test_no_goes_to_notification_without_send(self):
        g=self.graph()
        self.runner.start(g)
        with patch.object(self.runner,'_observe',return_value=(observation('READY'),None)):
            for _ in range(5):self.runner.tick()
        self.assertEqual(self.runner.phase,'done')
        self.assertEqual(self.engine.sent,[])
        self.assertEqual(self.engine.notified,['Rapport de cycle'])

    def test_quota_does_not_choose_no_by_mistake(self):
        self.runner.start(self.graph())
        with patch.object(self.runner,'_observe',return_value=(observation('QUOTA'),None)):
            for _ in range(6):self.runner.tick()
        self.assertEqual(self.runner.current.kind,'branch')
        self.assertFalse(self.engine.sent)

    def test_ocr_no_requires_nonempty_stable_read(self):
        g=self.graph();branch=g.nodes[1]
        branch.params['source']='ocr'
        branch.params['expected']='Finished'
        source=Path(self.tmp.name)/'proof.png'
        Image.new('RGB',(120,90),'white').save(source)
        branch.params['proof_id']=self.proofs.import_image(source)
        self.runner.start(g)
        with patch.object(self.runner,'_observe',return_value=(observation('READY'),object())), \
             patch('auto_ai_scheduler.workflow_run.ocr_image',return_value=''):
            for _ in range(5):self.runner.tick()
        self.assertEqual(self.runner.current.kind,'branch')
        with patch.object(self.runner,'_observe',return_value=(observation('READY'),object())), \
             patch('auto_ai_scheduler.workflow_run.ocr_image',return_value='Other indicator'):
            for _ in range(3):self.runner.tick()
        self.assertEqual(self.runner.phase,'done')
        self.assertFalse(self.engine.sent)

    def test_no_branch_may_not_send_directly(self):
        g=Workflow()
        a=g.add('event');a.params['event']='ready'
        b=g.add('branch')
        c=g.add('send')
        d=g.add('notify')
        g.connect(a.id,b.id)
        g.connect(b.id,c.id,branch='no')
        g.connect(b.id,d.id,branch='yes')
        with self.assertRaisesRegex(ValueError,'Chaque entrée'):
            execution_plan(g,self.proofs)

    def test_positive_quota_branch_cannot_send_without_new_safe_proof(self):
        g=Workflow()
        a=g.add('event');a.params['event']='quota'
        b=g.add('branch');b.params['state']='QUOTA'
        c=g.add('send')
        d=g.add('notify')
        g.connect(a.id,b.id)
        g.connect(b.id,c.id,branch='yes')
        g.connect(b.id,d.id,branch='no')
        with self.assertRaisesRegex(ValueError,'Chaque entrée'):
            execution_plan(g,self.proofs)

    def test_branch_with_missing_exit_is_refused(self):
        g=self.graph()
        g.edges=[e for e in g.edges if not(len(e)==3 and e[2]=='no')]
        with self.assertRaises(ValueError):
            execution_plan(g,self.proofs)

    def test_timeout_pauses_without_any_send(self):
        g=self.graph()
        g.nodes[1].params['timeout_seconds']='5'
        self.runner.start(g)
        self.runner.tick()  # clock
        self.now[0]+=6
        with patch.object(self.runner,'_observe') as observe:
            self.runner.tick()
            observe.assert_not_called()
        self.assertEqual(self.runner.phase,'paused')
        self.assertFalse(self.engine.sent)

    def quota_graph(self, mode='manual'):
        g=Workflow()
        a=g.add('event');a.params['event']='quota'
        b=g.add('quota_wait');b.params['resume']=mode
        c=g.add('state');c.params['state']='READY'
        d=g.add('send')
        g.connect(a.id,b.id)
        g.connect(b.id,c.id)
        g.connect(c.id,d.id)
        return g

    def test_quota_date_parser_is_explicit(self):
        self.assertEqual(parse_quota_reset('Your rate limit resets on Nov 6, 2026, 12:28 AM'),
                         datetime(2026,11,6,0,28))
        for invalid in ['Nov 6 12:28 AM','Nov 35, 2026, 12:28 AM',
                        'reset soon','Nov 6, 2026, 12:28']:
            self.assertIsNone(parse_quota_reset(invalid))

    def test_quota_manual_wait_needs_time_and_approval(self):
        self.runner.start(self.quota_graph())
        quota=observation('QUOTA','Your rate limit resets on Nov 6, 2026, 12:28 AM')
        with patch.object(self.runner,'_observe',return_value=(quota,None)):
            for _ in range(5):self.runner.tick()
        self.assertEqual(self.runner.current.kind,'quota_wait')
        self.assertTrue(self.runner.quota_seen)
        self.wall[0]=datetime(2026,11,6,0,25)
        with patch.object(self.runner,'_observe',return_value=(observation('READY'),None)):
            for _ in range(3):self.runner.tick()
        self.assertEqual(self.runner.current.kind,'quota_wait')
        self.wall[0]=datetime(2026,11,6,0,29)
        with patch.object(self.runner,'_observe',return_value=(observation('READY'),None)):
            self.runner.tick();self.runner.tick()
        self.assertEqual(self.runner.phase,'approval')
        self.assertFalse(self.engine.sent)
        self.assertTrue(self.runner.approve())
        with patch.object(self.runner,'_observe',return_value=(observation('READY'),None)):
            for _ in range(5):self.runner.tick()
        self.assertEqual(self.engine.sent,[('workflow','continue')])

    def test_quota_auto_is_opt_in_and_needs_two_clears(self):
        self.runner.start(self.quota_graph('auto'))
        quota=observation('QUOTA','Your rate limit resets on Nov 6, 2026, 12:28 AM')
        with patch.object(self.runner,'_observe',return_value=(quota,None)):
            for _ in range(5):self.runner.tick()
        self.wall[0]=datetime(2026,11,6,0,29)
        with patch.object(self.runner,'_observe',return_value=(observation('READY'),None)):
            self.runner.tick()
            self.assertEqual(self.runner.current.kind,'quota_wait')
            self.runner.tick()
            self.assertEqual(self.runner.current.kind,'state')
            for _ in range(3):self.runner.tick()
        self.assertEqual(len(self.engine.sent),1)

    def test_unreadable_quota_deadline_pauses(self):
        self.runner.start(self.quota_graph())
        with patch.object(self.runner,'_observe',return_value=(observation('QUOTA','reset Nov ???'),None)):
            for _ in range(3):self.runner.tick()
        self.assertEqual(self.runner.phase,'paused')
        self.assertEqual(self.engine.sent,[])

    def test_quota_never_clears_while_quota_visible(self):
        self.runner.start(self.quota_graph('auto'))
        quota=observation('QUOTA','Your rate limit resets on Nov 6, 2026, 12:28 AM')
        with patch.object(self.runner,'_observe',return_value=(quota,None)):
            for _ in range(6):self.runner.tick()
            self.wall[0]=datetime(2026,11,6,0,40)
            for _ in range(5):self.runner.tick()
        self.assertEqual(self.runner.current.kind,'quota_wait')
        self.assertFalse(self.engine.sent)


if __name__=='__main__':
    unittest.main()
