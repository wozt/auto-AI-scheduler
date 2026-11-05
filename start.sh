#!/usr/bin/env bash
# Auto AI Scheduler launcher. Do not silently edit global GTK configuration.
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
if [[ "${1:-}" == '--fix-gtk-config' ]]; then
    python3 - <<'PY'
from pathlib import Path
from datetime import datetime
import shutil
p=Path.home()/'.config'/'gtk-4.0'/'settings.ini'
if not p.exists():
    print(f'GTK4 settings file not found: {p}')
    raise SystemExit(0)
text=p.read_text(encoding='utf-8')
removed=('gtk-modules', 'gtk-application-prefer-dark-theme')
lines=[]
for line in text.splitlines(keepends=True):
    key=line.partition('=')[0].strip().lower()
    if key in removed:
        print('Removing unsupported GTK4 key:',key)
        continue
    lines.append(line)
new=''.join(lines)
if new==text:
    print('GTK4 settings already clean')
else:
    backup=p.with_name('settings.ini.bak-'+datetime.now().strftime('%Y%m%d%H%M%S'))
    shutil.copy2(p,backup)
    p.write_text(new,encoding='utf-8')
    print('Backup saved:',backup)
PY
    exit 0
fi
if [[ -x .venv/bin/python ]]; then
    exec .venv/bin/python -m auto_ai_scheduler "$@"
else
    exec python3 -m auto_ai_scheduler "$@"
fi
