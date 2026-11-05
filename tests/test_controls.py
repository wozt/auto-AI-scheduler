"""Control actions must be deterministic and safe without a running IDE/GTK."""
from datetime import datetime
from unittest.mock import patch
import unittest

from auto_ai_scheduler.config import validate
from auto_ai_scheduler.control import (
    ActionHistory, RunControls, next_action, recent_target,
)
from auto_ai_scheduler.engine import Scheduler, Session, TargetInfo
from auto_ai_scheduler.gui import (
    COMPACT_CSS, LEFT_SECTIONS, RIGHT_SECTIONS, TRANSPORT_CONTROLS,
)
from auto_ai_scheduler.vision import Observation
from auto_ai_scheduler import x11


class ControlTests(unittest.TestCase):
    def make(self, extra=None):
        engine = Scheduler(validate(extra or {}), lambda _: None)
        return engine, RunControls(engine)

    def test_play_pause_resume_stop_does_not_access_x11(self):
        engine, controls = self.make()
        engine.session.cycles = 3
        with patch.object(x11, 'windows') as scan, patch.object(x11, 'type_and_send') as send:
            self.assertTrue(controls.play())
            self.assertTrue(engine.enabled)
            self.assertEqual(controls.phase, 'running')
            self.assertFalse(controls.play())
            self.assertTrue(controls.pause())
            self.assertFalse(engine.enabled)
            self.assertTrue(controls.play())
            self.assertEqual(engine.session.cycles, 3)
            self.assertTrue(controls.stop())
            self.assertFalse(engine.enabled)
            self.assertEqual(controls.phase, 'stopped')
            self.assertFalse(controls.stop())
            scan.assert_not_called()
            send.assert_not_called()

    def test_history_navigation_never_triggers_send(self):
        engine, controls = self.make()
        h = controls.history
        with patch.object(engine, 'send') as send:
            self.assertFalse(h.can_previous)
            h.add('First')
            h.add('Second')
            self.assertIsNone(h.selected())  # predicted next action
            self.assertEqual(h.previous(), 'Second')
            self.assertEqual(h.previous(), 'First')
            self.assertEqual(h.previous(), 'First')
            self.assertEqual(h.next(), 'Second')
            self.assertIsNone(h.next())
            self.assertFalse(h.can_next)
            send.assert_not_called()

    def test_history_is_bounded(self):
        h = ActionHistory(limit=3)
        for index in range(5):
            h.add(str(index))
        self.assertEqual(h.items, ['2', '3', '4'])
        self.assertEqual(h.previous(), '4')

    def test_green_target_requires_a_recent_verified_window(self):
        engine, _ = self.make({'poll_seconds': 15})
        self.assertIsNone(recent_target(engine, now=1000))
        engine.last_target = TargetInfo('123', 'Devin Desktop', 'READY', 1000)
        self.assertEqual(recent_target(engine, now=1005).window_id, '123')
        self.assertIsNone(recent_target(engine, now=1046))
        self.assertIsNone(recent_target(engine, now=999))

    def test_predicted_action_changes_with_run_state(self):
        engine, controls = self.make({'mode': 'auto', 'scroll_seconds': 0})
        self.assertIn('Play', next_action(engine, controls.phase))
        controls.play()
        self.assertIn('premier envoi', next_action(engine, controls.phase))
        engine.session.waiting_for_response = True
        self.assertIn('Surveiller la réponse', next_action(engine, controls.phase))
        engine.session.quota_seen = True
        self.assertIn('Quota', next_action(engine, controls.phase))
        engine.session.quota_seen = False
        engine.pending_approval = True
        self.assertIn('approbation', next_action(engine, controls.phase))
        controls.pause()
        self.assertIn('En pause', next_action(engine, controls.phase))

    def test_schedule_preview_is_dry(self):
        engine, controls = self.make({
            'schedule_enabled': True, 'scheduled_at': '2026-11-06 03:00'})
        controls.play()
        with patch.object(engine, 'send') as send:
            self.assertIn('2026-11-06', next_action(engine, controls.phase, datetime(2026,11,5,9)))
            self.assertIn('planifié prêt', next_action(engine, controls.phase, datetime(2026,11,6,3)))
            send.assert_not_called()

    def test_old_edited_card_cannot_bootstrap_auto_mode(self):
        engine, controls = self.make({'mode':'auto', 'scroll_seconds':0})
        controls.play()
        obs = Observation(True, True, (200, 400), None, 'FINISHED',
                          'worked for 13m|edited 16 files', '', 'previous result')
        with patch.object(x11, 'user_idle_seconds', return_value=100), \
             patch.object(engine, 'inspect_target', return_value=obs), \
             patch.object(engine, 'send') as send, \
             patch('auto_ai_scheduler.engine.time.monotonic', side_effect=[50,65]):
            engine.tick()
            engine.tick()
            send.assert_not_called()

    def test_gui_has_all_settings_and_requested_controls(self):
        configured = [key for _, keys in LEFT_SECTIONS+RIGHT_SECTIONS for key in keys]
        self.assertEqual(set(configured), set(validate({}))-{'schedule_id'})
        self.assertEqual(len(configured), len(set(configured)))
        self.assertEqual(TRANSPORT_CONTROLS,
                         ('previous', 'play', 'pause', 'stop', 'next'))
        self.assertIn(b'.scheduler-state.found', COMPACT_CSS)
        self.assertIn(b'font-size: 12px', COMPACT_CSS)


if __name__ == '__main__':
    unittest.main()
