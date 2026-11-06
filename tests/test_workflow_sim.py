"""Full-scenario preview: no desktop automation, mail or replayed live sends."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from PIL import Image

from auto_ai_scheduler.proofs import ProofStore
from auto_ai_scheduler.workflow import Workflow
from auto_ai_scheduler.workflow_sim import SimulationInputs, simulate
from auto_ai_scheduler import x11


NOW = datetime(2026, 11, 6, 16, 0)


def base_graph():
    graph = Workflow(name='Private test with secret prompt')
    root = graph.add('clock')
    root.params['at'] = '2020-01-01 00:00'
    return graph, root


def connect(graph, *nodes):
    for left, right in zip(nodes, nodes[1:]):
        graph.connect(left.id, right.id)


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proofs = ProofStore(Path(self.temp.name)/'private')
        source = Path(self.temp.name)/'sample.png'
        Image.new('RGB', (220, 100), 'white').save(source)
        self.proof_id = self.proofs.import_image(source)

    def test_prompt_is_previewed_but_never_sent(self):
        graph, root = base_graph()
        proof = graph.add('ocr')
        proof.params.update(proof_id=self.proof_id, expected='Worked for 12m')
        send = graph.add('send')
        send.params['prompt'] = 'MY TOP SECRET PROMPT'
        connect(graph, root, proof, send)
        before = graph.to_dict()
        options = SimulationInputs(codex_state='FINISHED', at=NOW,
                                   observed={proof.id:self.proof_id})
        with patch('auto_ai_scheduler.workflow_sim.ocr_image', return_value='Worked for 12m'), \
             patch.object(x11, 'windows') as windows, \
             patch.object(x11, 'type_and_send') as send_real, \
             patch.object(x11, 'scroll_down') as scroll_real, \
             patch('smtplib.SMTP_SSL') as smtp:
            result = simulate(graph, self.proofs, options)
            self.assertEqual(result.outcome, 'complete')
            self.assertEqual(result.steps[-1].status, 'would')
            windows.assert_not_called()
            send_real.assert_not_called()
            scroll_real.assert_not_called()
            smtp.assert_not_called()
        self.assertNotIn('MY TOP SECRET PROMPT', '\n'.join(result.lines()))
        self.assertEqual(graph.to_dict(), before)
        self.assertEqual(list(self.proofs.directory.glob('*.json')), [])

    def test_no_observed_image_is_unknown_not_negative(self):
        graph, root = base_graph()
        proof = graph.add('ocr')
        proof.params.update(proof_id=self.proof_id, expected='Completed')
        send = graph.add('send')
        connect(graph, root, proof, send)
        result = simulate(graph,self.proofs,SimulationInputs(codex_state='FINISHED',at=NOW))
        self.assertEqual(result.outcome,'blocked')
        self.assertEqual(result.steps[-1].status,'blocked')
        self.assertEqual(len(result.steps),2)
        self.assertIn('aucune capture', result.steps[-1].reason)

    def test_reference_demo_must_be_explicit(self):
        graph, root = base_graph()
        proof = graph.add('ocr')
        proof.params.update(proof_id=self.proof_id,expected='Complete')
        connect(graph, root, proof)
        with patch('auto_ai_scheduler.workflow_sim.ocr_image',return_value='Complete'):
            result=simulate(graph,self.proofs,SimulationInputs(codex_state='FINISHED',at=NOW,
                                                               use_reference_images=True))
        self.assertEqual(result.outcome,'complete')
        self.assertIn('démo',result.steps[-1].reason)

    def test_ocr_negative_waits_and_cannot_send(self):
        graph, root = base_graph()
        proof = graph.add('ocr')
        proof.params.update(proof_id=self.proof_id,expected='Complete')
        send = graph.add('send')
        connect(graph, root, proof, send)
        with patch('auto_ai_scheduler.workflow_sim.ocr_image',return_value='Something else'):
            result=simulate(graph,self.proofs,SimulationInputs(codex_state='FINISHED',at=NOW,
                                                               use_reference_images=True))
        self.assertEqual(result.outcome,'waiting')
        self.assertEqual(len(result.steps),2)
        self.assertEqual(result.steps[-1].status,'waiting')

    def test_branch_yes_and_no_choose_one_path_only(self):
        graph, root = base_graph()
        branch = graph.add('branch')
        branch.params.update(source='state', state='FINISHED')
        yes = graph.add('notify')
        no = graph.add('wait')
        no.params['seconds']='10'
        graph.connect(root.id, branch.id)
        graph.connect(branch.id, yes.id, 'yes')
        graph.connect(branch.id, no.id, 'no')
        result_yes=simulate(graph,self.proofs,SimulationInputs(codex_state='FINISHED',at=NOW))
        self.assertEqual([s.node_id for s in result_yes.steps],[root.id,branch.id,yes.id])
        self.assertEqual(result_yes.steps[1].status,'yes')
        self.assertEqual(result_yes.outcome,'complete')
        result_no=simulate(graph,self.proofs,SimulationInputs(codex_state='READY',at=NOW,
                                                             elapsed_seconds=10))
        self.assertEqual([s.node_id for s in result_no.steps],[root.id,branch.id,no.id])
        self.assertEqual(result_no.steps[1].status,'no')
        self.assertEqual(result_no.outcome,'complete')
        result_unknown=simulate(graph,self.proofs,SimulationInputs(at=NOW))
        self.assertEqual(result_unknown.outcome,'blocked')
        self.assertEqual(len(result_unknown.steps),2)

    def test_ocr_branch_missing_evidence_must_not_take_no_exit(self):
        graph, root=base_graph()
        branch=graph.add('branch')
        branch.params.update(source='ocr', proof_id=self.proof_id, expected='Complete')
        yes=graph.add('notify')
        no=graph.add('wait')
        graph.connect(root.id,branch.id)
        graph.connect(branch.id,yes.id,'yes')
        graph.connect(branch.id,no.id,'no')
        result=simulate(graph,self.proofs,SimulationInputs(at=NOW,codex_state='READY'))
        self.assertEqual(result.outcome,'blocked')
        self.assertEqual(len(result.steps),2)
        with patch('auto_ai_scheduler.workflow_sim.ocr_image',return_value='Different'):
            negative=simulate(graph,self.proofs,SimulationInputs(at=NOW,codex_state='READY',
                                      observed={branch.id:self.proof_id},elapsed_seconds=60))
        self.assertEqual(negative.steps[1].status,'no')
        self.assertEqual(negative.steps[2].kind,'wait')

    def test_time_gates_and_cycle_finished(self):
        graph, root=base_graph()
        result=simulate(graph,self.proofs,SimulationInputs(at=datetime(2019,1,1)))
        self.assertEqual(result.outcome,'waiting')
        self.assertIn('non atteinte',result.steps[0].reason)
        graph2=Workflow()
        start=graph2.add('event')
        start.params['event']='cycle_finished'
        result=simulate(graph2,self.proofs,SimulationInputs(at=NOW, event='cycle_finished',
                                           codex_state='FINISHED',saw_running=False))
        self.assertEqual(result.outcome,'waiting')
        result=simulate(graph2,self.proofs,SimulationInputs(at=NOW, event='cycle_finished',
                                           codex_state='FINISHED',saw_running=True))
        self.assertEqual(result.outcome,'complete')

    def test_timeout_blocks_even_if_ocr_would_match(self):
        graph, root=base_graph()
        proof=graph.add('ocr')
        proof.params.update(proof_id=self.proof_id, expected='DONE', timeout_seconds='10')
        send=graph.add('send')
        connect(graph,root,proof,send)
        with patch('auto_ai_scheduler.workflow_sim.ocr_image',return_value='DONE') as ocr:
            result=simulate(graph,self.proofs,SimulationInputs(at=NOW,codex_state='READY',
                                                               elapsed_seconds=11,use_reference_images=True))
            ocr.assert_not_called()
        self.assertEqual(result.outcome,'blocked')
        self.assertIn('Timeout',result.steps[-1].reason)
        self.assertEqual(len(result.steps),2)

    def test_quota_replay_is_never_assumed(self):
        graph, root=base_graph()
        quota=graph.add('quota_wait')
        state=graph.add('state')
        state.params['state']='READY'
        send=graph.add('send')
        connect(graph, root, quota, state, send)
        options=SimulationInputs(at=NOW,codex_state='READY')
        self.assertEqual(simulate(graph,self.proofs,options).outcome,'blocked')
        options=SimulationInputs(at=NOW,codex_state='READY',quota_was_seen=True,
                                 quota_deadline=datetime(2026,11,6,18), quota_approved=True)
        self.assertEqual(simulate(graph,self.proofs,options).outcome,'waiting')
        options=SimulationInputs(at=NOW,codex_state='READY',quota_was_seen=True,
                                 quota_deadline=datetime(2026,11,6,12), quota_approved=False)
        self.assertEqual(simulate(graph,self.proofs,options).outcome,'waiting')
        options=SimulationInputs(at=NOW,codex_state='READY',quota_was_seen=True,
                                 quota_deadline=datetime(2026,11,6,12), quota_approved=True)
        result=simulate(graph,self.proofs,options)
        self.assertEqual(result.outcome,'complete')
        self.assertEqual(result.steps[-1].status,'would')

    def test_reject_malformed_config_and_cycle(self):
        graph, root=base_graph()
        for options in (SimulationInputs(codex_state='ROOT'),
                        SimulationInputs(event='garbage'),
                        SimulationInputs(elapsed_seconds=-1)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                simulate(graph,self.proofs,options)
        node=graph.add('wait')
        graph.connect(root.id,node.id)
        with self.assertRaises(ValueError):graph.connect(node.id,root.id)

    def test_live_classes_and_layout_are_unchanged(self):
        base=Path(__file__).resolve().parents[1]
        gui=(base/'auto_ai_scheduler/gui.py').read_text()
        editor=(base/'auto_ai_scheduler/editor.py').read_text()
        sim=(base/'auto_ai_scheduler/workflow_sim.py').read_text()
        self.assertIn('Gtk.Expander(label=',gui)
        self.assertIn('self.run_simulation',gui)
        self.assertIn('self.select_simulation_observation',gui)
        self.assertIn('simulation=lambda:self.simulation_result',gui)
        self.assertIn('simulation_classes',editor)
        self.assertNotIn('type_and_send(',sim)
        self.assertNotIn('inspect_target(',sim)
        self.assertNotIn('SMTP_SSL(',sim)


if __name__=='__main__':unittest.main()
