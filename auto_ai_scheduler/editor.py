"""GTK4 canvas and bottom timeline. No automatic desktop actions here."""
import math
from pathlib import Path

from .workflow import NODE_KINDS

NODE_W=218
NODE_H=88
PALETTE={'Déclencheur':(0.18,0.35,0.58), 'Condition':(0.50,0.33,0.13),
         'Action':(0.16,0.43,0.30)}


def hit_node(graph, x, y):
    for node in reversed(graph.nodes):
        if node.x<=x<=node.x+NODE_W and node.y<=y<=node.y+NODE_H:
            return node
    return None


def canvas_widget(Gtk, graph, on_select, on_changed, on_message, font=12):
    """Direct-manipulation node editor: select, drag, wire output→input."""
    area=Gtk.DrawingArea()
    area.set_content_width(1600)
    area.set_content_height(1050)
    area.set_hexpand(True)
    area.set_vexpand(True)
    pending={'from':None, 'branch':None}
    drag={'node':None,'x':0,'y':0}

    def draw(area, cr, width, height):
        cr.set_source_rgb(.12,.13,.15)
        cr.paint()
        cr.set_source_rgba(.7,.8,.9,.09)
        for x in range(0,width,30):
            for y in range(0,height,30):
                cr.arc(x,y,1.0,0,math.tau)
                cr.fill()
        for edge in graph.edges:
            a,b=edge[:2]
            branch=edge[2] if len(edge)==3 else None
            x,y=graph.find(a),graph.find(b)
            if x is None or y is None:continue
            sx,sy=x.x+NODE_W,x.y+(NODE_H/3 if branch=='yes' else 2*NODE_H/3 if branch=='no' else NODE_H/2)
            ex,ey=y.x,y.y+NODE_H/2
            cr.set_source_rgba(.54,.76,.93,.9)
            cr.set_line_width(3)
            cr.move_to(sx,sy)
            cr.curve_to(sx+75,sy,ex-75,ey,ex,ey)
            cr.stroke()
            if branch:
                cr.set_source_rgb(.7,.9,.7) if branch=='yes' else cr.set_source_rgb(.96,.69,.43)
                cr.set_font_size(max(10,font-2))
                cr.move_to(sx+9,sy-5)
                cr.show_text('OUI' if branch=='yes' else 'NON')
        for n in graph.nodes:
            r,g,b=PALETTE[NODE_KINDS[n.kind][1]]
            cr.set_source_rgba(.13,.17,.21,1)
            cr.rectangle(n.x,n.y,NODE_W,NODE_H)
            cr.fill()
            cr.set_source_rgb(r,g,b)
            cr.rectangle(n.x,n.y,NODE_W,27)
            cr.fill()
            cr.set_source_rgba(.75,.83,.89,.5)
            cr.set_line_width(1)
            cr.rectangle(n.x+.5,n.y+.5,NODE_W-1,NODE_H-1)
            cr.stroke()
            cr.set_source_rgb(.97,.97,.98)
            cr.select_font_face('Sans',0,1)
            cr.set_font_size(font+2)
            cr.move_to(n.x+10,n.y+19)
            cr.show_text(n.label)
            cr.select_font_face('Sans',0,0)
            cr.set_font_size(max(10,font-2))
            cr.move_to(n.x+10,n.y+49)
            summary=' · '.join(str(v) for v in list(n.params.values())[:2])[:28]
            cr.show_text(summary)
            cr.set_source_rgb(.78,.81,.84)
            cr.arc(n.x,n.y+NODE_H/2,5,0,math.tau)
            cr.fill()
            if n.kind=='branch':
                for label,dy,color in [('OUI',NODE_H/3,(.43,.84,.58)),('NON',NODE_H*2/3,(.94,.59,.31))]:
                    cr.set_source_rgb(*color)
                    cr.arc(n.x+NODE_W,n.y+dy,6,0,math.tau)
                    cr.fill()
                    cr.set_font_size(max(9,font-3))
                    cr.move_to(n.x+NODE_W-35,n.y+dy+4)
                    cr.show_text(label)
            else:
                cr.set_source_rgb(.43,.84,.58)
                cr.arc(n.x+NODE_W,n.y+NODE_H/2,6,0,math.tau)
                cr.fill()
            if pending['from']==n.id:
                cr.set_source_rgb(1,.93,.4)
                cr.arc(n.x+NODE_W,n.y+(NODE_H/3 if pending.get('branch')=='yes' else 2*NODE_H/3 if pending.get('branch')=='no' else NODE_H/2),10,0,math.tau)
                cr.stroke()
    area.set_draw_func(draw)

    def press(gesture,count,x,y):
        n=hit_node(graph,x,y)
        if n and x>=n.x+NODE_W-18:
            pending['from']=n.id
            pending['branch']=('yes' if y<n.y+NODE_H/2 else 'no') if n.kind=='branch' else None
            on_message('Connexion : sortie ' + (pending['branch'] or 'unique') + ' → port gauche de destination')
        elif n and x<=n.x+18 and pending['from']:
            try:
                graph.connect(pending['from'],n.id,branch=pending['branch'])
                on_changed()
                on_message('Blocs connectés')
            except ValueError as exc:
                on_message(str(exc))
            pending['from']=None
            pending['branch']=None
        elif n:
            on_select(n.id)
        else:
            pending['from']=None
            pending['branch']=None
            on_select(None)
        area.queue_draw()

    click=Gtk.GestureClick()
    click.connect('pressed',press)
    area.add_controller(click)

    def begin(gesture,x,y):
        n=hit_node(graph,x,y)
        drag['node']=n if n and x<n.x+NODE_W-18 and x>n.x+18 else None
        if drag['node'] is not None:
            drag['x']=n.x;drag['y']=n.y
            on_select(n.id)

    def update(gesture,dx,dy):
        n=drag['node']
        if n is None:return
        n.x=max(20,min(2500,round(drag['x']+dx)))
        n.y=max(20,min(2500,round(drag['y']+dy)))
        area.queue_draw()

    def finish(gesture,dx,dy):
        if drag['node'] is not None:
            on_changed()
            drag['node']=None

    mover=Gtk.GestureDrag()
    mover.connect('drag-begin',begin)
    mover.connect('drag-update',update)
    mover.connect('drag-end',finish)
    area.add_controller(mover)
    return area


def timeline_widget(Gtk, graph, proof_store, on_select, active_id=None):
    """Resizable bottom timeline: each ordered node is a time-track clip.

    Proof images only load from the user's private, local store.
    """
    wrap=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=4)
    header=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=7)
    heading=Gtk.Label(label='TIMELINE  ·  Scénario en lecture / édition',xalign=0)
    heading.set_hexpand(True)
    header.append(heading)
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

    def reload():
        child=track.get_first_child()
        while child is not None:
            next_child=child.get_next_sibling()
            track.remove(child)
            child=next_child
        for index,node in enumerate(graph.ordered()):
            frame=Gtk.Frame()
            if active_id is not None and active_id()==node.id:
                frame.add_css_class('scheduler-clip-active')
            frame.set_size_request(178,125)
            content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5)
            for margin in ('start','end','top','bottom'):
                getattr(content,'set_margin_'+margin)(6)
            tip=Gtk.Label(label=f'{index+1:02d}  {node.label}',xalign=0)
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
            frame.set_child(content)
            gesture=Gtk.GestureClick()
            gesture.connect('pressed',lambda _g,_c,_x,_y, ident=node.id:on_select(ident))
            frame.add_controller(gesture)
            track.append(frame)

    reload()
    return wrap,reload
