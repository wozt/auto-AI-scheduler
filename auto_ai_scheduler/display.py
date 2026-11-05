"""Validated display preferences: responsive GTK geometry and proportional fonts."""
DEFAULTS = {'window_width': 1540, 'window_height': 900, 'font_size': 12, 'theme': 'system'}


def validate(cfg):
    result={**DEFAULTS,**cfg}
    for field,minimum,maximum in [('window_width',960,3840),('window_height',600,2160),('font_size',10,20)]:
        result[field]=int(result[field])
        if not minimum<=result[field]<=maximum:
            raise ValueError(f'Invalid {field}')
    if result['theme'] not in ('system','dark','light'):
        raise ValueError('Invalid theme')
    return result


def css(font_size):
    f=validate({'font_size':font_size})['font_size']
    return (f'.scheduler-root, .scheduler-root label {{font-size:{f}px;}} '
            f'.scheduler-title {{font-size:{f+3}px; font-weight:700;}} '
            f'.scheduler-hint {{font-size:{max(9,f-1)}px; opacity:.85;}} '
            f'.scheduler-small {{font-size:{max(9,f-1)}px;}} '
            f'.scheduler-node-title {{font-size:{f+2}px; font-weight:700;}} '
            '.scheduler-status {border-radius:7px;padding:7px 12px;background:alpha(@window_fg_color,.065);}'
            '.scheduler-status.found {color:#57c88d;background:alpha(#208a50,.14);}'
            '.scheduler-panel {padding:6px;} '
            '.scheduler-timeline {background:alpha(@window_fg_color,.027);}'
            '.scheduler-clip-active {outline:2px solid #50c983;outline-offset:-2px;}'
            '.scheduler-root button {min-height:26px;}').encode('utf-8')
