"""Private, durable checkpoints for explicitly resumed visual workflows.

This is metadata only: no prompt, screenshot, OCR text, window title, e-mail
address, or graph parameters are recorded. A checkpoint NEVER autoruns.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from datetime import datetime, timezone

DEFAULT_PATH = Path.home() / '.local' / 'state' / 'auto-ai-scheduler' / 'workflow-checkpoint.json'
ACTIVE_PHASES = {'running', 'paused', 'approval'}
SEND_STATES = {'none', 'attempted', 'awaiting_response'}


def graph_fingerprint(graph):
    """Opaque digest: saved workflow content is never written to checkpoint."""
    content = json.dumps(graph.to_dict(), ensure_ascii=False, sort_keys=True,
                         separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(content).hexdigest()


def _validate(payload):
    if not isinstance(payload, dict) or set(payload) != {
            'schema', 'workflow', 'node', 'phase', 'send_state', 'updated_at'}:
        raise ValueError('Checkpoint invalide : structure inattendue')
    if payload['schema'] != 1 or not isinstance(payload['workflow'], str) or not re.fullmatch(
            r'[0-9a-f]{64}', payload['workflow']):
        raise ValueError('Checkpoint invalide : version ou empreinte')
    if not isinstance(payload['node'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',payload['node']):
        raise ValueError('Checkpoint invalide : bloc')
    if (not isinstance(payload['phase'], str) or not isinstance(payload['send_state'], str) or
            payload['phase'] not in ACTIVE_PHASES or payload['send_state'] not in SEND_STATES):
        raise ValueError('Checkpoint invalide : état')
    if not isinstance(payload['updated_at'],str) or len(payload['updated_at'])>40:
        raise ValueError('Checkpoint invalide : date')
    return payload


class CheckpointStore:
    """Atomic 0600 writes, 0700 private folder, explicit deletion only."""
    def __init__(self, path=DEFAULT_PATH):
        self.path = Path(path)

    def load(self):
        if self.path.is_symlink():
            raise ValueError('Checkpoint : lien symbolique refusé')
        if not self.path.exists():
            return None
        if not self.path.is_file() or self.path.stat().st_size > 4096:
            raise ValueError('Checkpoint : fichier inattendu ou trop grand')
        if self.path.stat().st_mode & 0o077:
            raise ValueError('Checkpoint : permissions trop ouvertes (0600 requis)')
        try:
            return _validate(json.loads(self.path.read_text(encoding='utf-8')))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError('Checkpoint illisible : ne pas redémarrer le scénario') from exc

    def save(self, *, workflow, node, phase, send_state='none'):
        payload = _validate({
            'schema':1, 'workflow':workflow, 'node':node,
            'phase':phase, 'send_state':send_state,
            'updated_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        })
        if self.path.is_symlink():
            raise ValueError('Checkpoint : lien symbolique refusé')
        if self.path.parent.is_symlink():
            raise ValueError('Checkpoint : dossier symbolique refusé')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        fd, name = tempfile.mkstemp(prefix='.checkpoint-', dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, sort_keys=True)
                f.write('\n')
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.path)
            # A rename is durable only after the directory metadata is synced.
            dfd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(dfd)
            finally: os.close(dfd)
        finally:
            if os.path.exists(name): os.unlink(name)

    def clear(self):
        if self.path.is_symlink():
            raise ValueError('Checkpoint : lien symbolique refusé')
        if self.path.exists():
            self.path.unlink()
            return True
        return False
