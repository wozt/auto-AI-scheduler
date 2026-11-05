"""Headless structural checks for the resizable single-page GTK layout."""
import ast
from pathlib import Path
import unittest

from auto_ai_scheduler.gui import COMPACT_CSS, LEFT_SECTIONS, RIGHT_SECTIONS
from auto_ai_scheduler.config import validate

UI_SOURCE = (Path(__file__).resolve().parent.parent / "auto_ai_scheduler" / "gui.py").read_text()


class LayoutHeightTests(unittest.TestCase):
    def test_viewport_owns_two_column_grid(self):
        self.assertIn("viewport = Gtk.ScrolledWindow()", UI_SOURCE)
        self.assertIn("viewport.set_child(columns)", UI_SOURCE)
        self.assertIn("root.append(viewport)", UI_SOURCE)
        self.assertNotIn("root.append(columns)", UI_SOURCE)
        self.assertIn("columns.attach(left, 0, 0, 1, 1)", UI_SOURCE)
        self.assertIn("columns.attach(right, 1, 0, 1, 1)", UI_SOURCE)

    def test_min_height_can_shrink(self):
        self.assertIn("viewport.set_propagate_natural_height(False)", UI_SOURCE)
        self.assertIn("viewport.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)", UI_SOURCE)
        self.assertIn("viewport.set_vexpand(True)", UI_SOURCE)
        self.assertIn("viewport.set_min_content_height(280)", UI_SOURCE)
        self.assertIn("win.set_size_request(910, 480)", UI_SOURCE)
        self.assertIn("win.set_default_size(1160, 700)", UI_SOURCE)

    def test_all_existing_controls_are_present(self):
        fields = [key for _, keys in LEFT_SECTIONS + RIGHT_SECTIONS for key in keys]
        self.assertEqual(set(fields), set(validate({})) - {"schedule_id"})
        self.assertEqual(len(fields), len(set(fields)))
        for button in ("previous", "play", "pause", "stop", "next", "save", "detect", "send", "approve"):
            self.assertIn(f"self._button(", UI_SOURCE)
            self.assertIn(f"'{button}'", UI_SOURCE)

    def test_compact_styles_and_valid_python(self):
        ast.parse(UI_SOURCE)
        self.assertIn(b".scheduler-section", COMPACT_CSS)
        self.assertIn(b".scheduler-settings-viewport", COMPACT_CSS)
        self.assertIn(b"font-size: 12px", COMPACT_CSS)

if __name__ == '__main__':
    unittest.main()
