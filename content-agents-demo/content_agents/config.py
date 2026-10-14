"""Runtime settings, read from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _has_claude_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


@dataclass
class Settings:
    workspace: Path = field(default_factory=lambda: Path(os.environ.get("CA_WORKSPACE", PROJECT_DIR / "workspace")))
    # "live" calls Claude; "demo" uses canned agent outputs so the pipeline runs offline.
    llm_mode: str = field(
        default_factory=lambda: os.environ.get("CA_LLM_MODE") or ("live" if _has_claude_credentials() else "demo")
    )
    model: str = field(default_factory=lambda: os.environ.get("CA_MODEL", "claude-opus-5-5"))
    # Researcher uses Claude's web search tool to pull live trends (live mode only).
    web_research: bool = field(default_factory=lambda: _bool("CA_WEB_RESEARCH", True))
    # Pull public trend feeds (Reddit, Google Trends RSS). Off -> bundled sample feed.
    live_trend_feeds: bool = field(default_factory=lambda: _bool("CA_LIVE_TRENDS", False))
    subreddits: list[str] = field(
        default_factory=lambda: os.environ.get("CA_SUBREDDITS", "marketing,socialmedia,Entrepreneur").split(",")
    )
    # Folder the video editor watches. Point it at a Google Drive for Desktop
    # folder (e.g. ~/Google Drive/My Drive/Content Inbox) and phone uploads land here.
    inbox_dir: Path | None = field(
        default_factory=lambda: Path(os.environ["CA_INBOX_DIR"]) if os.environ.get("CA_INBOX_DIR") else None
    )
    slack_webhook_url: str | None = field(default_factory=lambda: os.environ.get("SLACK_WEBHOOK_URL") or None)
    # Seconds of artificial delay per demo-mode agent call, so the dashboard animates.
    demo_latency_s: float = field(default_factory=lambda: float(os.environ.get("CA_DEMO_LATENCY", "1.2")))
    silence_threshold_db: float = -32.0
    min_silence_s: float = 0.55

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace)
        if self.inbox_dir is None:
            self.inbox_dir = self.workspace / "drive_inbox"
        for d in (self.workspace, self.inbox_dir, self.runs_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def runs_dir(self) -> Path:
        return self.workspace / "runs"

    @property
    def db_path(self) -> Path:
        return self.workspace / "content.db"

    def run_dir(self, run_id: str) -> Path:
        path = self.runs_dir / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path
