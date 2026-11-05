"""Safe debug controls for the GTK workflow editor.

Breakpoints contain opaque node IDs, not prompts, OCR output or screenshots.
Debugging never sends anything itself; the guarded WorkflowRunner owns execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile

from .proofs import match_text, ocr_image, parse_roi
from . import x11

BREAKPOINT_PATH = Path.home() / '.local' / 'share' / 'auto-ai-scheduler' / 'breakpoints.json'
_NODE_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')


def _check_ids(items):
    if not isinstance(items, (list, set, tuple)) or len(items) > 50 or any(
        not isinstance(item, str) or _NODE_ID.fullmatch(item) is None for item in items
    ):
        raise ValueError('Invalid breakpoint identifiers')
    return set(items)


class BreakpointStore:
    """Private, atomically replaced breakpoint list for the default workflow."""

    def __init__(self, path=BREAKPOINT_PATH):
        self.path = Path(path)

    def load(self):
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError('Breakpoint file symlink refused')
        if not self.path.exists():
            return set()
        if not self.path.is_file() or self.path.stat().st_size > 8192:
            raise ValueError('Breakpoint file invalid')
        if self.path.stat().st_mode & 0o077:
            raise ValueError('Breakpoint file must have mode 0600')
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or set(data) != {'schema', 'nodes'} or data['schema'] != 1:
            raise ValueError('Unsupported breakpoint file')
        return _check_ids(data['nodes'])

    def save(self, ids):
        chosen = _check_ids(ids)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError('Breakpoint symlink refused')
        if self.path.exists() and not self.path.is_file():
            raise ValueError('Breakpoint path is not a file')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        fd, name = tempfile.mkstemp(prefix='.breakpoints-', dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump({'schema': 1, 'nodes': sorted(chosen)}, output)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)


@dataclass(frozen=True)
class OCRDebugResult:
    matched: bool
    state: str
    description: str


def dry_run_ocr(engine, proofs, node):
    """Test a selected OCR condition with a fresh verified X11 screenshot.

    Return only an allowlisted result; never log/save actual OCR text, and never
    call send/scroll. No match is accepted while Codex is busy or in quota.
    """
    if node is None or not (node.kind == 'ocr' or
                             (node.kind == 'branch' and node.params.get('source') == 'ocr')):
        raise ValueError('Sélectionnez un bloc Preuve OCR ou Si/Sinon en mode OCR')
    proof_id = node.params.get('proof_id', '')
    if not proofs._file(proof_id).is_file():
        raise ValueError('Capture OCR de référence absente')
    expected = node.params.get('expected', '').strip()
    if not expected or len(expected) > 240:
        raise ValueError('Texte OCR attendu invalide')
    roi = node.params.get('roi', '')
    parse_roi(roi)
    captures = []

    def action(window_id, observation, offset):
        # The action runs while the project's IDE window is temporarily focused;
        # inspect_target restores both focus and mouse in a finally clause.
        if observation.state in ('READY', 'FINISHED') and observation.composer is not None:
            image, _ = x11.screenshot(window_id)
            captures.append(image)

    observation = engine.inspect_target(action)
    if observation is None:
        return OCRDebugResult(False, 'UNKNOWN', 'Fenêtre ou projet non confirmé')
    if observation.state not in ('READY', 'FINISHED') or not captures:
        return OCRDebugResult(False, observation.state, 'État Codex indisponible pour ce test')
    matched = match_text(ocr_image(captures[0], roi), expected)
    return OCRDebugResult(matched, observation.state,
                          'Preuve OCR détectée' if matched else 'Preuve OCR non détectée')
