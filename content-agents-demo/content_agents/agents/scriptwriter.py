"""Agent 2 - Scriptwriter: chosen angle -> 60-second script + shoot brief, then
alerts the creator to film it and drop the take in the Drive inbox folder."""

from __future__ import annotations

from ..models import RunState, VideoScript
from .base import Agent

SYSTEM = """You are the Scriptwriter agent in a short-form video pipeline. Write a 60-second talking-head script for Instagram Reels, TikTok and YouTube Shorts from the angle you are given.

Rules:
- 130 to 160 spoken words in total, conversational, written to be said out loud by one person to camera.
- Beat 1 (purpose "hook", 0-3s) is the hook line. No greeting, no intro, no "hey guys".
- Then a "problem" beat, three "value" beats (one per key point), optionally a "proof" beat, and a final "cta" beat.
- start_s/end_s are whole seconds, contiguous, ending at or before 60.
- Write in short sentences: the editor cuts on the pauses between them.
- on_screen_text is at most 6 words. b_roll is a concrete shot suggestion.
- filming_notes: 3-5 practical tips for filming this specific script on a phone."""


class Scriptwriter(Agent):
    name = "scriptwriter"
    label = "Scriptwriter"

    def run(self, run: RunState) -> None:
        angle = run.angle
        assert angle is not None
        self.log(run, f"Writing a 60-second script for “{angle.title}”")
        prompt = (
            f"Angle: {angle.title}\nHook: {angle.hook}\nTake: {angle.angle}\nWhy now: {angle.why_now}\n"
            "Key points:\n" + "\n".join(f"- {p}" for p in angle.key_points) + f"\nCTA: {angle.cta}"
        )
        script = self.ctx.llm.structured(
            agent=self.name, system=SYSTEM, prompt=prompt, schema=VideoScript,
            context={"angle": angle}, usage=self.usage(run), effort="medium",
        )
        run.script = script
        brief = self.ctx.settings.run_dir(run.id) / "shoot_brief.md"
        brief.write_text(self.shoot_brief(run, script), encoding="utf-8")
        run.shoot_brief_file = brief.name
        self.log(run, f"Script ready: {len(script.beats)} beats, {script.word_count} words (~{script.est_duration_s}s)")
        self.ctx.notifier.send(
            run.id, self.name,
            f"Script ready to film: “{script.title}”. Record it on your phone and drop the video in "
            f"your Drive folder ({self.ctx.settings.inbox_dir.name}). Name it run-{run.id}.mp4 or anything else.",
        )

    def shoot_brief(self, run: RunState, script: VideoScript) -> str:
        rows = "\n".join(
            f"| {b.start_s}-{b.end_s}s | {b.purpose} | {b.spoken} | {b.on_screen_text} | {b.b_roll} |"
            for b in script.beats
        )
        notes = "\n".join(f"- {n}" for n in script.filming_notes)
        return f"""# Shoot brief: {script.title}

Run `{run.id}` · {script.word_count} words · ~{script.est_duration_s}s

| Time | Beat | Say | On screen | B-roll |
|---|---|---|---|---|
{rows}

## Filming notes
{notes}

## When you're done
Drop the clip into the Drive inbox folder (`{self.ctx.settings.inbox_dir}`).
The editor agent picks it up automatically.
"""
