import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from auto_ai_scheduler.schedule import ClaimStore, due, job_id, parse_local
from auto_ai_scheduler.vision import Observation
from unittest.mock import patch
from auto_ai_scheduler.vision import inspect_rows, is_project_visible
from auto_ai_scheduler.engine import Scheduler
from auto_ai_scheduler.config import validate
from auto_ai_scheduler import x11


class OCRTests(unittest.TestCase):
    def test_project_without_private_screenshots(self):
        # Synthetic OCR only; private UI images never belong in the repository.
        rows=[('Explorer',(34,30,100,50)),('metroidvania',(40,55,150,77)),
              ('Ask for follow-up changes',(600,650,770,670))]
        result=inspect_rows(rows,'metroidvania',(1000,800))
        self.assertTrue(result.project_found)
        self.assertTrue(result.codex_found)

    def test_project_must_be_below_explorer(self):
        rows=[('Explorer',(40,55,115,75)),('metroidvania',(52,85,158,105)),
              ('Ask for follow-up changes',(550,650,740,665)),
              ('Worked for 13m 30s',(550,230,740,248)),('Edited 16 files',(550,390,740,411))]
        obs=inspect_rows(rows,'metroidvania',(1000,750))
        self.assertTrue(obs.project_found)
        self.assertEqual(obs.state,'FINISHED')
        self.assertEqual(obs.composer,(645,657))

    def test_chat_reference_is_not_project(self):
        rows=[('Explorer',(45,45,120,65)),('something-else',(50,80,180,95)),
              ('metroidvania',(600,300,720,320)),('Ask Codex',(600,690,710,707))]
        self.assertFalse(is_project_visible(rows,'metroidvania',(1000,800)))

    def test_no_codex_no_action(self):
        rows=[('Explorer',(45,45,120,65)),('metroidvania',(50,80,180,95))]
        self.assertFalse(inspect_rows(rows,'metroidvania',(1000,800)).codex_found)

    def test_quota_has_priority(self):
        rows=[('Explorer',(45,45,120,65)),('metroidvania',(50,80,180,95)),
              ("You're out of Codex messages",(600,700,860,720))]
        self.assertEqual(inspect_rows(rows,'metroidvania',(1000,800)).state,'QUOTA')

    def test_config_rejects_bad_limits(self):
        with self.assertRaises(ValueError):validate({'poll_seconds':0})

    def test_refuse_send_without_ide(self):
        engine=Scheduler(validate({}),lambda _:None)
        with patch.object(x11,'windows',return_value=[]):
            self.assertFalse(engine.send())

    def test_focus_restored_after_error(self):
        state=x11.DesktopState('1',120,240)
        with patch.object(x11,'desktop_state',return_value=state),\
             patch.object(x11,'command') as cmd,\
             patch.object(x11,'restore') as restore,\
             patch('auto_ai_scheduler.x11.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'test'):
                with x11.temporary_focus('2'):
                    raise RuntimeError('test')
            restore.assert_called_once_with(state)

class SchedulingTests(unittest.TestCase):
    def test_schedule_datetime_and_due(self):
        cfg=validate({'schedule_enabled':True,'scheduled_at':'2026-11-06 03:00'})
        self.assertFalse(due(cfg, datetime(2026,11,6,2,59)))
        self.assertTrue(due(cfg, datetime(2026,11,6,3,0)))
        for invalid in ('2026-11-06T03:00', '2026-02-30 03:00', 'yesterday'):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                parse_local(invalid)

    def test_claim_is_persistent_and_private(self):
        with TemporaryDirectory() as folder:
            file=Path(folder)/'claims.json'
            cfg=validate({'schedule_enabled':True,'scheduled_at':'2026-11-06 03:00',
                          'prompt':'private instructions', 'schedule_id':'job-1'})
            key=job_id(cfg)
            store=ClaimStore(file)
            self.assertTrue(store.claim(key))
            self.assertFalse(ClaimStore(file).claim(key))
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('private instructions',file.read_text())

    def test_monitor_never_touches_active_desktop(self):
        engine=Scheduler(validate({'schedule_enabled':True,'scheduled_at':'2020-01-01 00:00'}), lambda _:None)
        engine.enabled=True
        with patch.object(x11,'user_idle_seconds',return_value=0),patch.object(x11,'windows') as win:
            engine.tick()
            win.assert_not_called()

    def test_scheduled_send_is_one_shot_even_if_polled_twice(self):
        cfg=validate({'schedule_enabled':True,'scheduled_at':'2020-01-01 00:00',
                      'schedule_id':'once','scroll_seconds':0})
        observation=Observation(True,True,(500,700),None,'READY','','','')
        def visit(action=None):
            if action:
                action('100',observation,(0,0))
            return observation
        with TemporaryDirectory() as folder:
            engine=Scheduler(cfg,lambda _:None)
            engine.enabled=True
            engine.claims=ClaimStore(Path(folder)/'claims.json')
            with patch.object(x11,'user_idle_seconds',return_value=100), \
                 patch.object(engine,'inspect_target',side_effect=visit), \
                 patch.object(x11,'type_and_send') as send, \
                 patch('auto_ai_scheduler.engine.settings.save'):
                engine.tick()
                engine.tick()
                send.assert_called_once_with('100',(500,700),'continue')
                self.assertFalse(cfg['schedule_enabled'])
                self.assertTrue(engine.claims.contains(job_id(cfg)))

    def test_scheduled_job_waits_for_devin(self):
        cfg=validate({'schedule_enabled':True,'scheduled_at':'2020-01-01 00:00',
                      'schedule_id':'wait','scroll_seconds':0})
        engine=Scheduler(cfg,lambda _:None);engine.enabled=True
        with TemporaryDirectory() as folder:
            engine.claims=ClaimStore(Path(folder)/'claims.json')
            with patch.object(x11,'user_idle_seconds',return_value=100), \
                 patch.object(x11,'windows',return_value=[]), \
                 patch('auto_ai_scheduler.engine.settings.save'):
                engine.tick()
                self.assertTrue(cfg['schedule_enabled'])
                self.assertFalse(engine.claims.contains(job_id(cfg)))

if __name__=='__main__':unittest.main()
