"""Privacy-preserving, one-shot local schedule claim.

Do not store screenshots, OCR or prompt contents in the runtime state.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re

STATE_PATH = Path.home() / '.local' / 'state' / 'auto-ai-scheduler' / 'claims.json'
DATE_FORMAT = '%Y-%m-%d %H:%M'


def parse_local(value):
    """Accept only YYYY-MM-DD HH:MM, interpreted in the PC's local timezone."""
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', value):
        raise ValueError('Expected YYYY-MM-DD HH:MM')
    return datetime.strptime(value, DATE_FORMAT)


def due(cfg, now=None):
    if not cfg.get('schedule_enabled'):
        return False
    return (now or datetime.now()) >= parse_local(cfg['scheduled_at'])


def job_id(cfg):
    # Avoid saving any sensitive project, prompt or schedule content in the state file.
    payload = '\0'.join(str(cfg.get(k, '')) for k in
                        ('project', 'prompt', 'scheduled_at', 'schedule_id'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


class ClaimStore:
    """Durable at-most-once claim, written BEFORE keyboard input.

    A crash after claiming may require manual re-arming; it will not cause an
    automatic duplicate prompt. The claim is only made once the correct target
    and its composer were positively verified.
    """
    def __init__(self, path=STATE_PATH):
        self.path = Path(path)

    def contains(self, key):
        if not self.path.exists():
            return False
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        return key in payload.get('claimed', [])

    def claim(self, key):
        claimed = []
        if self.path.exists():
            claimed = json.loads(self.path.read_text(encoding='utf-8')).get('claimed', [])
        if key in claimed:
            return False
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        new = {'claimed': (claimed + [key])[-128:]}
        tmp = self.path.with_name(self.path.name + '.tmp')
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump(new, f)
                f.write('\n')
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)
        return True
