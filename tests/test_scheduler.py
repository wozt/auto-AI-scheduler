import unittest
from unittest.mock import patch
from auto_ai_scheduler.vision import inspect_rows, is_project_visible
from auto_ai_scheduler.engine import Scheduler
from auto_ai_scheduler.config import validate
from auto_ai_scheduler import x11


class OCRTests(unittest.TestCase):
    def test_real_project_reference(self):
        from pathlib import Path
        from PIL import Image
        from auto_ai_scheduler.vision import inspect
        fixture=Path(__file__).resolve().parent.parent / "references" / "project_explorer.png"
        self.assertTrue(inspect(Image.open(fixture),"metroidvania").project_found)

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

if __name__=='__main__':unittest.main()
