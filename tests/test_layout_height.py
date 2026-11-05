"""Contract of the GTK desktop workspace: three resizable upper panels and bottom timeline."""
import ast
from pathlib import Path
import unittest

from auto_ai_scheduler.gui import COMPACT_CSS, LEFT_SECTIONS, RIGHT_SECTIONS, TRANSPORT_CONTROLS
from auto_ai_scheduler.config import validate
from auto_ai_scheduler.display import css

UI_SOURCE=(Path(__file__).resolve().parents[1]/'auto_ai_scheduler'/'gui.py').read_text()


class LayoutTests(unittest.TestCase):
    def test_top_panels_are_resizable_and_timeline_at_bottom(self):
        self.assertIn('vertical=Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)',UI_SOURCE)
        self.assertIn('vertical.set_start_child(upper)',UI_SOURCE)
        self.assertIn('vertical.set_end_child(self.make_timeline())',UI_SOURCE)
        self.assertIn('upper=Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)',UI_SOURCE)
        self.assertIn('center_right=Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)',UI_SOURCE)
        self.assertIn('upper.set_start_child(self.make_library())',UI_SOURCE)
        self.assertIn('center_right.set_start_child(self.make_canvas())',UI_SOURCE)
        self.assertIn('center_right.set_end_child(self.make_inspector())',UI_SOURCE)

    def test_responsive_display_preferences(self):
        self.assertIn("win.set_default_size(self.cfg['window_width'],self.cfg['window_height'])",UI_SOURCE)
        self.assertIn('self.window.set_default_size(',UI_SOURCE)
        self.assertIn('win.set_size_request(920,560)',UI_SOURCE)
        self.assertIn('open_preferences',UI_SOURCE)
        self.assertIn('Adw.StyleManager.get_default().set_color_scheme',UI_SOURCE)
        self.assertIn(b'.scheduler-title',css(14))

    def test_settings_and_controls_are_present(self):
        fields=[k for _,keys in LEFT_SECTIONS+RIGHT_SECTIONS for k in keys]
        self.assertEqual(set(fields),set(validate({}))-{'schedule_id'})
        self.assertEqual(len(fields),len(set(fields)))
        self.assertEqual(TRANSPORT_CONTROLS,('previous','play','pause','stop','next'))
        self.assertIn(b'.scheduler-state.found',COMPACT_CSS)

    def test_python_compiles(self):
        ast.parse(UI_SOURCE)

if __name__=='__main__':unittest.main()
