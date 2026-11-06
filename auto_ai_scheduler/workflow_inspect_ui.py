"""GTK4-only view of redacted OCR state frames; never executes a workflow."""
from __future__ import annotations
from .workflow_inspect import view_window, align_reference, frame_details, compare_frames


def make_inspector(Gtk, get_state, on_seek, on_zoom, on_pin, on_compare):
    """Return (widget, refresh). The selected frame is controlled by the GUI."""
    wrap=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5)
    bar=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=6)
    wrap.append(bar)
    label=Gtk.Label(label='TRAMES OCR · lecture hors ligne',xalign=0)
    label.add_css_class('scheduler-small')
    bar.append(label)
    def button(title, callback, tooltip):
        b=Gtk.Button(label=title)
        b.set_tooltip_text(tooltip)
        b.connect('clicked',lambda *_: callback())
        bar.append(b)
        return b
    button('◀',lambda:on_seek(-1,relative=True),'Trame enregistrée précédente, sans aucune action dans Devin.')
    button('▶',lambda:on_seek(1,relative=True),'Trame enregistrée suivante, sans aucune action dans Devin.')
    bar.append(Gtk.Label(label='Zoom :'))
    zoom_widget=Gtk.SpinButton.new_with_range(1,8,1)
    zoom_widget.set_tooltip_text('Zoom temporel de 1× (séquence entière) à 8× autour de la trame sélectionnée.')
    zoom_widget.set_width_chars(2)
    bar.append(zoom_widget)
    zoom_widget.connect('value-changed',lambda w:on_zoom(w.get_value_as_int()))
    button('📌 Épingler référence',on_pin,'Copier volontairement la séquence actuelle dans reference.json (fichier privé 0600), pour une comparaison future.')
    button('⇄ Comparer',on_compare,'Comparer la trame courante avec la trame de référence la plus proche en temps relatif. Aucune donnée envoyée.')

    ruler=Gtk.DrawingArea()
    ruler.set_content_width(500)
    ruler.set_content_height(86)
    ruler.set_hexpand(True)
    ruler.set_tooltip_text('Chaque barre représente une observation OCR anonymisée, pas une capture écran. Cliquez pour vous positionner sur la trame la plus proche.')
    wrap.append(ruler)
    state={'blocked':False,'data':None,'ref':None,'position':0,'zoom':1,'compare':False}

    def draw(_area,cr,width,height):
        data=state['data']
        cr.set_source_rgb(.15,.17,.19)
        cr.rectangle(0,0,width,height)
        cr.fill()
        if data is None:return
        i=state['position']
        zoom=state['zoom']
        start,end=view_window(data,i,zoom)
        left,right=44,max(45,width-13)
        span=max(end-start,.001)
        cr.select_font_face('Sans',0,0)
        cr.set_font_size(10)
        for y,title,frames,origin in [(31,'ACT.',data['frames'],data['frames'][0]['seconds']),
                                     (61,'REF.',state['ref']['frames'],state['ref']['frames'][0]['seconds'])] if state['ref'] else [(31,'ACT.',data['frames'],data['frames'][0]['seconds'])]:
            cr.set_source_rgb(.77,.8,.82)
            cr.move_to(3,y)
            cr.show_text(title)
            cr.set_source_rgba(.8,.85,.9,.16)
            cr.move_to(left,y+4)
            cr.line_to(right,y+4)
            cr.stroke()
            for frame in frames:
                # Compare relative elapsed time, not the two machine clocks.
                seconds=data['frames'][0]['seconds'] + frame['seconds']-origin
                if seconds<start or seconds>end:continue
                x=left+(seconds-start)/span*(right-left)
                state_name=frame['state']
                color={'READY':(.3,.8,.54),'FINISHED':(.33,.72,.93),
                       'RUNNING':(.91,.68,.28),'QUOTA':(.9,.37,.32),'ERROR':(.92,.26,.29)}.get(state_name,(.66,.67,.67))
                cr.set_source_rgb(*color)
                cr.rectangle(x-2,y-13,4,18)
                cr.fill()
        x=left+(data['frames'][i]['seconds']-start)/span*(right-left)
        cr.set_source_rgba(1.,1.,1.,.85)
        cr.set_line_width(1.5)
        cr.move_to(x,15)
        cr.line_to(x,height-4)
        cr.stroke()
        cr.set_source_rgb(.81,.85,.89)
        cr.move_to(left,10)
        cr.show_text(f't+{start:.1f}s')
        cr.move_to(max(left,width-110),10)
        cr.show_text(f't+{end:.1f}s')

    ruler.set_draw_func(draw)
    gesture=Gtk.GestureClick()
    def click(_gesture,_count,x,_y):
        data=state['data']
        if data is None:return
        from .workflow_inspect import closest_frame
        width=max(ruler.get_width(),60)
        position=(x-44)/max(1,width-57)
        on_seek(closest_frame(data,state['position'],state['zoom'],position))
    gesture.connect('pressed',click)
    ruler.add_controller(gesture)
    seek=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,0,119,1)
    seek.set_draw_value(False)
    seek.set_hexpand(True)
    seek.set_tooltip_text('Déplacer le curseur pour examiner une observation OCR enregistrée. Zéro commande réelle.')
    def changed(widget):
        if state['blocked']:return
        on_seek(round(widget.get_value()))
    seek.connect('value-changed',changed)
    wrap.append(seek)
    info=Gtk.Label(label='Aucune séquence chargée · utilisez Enregistrer puis Terminer.',xalign=0)
    info.set_selectable(True)
    info.set_wrap(True)
    info.add_css_class('scheduler-small')
    info.set_tooltip_text('Données anonymisées : état, horodatage, présence du champ et votes OCR. Aucun screenshot ni transcription n’existe dans l’enregistrement.')
    info_scroll=Gtk.ScrolledWindow()
    info_scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
    info_scroll.set_propagate_natural_height(False)
    info_scroll.set_min_content_height(54)
    info_scroll.set_max_content_height(80)
    info_scroll.set_child(info)
    wrap.append(info_scroll)
    compare_label=Gtk.Label(label='',xalign=0,wrap=True)
    compare_label.add_css_class('scheduler-small')
    compare_label.set_selectable(True)
    compare_label.set_tooltip_text('Comparaison uniquement des états et votes OCR connus ; une absence de preuve reste indéterminée.')
    compare_scroll=Gtk.ScrolledWindow()
    compare_scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
    compare_scroll.set_propagate_natural_height(False)
    compare_scroll.set_min_content_height(35)
    compare_scroll.set_max_content_height(65)
    compare_scroll.set_child(compare_label)
    wrap.append(compare_scroll)

    def update():
        data,ref,index,zoom,compare,labels=get_state()
        state.update(data=data,ref=ref,position=index,zoom=zoom,compare=compare)
        state['blocked']=True
        try:
            zoom_widget.set_value(zoom)
            seek.set_range(0,max(1,len(data['frames'])-1) if data else 1)
            seek.set_value(index)
            seek.set_sensitive(data is not None and len(data['frames'])>1)
        finally:state['blocked']=False
        if data is None:
            info.set_text('Aucune séquence chargée · utilisez Rejouer pour ouvrir la dernière capture.')
            compare_label.set_text('')
        else:
            info.set_text('   ·   '.join(frame_details(data,index,labels)))
            if ref is None:
                compare_label.set_text('Référence absente : utilisez 📌 Épingler référence pour conserver une seconde séquence.')
            elif not compare:
                compare_label.set_text('Référence chargée. Cliquez sur ⇄ Comparer pour afficher les écarts.')
            else:
                try:
                    diff=compare_frames(data,ref,index,labels)
                    compare_label.set_text(f'Référence trame {diff.reference_index+1}/{len(ref["frames"])} · '+('Différences : ' if diff.different else '')+' ; '.join(diff.description))
                except ValueError as exc:
                    compare_label.set_text('Comparaison impossible : '+str(exc))
        ruler.queue_draw()
    update()
    return wrap,update
