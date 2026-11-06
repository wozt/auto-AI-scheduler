"""Temporal OCR recording/replay: no raw screens, no desktop actions."""
import copy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, Mock

from PIL import Image

from auto_ai_scheduler import x11
from auto_ai_scheduler.proofs import ProofStore
from auto_ai_scheduler.workflow import Workflow
from auto_ai_scheduler.workflow_checkpoint import graph_fingerprint
from auto_ai_scheduler.workflow_record import (
    RecordingStore, SequenceRecorder, MAX_FRAMES, replay, validate_recording,
)
from auto_ai_scheduler.vision import Observation


class TemporalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)
        self.proofs=ProofStore(self.path/'proofs')
        original=self.path/'source.png'
        Image.new('RGB',(180,90),'white').save(original)
        self.proof_id=self.proofs.import_image(original)

    def graph(self, trigger='event'):
        graph=Workflow(name='OCR recorded')
        a=graph.add(trigger)
        if trigger=='event':a.params['event']='ready'
        b=graph.add('ocr'); b.params.update(proof_id=self.proof_id,expected='Edited 7 files')
        c=graph.add('send');c.params['prompt']='sensitive instructions should not leak'
        graph.connect(a.id,b.id);graph.connect(b.id,c.id)
        return graph,a,b,c

    def frame(self, s, state='READY', matches=None, marker='', composer=True, quota=None):
        return {'seconds':float(s),
                'local_at':(datetime(2026,11,6,0,0,0)+timedelta(seconds=s)).isoformat(timespec='seconds'),
                'state':state,'composer':composer,
                'marker':hashlib.sha256(marker.encode()).hexdigest() if marker else '',
                'matches':matches or {},'quota_reset':quota}

    def document(self,graph,frames):
        return {'schema':1,'workflow':graph_fingerprint(graph),'frames':frames}

    def test_recording_is_private_and_contains_only_boolean_ocr(self):
        graph,a,b,c=self.graph()
        store=RecordingStore(self.path/'recordings'/'last.json')
        recorder=SequenceRecorder(store,clock=lambda:100,local_clock=lambda:datetime(2026,11,6))
        recorder.start(graph)
        observation=Observation(True,True,(100,100),None,'READY','Worked for 15m|Edited 7 files','','private prompt')
        engine=Mock(config={'idle_seconds':3})
        def inspect(action=None):
            if action:action('100',observation,(0,0))
            return observation
        engine.inspect_target.side_effect=inspect
        with patch.object(x11,'user_idle_seconds',return_value=100),\
             patch.object(x11,'screenshot',return_value=(Image.new('RGB',(300,200)),(0,0))),\
             patch('auto_ai_scheduler.workflow_record.ocr_image',return_value='Edited 7 files'):
            self.assertTrue(recorder.capture(engine))
            self.assertFalse(recorder.capture(engine)) # duplicate frame forbidden
        doc=recorder.stop()
        self.assertEqual(doc['frames'][0]['matches'],{b.id:True})
        self.assertEqual(len(doc['frames'][0]['marker']),64)
        self.assertFalse(recorder.active)
        self.assertEqual(store.load(),doc)
        self.assertEqual(store.path.stat().st_mode&0o777,0o600)
        self.assertEqual(store.path.parent.stat().st_mode&0o777,0o700)
        raw=store.path.read_text()
        for secret in ('sensitive instructions','Worked for 15m','Edited 7 files','private prompt','data:image'):
            self.assertNotIn(secret,raw)

    def test_skip_when_desktop_is_active_without_any_focus_change(self):
        graph,*_=self.graph()
        recorder=SequenceRecorder(RecordingStore(self.path/'result.json'),clock=lambda:100)
        recorder.start(graph)
        engine=Mock(config={'idle_seconds':8})
        with patch.object(x11,'user_idle_seconds',return_value=0):
            self.assertFalse(recorder.capture(engine))
        engine.inspect_target.assert_not_called()
        self.assertEqual(recorder.skipped,1)

    def test_two_consecutive_observations_required_for_condition(self):
        graph,a,b,c=self.graph()
        frames=[self.frame(0),self.frame(3),self.frame(6,'READY',{b.id:True}),
                self.frame(9,'READY',{b.id:True}),self.frame(12)]
        data=self.document(graph,frames)
        with patch.object(x11,'command',side_effect=AssertionError('X11 used')),\
             patch('auto_ai_scheduler.engine.Scheduler.send',side_effect=AssertionError('send used')):
            result=replay(graph,data,self.proofs)
        self.assertEqual(result.outcome,'complete')
        self.assertEqual([s.status for s in result.steps],['ok','ok','would'])
        self.assertIn('AUCUN ENVOI',result.steps[-1].reason)
        self.assertEqual(graph.find(c.id).params['prompt'],'sensitive instructions should not leak')
        # Missing the second positive frame cannot validate a prompt.
        result=replay(graph,self.document(graph,frames[:4]),self.proofs)
        self.assertEqual(result.outcome,'waiting')
        self.assertNotIn('would',[s.status for s in result.steps])

    def test_unknown_ocr_result_never_converts_to_negative_branch(self):
        graph=Workflow()
        a=graph.add('event');a.params['event']='ready'
        b=graph.add('branch');b.params.update(source='ocr',proof_id=self.proof_id,expected='Edited 7 files')
        yes=graph.add('state');yes.params['state']='READY'
        no=graph.add('notify')
        graph.connect(a.id,b.id);graph.connect(b.id,yes.id,'yes');graph.connect(b.id,no.id,'no')
        unknown=[self.frame(0),self.frame(3),self.frame(6,'READY',{b.id:None}),self.frame(9,'READY',{b.id:None})]
        result=replay(graph,self.document(graph,unknown),self.proofs)
        self.assertEqual(result.outcome,'waiting')
        self.assertEqual(result.next_node_id,b.id)
        negative=[self.frame(0),self.frame(3),self.frame(6,'READY',{b.id:False}),
                  self.frame(9,'READY',{b.id:False}),self.frame(12)]
        result=replay(graph,self.document(graph,negative),self.proofs)
        self.assertEqual(result.outcome,'complete')
        self.assertEqual(result.steps[1].route,'no')
        self.assertEqual(result.steps[-1].kind,'notify')

    def test_cycle_finished_needs_prior_running_and_distinct_marker(self):
        graph=Workflow()
        a=graph.add('event');a.params['event']='cycle_finished'
        good=[self.frame(0,'RUNNING',marker='old'),self.frame(3,'FINISHED',marker='new'),
              self.frame(6,'FINISHED',marker='new')]
        self.assertEqual(replay(graph,self.document(graph,good),self.proofs).outcome,'complete')
        no_running=[self.frame(0,'FINISHED',marker='new'),self.frame(3,'FINISHED',marker='new')]
        self.assertEqual(replay(graph,self.document(graph,no_running),self.proofs).outcome,'waiting')

    def test_delay_uses_elapsed_recorded_time_and_does_not_invent_frames(self):
        graph=Workflow();d=graph.add('delay');d.params['seconds']='10'
        self.assertEqual(replay(graph,self.document(graph,[self.frame(0),self.frame(9)]),self.proofs).outcome,'waiting')
        self.assertEqual(replay(graph,self.document(graph,[self.frame(0),self.frame(11)]),self.proofs).outcome,'complete')

    def test_manual_quota_replay_stays_waiting_without_approval(self):
        graph=Workflow();a=graph.add('event');a.params['event']='quota';b=graph.add('quota_wait')
        graph.connect(a.id,b.id)
        deadline='2026-11-06T00:00:07'
        frames=[self.frame(0,'QUOTA',quota=deadline),self.frame(3,'QUOTA',quota=deadline),
                self.frame(6,'QUOTA',quota=deadline),self.frame(9,'QUOTA',quota=deadline),
                self.frame(12),self.frame(15)]
        self.assertEqual(replay(graph,self.document(graph,frames),self.proofs).outcome,'waiting')
        self.assertEqual(replay(graph,self.document(graph,frames),self.proofs,quota_approved=True).outcome,'complete')

    def test_reject_tampering_bad_timestamps_and_changed_graph(self):
        graph,a,b,c=self.graph()
        data=self.document(graph,[self.frame(0),self.frame(3)])
        for transform in [lambda d:d['frames'][1].update(seconds=0),
                          lambda d:d['frames'][0]['matches'].update({b.id:1}),
                          lambda d:d['frames'][0].update(state='HACKED'),
                          lambda d:d.update(workflow='hi')]:
            changed=copy.deepcopy(data);transform(changed)
            with self.assertRaises(ValueError):validate_recording(changed)
        other=Workflow.from_dict(graph.to_dict());other.find(c.id).params['prompt']='different'
        with self.assertRaisesRegex(ValueError,'changé'):replay(other,data,self.proofs)

    def test_reject_symlink_and_world_readable_recording(self):
        graph,*_=self.graph();data=self.document(graph,[self.frame(0)])
        store=RecordingStore(self.path/'last.json');store.save(data)
        store.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'permissions'):store.load()
        link=RecordingStore(self.path/'linked.json');link.path.symlink_to(store.path)
        with self.assertRaisesRegex(ValueError,'symbolique'):link.load()
        with self.assertRaisesRegex(ValueError,'symbolique'):link.save(data)

    def test_max_frames_and_no_automatic_screen_save(self):
        graph,*_=self.graph()
        recorder=SequenceRecorder(RecordingStore(self.path/'recording.json'))
        recorder.start(graph)
        self.assertEqual(MAX_FRAMES,120)
        self.assertEqual(recorder.frames,[])
        self.assertFalse(recorder.store.path.exists())
        with self.assertRaises(ValueError):recorder.start(graph)
        with self.assertRaises(ValueError):SequenceRecorder().start(graph,interval=0)

    def test_gui_records_and_replays_inside_bottom_timeline(self):
        source=(Path(__file__).resolve().parents[1]/'auto_ai_scheduler'/'gui.py').read_text()
        self.assertIn('recorder_expander=Gtk.Expander',source)
        self.assertIn("'record_start'",source)
        self.assertIn("'record_stop'",source)
        self.assertIn("'record_replay'",source)
        self.assertIn('self.recorder.capture(self.engine)',source)
        self.assertIn('self.simulation_result=replay(self.graph,recording,self.proof_store',source)
        self.assertIn('if self.recorder.active:',source)


if __name__=='__main__':unittest.main()
