"""Offline stand-in for Claude, so the whole pipeline can be demoed without an
API key. It returns the same typed objects the live agents get from Claude,
built from a small bank of hand-written angles (with a generic fallback for
any other topic). Set ANTHROPIC_API_KEY to switch every agent to live Claude.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .models import (
    AgentUsage,
    ContentAngle,
    CopyPack,
    PlatformCopy,
    ResearchOutput,
    ScriptBeat,
    TrendSignal,
    VideoScript,
)

BANK: list[dict[str, Any]] = [
    {
        "match": ["ai tool", "ai", "automation", "agent"],
        "title": "3 AI tools that turn a 5-person team into a marketing department",
        "hook": "You don't need a bigger team. You need these three AI tools.",
        "angle": "Most small businesses think marketing needs more people. It doesn't. Three cheap AI workflows cover research, writing and editing, so one person can ship content every day.",
        "why_now": "'AI ran my marketing' case studies are the top-performing format in small-business threads this week",
        "key_points": [
            "First, a research agent. It scans what's trending in your niche every morning and hands you three video ideas with the hooks already written.",
            "Second, a script assistant. Give it one idea and it drafts a sixty second script in your voice, so all you do is press record.",
            "Third, an auto editor. It cuts the dead air, crops to vertical and adds captions while you get on with your day.",
        ],
        "bullets": ["A research agent that finds trending ideas daily", "A script assistant that writes in your voice", "An auto-editor that cuts, crops and captions"],
        "proof": "One client of ours went from posting twice a month to five times a week. Same team. Same budget.",
        "cta": "Follow for the full setup, and comment AGENT if you want the template.",
        "score": 8,
        "tags": ["aitools", "smallbusiness", "marketingtips", "contentcreation", "automation"],
    },
    {
        "match": ["ai tool", "ai", "automation", "agent"],
        "title": "I let AI run my content for 7 days. Here's what actually worked",
        "hook": "I handed my content to AI for a week. Two things worked. One flopped hard.",
        "angle": "A transparent week-long experiment builds more trust than a tool list, and the flop is the part people share.",
        "why_now": "Experiment-style 'I tried X for 7 days' videos are spiking across Shorts and Reels",
        "key_points": [
            "What worked: trend research. The AI found angles I would never have thought of, and two of them beat my average views.",
            "What also worked: auto captions and silence cutting. My edit time dropped from an hour to about five minutes.",
            "What flopped: fully AI-written captions with no edits. They sounded generic, and engagement fell straight away.",
        ],
        "bullets": ["Trend research: two videos beat my average", "Auto-editing: one hour down to five minutes", "Unedited AI captions: engagement dropped"],
        "proof": "So the lesson is simple. Let AI do the heavy lifting, but keep your own voice on everything people read.",
        "cta": "Follow for week two, where I fix the flop.",
        "score": 7,
        "tags": ["aiexperiment", "contentstrategy", "creatortips", "socialmediamarketing"],
    },
    {
        "match": ["reel", "flop", "first 3", "hook", "views"],
        "title": "Your Reel lost them in 1.7 seconds. Here's the fix",
        "hook": "If your Reels die at two hundred views, it's the first second. Not the algorithm.",
        "angle": "Retention in the opening second decides distribution, and three edits to the opening fix most low-view Reels.",
        "why_now": "Creators are sharing retention graphs showing the steepest drop happens before second two",
        "key_points": [
            "Fix one. Start mid-sentence. Cut every hello, every intro, every breath before your first real word.",
            "Fix two. Put the hook on screen in big text, so people watching on mute still get the point.",
            "Fix three. Cut every pause longer than half a second. Dead air is where people swipe away.",
        ],
        "bullets": ["Start mid-sentence: no hellos, no intro", "Put the hook on screen for mute viewers", "Cut every pause over half a second"],
        "proof": "We made these three changes on one account, and the average watch time doubled in two weeks.",
        "cta": "Save this and check your last three Reels against it.",
        "score": 9,
        "tags": ["reelstips", "instagramgrowth", "contenttips", "videoediting", "socialmediatips"],
    },
    {
        "match": ["reel", "flop", "first 3", "hook", "views", "audio"],
        "title": "Stop chasing trending audio. Do this instead",
        "hook": "Trending audio isn't growing your account. Here's what is.",
        "angle": "For business accounts, a clear spoken point beats a borrowed sound, because trend audio brings views but not buyers.",
        "why_now": "Marketers are debating whether trend audio still moves reach for brand accounts",
        "key_points": [
            "Trend audio gets you views from people who will never buy from you.",
            "A spoken, specific point attracts the exact customer who has that problem.",
            "So use trend audio for reach once a week, and talk to your buyer the other six days.",
        ],
        "bullets": ["Trend audio: views, not buyers", "Spoken specifics attract real customers", "Mix it: one trend post, six buyer posts"],
        "proof": "Our best client post last month had no music at all. It booked eleven sales calls.",
        "cta": "Follow for more content that actually sells.",
        "score": 6,
        "tags": ["instagramtips", "contentmarketing", "smallbusinesstips", "reels"],
    },
    {
        "match": ["ugc", "polished", "brand video", "ads"],
        "title": "Why a $0 phone video beat our $5,000 ad",
        "hook": "Our five thousand dollar ad lost to a video shot on a phone in ten minutes.",
        "angle": "Raw creator-style video outperforms polished ads on short-form platforms because it looks like content, not an interruption.",
        "why_now": "UGC-style ads are the most-discussed creative trend in performance marketing this month",
        "key_points": [
            "Polished ads look like ads, and people have trained themselves to swipe past them.",
            "A phone video looks like a post from a friend, so people give it the three seconds it needs.",
            "The winning formula is simple: a real face, a real problem, and one clear result.",
        ],
        "bullets": ["Polished ads get swiped on sight", "Phone video earns the first three seconds", "Real face + real problem + one result"],
        "proof": "Our phone video cost nothing, and it brought in leads at a third of the price of the polished ad.",
        "cta": "Comment UGC and I'll send you the script we used.",
        "score": 8,
        "tags": ["ugc", "ugccreator", "facebookads", "marketingstrategy", "adcreative"],
    },
]


def _generic(topic: str) -> dict[str, Any]:
    topic_clean = topic.strip().rstrip(".")
    words = [w for w in re.findall(r"[a-zA-Z]+", topic_clean.lower()) if len(w) > 3][:3]
    return {
        "title": f"The {topic_clean} mistake almost everyone makes",
        "hook": f"Everyone gets {topic_clean} wrong in the same way. Here's the fix.",
        "angle": f"Most advice on {topic_clean} is generic. One specific, repeatable fix gets better results than ten tips.",
        "why_now": f"Conversation around {topic_clean} is picking up in the trend feeds this week",
        "key_points": [
            "Step one. Pick one outcome you want, and ignore every metric that doesn't move it.",
            "Step two. Find the one thing your best results have in common, and do more of exactly that.",
            "Step three. Cut everything else for thirty days, then measure again.",
        ],
        "bullets": ["Pick one outcome", "Double down on what already works", "Cut the rest for 30 days"],
        "proof": "Most people see a difference within the first two weeks of doing this.",
        "cta": "Follow for part two.",
        "score": 6,
        "tags": [*words, "marketingtips", "contentcreator"],
    }


def _bank_for_title(title: str) -> dict[str, Any] | None:
    return next((b for b in BANK if b["title"] == title), None)


def _entries_for_topics(topics: list[str]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for topic in topics:
        t = topic.lower()
        matched = [b for b in BANK if any(re.search(rf"\b{re.escape(m.strip())}s?\b", t) for m in b["match"])]
        for entry in matched or [_generic(topic)]:
            if entry not in entries:
                entries.append(entry)
    return entries


def _signals_for(entry: dict[str, Any], signals: list[TrendSignal]) -> list[str]:
    keys = [m.strip() for m in entry.get("match", [])] + entry["tags"]
    picked = [s.title for s in signals if any(k and k in s.title.lower() for k in keys)]
    return (picked or [s.title for s in signals])[:2]


class DemoLLM:
    mode = "demo"

    def __init__(self, latency_s: float = 1.2) -> None:
        self.latency_s = latency_s

    def web_research(self, *, system: str, prompt: str, usage: AgentUsage) -> str | None:
        return None

    def structured(self, *, agent, system, prompt, schema, context, usage, effort="medium"):
        started = time.monotonic()
        time.sleep(self.latency_s)
        if schema is ResearchOutput:
            result = self._research(context)
        elif schema is VideoScript:
            result = self._script(context["angle"])
        elif schema is CopyPack:
            result = self._copy(context["angle"], context["script"])
        else:  # pragma: no cover - only the three schemas above are used
            raise TypeError(f"DemoLLM has no canned output for {schema.__name__}")
        usage.calls += 1
        usage.seconds += time.monotonic() - started
        return result

    def _research(self, context: dict[str, Any]) -> ResearchOutput:
        signals: list[TrendSignal] = context["signals"]
        angles = [
            ContentAngle(
                title=e["title"],
                hook=e["hook"],
                angle=e["angle"],
                why_now=e["why_now"],
                key_points=e["key_points"],
                cta=e["cta"],
                target_platforms=["instagram", "tiktok", "youtube_shorts"],
                virality_score=e["score"],
                source_signals=_signals_for(e, signals),
            )
            for e in _entries_for_topics(context["topics"])
        ]
        angles.sort(key=lambda a: a.virality_score, reverse=True)
        return ResearchOutput(angles=angles[:5])

    def _script(self, angle: ContentAngle) -> VideoScript:
        entry = _bank_for_title(angle.title) or {"bullets": angle.key_points, "proof": None}
        plan = [("hook", angle.hook, _short(angle.hook), "Tight face shot, start talking on frame 1"),
                ("problem", angle.angle, _short(angle.title), "Scrolling feed on phone, fast swipe")]
        plan += [("value", p, b, "Screen recording or hands-on demo")
                 for p, b in zip(angle.key_points, entry["bullets"])]
        if entry.get("proof"):
            plan.append(("proof", entry["proof"], "The result", "Results screenshot or analytics graph"))
        plan.append(("cta", angle.cta, _short(angle.cta), "Back to face, point at follow button"))
        beats, t = [], 0.0
        for purpose, spoken, on_screen, b_roll in plan:
            end = t + len(spoken.split()) / 2.6  # spoken pace, words per second
            beats.append(ScriptBeat(start_s=round(t), end_s=max(round(end), round(t) + 1), purpose=purpose,
                                    spoken=spoken, on_screen_text=on_screen, b_roll=b_roll))
            t = end
        return VideoScript(
            title=angle.title,
            beats=beats,
            filming_notes=[
                "Film vertical or horizontal, either works: the editor crops to 9:16 automatically.",
                "Don't worry about pauses or restarts. Silences are cut automatically.",
                "Face a window for light; hold the phone at eye level.",
                "Say each beat as one take; leave a breath between beats.",
            ],
        )

    def _copy(self, angle: ContentAngle, script: VideoScript) -> CopyPack:
        entry = _bank_for_title(angle.title) or _generic(angle.title)
        bullets = "\n".join(f"→ {b}" for b in entry["bullets"])
        tags = entry["tags"]
        return CopyPack(posts=[
            PlatformCopy(platform="instagram", title=angle.hook,
                         caption=f"{angle.hook}\n\n{bullets}\n\n{angle.cta}\n\nSave this for your next post.",
                         hashtags=tags[:5]),
            PlatformCopy(platform="tiktok", title=angle.hook,
                         caption=f"{angle.hook} {angle.cta}",
                         hashtags=tags[:4]),
            PlatformCopy(platform="youtube_shorts", title=angle.title[:95],
                         caption=f"{angle.angle}\n\n{bullets}\n\n{angle.cta}",
                         hashtags=["shorts", *tags[:2]]),
        ])


def _short(text: str, words: int = 6) -> str:
    parts = text.rstrip(".!?").split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")
