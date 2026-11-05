"""GTK4 / libadwaita app. UI avoids manipulating IDE when stopped."""
from datetime import datetime
from uuid import uuid4
from . import config
from .engine import Scheduler


def launch():
    import gi
    gi.require_version('Gtk','4.0')
    gi.require_version('Adw','1')
    from gi.repository import Gtk, Adw, GLib

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id='click.woztrium.AutoAIScheduler')
            self.cfg = config.load()
            self.engine = Scheduler(self.cfg, self.log)
            self.timer = None

        def log(self, text):
            stamp = datetime.now().strftime('%H:%M:%S')
            self.status.set_text(text)
            self.logs.get_buffer().insert(self.logs.get_buffer().get_end_iter(),f'{stamp}  {text}\n')

        def do_activate(self):
            win = Adw.ApplicationWindow(application=self)
            win.set_title('Auto AI Scheduler')
            win.set_default_size(850,690)
            toolbar = Adw.ToolbarView()
            header = Adw.HeaderBar()
            toolbar.add_top_bar(header)
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
            root.set_margin_start(20); root.set_margin_end(20)
            root.set_margin_top(12); root.set_margin_bottom(12)
            toolbar.set_content(root)
            win.set_content(toolbar)
            self.fields = {}

            group = Adw.PreferencesGroup(title='Cible X11', description='Le projet doit être visible en haut à gauche dans Explorer')
            scroll=Gtk.ScrolledWindow(vexpand=True)
            prefs=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
            scroll.set_child(prefs)
            root.append(scroll)
            prefs.append(group)
            for key,title in [('project','Projet cible'),('app_pattern','Titre de la fenêtre IDE'),('prompt','Prompt (une ligne)')]:
                row=Adw.EntryRow(title=title)
                row.set_text(str(self.cfg[key])); self.fields[key]=row; group.add(row)
            mode_row=Adw.ComboRow(title='Après une fin de cycle')
            modes=Gtk.StringList.new(['Manuel','Automatique','Confirmation locale'])
            mode_row.set_model(modes)
            choices=['manual','auto','approval']
            mode_row.set_selected(choices.index(self.cfg['mode']))
            group.add(mode_row)

            planned=Adw.PreferencesGroup(title='Planification',description='Heure locale du PC. Devin et cette application doivent rester ouverts.')
            prefs.append(planned)
            scheduled=Adw.SwitchRow(title='Activer un envoi unique à heure fixe')
            scheduled.set_active(bool(self.cfg['schedule_enabled']))
            self.fields['schedule_enabled']=scheduled; planned.add(scheduled)
            schedule_date=Adw.EntryRow(title='Date et heure (AAAA-MM-JJ HH:MM)')
            schedule_date.set_text(self.cfg['scheduled_at'])
            self.fields['scheduled_at']=schedule_date; planned.add(schedule_date)
            scheduled.connect('notify::active',lambda *args: schedule_date.set_sensitive(scheduled.get_active()))
            schedule_date.set_sensitive(scheduled.get_active())

            settings=Adw.PreferencesGroup(title='Surveillance',description='OCR local, sans API privée')
            prefs.append(settings)
            for key,title in [('poll_seconds','Contrôle toutes les (secondes)'),('scroll_seconds','Défilement toutes les (secondes, 0 désactive)'),('scroll_steps','Crans de défilement'),('max_cycles','Cycles maximum'),('cooldown_seconds','Pause minimum entre envois (secondes)'),
                              ('idle_seconds','Inactivité requise avant action (secondes)')]:
                row=Adw.SpinRow.new_with_range(0 if key in ('scroll_seconds','idle_seconds') else 1,3600,1)
                row.set_title(title); row.set_value(self.cfg[key]); self.fields[key]=row; settings.add(row)
            mail=Adw.PreferencesGroup(title='Notifications mail (SMTP SSL)',description='Secrets dans AUTO_AI_SMTP_USER / AUTO_AI_SMTP_PASSWORD. Pas de confirmation par mail dans ce squelette.')
            prefs.append(mail)
            toggle=Adw.SwitchRow(title='Activer les notifications')
            toggle.set_active(bool(self.cfg['email_enabled']));self.fields['email_enabled']=toggle;mail.add(toggle)
            for key,title in [('email_to','Destinataire'),('email_from','Expéditeur'),('smtp_host','Serveur SMTP')]:
                row=Adw.EntryRow(title=title);row.set_text(str(self.cfg[key])); self.fields[key]=row; mail.add(row)

            buttons=Gtk.Box(spacing=8)
            root.append(buttons)
            for caption,cb in [('Enregistrer',self.save),('Tester la détection',self.detect),('Envoyer maintenant',self.send),('Démarrer',self.start),('Arrêter',self.stop),('Approuver',self.approve)]:
                button=Gtk.Button(label=caption)
                button.connect('clicked',cb)
                buttons.append(button)
            self.status=Gtk.Label(label='Inactif',xalign=0,wrap=True)
            root.append(self.status)
            pane=Gtk.ScrolledWindow(vexpand=True)
            self.logs=Gtk.TextView(editable=False,cursor_visible=False,monospace=True)
            pane.set_child(self.logs);root.append(pane)
            self.mode_row=mode_row
            self.timer=GLib.timeout_add_seconds(5,self._poll)
            win.present()

        def save(self,*_):
            try:
                updated=dict(self.cfg)
                for key,row in self.fields.items():
                    if key in ('email_enabled','schedule_enabled'):updated[key]=row.get_active()
                    elif isinstance(row,Adw.SpinRow):updated[key]=int(row.get_value())
                    else:updated[key]=row.get_text()
                updated['mode']=['manual','auto','approval'][self.mode_row.get_selected()]
                if updated['schedule_enabled'] and (
                    not self.cfg['schedule_enabled'] or
                    any(updated[k] != self.cfg[k] for k in ('scheduled_at','project','prompt'))
                ):
                    updated['schedule_id'] = uuid4().hex
                config.save(updated)
                self.cfg=updated
                self.engine.config=updated
                self.log('Configuration enregistrée')
                return True
            except Exception as exc:
                self.log(f'Configuration invalide : {exc}')
                return False

        def detect(self,*_):
            if not self.save(): return
            obs=self.engine.inspect_target()
            if obs is None:self.log('Aucune fenêtre Devin+Codex avec le projet cible')
            else:self.log(f'Projet trouvé ; Codex={obs.state} ; champ={obs.composer}')

        def send(self,*_):
            if not self.save(): return
            self.engine.send('manual')

        def start(self,*_):
            if not self.save(): return
            self.engine.enabled=True
            self.log('Surveillance active (cette fenêtre GTK doit rester ouverte)')

        def stop(self,*_):
            self.engine.enabled=False
            self.log('Surveillance arrêtée')

        def approve(self,*_):
            self.engine.approve()

        def _poll(self):
            try:
                self.engine.tick()
                if self.fields['schedule_enabled'].get_active() != self.cfg['schedule_enabled']:
                    self.fields['schedule_enabled'].set_active(self.cfg['schedule_enabled'])
            except Exception as exc:self.log('Surveillance : '+str(exc))
            return True

    App().run(None)
