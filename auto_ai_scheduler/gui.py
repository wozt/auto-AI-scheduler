"""Single-page, compact GTK4/libadwaita dashboard for Codex in Devin Desktop.

All X11 automation remains in Scheduler. Navigation buttons only browse the
local history/next-action preview; they never send or undo Codex commands.
"""
from datetime import datetime
from uuid import uuid4

from . import config
from .control import RunControls, next_action, recent_target
from .engine import Scheduler


# Keep a plain-Python manifest for headless UI coverage, matching patch 0003.
LEFT_SECTIONS = (
    ('Cible X11', ('project', 'app_pattern', 'prompt', 'mode')),
    ('Planification', ('schedule_enabled', 'scheduled_at')),
)
RIGHT_SECTIONS = (
    ('Surveillance', ('poll_seconds', 'scroll_seconds', 'scroll_steps',
                      'max_cycles', 'cooldown_seconds', 'idle_seconds')),
    ('Notifications SMTP SSL', ('email_enabled', 'email_to', 'email_from',
                                'smtp_host', 'smtp_port', 'email_on_prompt',
                                'email_on_error')),
)
TRANSPORT_CONTROLS = ('previous', 'play', 'pause', 'stop', 'next')

# Styling is applied to this app's main content only, not the system theme.
COMPACT_CSS = b"""
.scheduler-root { font-size: 12px; }
.scheduler-root label { font-size: 12px; }
.scheduler-section { border-radius: 9px; }
.scheduler-section .scheduler-title { font-weight: 700; font-size: 13px; }
.scheduler-section .scheduler-hint { font-size: 11px; opacity: 0.8; }
.scheduler-row { min-height: 26px; }
.scheduler-row entry { min-height: 25px; padding: 1px 7px; }
.scheduler-row spinbutton { min-height: 25px; }
.scheduler-row dropdown button { min-height: 25px; padding: 1px 6px; }
.scheduler-row switch { margin: 0px; }
.scheduler-controls button, .scheduler-tools button { min-height: 29px; padding: 3px 11px; }
.scheduler-state { padding: 6px 11px; border-radius: 7px;
  background: alpha(@window_fg_color, .045); border: 1px solid alpha(@window_fg_color, .12); }
.scheduler-state.found { color: #57c88d;
  background: alpha(#208a50, .14); border-color: alpha(#3dc57d, .42); }
.scheduler-state .scheduler-next { font-size: 12px; }
.scheduler-log { font-family: monospace; font-size: 11px; }
.scheduler-event { font-size: 11px; opacity: .84; }
"""


