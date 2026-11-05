"""Resizable GTK4 visual scenario editor and guarded timeline executor.

The graph runner only dispatches through Scheduler; explicit Scénario Play is
required. The legacy monitor and workflow executor never run simultaneously.
"""
from datetime import datetime
from uuid import uuid4

from . import config, display, x11
from .control import RunControls, next_action, recent_target
from .engine import Scheduler
from .workflow import NODE_KINDS, WorkflowStore, planned_send
from .workflow_run import WorkflowRunner
from .workflow_checkpoint import CheckpointStore
from .proofs import ProofStore
from . import editor

LEFT_SECTIONS = (
    ('Cible X11', ('project', 'app_pattern', 'prompt', 'mode')),
    ('Planification', ('schedule_enabled', 'scheduled_at')),
)
RIGHT_SECTIONS = (
    ('Surveillance', ('poll_seconds', 'scroll_seconds', 'scroll_steps',
                      'max_cycles', 'cooldown_seconds', 'idle_seconds')),
    ('Notifications SMTP SSL', ('email_enabled', 'email_to', 'email_from',
                                'smtp_host', 'smtp_port', 'email_on_prompt', 'email_on_error')),
    ('Affichage', ('window_width','window_height','font_size','theme')),
)
TRANSPORT_CONTROLS = ('previous','play','pause','stop','next')
COMPACT_CSS = display.css(12) + b'\n.scheduler-state.found {color:#57c88d;} .scheduler-root {font-size: 12px;}\n'

HELP = {
    'project':'Nom exact affiché sous Explorer dans Devin ; vérifié par OCR avant toute action.',
    'app_pattern':'Expression utilisée par xdotool pour retrouver les fenêtres Devin.',
    'prompt':'Texte envoyé uniquement après validation du projet et du champ Codex.',
    'mode':'Manuel = jamais de reprise automatique ; Automatique = reprise sous conditions ; Confirmation = attendre votre accord.',
    'schedule_enabled':'Active un envoi unique à une heure locale indiquée. La fenêtre doit rester ouverte.',
    'scheduled_at':'Date et heure de l’ordinateur au format AAAA-MM-JJ HH:MM.',
    'poll_seconds':'Intervalle entre deux vérifications OCR, en secondes.',
    'scroll_seconds':'Intervalle de défilement vers le bas du chat Codex. 0 le désactive.',
    'scroll_steps':'Nombre de crans de molette lorsque le défilement est demandé.',
    'max_cycles':'Nombre maximum de prompts envoyés par cette session.',
    'cooldown_seconds':'Protection contre des envois trop rapprochés.',
    'idle_seconds':'Durée d’inactivité du bureau avant que le superviseur déplace le focus.',
    'email_enabled':'Active l’envoi de notifications SMTP, pas de confirmation IMAP.',
    'email_to':'Adresse qui reçoit les rapports.',
    'email_from':'Adresse expéditrice du serveur SMTP.',
    'smtp_host':'Nom d’hôte de votre serveur SMTP SSL.',
    'smtp_port':'Port SSL habituel : 465.',
    'email_on_prompt':'Un message après chaque envoi confirmé.',
    'email_on_error':'Un message en cas d’erreur bloquant l’automatisation.',
    'window_width':'Largeur de la fenêtre au démarrage, en pixels. Ajustable en direct.',
    'window_height':'Hauteur de la fenêtre au démarrage, en pixels.',
    'font_size':'Taille de texte de base ; les titres et en-têtes restent légèrement plus grands.',
    'theme':'Apparence libadwaita : système, clair ou sombre.',
}


