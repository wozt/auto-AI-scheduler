"""Bounded, opt-in scheduler with exact window/project selection."""
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
import hashlib
import os
import smtplib
import ssl
import time
from . import vision, x11


@dataclass
class Session:
    last_sent: float = 0.0
    last_scroll: float = 0.0
    cycles: int = 0
    stable: int = 0
    previous: str = ''
    baseline_marker: str = ''
    waiting_for_response: bool = False
    approved: bool = False
    quota_seen: bool = False


class Scheduler:
    def __init__(self, config, log):
        self.config = config
        self.log = log
        self.session = Session()
        self.enabled = False
        self.pending_approval = False

    def _notify(self, subject, body):
        c = self.config
        if not c.get('email_enabled'):
            return
        user = os.environ.get('AUTO_AI_SMTP_USER','')
        password = os.environ.get('AUTO_AI_SMTP_PASSWORD','')
        if not all([user,password,c.get('smtp_host'),c.get('email_to'),c.get('email_from')]):
            self.log('Mail not configured; no message sent')
            return
        try:
            msg = EmailMessage()
            msg['Subject'] = '[Auto AI Scheduler] ' + subject
            msg['From'] = c['email_from']; msg['To'] = c['email_to']
            msg.set_content(body)
            with smtplib.SMTP_SSL(c['smtp_host'], c['smtp_port'], context=ssl.create_default_context(), timeout=15) as smtp:
                smtp.login(user,password)
                smtp.send_message(msg)
        except Exception as exc:
            self.log(f'Mail delivery failed: {exc}')

    def _candidates(self):
        return x11.windows(self.config['app_pattern'])

    def inspect_target(self, action=None):
        """Find the correct visible IDE project by visiting candidate window IDs.

        `action(wid, observation, offset)` executes inside the temporary-focus
        context, before focus and pointer are restored automatically.
        """
        candidates = self._candidates()
        if not candidates:
            self.log('Devin absent: no action')
            return None
        for wid,title in candidates:
            try:
                with x11.temporary_focus(wid):
                    image, offset = x11.screenshot(wid)
                    obs = vision.inspect(image, self.config['project'])
                    if not (obs.project_found and obs.codex_found):
                        continue
                    if action:
                        action(wid, obs, offset)
                    return obs
            except Exception as exc:
                self.log(f'Window {wid} could not be inspected: {exc}')
        self.log(f'Project {self.config["project"]!r} with Codex not found: no action')
        return None

    def send(self, reason='manual'):
        """Single verified send, never from a stale or unrecognized chat."""
        if self.session.cycles >= self.config['max_cycles']:
            self.log('Cycle limit reached')
            return False
        if time.monotonic() - self.session.last_sent < self.config['cooldown_seconds']:
            self.log('Cooldown: no send')
            return False
        success = False
        def perform(wid, obs, offset):
            nonlocal success
            if obs.state not in ('READY','FINISHED') or obs.composer is None:
                self.log(f'UI state {obs.state}: no send')
                return
            if reason != 'manual' and (obs.state != 'FINISHED' or self.session.stable < 2):
                self.log('Unstable cycle completion: no automatic send')
                return
            abs_xy = (offset[0]+obs.composer[0], offset[1]+obs.composer[1])
            x11.type_and_send(wid,abs_xy,self.config['prompt'])
            self.session.last_sent = time.monotonic()
            self.session.baseline_marker = obs.cycle_marker
            self.session.waiting_for_response = True
            self.session.stable = 0
            self.session.cycles += 1
            success = True
        self.inspect_target(perform)
        if success:
            self.log(f'Sent prompt #{self.session.cycles} ({reason})')
            if self.config['email_on_prompt']:
                self._notify('Prompt sent', f'Project: {self.config["project"]}\nCycle: {self.session.cycles}')
        return success

    def scroll(self):
        did_scroll = False
        def perform(wid,obs,offset):
            nonlocal did_scroll
            if obs.scroll_anchor:
                x11.scroll_down((offset[0]+obs.scroll_anchor[0],offset[1]+obs.scroll_anchor[1]),
                                self.config['scroll_steps'])
                did_scroll=True
        self.inspect_target(perform)
        return did_scroll

    def tick(self):
        if not self.enabled:
            return
        now = time.monotonic()
        if self.config['scroll_seconds'] and now-self.session.last_scroll >= self.config['scroll_seconds']:
            self.scroll()
            self.session.last_scroll = now
        obs = self.inspect_target()
        if obs is None:
            return
        fingerprint = hashlib.sha256((obs.cycle_marker+'\n'+obs.text[-1200:]).encode()).hexdigest()
        self.session.stable = self.session.stable+1 if self.session.previous == fingerprint else 1
        self.session.previous = fingerprint
        if obs.state == 'QUOTA':
            if not self.session.quota_seen:
                self.log('Codex quota: '+(obs.reset_text or 'reset time not readable'))
                self._notify('Codex quota reached',obs.reset_text or 'Quota; reset time not readable')
            self.session.quota_seen = True
            return
        if self.session.quota_seen:
            # Never auto-resume blindly when quota disappears: request manual confirmation.
            self.session.quota_seen = False
            self.pending_approval = True
            self.log('Quota disappeared. Awaiting manual approval.')
            return
        if obs.state == 'ERROR':
            self.enabled = False
            self.log('Codex UI error: automatic sends stopped')
            if self.config['email_on_error']:
                self._notify('Codex UI error','Automation stopped after UI error')
            return
        if obs.state != 'FINISHED' or self.session.stable < 2:
            return
        if self.session.waiting_for_response:
            # Prevent the old 'Edited files' card from triggering a duplicate send.
            if obs.cycle_marker == self.session.baseline_marker or now-self.session.last_sent < 25:
                return
            self.session.waiting_for_response = False
            self.log(f'Cycle {self.session.cycles} completed')
            self._notify('Cycle completed',f'Project: {self.config["project"]}\nCycle: {self.session.cycles}')
        if self.config['mode'] == 'approval' or self.pending_approval:
            self.pending_approval = True
            return
        if self.config['mode'] == 'auto':
            self.send('auto')

    def approve(self):
        self.pending_approval = False
        self.send('manual')
