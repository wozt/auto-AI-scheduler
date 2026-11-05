"""OCR of the Explorer header and Codex pane; OCR text never becomes a command."""
from dataclasses import dataclass
import re
from PIL import Image, ImageEnhance


@dataclass(frozen=True)
class Observation:
    project_found: bool
    codex_found: bool
    composer: tuple[int, int] | None
    scroll_anchor: tuple[int, int] | None
    state: str
    cycle_marker: str
    reset_text: str
    text: str


def lines(image):
    import pytesseract
    scale = 2
    big = image.convert('RGB').resize((image.width*scale, image.height*scale), Image.Resampling.LANCZOS)
    big = ImageEnhance.Contrast(big).enhance(1.2)
    data = pytesseract.image_to_data(big, lang='eng', config='--psm 11', output_type=pytesseract.Output.DICT)
    groups = {}
    for i, word in enumerate(data['text']):
        word = word.strip()
        if word:
            key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
            groups.setdefault(key, []).append((data['left'][i], data['top'][i],
                                                data['width'][i], data['height'][i], word))
    out = []
    for words in groups.values():
        words.sort()
        x1 = min(w[0] for w in words)//scale
        y1 = min(w[1] for w in words)//scale
        x2 = max(w[0]+w[2] for w in words)//scale
        y2 = max(w[1]+w[3] for w in words)//scale
        out.append((' '.join(w[4] for w in words), (x1,y1,x2,y2)))
    return sorted(out, key=lambda row:(row[1][1], row[1][0]))


def is_project_visible(ocr_rows, project, size):
    """Project must appear in the upper-left Explorer, not the terminal or chat."""
    width, height = size
    upper_left = [(t, box) for t,box in ocr_rows if box[0] < width*.45 and box[1] < min(max(height*.38, 140), 440)]
    explorer = [(t,b) for t,b in upper_left if re.search(r'\bexplorer\b', t, re.I)]
    if not explorer:
        return False
    start_y = min(b[1] for _,b in explorer)
    expected = project.casefold().strip()
    return any((t.casefold().strip(' >⌄▾').strip() == expected or
                bool(re.fullmatch(r'[a-z]\s+' + re.escape(expected), t.casefold().strip())))
               and start_y < b[1] < start_y+210 for t,b in upper_left)


def inspect(image, project):
    rows = lines(image)
    return inspect_rows(rows, project, image.size)


def inspect_rows(rows, project, size):
    w,h = size
    text = '\n'.join(t for t,_ in rows)
    lower = text.lower()
    project_found = is_project_visible(rows, project, size)
    composer = None
    for t,(x1,y1,x2,y2) in reversed(rows):
        if re.search(r'ask\s+for\s+follow.?up\s+changes|message\s+codex|ask\s+codex', t, re.I):
            composer = ((x1+x2)//2, (y1+y2)//2)
            break
    # Marker alone is not sufficient to send a second prompt; supervisor tracks each send.
    worked = re.search(r'worked\s+for\s+\d+\s*[mh]', lower)
    edited = re.search(r'edited\s+\d+\s+files?', lower)
    marker = f'{worked.group() if worked else ""}|{edited.group() if edited else ""}'
    quota = bool(re.search(r'out\s+of\s+codex\s+messages|hit\s+your\s+usage\s+limit', lower))
    ui_error = bool(re.search(r'failed\s+to\s+send|error\s+loading\s+conversation|stream\s+disconnected', lower))
    busy = bool(re.search(r'working\s+for|stop\s+generating', lower))
    state = 'QUOTA' if quota else 'ERROR' if ui_error else 'RUNNING' if busy else 'FINISHED' if worked and edited and composer else 'READY' if composer else 'UNKNOWN'
    reset = re.search(r'(?:rate\s+limit\s+resets\s+on|try\s+again\s+at)[^\n]{0,100}', text, re.I)
    # A scroll anchor is selected inside Codex, never in the Explorer tree.
    anchor = (composer[0], max(100, min(h-100, int(h*.42)))) if composer else None
    codex_found = composer is not None or quota or bool(worked or edited)
    return Observation(project_found,codex_found,composer,anchor,state,marker,
                       reset.group() if reset else '',text)
