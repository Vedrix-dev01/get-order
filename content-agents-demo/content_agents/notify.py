"""Alerts to the creator (e.g. 'your script is ready, go film').

Always written to the run's activity log; also posted to Slack when
SLACK_WEBHOOK_URL is set. Email/SMS would plug in here the same way.
"""

from __future__ import annotations

import json
import urllib.request

from .config import Settings
from .store import Store


class Notifier:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store

    def send(self, run_id: str, agent: str, text: str) -> None:
        self.store.log(run_id, agent, f"🔔 {text}", level="alert")
        if not self.settings.slack_webhook_url:
            return
        try:
            req = urllib.request.Request(
                self.settings.slack_webhook_url,
                data=json.dumps({"text": text}).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=8).close()
        except Exception as exc:
            self.store.log(run_id, agent, f"Slack alert failed: {exc}", level="warn")
