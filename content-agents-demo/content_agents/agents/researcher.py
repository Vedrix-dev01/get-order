"""Agent 1 - Researcher: raw topic ideas + live trend signals -> ranked content
angles, saved to the content-angle library in the database."""

from __future__ import annotations

from ..models import ResearchOutput, RunState
from ..trends import gather_signals
from .base import Agent

SYSTEM = """You are the Research agent in a five-agent short-form video pipeline run for a marketing agency.
Your output feeds a scriptwriter that turns one angle into a 60-second talking-head video for Instagram Reels, TikTok and YouTube Shorts.

Turn the creator's raw topic ideas into specific, timely content angles:
- Ground every angle in the trend signals provided. Name the signals you used in source_signals, copying their titles.
- An angle is a specific take with a point of view, not a topic. "AI tools for marketing" is a topic; "3 AI tools that turn a 5-person team into a marketing department" is an angle.
- The hook is the first spoken line. It must create tension or curiosity in under 15 words and make sense with the sound off when shown as on-screen text.
- key_points are three concrete, spoken-language points, each deliverable in about 10 seconds.
- Score virality_score from 1 to 10 honestly: reserve 9-10 for angles that pair a strong trend signal with a sharp hook.
Return 3 to 5 angles, best first."""


class Researcher(Agent):
    name = "researcher"
    label = "Researcher"

    def run(self, run: RunState) -> None:
        self.log(run, f"Researching {len(run.topics)} topic idea(s): " + "; ".join(run.topics))
        run.signals = gather_signals(self.ctx.settings, log=lambda m: self.log(run, m))

        web_brief = None
        if self.ctx.llm.mode == "live" and self.ctx.settings.web_research:
            self.log(run, "Searching the web for what's trending right now…")
            web_brief = self.ctx.llm.web_research(
                system="You research short-form video trends for a marketing agency. Be specific and cite what you find.",
                prompt=(
                    "Find what is trending this week on TikTok, Instagram Reels and YouTube Shorts for these topics: "
                    + "; ".join(run.topics)
                    + ". Summarize the 6-10 strongest signals (formats, hooks, discussions) in a bulleted brief."
                ),
                usage=self.usage(run),
            )
            if web_brief:
                self.log(run, "Web trend brief collected")

        signal_lines = "\n".join(f"- [{s.source}] {s.title} ({s.metric})" for s in run.signals)
        prompt = f"""Raw topic ideas from the creator:
{chr(10).join(f'- {t}' for t in run.topics)}

Trend signals:
{signal_lines}
"""
        if web_brief:
            prompt += f"\nWeb research brief:\n{web_brief}\n"

        result = self.ctx.llm.structured(
            agent=self.name, system=SYSTEM, prompt=prompt, schema=ResearchOutput,
            context={"topics": run.topics, "signals": run.signals}, usage=self.usage(run), effort="high",
        )
        run.angles = sorted(result.angles, key=lambda a: a.virality_score, reverse=True)
        self.ctx.store.save_angles(run.id, run.angles)
        best = run.angles[0]
        self.log(run, f"Saved {len(run.angles)} angles to the database. Top pick ({best.virality_score}/10): “{best.title}”")
