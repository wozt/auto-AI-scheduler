"""GTK4 canvas and bottom timeline. No automatic desktop actions here."""
import math
from pathlib import Path

from .workflow import NODE_KINDS
from .canvas_view import CanvasView, NODE_W, NODE_H

PALETTE={'Déclencheur':(0.18,0.35,0.58), 'Condition':(0.50,0.33,0.13),
         'Action':(0.16,0.43,0.30)}


def hit_node(graph, x, y):
    for node in reversed(graph.nodes):
        if node.x<=x<=node.x+NODE_W and node.y<=y<=node.y+NODE_H:
            return node
    return None


def canvas_widget(Gtk, graph, on_select, on_changed, on_message, font=12, view=None, on_view_changed=None, on_undo=None, on_redo=None, on_before_edit=None):
    """GTK4 viewport editor: wheel zoom, pan, lasso, multi-drag and safe wiring.

    Only node dragging mutates workflow positions. Camera/selection never
    mutate graph data or send X11 commands. The bottom timeline is separate.
    """
    from gi.repository import Gdk

    view = view if view is not None else CanvasView()
    area = Gtk.DrawingArea()
    # A viewport-sized canvas: pan/zoom are camera transforms, not scrollbars
    # backed by a fixed 1600x1050 minimum on small KDE window allocations.
    area.set_content_width(1)
    area.set_content_height(1)
    area.set_hexpand(True)
    area.set_vexpand(True)
    area.set_focusable(True)
    area.set_tooltip_text(
        'Molette : zoom sous le pointeur · Milieu ou Espace+glisser : déplacer la vue · '
        'Maj/Ctrl+clic : sélection multiple · Glisser sur le fond : sélection rectangulaire · '
        'Ctrl+A : tout sélectionner · Échap : annuler une connexion')
    pending = {'from': None, 'branch': None}
    pointer = {'x': 200.0, 'y': 160.0, 'space': False}
    drag = {'mode': None, 'initial': {}, 'start': (0., 0.), 'last': (0., 0.),
            'rectangle': None, 'additive': False, 'moved': False}

    def modifiers(controller):
        return bool(controller.get_current_event_state() &
                    (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK))

    def changed_selection(ident):
        on_select(ident if ident in view.selected else
                  next(iter(sorted(view.selected)), None))

    def draw(_area, cr, width, height):
        cr.set_source_rgb(.12, .13, .15)
        cr.paint()
        # Grid is world-anchored but screen-clipped and cheap at any zoom.
        grid = 30.0 * view.zoom
        first_x = view.pan_x % grid
        first_y = view.pan_y % grid
        cr.set_source_rgba(.7, .8, .9, .09)
        cr.set_line_width(1.)
        x = first_x
        while x < width:
            cr.move_to(x, 0)
            cr.line_to(x, height)
            x += grid
        y = first_y
        while y < height:
            cr.move_to(0, y)
            cr.line_to(width, y)
            y += grid
        cr.stroke()

        cr.save()
        cr.translate(view.pan_x, view.pan_y)
        cr.scale(view.zoom, view.zoom)
        cr.select_font_face('Sans', 0, 0)
        for edge in graph.edges:
            a, b = edge[:2]
            branch = edge[2] if len(edge) == 3 else None
            src, dst = graph.find(a), graph.find(b)
            if src is None or dst is None:
                continue
            sx, sy = (src.x+NODE_W,
                      src.y+(NODE_H/3 if branch=='yes' else
                             2*NODE_H/3 if branch=='no' else NODE_H/2))
            ex, ey = dst.x, dst.y+NODE_H/2
            bend = max(35., min(115., abs(ex-sx)*.45))
            control1 = (sx+bend, sy)
            control2 = (ex-bend, ey)
            color = ((.36, .81, .52) if branch == 'yes' else
                     (.94, .58, .33) if branch == 'no' else (.53, .73, .95))
            cr.move_to(sx, sy)
            cr.curve_to(*control1, *control2, ex, ey)
            cr.set_line_width(5.)
            cr.set_source_rgba(.04, .08, .12, .85)
            cr.stroke_preserve()
            cr.set_source_rgb(*color)
            cr.set_line_width(2.6)
            cr.stroke()
            # Arrow at the input (only a visual cue; edge direction is unchanged).
            cr.move_to(ex-11., ey-5.)
            cr.line_to(ex-2., ey)
            cr.line_to(ex-11., ey+5.)
            cr.set_line_width(2.3)
            cr.stroke()
            if branch:
                cr.set_source_rgb(*color)
                cr.set_font_size(max(10, font-2))
                cr.move_to(sx+9, sy-8)
                cr.show_text('OUI' if branch == 'yes' else 'NON')

        for n in graph.nodes:
            r, g, b = PALETTE[NODE_KINDS[n.kind][1]]
            selected = n.id in view.selected
            if selected:
                cr.set_source_rgba(.35, .77, 1., .24)
                cr.rectangle(n.x-5, n.y-5, NODE_W+10, NODE_H+10)
                cr.fill()
            cr.set_source_rgb(.13, .17, .21)
            cr.rectangle(n.x, n.y, NODE_W, NODE_H)
            cr.fill()
            cr.set_source_rgb(r, g, b)
            cr.rectangle(n.x, n.y, NODE_W, 27)
            cr.fill()
            cr.set_source_rgb(.48, .85, 1.) if selected else cr.set_source_rgba(.75, .83, .89, .5)
            cr.set_line_width(2.5 if selected else 1.)
            cr.rectangle(n.x+.5, n.y+.5, NODE_W-1, NODE_H-1)
            cr.stroke()
            cr.set_source_rgb(.97, .97, .98)
            cr.select_font_face('Sans', 0, 1)
            cr.set_font_size(font+2)
            cr.move_to(n.x+10, n.y+19)
            cr.show_text(n.label)
            cr.select_font_face('Sans', 0, 0)
            cr.set_font_size(max(10, font-2))
            cr.move_to(n.x+10, n.y+49)
            summary = ' · '.join(str(v) for v in list(n.params.values())[:2])[:28]
            cr.show_text(summary)
            cr.set_source_rgb(.78, .81, .84)
            cr.arc(n.x, n.y+NODE_H/2, 5, 0, math.tau)
            cr.fill()
            ports = (('yes', NODE_H/3, (.43, .84, .58)),
                     ('no', NODE_H*2/3, (.94, .59, .31))) if n.kind == 'branch' else (
                     (None, NODE_H/2, (.43, .84, .58)),)
            for label, dy, color in ports:
                cr.set_source_rgb(*color)
                cr.arc(n.x+NODE_W, n.y+dy, 6, 0, math.tau)
                cr.fill()
                if label:
                    cr.set_font_size(max(9, font-3))
                    cr.move_to(n.x+NODE_W-35, n.y+dy+4)
                    cr.show_text('OUI' if label == 'yes' else 'NON')
                if pending['from'] == n.id and pending['branch'] == label:
                    cr.set_source_rgb(1., .93, .4)
                    cr.arc(n.x+NODE_W, n.y+dy, 10, 0, math.tau)
                    cr.set_line_width(2)
                    cr.stroke()
        cr.restore()

        # Lasso is in screen space, so its border is always crisp at all zooms.
        if drag['rectangle'] is not None:
            x1, y1, x2, y2 = drag['rectangle']
            left, top, rw, rh = min(x1, x2), min(y1, y2), abs(x2-x1), abs(y2-y1)
            cr.set_source_rgba(.35, .65, 1., .14)
            cr.rectangle(left, top, rw, rh)
            cr.fill_preserve()
            cr.set_source_rgba(.58, .83, 1., .95)
            cr.set_line_width(1.5)
            cr.set_dash([5., 4.])
            cr.stroke()
            cr.set_dash([])

    area.set_draw_func(draw)

    motion = Gtk.EventControllerMotion()
    motion.connect('motion', lambda _c,x,y: pointer.update(x=x,y=y))
    area.add_controller(motion)

    def wheel(_controller, _dx, dy):
        view.wheel(dy, pointer['x'], pointer['y'])
        if on_view_changed is not None:
            on_view_changed()
        area.queue_draw()
        return True

    scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
    scroll.connect('scroll', wheel)
    area.add_controller(scroll)

    def press(gesture, _count, sx, sy):
        area.grab_focus()
        pointer.update(x=sx,y=sy)
        if pointer['space']:
            return
        wx, wy = view.screen_to_world(sx, sy)
        n = view.hit(graph, sx, sy)
        if n is not None and wx >= n.x+NODE_W-18:
            pending['from'] = n.id
            pending['branch'] = ('yes' if wy<n.y+NODE_H/2 else 'no') if n.kind == 'branch' else None
            on_message('Connexion : sortie '+(pending['branch'] or 'unique')+' → port gauche de destination')
        elif n is not None and wx <= n.x+18 and pending['from']:
            try:
                if on_before_edit is not None:on_before_edit()
                graph.connect(pending['from'], n.id, branch=pending['branch'])
                on_changed()
                on_message('Blocs connectés')
            except ValueError as exc:
                on_message(str(exc))
            pending.update({'from': None, 'branch': None})
        elif n is not None:
            view.choose(n.id, modifiers(gesture))
            changed_selection(n.id)
        else:
            pending.update({'from': None, 'branch': None})
            view.choose(None, modifiers(gesture))
            changed_selection(None)
        area.queue_draw()

    click = Gtk.GestureClick()
    click.set_button(1)
    click.connect('pressed', press)
    area.add_controller(click)

    def begin(gesture, sx, sy):
        drag.update(mode=None, initial={}, start=(sx,sy), last=(0.,0.),
                    rectangle=None, additive=modifiers(gesture), moved=False)
        if pointer['space']:
            drag['mode'] = 'pan'
            return
        node = view.hit(graph,sx,sy)
        if node is not None:
            wx, wy = view.screen_to_world(sx, sy)
            if wx <= node.x+18 or wx >= node.x+NODE_W-18:
                return
            if on_before_edit is not None:on_before_edit()
            if node.id not in view.selected:
                view.choose(node.id, additive=drag['additive'])
                changed_selection(node.id)
            if node.id in view.selected:
                drag['mode'] = 'nodes'
                drag['initial'] = {n.id:(n.x,n.y) for n in graph.nodes if n.id in view.selected}
        else:
            drag['mode'] = 'rectangle'
            drag['rectangle'] = (sx, sy, sx, sy)
        area.queue_draw()

    def update(_gesture, dx, dy):
        mode = drag['mode']
        if mode == 'nodes':
            drag['moved'] |= view.move_selection(graph, drag['initial'], dx, dy)
        elif mode == 'pan':
            lx, ly = drag['last']
            view.pan_by(dx-lx, dy-ly)
            drag['last'] = (dx,dy)
            if on_view_changed is not None:on_view_changed()
        elif mode == 'rectangle':
            sx, sy = drag['start']
            drag['rectangle'] = (sx,sy,sx+dx,sy+dy)
        if mode == 'nodes' and on_view_changed is not None:on_view_changed()
        area.queue_draw()

    def finish(_gesture, dx, dy):
        mode = drag['mode']
        if mode == 'nodes' and drag['moved']:
            on_changed()   # One workflow save for the entire grouped gesture.
        elif mode == 'rectangle' and dx*dx+dy*dy > 16:
            sx, sy = drag['start']
            view.rectangle(graph, (sx,sy), (sx+dx,sy+dy), additive=drag['additive'])
            changed_selection(None)
        drag['mode'] = None
        drag['rectangle'] = None
        area.queue_draw()

    mover = Gtk.GestureDrag()
    mover.set_button(1)
    mover.connect('drag-begin', begin)
    mover.connect('drag-update', update)
    mover.connect('drag-end', finish)
    area.add_controller(mover)

    def middle_begin(_gesture, x, y):
        drag['last'] = (0.,0.)
    def middle_update(_gesture, dx, dy):
        lx, ly = drag['last']
        view.pan_by(dx-lx,dy-ly)
        drag['last'] = (dx,dy)
        if on_view_changed is not None:on_view_changed()
        area.queue_draw()
    middle = Gtk.GestureDrag()
    middle.set_button(2)
    middle.connect('drag-begin', middle_begin)
    middle.connect('drag-update', middle_update)
    area.add_controller(middle)

    def key_pressed(_controller, key, _keycode, state):
        if state & Gdk.ModifierType.CONTROL_MASK:
            if key in (getattr(Gdk,"KEY_z",ord("z")),getattr(Gdk,"KEY_Z",ord("Z"))) and on_undo is not None and not state & Gdk.ModifierType.SHIFT_MASK:
                on_undo()
                return True
            if ((key in (getattr(Gdk,"KEY_z",ord("z")),getattr(Gdk,"KEY_Z",ord("Z"))) and state & Gdk.ModifierType.SHIFT_MASK) or
                key in (getattr(Gdk,"KEY_y",ord("y")),getattr(Gdk,"KEY_Y",ord("Y")))) and on_redo is not None:
                on_redo()
                return True
        if key == Gdk.KEY_space:
            pointer['space'] = True
            return True
        if key == Gdk.KEY_Escape:
            pending.update({'from': None, 'branch': None})
            drag['rectangle'] = None
            area.queue_draw()
            return True
        if key in (Gdk.KEY_a,Gdk.KEY_A) and state & Gdk.ModifierType.CONTROL_MASK:
            view.selected = {n.id for n in graph.nodes}
            changed_selection(None)
            area.queue_draw()
            return True
        return False
    def key_released(_controller, key, _keycode, _state):
        if key == Gdk.KEY_space:
            pointer['space'] = False
    keys = Gtk.EventControllerKey()
    keys.connect('key-pressed', key_pressed)
    keys.connect('key-released', key_released)
    area.add_controller(keys)
    return area

