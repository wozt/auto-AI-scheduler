"""Minimap viewport transformations and independent GTK4 navigation overlay."""
from dataclasses import dataclass
from .canvas_view import NODE_W,NODE_H


@dataclass(frozen=True)
class MiniMapGeometry:
    left:float
    top:float
    scale:float
    offset_x:float
    offset_y:float

    def project(self,x,y):
        return self.offset_x+(x-self.left)*self.scale,self.offset_y+(y-self.top)*self.scale

    def unproject(self,x,y):
        return self.left+(x-self.offset_x)/self.scale,self.top+(y-self.offset_y)/self.scale

    def viewport(self,view,width,height):
        if width<=0 or height<=0:return (0.,0.,0.,0.)
        a=view.screen_to_world(0,0)
        b=view.screen_to_world(width,height)
        x,y=self.project(*a)
        x2,y2=self.project(*b)
        return x,y,x2-x,y2-y

    def recenter(self,view,x,y,width,height):
        wx,wy=self.unproject(x,y)
        view.pan_x=width/2-wx*view.zoom
        view.pan_y=height/2-wy*view.zoom


def compute_minimap(graph,view,viewport_w,viewport_h,width=198,height=135,pad=9):
    if width<=2*pad or height<=2*pad or view.zoom<=0:
        raise ValueError('Minimap trop petite')
    bounds=[(n.x,n.y,n.x+NODE_W,n.y+NODE_H) for n in graph.nodes]
    if viewport_w>0 and viewport_h>0:
        x,y=view.screen_to_world(0,0)
        xx,yy=view.screen_to_world(viewport_w,viewport_h)
        bounds.append((x,y,xx,yy))
    if not bounds:bounds=[(0,0,900,600)]
    left=min(b[0] for b in bounds)-25
    top=min(b[1] for b in bounds)-25
    right=max(b[2] for b in bounds)+25
    bottom=max(b[3] for b in bounds)+25
    scale=min((width-2*pad)/max(1,right-left),(height-2*pad)/max(1,bottom-top))
    return MiniMapGeometry(left,top,scale,(width-(right-left)*scale)/2,(height-(bottom-top)*scale)/2)


def minimap_widget(Gtk,graph,view,get_viewport_size,on_navigate):
    area=Gtk.DrawingArea()
    area.set_content_width(198);area.set_content_height(135)
    area.set_size_request(198,135)
    area.set_halign(Gtk.Align.END);area.set_valign(Gtk.Align.END)
    area.set_margin_end(12);area.set_margin_bottom(12)
    area.add_css_class('scheduler-minimap')
    area.set_tooltip_text('Minimap : cliquer ou glisser pour centrer le canevas, sans déplacer les blocs.')

    def geometry(w,h):return compute_minimap(graph,view,*get_viewport_size(),w,h)

    def draw(_widget,cr,w,h):
        t=geometry(w,h)
        cr.set_source_rgba(.07,.09,.12,.95)
        cr.rectangle(0,0,w,h);cr.fill()
        cr.set_source_rgba(.55,.72,.85,.65)
        cr.set_line_width(1.)
        for edge in graph.edges:
            a,b=edge[:2]
            source,target=graph.find(a),graph.find(b)
            if source is None or target is None:continue
            x1,y1=t.project(source.x+NODE_W,source.y+NODE_H/2)
            x2,y2=t.project(target.x,target.y+NODE_H/2)
            cr.move_to(x1,y1);cr.line_to(x2,y2);cr.stroke()
        for node in graph.nodes:
            x,y=t.project(node.x,node.y)
            cr.set_source_rgb(.3,.7,.9) if node.id in view.selected else cr.set_source_rgb(.34,.42,.54)
            cr.rectangle(x,y,max(3,NODE_W*t.scale),max(3,NODE_H*t.scale));cr.fill()
        x,y,ww,hh=t.viewport(view,*get_viewport_size())
        cr.set_source_rgba(.4,.9,.6,.16)
        cr.rectangle(x,y,ww,hh);cr.fill_preserve()
        cr.set_source_rgba(.4,.95,.6,.95)
        cr.set_line_width(1.5);cr.stroke()
        cr.set_source_rgba(.7,.8,.9,.4)
        cr.rectangle(.7,.7,w-1.4,h-1.4);cr.set_line_width(1.);cr.stroke()

    area.set_draw_func(draw)

    def navigate(x,y):
        w=max(1,area.get_width());h=max(1,area.get_height())
        t=geometry(w,h)
        t.recenter(view,x,y,*get_viewport_size())
        on_navigate();area.queue_draw()

    click=Gtk.GestureClick();click.set_button(1)
    click.connect('pressed',lambda _g,_count,x,y:navigate(x,y))
    area.add_controller(click)
    drag=Gtk.GestureDrag();drag.set_button(1)
    origin={'x':0.,'y':0.}
    drag.connect('drag-begin',lambda _g,x,y:origin.update(x=x,y=y))
    drag.connect('drag-update',lambda _g,dx,dy:navigate(origin['x']+dx,origin['y']+dy))
    area.add_controller(drag)
    return area
