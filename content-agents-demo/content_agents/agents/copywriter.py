"""Agent 4 - Copywriter: finished script -> platform-specific titles, captions
and hashtags, then hard-checked against each platform's limits."""

from __future__ import annotations

import re

from ..models import PLATFORMS, CopyPack, PlatformCopy, RunState
from .base import Agent

# Platform limits enforced after generation (Claude writes to the guidance;
# these rules guarantee nothing over-limit ever reaches the publisher).
LIMITS = {
    "instagram": {"title": 150, "text": 2200, "hashtags": 30, "label": "Instagram Reels"},
    "tiktok": {"title": 150, "text": 2200, "hashtags": 10, "label": "TikTok"},
    "youtube_shorts": {"title": 100, "text": 5000, "hashtags": 15, "label": "YouTube Shorts"},
}

SYSTEM = """You are the Copywriter agent in a short-form video pipeline. The video is already edited. Write the post copy for each platform.

Write one post for each of: instagram, tiktok, youtube_shorts.
- instagram: title = the hook line. caption opens with the hook, then 3 short scannable lines (use → bullets), then the CTA and a save/share prompt. 3-5 niche hashtags.
- tiktok: title = the hook line. caption is 1-2 punchy sentences, under 150 characters before hashtags. 3-5 hashtags.
- youtube_shorts: title under 70 characters, searchable, front-loads the keyword; no clickbait it can't back up. caption is a 2-4 sentence description with the key points. hashtags: "shorts" plus 1-2 topic tags.
Hashtags are words without the # symbol and without spaces. Match the creator's voice from the script; no emojis unless the script's tone calls for them."""


def compose(post: PlatformCopy) -> str:
    """The final text posted: caption + hashtags."""
    tags = " ".join(f"#{t}" for t in post.hashtags)
    return f"{post.caption}\n\n{tags}".strip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:") + "…"


def enforce_limits(pack: CopyPack) -> tuple[CopyPack, list[str]]:
    """Normalize hashtags and trim anything over a platform limit. Returns the
    fixed pack and a list of human-readable notes about what changed."""
    notes: list[str] = []
    posts: list[PlatformCopy] = []
    for platform in PLATFORMS:
        post = pack.for_platform(platform)
        rules = LIMITS[platform]
        if post is None:
            notes.append(f"{rules['label']}: no copy generated")
            continue
        tags: list[str] = []
        for raw in post.hashtags:
            tag = re.sub(r"[^\w]", "", raw.lstrip("#")).lower()
            if tag and tag not in tags:
                tags.append(tag)
        if len(tags) > rules["hashtags"]:
            notes.append(f"{rules['label']}: trimmed hashtags {len(tags)} → {rules['hashtags']}")
            tags = tags[: rules["hashtags"]]
        title = _truncate(post.title.strip(), rules["title"])
        if title != post.title.strip():
            notes.append(f"{rules['label']}: title shortened to {rules['title']} chars")
        tag_len = len(" ".join(f"#{t}" for t in tags)) + 2
        caption = _truncate(post.caption.strip(), rules["text"] - tag_len)
        if caption != post.caption.strip():
            notes.append(f"{rules['label']}: caption shortened to fit {rules['text']} chars")
        posts.append(PlatformCopy(platform=platform, title=title, caption=caption, hashtags=tags))
    return CopyPack(posts=posts), notes


class Copywriter(Agent):
    name = "copywriter"
    label = "Copywriter"

    def run(self, run: RunState) -> None:
        assert run.script and run.angle
        self.log(run, "Writing captions, titles and hashtags for Instagram, TikTok and YouTube Shorts")
        script_text = "\n".join(f"[{b.purpose}] {b.spoken}" for b in run.script.beats)
        prompt = f"Video title: {run.script.title}\nHook: {run.angle.hook}\nCTA: {run.angle.cta}\n\nScript:\n{script_text}"
        pack = self.ctx.llm.structured(
            agent=self.name, system=SYSTEM, prompt=prompt, schema=CopyPack,
            context={"angle": run.angle, "script": run.script}, usage=self.usage(run), effort="medium",
        )
        run.copy_pack, run.copy_issues = enforce_limits(pack)
        for note in run.copy_issues:
            self.log(run, f"Limit check: {note}", level="warn")
        self.log(run, f"Copy ready for {len(run.copy_pack.posts)} platforms; all within platform limits")
