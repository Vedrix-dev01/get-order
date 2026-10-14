"""Data contracts passed between the five agents.

Every hand-off is a typed object, so each agent can be tested, swapped or
re-run on its own, and the Claude-backed agents get schema-validated output.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

Platform = Literal["instagram", "tiktok", "youtube_shorts"]
PLATFORMS: tuple[Platform, ...] = ("instagram", "tiktok", "youtube_shorts")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- Agent 1: Researcher -----------------------------------------------------


class TrendSignal(BaseModel):
    source: str
    title: str
    metric: str = ""
    url: str | None = None


class ContentAngle(BaseModel):
    title: str = Field(description="Working title for the video")
    hook: str = Field(description="First spoken line, under 15 words, written to stop the scroll")
    angle: str = Field(description="The specific take or argument the video makes")
    why_now: str = Field(description="Which trend signal makes this timely")
    key_points: list[str] = Field(description="Three concrete value points the video delivers")
    cta: str = Field(description="Call to action for the end of the video")
    target_platforms: list[Platform]
    virality_score: int = Field(description="1-10 estimate of breakout potential")
    source_signals: list[str] = Field(description="Titles of the trend signals this angle draws on")


class ResearchOutput(BaseModel):
    angles: list[ContentAngle]


# --- Agent 2: Scriptwriter ---------------------------------------------------


class ScriptBeat(BaseModel):
    start_s: int
    end_s: int
    purpose: Literal["hook", "problem", "value", "proof", "cta"]
    spoken: str
    on_screen_text: str
    b_roll: str


class VideoScript(BaseModel):
    title: str
    beats: list[ScriptBeat]
    filming_notes: list[str]

    @property
    def full_text(self) -> str:
        return " ".join(b.spoken.strip() for b in self.beats)

    @property
    def sentences(self) -> list[str]:
        parts = re.split(r"(?<=[.!?])\s+", self.full_text)
        return [p.strip() for p in parts if p.strip()]

    @property
    def word_count(self) -> int:
        return len(self.full_text.split())

    @property
    def est_duration_s(self) -> int:
        # ~2.6 spoken words/second is a typical short-form delivery pace.
        return round(self.word_count / 2.6)


# --- Agent 3: Video editor ---------------------------------------------------


class EditResult(BaseModel):
    source_file: str
    output_file: str
    thumbnail_file: str
    captions_file: str
    input_duration_s: float
    output_duration_s: float
    silence_removed_s: float
    cuts: int
    resolution: str
    caption_source: Literal["whisper", "script-aligned"]


# --- Agent 4: Copywriter -----------------------------------------------------


class PlatformCopy(BaseModel):
    platform: Platform
    title: str = Field(description="YouTube title, or the short headline for IG/TikTok")
    caption: str = Field(description="Post caption / description body, without hashtags")
    hashtags: list[str] = Field(description="Hashtags without the # symbol")


class CopyPack(BaseModel):
    posts: list[PlatformCopy]

    def for_platform(self, platform: Platform) -> PlatformCopy | None:
        return next((p for p in self.posts if p.platform == platform), None)


# --- Agent 5: Publisher ------------------------------------------------------


class ApiRequest(BaseModel):
    """The exact call the publisher makes (or would make, in dry-run)."""

    step: str
    method: str
    url: str
    body: dict


class DraftRecord(BaseModel):
    platform: Platform
    status: Literal["queued_as_draft", "published", "skipped", "failed"]
    dry_run: bool
    draft_mechanism: str
    requests: list[ApiRequest]
    publish_requests: list[ApiRequest]
    remote_id: str | None = None
    updated_at: str = Field(default_factory=now_iso)


# --- Run state ---------------------------------------------------------------

RunStatus = Literal[
    "researching",
    "awaiting_angle_approval",
    "scripting",
    "awaiting_footage",
    "editing",
    "writing_copy",
    "queuing_drafts",
    "awaiting_final_approval",
    "published",
    "failed",
]


class AgentUsage(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0


class RunState(BaseModel):
    id: str
    created_at: str = Field(default_factory=now_iso)
    topics: list[str]
    auto_approve: bool = False
    llm_mode: str = "demo"
    status: RunStatus = "researching"
    error: str | None = None

    signals: list[TrendSignal] = []
    angles: list[ContentAngle] = []
    selected_angle: int | None = None
    script: VideoScript | None = None
    shoot_brief_file: str | None = None
    footage_file: str | None = None
    edit: EditResult | None = None
    copy_pack: CopyPack | None = None
    copy_issues: list[str] = []
    drafts: list[DraftRecord] = []
    usage: dict[str, AgentUsage] = {}

    @property
    def angle(self) -> ContentAngle | None:
        if self.selected_angle is None:
            return None
        return self.angles[self.selected_angle]