def timeline_widget(Gtk, graph, proof_store, on_select, active_id=None, trace=None, breakpoints=None, simulation=None):
    """Resizable bottom timeline: each ordered node is a time-track clip.

    Proof images only load from the user's private, local store.
    """
    wrap=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4)
    header=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=7)
    heading=Gtk.Label(label='TIMELINE  ·  Scénario en lecture / édition',xalign=0)
    heading.set_hexpand(True)
    header.append(heading)
    legend=Gtk.Label(label='✓ Terminé  ·  ◐ Preuve OCR  ·  ! Bloqué  ·  ⌛ Timeout',xalign=1)
    legend.set_tooltip_text('Les indicateurs décrivent la session actuelle ; ils ne sont jamais des preuves suffisantes pour envoyer un prompt.')
    legend.add_css_class('scheduler-small')
    header.append(legend)
    wrap.append(header)
    scroll=Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC,Gtk.PolicyType.NEVER)
    scroll.set_hexpand(True)
    scroll.set_vexpand(True)
    wrap.append(scroll)
    track=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
    track.set_margin_top(7)
    track.set_margin_bottom(7)
    track.set_margin_start(8)
    scroll.set_child(track)
    markers = {}
    simulation_classes = tuple('scheduler-sim-' + status for status in ('ok','yes','no','would','waiting','blocked'))
    trace_classes = tuple('scheduler-trace-' + status for status in (
        'active', 'waiting', 'ocr_yes', 'ocr_no', 'done', 'paused',
        'blocked', 'timeout', 'attempt', 'response', 'restored'))

    def update_trace():
        if trace is None:
            return
        for ident, (frame, indicator) in markers.items():
            entry = trace.latest.get(ident)
            label, detail = trace.summary(ident)
            indicator.set_text(label)
            indicator.set_tooltip_text(detail)
            frame.set_tooltip_text(detail)
            for css_class in trace_classes:
                frame.remove_css_class(css_class)
            if entry is not None:
                frame.add_css_class('scheduler-trace-' + entry.status)
            for css_class in simulation_classes:
                frame.remove_css_class(css_class)
            result=simulation() if simulation is not None else None
            simulated=result.latest.get(ident) if result is not None else None
            if simulated is not None:
                frame.add_css_class('scheduler-sim-' + simulated.status)
                indicator.set_text('🧪 ' + simulated.caption)
                indicator.set_tooltip_text('Simulation fictive seulement : ' + simulated.reason + '\nAucune action réelle exécutée.')
                frame.set_tooltip_text('Simulation hors ligne : ' + simulated.reason)

    def reload():
        markers.clear()
        child=track.get_first_child()
        while child is not None:
            next_child=child.get_next_sibling()
            track.remove(child)
            child=next_child
        for index,node in enumerate(graph.ordered()):
            frame=Gtk.Frame()
            if active_id is not None and active_id()==node.id:
                frame.add_css_class('scheduler-clip-active')
            frame.set_size_request(186,151)
            content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5)
            for margin in ('start','end','top','bottom'):
                getattr(content,'set_margin_'+margin)(6)
            is_breakpoint=breakpoints is not None and node.id in breakpoints()
            tip=Gtk.Label(label=('● ' if is_breakpoint else '')+f'{index+1:02d}  {node.label}',xalign=0)
            if is_breakpoint:
                tip.add_css_class('scheduler-breakpoint')
                frame.set_tooltip_text('● Point d’arrêt : l’exécution s’arrête avant ce bloc en mode débogage')
            tip.add_css_class('scheduler-small')
            content.append(tip)
            if node.kind=='ocr' and node.params.get('proof_id'):
                try:
                    path=proof_store._file(node.params['proof_id'])
                    if Path(path).is_file():
                        pic=Gtk.Picture.new_for_filename(str(path))
                        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                        pic.set_size_request(135,52)
                        content.append(pic)
                except ValueError:
                    pass
            else:
                label=Gtk.Label(label=NODE_KINDS[node.kind][1],xalign=.5)
                label.set_size_request(135,52)
                content.append(label)
            caption=Gtk.Label(label=' → '.join(list(node.params.values())[:1])[:22],xalign=0)
            caption.set_ellipsize(3)  # Pango.EllipsizeMode.END
            caption.add_css_class('scheduler-small')
            content.append(caption)
            indicator=Gtk.Label(label='· Non exécuté',xalign=0)
            indicator.add_css_class('scheduler-small')
            indicator.set_ellipsize(3)
            indicator.set_tooltip_text('Étape non exécutée dans cette session')
            content.append(indicator)
            markers[node.id]=(frame,indicator)
            frame.set_child(content)
            gesture=Gtk.GestureClick()
            gesture.connect('pressed',lambda _g,_c,_x,_y, ident=node.id:on_select(ident))
            frame.add_controller(gesture)
            track.append(frame)
        update_trace()

    reload()
    return wrap,reload,update_trace