def launch():
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Gtk, Adw, GLib, Pango

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id='click.woztrium.AutoAIScheduler')
            self.cfg = config.load()
            self.engine = Scheduler(self.cfg, self.log)
            self.controls = RunControls(self.engine)
            self.fields = {}
            self.buttons = {}
            self.window = None
            self.last_event = None
            self.state_box = None
            self.target_label = None
            self.next_label = None
            self.history_label = None
            self.logs = None
            self.status = None
            self.mode_preview = None

        def log(self, message):
            stamp = datetime.now().strftime('%H:%M:%S')
            entry = f'{stamp}  {message}'
            self.controls.history.add(entry)
            if self.status is not None:
                self.status.set_text(message)
            if self.logs is not None:
                buffer = self.logs.get_buffer()
                buffer.insert(buffer.get_end_iter(), entry + '\n')
            self._refresh()

        def _section(self, title, hint=None):
            frame = Gtk.Frame()
            frame.add_css_class('scheduler-section')
            frame.set_hexpand(True)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            for side in ('start', 'end', 'top', 'bottom'):
                getattr(content, f'set_margin_{side}')(7)
            heading = Gtk.Label(label=title, xalign=0)
            heading.add_css_class('scheduler-title')
            content.append(heading)
            if hint:
                note = Gtk.Label(label=hint, xalign=0, wrap=True)
                note.add_css_class('scheduler-hint')
                content.append(note)
            frame.set_child(content)
            return frame, content

        def _field(self, panel, key, title, kind='text', bounds=None):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.add_css_class('scheduler-row')
            caption = Gtk.Label(label=title, xalign=0)
            caption.set_hexpand(True)
            caption.set_tooltip_text(title)
            row.append(caption)
            if kind == 'switch':
                widget = Gtk.Switch()
                widget.set_active(bool(self.cfg[key]))
                widget.set_valign(Gtk.Align.CENTER)
            elif kind == 'spin':
                low, high = bounds
                widget = Gtk.SpinButton.new_with_range(low, high, 1)
                widget.set_value(int(self.cfg[key]))
                widget.set_numeric(True)
                widget.set_width_chars(5)
            elif kind == 'mode':
                widget = Gtk.DropDown.new_from_strings([
                    'Manuel', 'Automatique', 'Confirmation locale'])
                widget.set_selected(['manual', 'auto', 'approval'].index(self.cfg['mode']))
            else:
                widget = Gtk.Entry()
                widget.set_text(str(self.cfg[key]))
                widget.set_width_chars(15)
                widget.set_hexpand(True)
            if kind != 'text':
                widget.set_halign(Gtk.Align.END)
            row.append(widget)
            panel.append(row)
            self.fields[key] = widget
            return widget

        def _button(self, parent, key, title, callback, tooltip):
            btn = Gtk.Button(label=title)
            btn.set_tooltip_text(tooltip)
            btn.set_hexpand(True)
            btn.connect('clicked', callback)
            parent.append(btn)
            self.buttons[key] = btn
            return btn

        def do_activate(self):
            if self.window is not None:
                self.window.present()
                return
            win = Adw.ApplicationWindow(application=self)
            self.window = win
            win.set_title('Auto AI Scheduler')
            win.set_default_size(1160, 700)
            win.set_size_request(1010, 635)

            css = Gtk.CssProvider()
            css.load_from_data(COMPACT_CSS)
            Gtk.StyleContext.add_provider_for_display(
                win.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(Adw.HeaderBar())
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            root.add_css_class('scheduler-root')
            for side in ('start', 'end', 'top', 'bottom'):
                getattr(root, f'set_margin_{side}')(9)
            toolbar.set_content(root)
            win.set_content(toolbar)

            state = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            state.add_css_class('scheduler-state')
            self.state_box = state
            self.target_label = Gtk.Label(label='Fenêtre : non vérifiée', xalign=0)
            self.target_label.set_hexpand(True)
            self.target_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            state.append(self.target_label)
            self.next_label = Gtk.Label(label='Prochaine : Play', xalign=1)
            self.next_label.set_hexpand(True)
            self.next_label.set_ellipsize(Pango.EllipsizeMode.END)
            self.next_label.add_css_class('scheduler-next')
            state.append(self.next_label)
            root.append(state)

            transports = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            transports.add_css_class('scheduler-controls')
            root.append(transports)
            self._button(transports, 'previous', '⏮ Précédente', self.previous,
                         'Voir une action passée dans le journal. Ne la réexécute pas.')
            self._button(transports, 'play', '▶ Play', self.play,
                         'Démarrer ou reprendre le superviseur')
            self._button(transports, 'pause', '⏸ Pause', self.pause,
                         'Suspendre les actions automatiques en conservant le cycle en cours')
            self._button(transports, 'stop', '■ Stop', self.stop,
                         'Arrêter le superviseur, sans modifier les tâches sauvegardées')
            self._button(transports, 'next', '⏭ Suivante (aperçu)', self.next,
                         'Voir la prochaine action anticipée. Ne lance aucun prompt.')

            columns = Gtk.Grid(column_spacing=10)
            columns.set_column_homogeneous(True)
            columns.set_hexpand(True)
            columns.set_vexpand(True)
            root.append(columns)
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            left.set_hexpand(True)
            right.set_hexpand(True)
            columns.attach(left, 0, 0, 1, 1)
            columns.attach(right, 1, 0, 1, 1)

            frame, panel = self._section('Cible X11', 'Explorer et Codex doivent être visibles dans la même fenêtre')
            left.append(frame)
            self._field(panel, 'project', 'Projet cible')
            self._field(panel, 'app_pattern', 'Fenêtre IDE')
            self._field(panel, 'prompt', 'Prompt')
            self._field(panel, 'mode', 'Fin de cycle', kind='mode')

            frame, panel = self._section('Planification')
            left.append(frame)
            enabled = self._field(panel, 'schedule_enabled', 'Envoi unique activé', 'switch')
            date_entry = self._field(panel, 'scheduled_at', 'Date/heure locale')
            date_entry.set_placeholder_text('AAAA-MM-JJ HH:MM')
            date_entry.set_sensitive(enabled.get_active())
            enabled.connect('notify::active',
                            lambda *_: date_entry.set_sensitive(enabled.get_active()))

            frame, panel = self._section('Historique · lecture seule')
            frame.set_vexpand(True)
            left.append(frame)
            self.history_label = Gtk.Label(label='Prochaine action anticipée', xalign=0)
            self.history_label.set_ellipsize(Pango.EllipsizeMode.END)
            self.history_label.add_css_class('scheduler-event')
            panel.append(self.history_label)
            pane = Gtk.ScrolledWindow(vexpand=True)
            pane.set_min_content_height(92)
            self.logs = Gtk.TextView(editable=False, cursor_visible=False,
                                     monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
            self.logs.add_css_class('scheduler-log')
            pane.set_child(self.logs)
            panel.append(pane)

            frame, panel = self._section('Surveillance')
            right.append(frame)
            for key, title, low, high in [
                ('poll_seconds', 'Intervalle OCR (s)', 5, 3600),
                ('scroll_seconds', 'Défilement (s, 0=off)', 0, 3600),
                ('scroll_steps', 'Crans de défilement', 1, 80),
                ('max_cycles', 'Cycles maximum', 1, 100),
                ('cooldown_seconds', 'Pause entre prompts (s)', 15, 3600),
                ('idle_seconds', 'Inactivité avant action (s)', 0, 3600),
            ]:
                self._field(panel, key, title, 'spin', (low, high))

            frame, panel = self._section('Notifications SMTP SSL',
                                         'Secrets : AUTO_AI_SMTP_USER / AUTO_AI_SMTP_PASSWORD')
            right.append(frame)
            mail = self._field(panel, 'email_enabled', 'Activer les emails', 'switch')
            self._field(panel, 'email_to', 'Destinataire')
            self._field(panel, 'email_from', 'Expéditeur')
            self._field(panel, 'smtp_host', 'Serveur SMTP')
            self._field(panel, 'smtp_port', 'Port SSL', 'spin', (1, 65535))
            self._field(panel, 'email_on_prompt', 'Email après envoi', 'switch')
            self._field(panel, 'email_on_error', 'Email en cas d’erreur', 'switch')
            mail_keys = ('email_to', 'email_from', 'smtp_host', 'smtp_port',
                         'email_on_prompt', 'email_on_error')
            for key in mail_keys:
                self.fields[key].set_sensitive(mail.get_active())
            mail.connect('notify::active', lambda *_: [
                self.fields[key].set_sensitive(mail.get_active()) for key in mail_keys])

            tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            tools.add_css_class('scheduler-tools')
            root.append(tools)
            self._button(tools, 'save', 'Enregistrer', self.save,
                         'Enregistre tous les réglages sans démarrer la surveillance')
            self._button(tools, 'detect', 'Tester la détection', self.detect,
                         'Recherche une fenêtre de projet et restaure le focus/la souris')
            self._button(tools, 'send', 'Envoyer maintenant', self.send,
                         'Envoie explicitement le prompt dans la fenêtre vérifiée')
            self._button(tools, 'approve', 'Approuver', self.approve,
                         'Autoriser le prochain prompt après la demande de confirmation')
            self.status = Gtk.Label(label='Inactif · appuyer sur Play', xalign=0)
            self.status.set_ellipsize(Pango.EllipsizeMode.END)
            self.status.add_css_class('scheduler-event')
            root.append(self.status)
            self._refresh()
            self.timer = GLib.timeout_add_seconds(3, self._poll)
            win.present()

        def _refresh(self):
            if self.state_box is None:
                return
            target = recent_target(self.engine)
            if target is not None:
                # The green state is tied to a recent verified OCR window, never
                # to title matching alone or a stale identification.
                self.state_box.add_css_class('found')
                self.target_label.set_text(
                    f'● Fenêtre trouvée : {target.title} · {self.cfg["project"]} · Codex {target.ui_state}')
            else:
                self.state_box.remove_css_class('found')
                self.target_label.set_text('○ Fenêtre : non vérifiée ou détection expirée')
            upcoming = next_action(self.engine, self.controls.phase)
            self.next_label.set_text('Prochaine : ' + upcoming)
            self.next_label.set_tooltip_text(upcoming)
            selection = self.controls.history.selected()
            self.history_label.set_text('Historique : ' + selection if selection else
                                        'Action anticipée : ' + upcoming)
            for key, active in {
                'play': self.controls.phase != 'running',
                'pause': self.controls.phase == 'running',
                'stop': self.controls.phase != 'stopped',
                'previous': self.controls.history.can_previous,
                'next': self.controls.history.can_next,
                'approve': self.engine.pending_approval,
            }.items():
                if key in self.buttons:
                    self.buttons[key].set_sensitive(active)

        def save(self, *_):
            try:
                updated = dict(self.cfg)
                switches = ('email_enabled', 'schedule_enabled',
                            'email_on_prompt', 'email_on_error')
                numeric = ('poll_seconds', 'scroll_seconds', 'scroll_steps',
                           'max_cycles', 'cooldown_seconds', 'idle_seconds',
                           'smtp_port')
                for key, widget in self.fields.items():
                    if key in switches:
                        updated[key] = widget.get_active()
                    elif key == 'mode':
                        updated[key] = ['manual', 'auto', 'approval'][widget.get_selected()]
                    elif key in numeric:
                        updated[key] = widget.get_value_as_int()
                    else:
                        updated[key] = widget.get_text()
                if updated['schedule_enabled'] and (
                    not self.cfg['schedule_enabled'] or
                    any(updated[key] != self.cfg[key] for key in
                        ('scheduled_at', 'project', 'prompt'))
                ):
                    updated['schedule_id'] = uuid4().hex
                # Validation occurs before modifying either configuration object.
                config.save(updated)
                self.cfg = updated
                self.engine.config = updated
                self.log('Configuration enregistrée')
                return True
            except Exception as exc:
                self.log(f'Configuration invalide : {exc}')
                return False

        def detect(self, *_):
            if not self.save():
                return
            obs = self.engine.inspect_target()
            if obs is None:
                self.log('Aucune fenêtre Devin+Codex avec le projet cible')
            else:
                self.log(f'Projet trouvé · Codex : {obs.state} · champ : {obs.composer}')

        def send(self, *_):
            if self.save():
                if not self.engine.send('manual'):
                    self.log('Envoi manuel refusé : vérifiez l’état Codex et les limites')

        def play(self, *_):
            if self.save() and self.controls.play():
                self.log('Play · surveillance démarrée / reprise')

        def pause(self, *_):
            if self.controls.pause():
                self.log('Pause · aucune action automatique')

        def stop(self, *_):
            if self.controls.stop():
                self.log('Stop · surveillance désactivée ; programmation conservée')

        def previous(self, *_):
            self.controls.history.previous()
            self._refresh()

        def next(self, *_):
            self.controls.history.next()
            self._refresh()

        def approve(self, *_):
            if self.save() and self.engine.pending_approval:
                self.engine.approve()
                self.log('Approbation demandée pour la prochaine action')

        def _poll(self):
            try:
                if self.controls.phase == 'running':
                    self.engine.tick()
                    # The engine may disable itself on errors.
                    if not self.engine.enabled:
                        self.controls.phase = 'stopped'
                if self.fields['schedule_enabled'].get_active() != self.cfg['schedule_enabled']:
                    self.fields['schedule_enabled'].set_active(self.cfg['schedule_enabled'])
            except Exception as exc:
                self.log('Surveillance : ' + str(exc))
            self._refresh()
            return True

    App().run(None)
