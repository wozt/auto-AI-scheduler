"""Small, validated, user-local configuration."""
import json
from .schedule import parse_local
from .display import DEFAULTS as DISPLAY_DEFAULTS, validate as validate_display
from pathlib import Path

CONFIG = Path.home() / '.config' / 'auto-ai-scheduler' / 'settings.json'
DEFAULTS = {
    'project': 'metroidvania', 'app_pattern': 'Devin',
    'prompt': 'continue', 'mode': 'manual',
    'poll_seconds': 15, 'scroll_seconds': 60, 'scroll_steps': 12,
    'max_cycles': 10, 'cooldown_seconds': 60,
    'email_to': '', 'email_from': '', 'smtp_host': '', 'smtp_port': 465,
    'email_enabled': False, 'email_on_prompt': True, 'email_on_error': True,
    'schedule_enabled': False, 'scheduled_at': '', 'schedule_id': '',
    'idle_seconds': 8,
    **DISPLAY_DEFAULTS,
}


def validate(cfg):
    cfg = {**DEFAULTS, **cfg}
    if not cfg['project'].strip() or not cfg['app_pattern'].strip():
        raise ValueError('Project and app name are mandatory')
    if cfg['mode'] not in ('manual', 'auto', 'approval'):
        raise ValueError('Invalid mode')
    for k, low, high in [('poll_seconds', 5, 3600), ('scroll_seconds', 0, 3600),
                         ('scroll_steps', 1, 80), ('max_cycles', 1, 100),
                         ('cooldown_seconds', 15, 3600), ('smtp_port', 1, 65535),
                         ('idle_seconds', 0, 3600)]:
        cfg[k] = int(cfg[k])
        if not low <= cfg[k] <= high:
            raise ValueError(f'Invalid {k}')
    cfg.update(validate_display({k: cfg[k] for k in DISPLAY_DEFAULTS}))
    if cfg['schedule_enabled']:
        parse_local(cfg['scheduled_at'])
    return cfg


def load():
    if CONFIG.exists():
        return validate(json.loads(CONFIG.read_text(encoding='utf-8')))
    return validate({})


def save(cfg):
    cfg = validate(cfg)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.parent.chmod(0o700)
    tmp = CONFIG.with_suffix('.tmp')
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.chmod(0o600)
    tmp.replace(CONFIG)
