"""Read-only, offline timeline inspection and comparison of private OCR recordings.

The recorded 'frames' are redacted state/boolean observations, NOT screenshots.
No X11, Scheduler, SMTP, screenshot, or prompt operations are imported here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from .workflow_record import RecordingStore, validate_recording

REFERENCE_PATH = Path.home() / '.local' / 'share' / 'auto-ai-scheduler' / 'recordings' / 'reference.json'


def frame_details(recording, index, labels=None):
    """Show the current state and per-node boolean outcomes, never raw OCR."""
    doc = validate_recording(recording)
    frames = doc['frames']
    if type(index) is not int or not 0 <= index < len(frames):
        raise ValueError('Trame hors séquence')
    frame = frames[index]
    lines = [f'Trame {index + 1}/{len(frames)} · t+{frame["seconds"]:.1f} s',
             f'Horodatage local : {frame["local_at"]}',
             f'Codex : {frame["state"]} · champ : {"présent" if frame["composer"] else "absent"}',
             f'Marqueur de cycle : {"présent (empreinte privée)" if frame["marker"] else "absent"}',
             f'Quota : {"échéance identifiée" if frame["quota_reset"] else "échéance inconnue"}']
    for ident, value in sorted(frame['matches'].items()):
        name = (labels or {}).get(ident, 'Condition OCR')
        # Node labels must be predefined names, not user-defined OCR content.
        lines.append(f'{name} [{ident[:8]}] : {"OUI" if value is True else "NON" if value is False else "indéterminé"}')
    return tuple(lines)


def align_reference(recording, reference, index):
    """Align by elapsed seconds (nearest frame), not unstable frame ordinal."""
    doc = validate_recording(recording)
    ref = validate_recording(reference)
    if doc['workflow'] != ref['workflow']:
        raise ValueError('Comparaison impossible : les scénarios sont différents')
    if type(index) is not int or not 0 <= index < len(doc['frames']):
        raise ValueError('Trame hors séquence')
    t = doc['frames'][index]['seconds'] - doc['frames'][0]['seconds']
    base = ref['frames'][0]['seconds']
    return min(range(len(ref['frames'])), key=lambda i: (abs(ref['frames'][i]['seconds']-base-t),i))


@dataclass(frozen=True)
class FrameDifference:
    current_index: int
    reference_index: int
    different: bool
    description: tuple[str, ...]


def compare_frames(recording, reference, index, labels=None):
    """Compare redacted observations only: state, composer and known OCR votes."""
    doc = validate_recording(recording)
    ref = validate_recording(reference)
    other_index = align_reference(doc, ref, index)
    left, right = doc['frames'][index], ref['frames'][other_index]
    changes=[]
    if left['state'] != right['state']:
        changes.append('État : actuel '+left['state']+' / référence '+right['state'])
    if left['composer'] != right['composer']:
        changes.append('Champ Codex : présence différente')
    if bool(left['quota_reset']) != bool(right['quota_reset']):
        changes.append('Quota : disponibilité de l’échéance différente')
    # Avoid revealing hashes or assuming that two independently recorded
    # cycles share a meaningful hash. Check only the presence of markers.
    if bool(left['marker']) != bool(right['marker']):
        changes.append('Marqueur de cycle : présence différente')
    keys=set(left['matches']) | set(right['matches'])
    for ident in sorted(keys):
        a=left['matches'].get(ident)
        b=right['matches'].get(ident)
        if a is b:continue
        name=(labels or {}).get(ident,'Condition OCR')
        to_text=lambda v: 'OUI' if v is True else 'NON' if v is False else 'indéterminé'
        changes.append(f'{name} [{ident[:8]}] : {to_text(a)} / {to_text(b)}')
    return FrameDifference(index, other_index, bool(changes),tuple(changes) if changes else ('Aucune différence dans les états enregistrés.',))


def view_window(recording, index, zoom=1):
    """Time interval around the selected frame, for a 1x..8x time ruler."""
    frames=validate_recording(recording)['frames']
    if type(index) is not int or not 0 <= index < len(frames):
        raise ValueError('Trame hors séquence')
    if type(zoom) is not int or not 1 <= zoom <= 8:
        raise ValueError('Zoom hors limites')
    origin=frames[0]['seconds']
    end=frames[-1]['seconds']
    duration=max(end-origin,1.0)
    span=duration/zoom
    point=frames[index]['seconds']
    start=max(origin,min(end-span,point-span/2))
    return start,start+span


def closest_frame(recording, index, zoom, proportion):
    """Select nearest measured frame for a click in the visible time viewport."""
    if isinstance(proportion,bool) or not isinstance(proportion,(int,float)):
        raise ValueError('Position invalide')
    start,end=view_window(recording,index,zoom)
    target=start+(end-start)*max(0.0,min(1.0,proportion))
    frames=recording['frames']
    return min(range(len(frames)),key=lambda i:(abs(frames[i]['seconds']-target),i))


def save_comparison_reference(source, destination=None):
    """Opt-in copy to a separate private 0600 recording, no raw images added."""
    doc=source.load() if isinstance(source,RecordingStore) else validate_recording(source)
    if doc is None:
        raise ValueError('Aucun enregistrement à épingler')
    output=destination or RecordingStore(REFERENCE_PATH)
    output.save(doc)
    return len(doc['frames'])
