"""Reversible, X11-only window operations. No blind Alt+Tab traversal."""
from contextlib import contextmanager
from dataclasses import dataclass
import re
import subprocess
import time

from PIL import ImageGrab


def command(*args, input_text=None, check=True):
    p = subprocess.run(args, input=input_text, capture_output=True, text=True, check=check, timeout=12)
    return p.stdout.strip()


@dataclass(frozen=True)
class DesktopState:
    focus: str | None
    x: int
    y: int


def desktop_state():
    focus = command('xdotool', 'getwindowfocus', check=False) or None
    pos = command('xdotool', 'getmouselocation', '--shell')
    values = dict(re.findall(r'^(X|Y)=(-?\d+)$', pos, re.M))
    return DesktopState(focus, int(values['X']), int(values['Y']))


def restore(state):
    # Focus and pointer are restored on every exit path, including exceptions.
    try:
        if state.focus:
            command('xdotool', 'windowactivate', '--sync', state.focus, check=False)
    finally:
        command('xdotool', 'mousemove', '--sync', str(state.x), str(state.y), check=False)


@contextmanager
def temporary_focus(wid):
    before = desktop_state()
    try:
        command('xdotool', 'windowactivate', '--sync', wid)
        time.sleep(.16)
        yield
    finally:
        restore(before)


def user_idle_seconds():
    """Read X11 idle time; fail closed if xprintidle is unavailable."""
    raw = command('xprintidle')
    if not raw.isdecimal():
        raise RuntimeError('xprintidle produced no idle time')
    return int(raw) / 1000.0


def windows(pattern):
    """Find IDE top-level windows; do nothing when none are present."""
    ids = command('xdotool', 'search', '--onlyvisible', '--name', pattern, check=False)
    result = []
    for wid in dict.fromkeys(ids.splitlines()):
        if wid.isdigit():
            title = command('xdotool', 'getwindowname', wid, check=False)
            result.append((wid, title))
    return result


def geometry(wid):
    data = command('xdotool', 'getwindowgeometry', '--shell', wid)
    fields = dict(re.findall(r'^(X|Y|WIDTH|HEIGHT)=(-?\d+)$', data, re.M))
    return tuple(int(fields[k]) for k in ('X', 'Y', 'WIDTH', 'HEIGHT'))


def screenshot(wid):
    """Screenshots require a visible, activated window; occluded windows are not trusted."""
    x, y, w, h = geometry(wid)
    screen = ImageGrab.grab()
    a, b = max(0, x), max(0, y)
    c, d = min(screen.width, x+w), min(screen.height, y+h)
    if c-a < 350 or d-b < 240:
        raise RuntimeError('Window out of view / too small')
    return screen.crop((a, b, c, d)), (a, b)


def type_and_send(wid, absolute_xy, prompt):
    if not prompt.strip() or '\n' in prompt or len(prompt) > 4000:
        raise ValueError('Only a single-line prompt of at most 4000 characters is supported')
    if command('xdotool', 'getwindowfocus', check=False) != wid:
        raise RuntimeError('Focus changed during action; refusing to type')
    command('xdotool', 'mousemove', '--sync', str(absolute_xy[0]), str(absolute_xy[1]))
    command('xdotool', 'click', '1')
    if command('xdotool', 'getwindowfocus', check=False) != wid:
        raise RuntimeError('Focus changed after click; refusing to type')
    # Type directly: does not overwrite the user's clipboard.
    command('xdotool', 'type', '--clearmodifiers', '--delay', '1', '--', prompt)
    command('xdotool', 'key', '--clearmodifiers', 'Return')


def scroll_down(absolute_xy, steps):
    command('xdotool', 'mousemove', '--sync', str(absolute_xy[0]), str(absolute_xy[1]))
    command('xdotool', 'click', '--repeat', str(steps), '--delay', '35', '5')
