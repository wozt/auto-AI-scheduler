"""Offline temporal state timeline, redaction and two-recording comparison."""
from datetime import datetime, timedelta
import copy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from auto_ai_scheduler import x11
from auto_ai_scheduler.workflow_inspect import (
    frame_details, align_reference, compare_frames, view_window,
    closest_frame, save_comparison_reference, REFERENCE_PATH,
)
from auto_ai_scheduler.workflow_record import RecordingStore, replay
from auto_ai_scheduler.workflow_checkpoint import graph_fingerprint
from auto_ai_scheduler.workflow import Workflow
from auto_ai_scheduler.proofs import ProofStore


class FrameInspectorTests(unittest.TestCase):
    def setUp(self):
        self.d=TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.root=Path(self.d.name)
        self.workflow=Workflow()
        event=self.workflow.add('event');event.params['event']='ready'
        self.ocr=self.workflow.add('state');self.ocr.params['state']='READY'
        self.send=self.workflow.add('send');self.send.params['prompt']='MY SECRET PROMPT'
        self.workflow.connect(event.id,self.ocr.id)
        self.workflow.connect(self.ocr.id,self.send.id)
        self.hash=graph_fingerprint(self.workflow)

    def frame(self,time,state='READY',value=True):
        return {'seconds':float(time),
                'local_at':(datetime(2026,11,6)+timedelta(seconds=time)).isoformat(timespec='seconds'),
                'state':state,'composer':state!='QUOTA',
                'marker':hashlib.sha256(str(time).encode()).hexdigest(),
                'quota_reset':None,'matches':{self.ocr.id:value}}

    def doc(self,*frames,hash_override=None):
        return {'schema':1,'workflow':hash_override or self.hash,'frames':list(frames)}

    def test_details_are_redacted_and_show_unknown_correctly(self):
        doc=self.doc(self.frame(0,'READY',None),self.frame(3,'FINISHED',False))
        text=' '.join(frame_details(doc,0,{self.ocr.id:'État Codex'}))
        self.assertIn('indéterminé',text)
        self.assertIn('Horodatage local',text)
        self.assertNotIn('MY SECRET PROMPT',text)
        self.assertNotIn(doc['frames'][0]['marker'],text)
        self.assertIn('NON',' '.join(frame_details(doc,1)))

    def test_compare_uses_relative_time_not_index(self):
        cur=self.doc(self.frame(0),self.frame(4),self.frame(11,'FINISHED'))
        ref=self.doc(self.frame(100),self.frame(110,'FINISHED'))
        self.assertEqual(align_reference(cur,ref,2),1)
        self.assertFalse(compare_frames(cur,ref,2).different)
        self.assertEqual(align_reference(cur,ref,0),0)

    def test_compare_flags_known_ocr_state_differences(self):
        left=self.doc(self.frame(0,'READY',True))
        right=self.doc(self.frame(0,'QUOTA',False))
        result=compare_frames(left,right,0)
        self.assertTrue(result.different)
        self.assertIn('État',str(result.description))
        self.assertIn('OUI / NON',str(result.description))

    def test_compare_unknown_does_not_mean_no(self):
        left=self.doc(self.frame(0,value=None))
        right=self.doc(self.frame(0,value=False))
        result=compare_frames(left,right,0)
        self.assertIn('indéterminé / NON',' '.join(result.description))
        self.assertNotIn('OUI / NON',' '.join(result.description))

    def test_mismatched_workflow_rejected(self):
        a=self.doc(self.frame(0))
        b=self.doc(self.frame(0),hash_override='a'*64)
        with self.assertRaisesRegex(ValueError,'différents'):
            compare_frames(a,b,0)

    def test_zoom_viewport_and_nearest_scrubbing(self):
        doc=self.doc(*(self.frame(s) for s in [0,2,6,10,14,20]))
        a,b=view_window(doc,3,1)
        self.assertEqual((a,b),(0.,20.))
        start,end=view_window(doc,3,4)
        self.assertAlmostEqual(end-start,5.0)
        self.assertLessEqual(start,10)
        self.assertGreaterEqual(end,10)
        self.assertEqual(closest_frame(doc,3,1,0.0),0)
        self.assertEqual(closest_frame(doc,3,1,1.0),5)

    def test_invalid_index_zoom_and_input_rejected(self):
        doc=self.doc(self.frame(0))
        for index in [-1,1,1.2,True]:
            with self.subTest(index=index),self.assertRaises(ValueError):
                frame_details(doc,index)
        for zoom in [0,9,1.5]:
            with self.subTest(zoom=zoom),self.assertRaises(ValueError):
                view_window(doc,0,zoom)
        with self.assertRaises(ValueError):closest_frame(doc,0,1,True)

    def test_pin_is_private_and_does_not_modify_last(self):
        doc=self.doc(self.frame(0))
        source=RecordingStore(self.root/'last.json')
        reference=RecordingStore(self.root/'reference.json')
        source.save(doc)
        self.assertEqual(save_comparison_reference(source,reference),1)
        self.assertEqual(source.load(),reference.load())
        self.assertEqual(reference.path.stat().st_mode&0o777,0o600)
        self.assertEqual(reference.path.parent.stat().st_mode&0o777,0o700)
        self.assertNotIn('MY SECRET PROMPT',reference.path.read_text())
        self.assertNotIn('data:image',reference.path.read_text())
        self.assertTrue(source.path.exists())

    def test_comparison_is_offline_and_non_mutating(self):
        left=self.doc(self.frame(0),self.frame(3,value=False))
        right=self.doc(self.frame(0),self.frame(3,value=True))
        snapshot=copy.deepcopy((left,right))
        with patch.object(x11,'command',side_effect=AssertionError('X11 invoked')),\
             patch('auto_ai_scheduler.engine.Scheduler.send',side_effect=AssertionError('send invoked')),\
             patch('smtplib.SMTP_SSL',side_effect=AssertionError('SMTP invoked')):
            compare_frames(left,right,1)
            frame_details(left,1)
            view_window(left,1,5)
        self.assertEqual((left,right),snapshot)

    def test_prefix_replay_does_not_send(self):
        doc=self.doc(self.frame(0),self.frame(3),self.frame(6))
        proofs=ProofStore(self.root/'proofs')
        with patch.object(x11,'command',side_effect=AssertionError('X11 invoked')),\
             patch('auto_ai_scheduler.engine.Scheduler.send',side_effect=AssertionError('send invoked')):
            result=replay(self.workflow,{**doc,'frames':doc['frames'][:2]},proofs)
            self.assertEqual(result.outcome,'waiting')

    def test_gui_integrates_lower_expander_and_zoom(self):
        gui=(Path(__file__).resolve().parents[1]/'auto_ai_scheduler/gui.py').read_text()
        ui=(Path(__file__).resolve().parents[1]/'auto_ai_scheduler/workflow_inspect_ui.py').read_text()
        for item in ('recording_inspector_update','seek_recording_frame','pin_recording',
                     'compare_recording','recorder_panel.append(frame_inspector)'):
            self.assertIn(item,gui)
        for item in ('Gtk.DrawingArea()', 'Gtk.Scale.new_with_range',
                     'set_draw_func', 'set_tooltip_text', 'TRAMES OCR'):
            self.assertIn(item,ui)
        self.assertIn('Enregistrer',gui)
        self.assertIn('Référence',ui)
        self.assertTrue(str(REFERENCE_PATH).endswith('/recordings/reference.json'))


if __name__=='__main__':unittest.main()