def launch():
    import gi
    gi.require_version('Gtk','4.0')
    gi.require_version('Adw','1')
    from gi.repository import Gtk,Adw,GLib,Pango

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id='click.woztrium.AutoAIScheduler')
            self.cfg=config.load()
            self.engine=Scheduler(self.cfg,self.log)
            self.controls=RunControls(self.engine)
            self.workflow_store=WorkflowStore()
            self.proof_store=ProofStore()
            self.graph=self.workflow_store.load()
            self.checkpoint_store=CheckpointStore()
            self.runner=WorkflowRunner(self.engine,self.proof_store,self.log,
                                       checkpoints=self.checkpoint_store)
            self._displayed_step=None
            self.runner_label=None
            self.selected=None
            self.window=None
            self.status=None
            self.status_box=None
            self.target_label=None
            self.next_label=None
            self.log_box=None
            self.log_widget=None
            self.canvas=None
            self.timeline_reload=None
            self.timeline_update_trace=None
            self.trace_widget=None
            self._shown_trace_revision=-1
            self.inspector=None
            self.preview=None
            self.css_provider=None
            self.display_settings=None
            self.buttons={}
            self._save_timeout=None

        def log(self,message):
            line=f'{datetime.now():%H:%M:%S}  {message}'
            self.controls.history.add(line)
            if self.status is not None:self.status.set_text(message)
            if self.log_widget is not None:
                buf=self.log_widget.get_buffer()
                buf.insert(buf.get_end_iter(),line+'\n')
            self.refresh()

        def css_apply(self):
            if self.css_provider is None:return
            self.css_provider.load_from_data(display.css(self.cfg['font_size']))
            scheme={'system':Adw.ColorScheme.DEFAULT, 'dark':Adw.ColorScheme.FORCE_DARK,
                    'light':Adw.ColorScheme.FORCE_LIGHT}[self.cfg['theme']]
            Adw.StyleManager.get_default().set_color_scheme(scheme)

        def status_line(self):
            state=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=14)
            state.add_css_class('scheduler-status')
            state.add_css_class('scheduler-state')
            self.status_box=state
            self.target_label=Gtk.Label(label='Fenêtre : non vérifiée',xalign=0)
            self.target_label.set_hexpand(True)
            self.target_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            self.target_label.set_tooltip_text('Vert uniquement après OCR récent du projet et de Codex.')
            state.append(self.target_label)
            self.next_label=Gtk.Label(label='Prochaine : Play',xalign=1)
            self.next_label.set_hexpand(True)
            self.next_label.set_ellipsize(Pango.EllipsizeMode.END)
            state.append(self.next_label)
            return state

        def button(self,box,key,label,callback,help_text):
            btn=Gtk.Button(label=label)
            btn.set_tooltip_text(help_text)
            btn.connect('clicked',callback)
            box.append(btn)
            self.buttons[key]=btn
            return btn

        def do_activate(self):
            if self.window:
                self.window.present()
                return
            win=Adw.ApplicationWindow(application=self)
            self.window=win
            win.set_title('Auto AI Scheduler · Visual Workflow')
            win.set_default_size(self.cfg['window_width'],self.cfg['window_height'])
            win.set_size_request(920,560)
            self.css_provider=Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                win.get_display(),self.css_provider,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self.css_apply()

            toolbar=Adw.ToolbarView()
            header=Adw.HeaderBar()
            heading=Gtk.Label(label='Auto AI Scheduler  ·  Scénarios',xalign=0)
            heading.add_css_class('scheduler-title')
            header.set_title_widget(heading)
            prefs=Gtk.Button(label='⚙ Préférences')
            prefs.set_tooltip_text('Ouvre les réglages du superviseur et de l’affichage, notamment résolution et police.')
            prefs.connect('clicked',self.open_preferences)
            header.pack_end(prefs)
            toolbar.add_top_bar(header)
            root=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5)
            root.add_css_class('scheduler-root')
            for side in ('start','end','top','bottom'):
                getattr(root,'set_margin_'+side)(5)
            toolbar.set_content(root)
            win.set_content(toolbar)
            root.append(self.status_line())

            actions=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=5)
            root.append(actions)
            self.button(actions,'previous','⏮',self.previous,'Parcourir l’historique sans réexécuter une action.')
            self.button(actions,'play','▶ Play',self.play,'Démarrer ou reprendre la surveillance Codex existante.')
            self.button(actions,'pause','⏸ Pause',self.pause,'Suspendre toute action automatique.')
            self.button(actions,'stop','■ Stop',self.stop,'Arrêter la surveillance tout en conservant le scénario.')
            self.button(actions,'next','⏭',self.next,'Afficher la prochaine action anticipée sans la déclencher.')
            self.button(actions,'save','Enregistrer scénario',self.save_workflow,'Enregistrer le graphe localement dans le profil utilisateur.')
            self.button(actions,'detect','Détecter Devin',self.detect,'Vérifier le projet cible puis restaurer fenêtre et souris.')
            self.button(actions,'send','Envoyer maintenant',self.send,'Envoi manuel du prompt général configuré dans Préférences.')
            force=self.button(actions,'force_test','⚡ Force test',self.force_test,
                              'Envoie réellement le prompt du bloc Envoyer prompt sélectionné ; ignore uniquement les déclencheurs et la timeline, pas les protections X11/OCR/quota.')
            force.add_css_class('suggested-action')
            self.button(actions,'approve','Approuver',self.approve,'Autoriser l’envoi après une demande de validation locale.')

            # Two nested horizontal Paned widgets create three freely resizable
            # upper panes. Vertical Paned guarantees timeline stays on the bottom.
            vertical=Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
            vertical.set_wide_handle(True)
            vertical.set_resize_start_child(True)
            vertical.set_shrink_start_child(True)
            vertical.set_resize_end_child(True)
            vertical.set_shrink_end_child(True)
            vertical.set_vexpand(True)
            root.append(vertical)

            upper=Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            upper.set_wide_handle(True)
            upper.set_resize_start_child(False)
            upper.set_resize_end_child(True)
            upper.set_shrink_start_child(True)
            upper.set_shrink_end_child(True)
            vertical.set_start_child(upper)
            upper.set_position(245)
            upper.set_start_child(self.make_library())

            center_right=Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            center_right.set_wide_handle(True)
            center_right.set_resize_start_child(True)
            center_right.set_resize_end_child(False)
            center_right.set_shrink_start_child(True)
            center_right.set_shrink_end_child(True)
            upper.set_end_child(center_right)
            center_right.set_position(max(480,self.cfg['window_width']-580))
            center_right.set_start_child(self.make_canvas())
            center_right.set_end_child(self.make_inspector())

            vertical.set_end_child(self.make_timeline())
            vertical.set_position(max(340,self.cfg['window_height']-310))

            self.status=Gtk.Label(label='Inactif · Play surveille Codex ; ▶ Scénario lance la timeline validée.',xalign=0)
            self.status.set_ellipsize(Pango.EllipsizeMode.END)
            self.status.set_tooltip_text('Exécution des scénarios seulement via ▶ Scénario, après validation du déclencheur et des preuves. Aucun démarrage automatique.')
            root.append(self.status)
            self.select_node(self.graph.nodes[0].id if self.graph.nodes else None)
            self.refresh()
            self.timer=GLib.timeout_add_seconds(3,self._poll)
            win.present()

        def section_title(self,text,hint=''):
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=2)
            title=Gtk.Label(label=text,xalign=0)
            title.add_css_class('scheduler-title')
            box.append(title)
            if hint:
                note=Gtk.Label(label=hint,xalign=0,wrap=True)
                note.add_css_class('scheduler-hint')
                note.set_tooltip_text(hint)
                box.append(note)
            return box

        def make_library(self):
            wrap=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=7)
            wrap.add_css_class('scheduler-panel')
            wrap.set_size_request(190,-1)
            wrap.append(self.section_title('Bibliothèque de blocs','Ajouter des étapes au scénario ; elles ne s’exécutent que par ▶ Scénario'))
            scroll=Gtk.ScrolledWindow()
            scroll.set_vexpand(True)
            wrap.append(scroll)
            stack=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6)
            scroll.set_child(stack)
            for category in ('Déclencheur','Condition','Action'):
                frame=Gtk.Frame(label=category)
                pack=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4)
                for side in ('start','end','top','bottom'):
                    getattr(pack,'set_margin_'+side)(5)
                frame.set_child(pack)
                stack.append(frame)
                for kind,(title,group,defaults,help_text) in NODE_KINDS.items():
                    if category!=group:continue
                    btn=Gtk.Button(label='+ '+title)
                    btn.set_tooltip_text(help_text+'\nAjoute un bloc modifiable à l’éditeur central.')
                    btn.connect('clicked',lambda _b,k=kind:self.add_node(k))
                    pack.append(btn)
            hint=Gtk.Label(label='Relier : cliquez le port vert, puis le port gauche. Glisser : déplacer un bloc.',wrap=True,xalign=0)
            hint.set_tooltip_text('Les liens sont orientés et les cycles sont interdits.')
            stack.append(hint)
            return wrap

        def make_canvas(self):
            wrap=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
            wrap.append(self.section_title('Éditeur visuel','Glisser les blocs ; relier les ports ; sélectionner pour modifier à droite'))
            viewport=Gtk.ScrolledWindow()
            viewport.set_hexpand(True)
            viewport.set_vexpand(True)
            viewport.set_policy(Gtk.PolicyType.AUTOMATIC,Gtk.PolicyType.AUTOMATIC)
            self.canvas=editor.canvas_widget(Gtk,self.graph,self.select_node,
                                             self.graph_changed,self.log,self.cfg['font_size'])
            viewport.set_child(self.canvas)
            wrap.append(viewport)
            return wrap

        def make_inspector(self):
            wrap=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5)
            wrap.add_css_class('scheduler-panel')
            wrap.set_size_request(225,-1)
            wrap.append(self.section_title('Propriétés & OCR','Sélectionnez un bloc dans le graphe ou la timeline'))
            self.inspector=Gtk.ScrolledWindow()
            self.inspector.set_vexpand(True)
            wrap.append(self.inspector)
            return wrap

        def make_timeline(self):
            lower=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4)
            lower.add_css_class('scheduler-timeline')
            bar=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=6)
            lower.append(bar)
            bar.append(Gtk.Label(label='EXÉCUTION DU SCÉNARIO',xalign=0))
            for label,method,help_text in (
                ('▶ Scénario',self.workflow_start,'Armer un scénario validé. N’envoie rien tant que le déclencheur et les conditions ne sont pas vérifiés.'),
                ('⏸ Scénario',self.workflow_pause,'Suspendre la timeline et toute action de ce scénario.'),
                ('■ Scénario',self.workflow_stop,'Arrêter le scénario, sans reprendre le superviseur classique.'),
                ('↶ Restaurer',self.workflow_restore,'Charger la progression privée après redémarrage. Toujours en pause, sans clic sur Devin.'),
                ('✕ Effacer reprise',self.workflow_forget,'Supprimer volontairement le checkpoint après confirmation ; attention aux envois déjà tentés.'),
            ):
                btn=Gtk.Button(label=label)
                btn.set_tooltip_text(help_text)
                btn.connect('clicked',method)
                bar.append(btn)
            self.runner_label=Gtk.Label(label='Scénario inactif',xalign=1)
            self.runner_label.set_hexpand(True)
            self.runner_label.set_ellipsize(Pango.EllipsizeMode.END)
            self.runner_label.set_tooltip_text('Indique le bloc en attente ou en cours. Les scénarios sont exécutés uniquement après un clic sur Scénario.')
            bar.append(self.runner_label)
            timeline,reload,update_trace=self._timeline_components()
            self.timeline_reload=reload
            self.timeline_update_trace=update_trace
            timeline.set_vexpand(True)
            lower.append(timeline)
            # Resizable dual journal: structured, redacted execution trace on
            # the left; existing human-readable application messages on right.
            # Both remain inside the bottom timeline panel.
            split=Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
            split.set_wide_handle(True)
            split.set_shrink_start_child(True)
            split.set_shrink_end_child(True)
            split.set_size_request(-1,95)
            lower.append(split)

            trace_panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
            trace_title=Gtk.Label(label='JOURNAL D’EXÉCUTION · heure / bloc / durée / résultat',xalign=0)
            trace_title.add_css_class('scheduler-small')
            trace_title.set_tooltip_text('Statuts structurés uniquement : aucun texte OCR, prompt ou capture n’est conservé dans ce journal (mémoire de session).')
            trace_panel.append(trace_title)
            trace_scroll=Gtk.ScrolledWindow()
            trace_scroll.set_vexpand(True)
            self.trace_widget=Gtk.TextView(editable=False,cursor_visible=False,monospace=True)
            self.trace_widget.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            self.trace_widget.add_css_class('scheduler-small')
            self.trace_widget.set_tooltip_text('Le statut et la durée de chaque bloc sont aussi visibles sur les miniatures ci-dessus.')
            trace_scroll.set_child(self.trace_widget)
            trace_panel.append(trace_scroll)
            split.set_start_child(trace_panel)

            log_panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
            lbl=Gtk.Label(label='JOURNAL GÉNÉRAL · messages de surveillance',xalign=0)
            lbl.add_css_class('scheduler-small')
            lbl.set_tooltip_text('Liste des messages locaux du superviseur.')
            log_panel.append(lbl)
            logscroll=Gtk.ScrolledWindow()
            logscroll.set_vexpand(True)
            self.log_widget=Gtk.TextView(editable=False,cursor_visible=False,monospace=True)
            self.log_widget.add_css_class('scheduler-small')
            logscroll.set_child(self.log_widget)
            log_panel.append(logscroll)
            split.set_end_child(log_panel)
            split.set_position(max(380,self.cfg['window_width']//2))
            return lower

        def _timeline_components(self):
            return editor.timeline_widget(Gtk,self.graph,self.proof_store,self.select_node,
                                          active_id=lambda: self.runner.current.id if self.runner.phase in ('running','paused','approval') and self.runner.current else None,
                                          trace=self.runner.trace)

        def graph_changed(self):
            self.workflow_store.save(self.graph)
            if self.canvas:self.canvas.queue_draw()
            if self.timeline_reload:self.timeline_reload()
            if self.timeline_update_trace:self.timeline_update_trace()

        def add_node(self,kind):
            count=len(self.graph.nodes)
            node=self.graph.add(kind,100+(count%4)*260,80+(count//4)*125)
            self.graph_changed()
            self.select_node(node.id)
            self.log('Bloc ajouté : '+node.label)

        def select_node(self,ident):
            self.selected=ident
            self.update_inspector()
            if self.canvas:self.canvas.queue_draw()

        def update_inspector(self):
            if self.inspector is None:return
            node=self.graph.find(self.selected)
            panel=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
            for side in ('start','end','top','bottom'):
                getattr(panel,'set_margin_'+side)(7)
            if node is None:
                panel.append(Gtk.Label(label='Aucun bloc sélectionné.\nAjoutez-en un depuis la bibliothèque.',wrap=True))
                self.inspector.set_child(panel)
                return
            label=Gtk.Label(label=f'{node.label} · {NODE_KINDS[node.kind][1]}',xalign=0)
            label.add_css_class('scheduler-title')
            panel.append(label)
            desc=Gtk.Label(label=NODE_KINDS[node.kind][3],wrap=True,xalign=0)
            desc.set_tooltip_text(NODE_KINDS[node.kind][3])
            panel.append(desc)
            if node.kind == 'branch':
                help_label=Gtk.Label(label='Port supérieur = OUI  •  port inférieur = NON\nsource : state ou ocr ; timeout_seconds = 0 pour illimité.', xalign=0, wrap=True)
                help_label.set_tooltip_text('Reliez deux destinations distinctes. En mode OCR, importez une preuve locale et indiquez expected/roi ; aucune absence de preuve ne déclenche un envoi.')
                panel.append(help_label)
            elif node.kind == 'quota_wait':
                help_label=Gtk.Label(label='Reprise : manual (approbation) ou auto (explicite).\nLa date est lue dans le bandeau Codex ; deux vérifications sont exigées.', xalign=0, wrap=True)
                help_label.set_tooltip_text('Pas de contournement du quota. Si la date est illisible, le scénario se suspend. Sans nouvelle confirmation de l’état Codex, aucun prompt n’est envoyé.')
                panel.append(help_label)
            for key,value in node.params.items():
                row=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=2)
                title=Gtk.Label(label=key,xalign=0)
                title.set_tooltip_text('Paramètre du bloc ; sauvegardé localement, jamais envoyé automatiquement.')
                row.append(title)
                options = {
                    ('branch','source'): [('state','État Codex'),('ocr','Preuve OCR')],
                    ('branch','state'): [(x,x) for x in ('READY','RUNNING','FINISHED','QUOTA','ERROR')],
                    ('quota_wait','resume'): [('manual','Après approbation'),('auto','Automatique (opt-in)')],
                }.get((node.kind,key))
                if options:
                    widget=Gtk.DropDown.new_from_strings([label for _,label in options])
                    values=[ident for ident,_ in options]
                    widget.set_selected(values.index(value) if value in values else 0)
                    widget.connect('notify::selected',
                                   lambda dropdown,_pspec,k=key,n=node,vals=values:
                                   self.param_changed(n,k,vals[dropdown.get_selected()]))
                    widget.set_tooltip_text('Choisissez une valeur autorisée pour le bloc. La reprise automatique après quota doit être activée volontairement.')
                else:
                    widget=Gtk.Entry(text=value)
                    widget.set_tooltip_text('Paramètre du bloc. ROI en % : x,y,largeur,hauteur. timeout_seconds=0 désactive la limite de durée.')
                    widget.connect('changed',lambda entry,k=key,n=node:self.param_changed(n,k,entry.get_text()))
                row.append(widget)
                panel.append(row)
            if node.kind in ('ocr','branch'):
                self._ocr_editor(panel,node)
            remove=Gtk.Button(label='Supprimer ce bloc')
            remove.set_tooltip_text('Supprime le bloc et ses connexions de ce scénario local.')
            remove.connect('clicked',lambda *_:self.remove_node(node.id))
            panel.append(remove)
            self.inspector.set_child(panel)

        def _ocr_editor(self,panel,node):
            photo=Gtk.Button(label='Importer une capture de référence')
            photo.set_tooltip_text('Image locale, stockée uniquement sous ~/.local/share/auto-ai-scheduler/proofs (permissions 0600).')
            photo.connect('clicked',lambda *_:self.select_proof_file(node))
            panel.append(photo)
            if node.params.get('proof_id'):
                try:
                    path=self.proof_store._file(node.params['proof_id'])
                    if path.exists():
                        pic=Gtk.Picture.new_for_filename(str(path))
                        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                        pic.set_size_request(-1,110)
                        panel.append(pic)
                except ValueError:pass
            check=Gtk.Button(label='Tester la preuve OCR')
            check.set_tooltip_text('Teste la référence enregistrée, sans interaction avec Devin.')
            check.connect('clicked',lambda *_:self.test_proof(node))
            panel.append(check)
            live=Gtk.Button(label='Comparer avec Devin (manuel)')
            live.set_tooltip_text('Lit la fenêtre Devin confirmée par OCR, restaure focus et souris, sans envoyer de prompt.')
            live.connect('clicked',lambda *_:self.test_proof(node,live=True))
            panel.append(live)

        def select_proof_file(self,node):
            dialog=Gtk.FileDialog(title='Sélectionner une preuve OCR locale')
            def done(d,result):
                try:
                    chosen=d.open_finish(result)
                    if not chosen:return
                    proof_id=self.proof_store.import_image(chosen.get_path())
                    spec,actual=self.proof_store.generate_test(proof_id,node.params.get('expected',''))
                    node.params['proof_id']=proof_id
                    node.params['expected']=spec.expected
                    node.params['roi']=spec.roi
                    self.graph_changed()
                    self.select_node(node.id)
                    self.log('Test OCR généré dans les données locales (preuve non publiée).')
                except Exception as exc:
                    self.log('Import OCR : '+str(exc))
            dialog.open(self.window,None,done)

        def param_changed(self,node,key,value):
            node.params[key]=value
            if self._save_timeout is not None:
                GLib.source_remove(self._save_timeout)
            def flush():
                self._save_timeout=None
                self.graph_changed()
                return False
            self._save_timeout=GLib.timeout_add(450,flush)

        def remove_node(self,ident):
            self.graph.delete(ident)
            self.graph_changed()
            self.select_node(None)
            self.log('Bloc supprimé du scénario local')

        def test_proof(self,node,live=False):
            if not node.params.get('proof_id'):
                self.log('Importez d’abord une preuve OCR.')
                return
            try:
                if live:
                    result={'matched':False,'seen':False}
                    def action(wid,obs,offset):
                        screenshot,_=x11.screenshot(wid)
                        result['matched'],actual=self.proof_store.test(node.params['proof_id'],screenshot)
                        result['seen']=True
                    self.engine.inspect_target(action)
                    if not result['seen']:
                        self.log('Preuve OCR : fenêtre Devin/projet non identifié(e)')
                    else:
                        self.log('OCR actuel : '+('détecté' if result['matched'] else 'absent'))
                else:
                    matched,actual=self.proof_store.test(node.params['proof_id'])
                    self.log('Test de référence OCR : '+('réussi' if matched else 'échec'))
            except Exception as exc:
                self.log('Test OCR : '+str(exc))

        def save_workflow(self,*_):
            self.graph_changed()
            self.log('Scénario sauvegardé localement. Un clic sur ▶ Scénario est requis pour exécuter.')

        def open_preferences(self,*_):
            if self.display_settings is not None:
                self.display_settings.present()
                return
            win=Gtk.Window(title='Préférences · Auto AI Scheduler',transient_for=self.window)
            self.display_settings=win
            win.set_default_size(950,730)
            win.connect('close-request',lambda *_:self._settings_closed())
            scroll=Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
            win.set_child(scroll)
            parent=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
            for side in ('start','end','top','bottom'):
                getattr(parent,'set_margin_'+side)(12)
            scroll.set_child(parent)
            parent.append(self.section_title('Préférences de l’application','Paramètres du superviseur et de la fenêtre graphique'))
            grid=Gtk.Grid(column_spacing=12,row_spacing=6)
            grid.set_column_homogeneous(True)
            parent.append(grid)
            fields={}
            groups=[
                ('Affichage', [('window_width','Largeur initiale','spin',960,3840),('window_height','Hauteur initiale','spin',600,2160),
                               ('font_size','Police (px)','spin',10,20),('theme','Thème','theme',0,0)]),
                ('Devin et planification', [('project','Projet cible','text',0,0),('app_pattern','Fenêtre IDE','text',0,0),
                                            ('prompt','Prompt Codex','text',0,0),('mode','Après cycle','mode',0,0),
                                            ('schedule_enabled','Horaire activé','switch',0,0),('scheduled_at','Date/heure locale','text',0,0)]),
                ('Surveillance', [('poll_seconds','OCR toutes les (s)','spin',5,3600),
                                   ('scroll_seconds','Scroll toutes les (s)','spin',0,3600),
                                   ('scroll_steps','Crans','spin',1,80),('max_cycles','Cycles max','spin',1,100),
                                   ('cooldown_seconds','Cooldown (s)','spin',15,3600),
                                   ('idle_seconds','Inactivité requise (s)','spin',0,3600)]),
                ('Email', [('email_enabled','Activer SMTP','switch',0,0),('email_to','Destinataire','text',0,0),
                            ('email_from','Expéditeur','text',0,0),('smtp_host','Serveur SMTP','text',0,0),
                            ('smtp_port','Port SSL','spin',1,65535),
                            ('email_on_prompt','Notifier les prompts','switch',0,0),
                            ('email_on_error','Notifier les erreurs','switch',0,0)]),
            ]
            for index,(name,items) in enumerate(groups):
                frame=Gtk.Frame(label=name)
                inner=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6)
                for side in ('start','end','top','bottom'):
                    getattr(inner,'set_margin_'+side)(9)
                frame.set_child(inner)
                grid.attach(frame,index%2,index//2,1,1)
                for key,title,kind,low,high in items:
                    row=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=6)
                    caption=Gtk.Label(label=title,xalign=0)
                    caption.set_hexpand(True)
                    caption.set_tooltip_text(HELP[key])
                    row.append(caption)
                    if kind=='spin':
                        widget=Gtk.SpinButton.new_with_range(low,high,1)
                        widget.set_value(self.cfg[key])
                    elif kind=='switch':
                        widget=Gtk.Switch()
                        widget.set_active(bool(self.cfg[key]))
                        widget.set_valign(Gtk.Align.CENTER)
                    elif kind=='mode':
                        widget=Gtk.DropDown.new_from_strings(['Manuel','Automatique','Confirmation'])
                        widget.set_selected(['manual','auto','approval'].index(self.cfg[key]))
                    elif kind=='theme':
                        widget=Gtk.DropDown.new_from_strings(['Système','Sombre','Clair'])
                        widget.set_selected(['system','dark','light'].index(self.cfg[key]))
                    else:
                        widget=Gtk.Entry(text=str(self.cfg[key]))
                        widget.set_width_chars(15)
                    widget.set_tooltip_text(HELP[key])
                    fields[key]=widget
                    row.append(widget)
                    inner.append(row)
            savebtn=Gtk.Button(label='Enregistrer et appliquer les préférences')
            savebtn.set_tooltip_text('Enregistre tous les paramètres et redimensionne la fenêtre ; les titres suivent la taille du texte.')
            def save_prefs(_button):
                proposed=dict(self.cfg)
                for key,w in fields.items():
                    if key in ('email_enabled','schedule_enabled','email_on_prompt','email_on_error'):
                        proposed[key]=w.get_active()
                    elif key in ('mode','theme'):
                        choices=['manual','auto','approval'] if key=='mode' else ['system','dark','light']
                        proposed[key]=choices[w.get_selected()]
                    elif key in ('window_width','window_height','font_size','poll_seconds','scroll_seconds',
                                 'scroll_steps','max_cycles','cooldown_seconds','idle_seconds','smtp_port'):
                        proposed[key]=w.get_value_as_int()
                    else:proposed[key]=w.get_text()
                try:
                    proposed=config.validate(proposed)
                    if proposed['schedule_enabled'] and (
                        not self.cfg['schedule_enabled'] or
                        any(proposed[k]!=self.cfg[k] for k in ('scheduled_at','project','prompt'))):
                        proposed['schedule_id']=uuid4().hex
                    config.save(proposed)
                    self.cfg=proposed
                    self.engine.config=proposed
                    self.css_apply()
                    self.window.set_default_size(proposed['window_width'],proposed['window_height'])
                    self.window.set_size_request(920,560)
                    # GTK4 resize() is not available on ApplicationWindow.
                    self.log('Préférences enregistrées · nouvelle géométrie au prochain lancement')
                except Exception as exc:
                    self.log('Paramètres invalides : '+str(exc))
            savebtn.connect('clicked',save_prefs)
            parent.append(savebtn)
            win.present()

        def _settings_closed(self):
            self.display_settings=None
            return False

        def detect(self,*_):
            obs=self.engine.inspect_target()
            if obs is None:self.log('Devin/Codex avec projet cible introuvable')
            else:self.log(f'Projet confirmé · Codex {obs.state}')

        def send(self,*_):
            if self.runner.phase in ('running','paused','approval'):
                self.log('Envoi manuel interdit pendant un scénario armé ; arrêtez-le d’abord')
                return
            if not self.engine.send('manual'):
                self.log('Envoi manuel refusé (état, quota, projet ou cooldown)')

        def force_test(self,*_):
            if self.runner.phase in ('running','paused','approval'):
                self.log('Force test indépendant : arrêtez le scénario avant tout envoi')
                return
            node = planned_send(self.graph, self.selected)
            if node is None:
                self.log('Force test : ajoutez un bloc « Envoyer prompt » au scénario')
                return
            prompt = node.params.get('prompt', '')
            if not prompt.strip():
                self.log('Force test : le prompt du bloc est vide')
                return
            # Confirmation displays the exact prompt and its source; the
            # graph and timeline are not moved on either branch.
            dialog = Adw.MessageDialog.new(self.window,
                        'Force test · envoi réel dans Codex',
                        'Bloc : ' + node.label + '\n\nPrompt à envoyer :\n' + prompt +
                        '\n\nAucun déclencheur du graphe ne sera exécuté et ' +
                        'la timeline ne changera pas. La programmation reste active.')
            dialog.add_response('cancel', 'Annuler')
            dialog.add_response('send', 'Envoyer ce prompt')
            dialog.set_response_appearance('send', Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response('cancel')
            dialog.set_close_response('cancel')
            def chosen(_dialog, response):
                if response != 'send':
                    return
                if not self.engine.force_test(prompt):
                    self.log('Force test refusé : contrôle projet/Codex, quota, travail actif ou cooldown')
                else:
                    self.log('Force test exécuté depuis le bloc ' + node.id + ' · timeline inchangée')
            dialog.connect('response', chosen)
            dialog.present()

        def workflow_start(self,*_):
            try:
                if self.runner.phase == 'paused':
                    self.runner.resume()
                else:
                    if self.controls.phase == 'running':
                        self.controls.stop()
                    self.workflow_store.save(self.graph)
                    self.runner.start(self.graph)
                self.refresh()
            except ValueError as exc:
                self.log('Scénario refusé : ' + str(exc))

        def workflow_restore(self,*_):
            try:
                if self.controls.phase == 'running':
                    self.controls.stop()
                # Load exact saved graph. Unsaved visual changes must never
                # substitute a different prompt when restoring a checkpoint.
                self.runner.restore(self.workflow_store.load())
                self.refresh()
            except (ValueError, OSError) as exc:
                self.log('Restauration refusée : ' + str(exc))

        def workflow_forget(self,*_):
            try:
                checkpoint=self.checkpoint_store.load()
            except (ValueError, OSError) as exc:
                self.log('Checkpoint illisible : '+str(exc))
                return
            if checkpoint is None:
                self.log('Aucune reprise à effacer')
                return
            dialog=Adw.MessageDialog.new(self.window,'Effacer la progression ?',
                  'Cette suppression est définitive. Un prompt a peut-être déjà été envoyé. '
                  'Vérifiez Codex avant de redémarrer le scénario depuis le début.')
            dialog.add_response('cancel','Annuler')
            dialog.add_response('clear','Effacer le checkpoint')
            dialog.set_response_appearance('clear',Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response('cancel')
            dialog.set_close_response('cancel')
            def confirmed(_dialog,response):
                if response!='clear':return
                try:
                    if self.runner.phase=='running':
                        self.runner.pause()
                    self.runner.forget_checkpoint()
                    self.log('Progression oubliée sur demande · vérifiez Codex avant un nouveau départ')
                    self.refresh()
                except (ValueError,OSError) as exc:
                    self.log('Impossible d’effacer : '+str(exc))
            dialog.connect('response',confirmed)
            dialog.present()

        def workflow_pause(self,*_):
            self.runner.pause()
            self.refresh()

        def workflow_stop(self,*_):
            self.runner.stop()
            self.refresh()

        def play(self,*_):
            if self.runner.phase in ('running','paused','approval'):
                self.log('Arrêtez le scénario avant de lancer le superviseur classique')
                return
            try:
                if self.checkpoint_store.load() is not None:
                    self.log('Reprise de scénario en attente : restaurer ou effacer le checkpoint avant Play')
                    return
            except (ValueError,OSError) as exc:
                self.log('Checkpoint illisible : '+str(exc))
                return
            if self.controls.play():self.log('Surveillance Codex classique démarrée')

        def pause(self,*_):
            if self.controls.pause():self.log('Pause du superviseur')

        def stop(self,*_):
            if self.controls.stop():self.log('Superviseur arrêté')

        def previous(self,*_):
            self.controls.history.previous()
            self.refresh()

        def next(self,*_):
            self.controls.history.next()
            self.refresh()

        def approve(self,*_):
            if self.runner.approve():
                self.refresh()
            elif self.engine.pending_approval:
                self.engine.approve()
                self.log('Approbation locale demandée')

        def refresh(self):
            if self.status_box is None:return
            target=recent_target(self.engine)
            if target is not None:
                self.status_box.add_css_class('found')
                self.target_label.set_text(f'● Devin {target.title}  ·  {self.cfg["project"]}  ·  {target.ui_state}')
            else:
                self.status_box.remove_css_class('found')
                self.target_label.set_text('○ Fenêtre non encore vérifiée / OCR expiré')
            upcoming=next_action(self.engine,self.controls.phase)
            if self.runner.phase in ('running','paused','approval') and self.runner.current:
                upcoming='Scénario · '+self.runner.current.label
            self.next_label.set_text('Prochaine : '+upcoming)
            if self.runner_label is not None:
                node=self.runner.current
                caption='Timeline : '+self.runner.phase+(' · '+node.label+' ['+str(self.runner.index+1)+'/'+str(len(self.runner.path))+']' if node else '')
                if self.runner.phase=='stopped':
                    try:
                        saved=self.checkpoint_store.load()
                        if saved:caption='⚠ Progression sauvegardée · ↶ Restaurer (aucune exécution automatique)'
                    except (ValueError,OSError):
                        caption='⚠ Checkpoint illisible · démarrage désactivé'
                self.runner_label.set_text(caption)
            displayed=self.runner.current.id if self.runner.current and self.runner.phase in ('running','paused','approval') else None
            if self._displayed_step!=displayed:
                self._displayed_step=displayed
                if self.timeline_reload:self.timeline_reload()
            if self.timeline_update_trace:
                self.timeline_update_trace()
            if self.trace_widget is not None and self._shown_trace_revision != self.runner.trace.revision:
                # The trace contains only predefined status/reason descriptions.
                # Never show OCR strings, prompts or raw exception messages.
                lines=self.runner.trace.lines({node.id:node.label for node in self.runner.path})
                self.trace_widget.get_buffer().set_text(
                    '\n'.join(lines) if lines else 'Aucune étape exécutée dans cette session.')
                self._shown_trace_revision=self.runner.trace.revision
            self.next_label.set_tooltip_text(upcoming)
            if 'force_test' in self.buttons:
                self.buttons['force_test'].set_sensitive(
                    planned_send(self.graph, self.selected) is not None)
            for key,allow in {'play':self.controls.phase!='running',
                              'pause':self.controls.phase=='running',
                              'stop':self.controls.phase!='stopped',
                              'previous':self.controls.history.can_previous,
                              'next':self.controls.history.can_next,
                              'approve':self.engine.pending_approval or self.runner.phase=='approval'}.items():
                if key in self.buttons:self.buttons[key].set_sensitive(allow)

        def _poll(self):
            try:
                if self.runner.phase=='running':
                    self.runner.tick()
                elif self.controls.phase=='running':
                    self.engine.tick()
                    if not self.engine.enabled:self.controls.phase='stopped'
            except Exception as exc:self.log('Surveillance : '+str(exc))
            self.refresh()
            return True

    App().run(None)
