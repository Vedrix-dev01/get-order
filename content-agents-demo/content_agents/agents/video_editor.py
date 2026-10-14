"""Agent 3 - Video editor: raw phone take -> vertical, jump-cut, captioned
video ready for Reels/TikTok/Shorts. Pure ffmpeg; no LLM call needed."""

from __future__ import annotations

from pathlib import Path

from .. import media
from ..models import EditResult, RunState
from .base import Agent


class VideoEditor(Agent):
    name = "video_editor"
    label = "Video Editor"

    def run(self, run: RunState) -> None:
        settings = self.ctx.settings
        run_dir = settings.run_dir(run.id)
        src = run_dir / run.footage_file
        info = media.probe(src)
        self.log(run, f"Picked up {src.name}: {info['width']}x{info['height']}, {info['duration']:.1f}s")

        if info["has_audio"]:
            silences = media.detect_silences(src, settings.silence_threshold_db, settings.min_silence_s,
                                             info["duration"])
            segments = media.speech_segments(info["duration"], silences)
        else:
            segments = [(0.0, info["duration"])]
        kept = sum(b - a for a, b in segments)
        self.log(run, f"Found {len(segments)} speech runs; cutting {info['duration'] - kept:.1f}s of silence")

        caption_source = "script-aligned"
        words = media.transcribe_words(src) if info["has_audio"] else None
        if words:
            caption_source = "whisper"
            cards = media.cards_from_words(words, segments)
        else:
            sentences = run.script.sentences if run.script else []
            cards = media.chunk_cards(media.align_sentences(sentences, segments))
        hook = run.angle.hook if run.angle else None
        ass_file = run_dir / "captions.ass"
        media.write_ass(cards, hook, ass_file)
        self.log(run, f"Built {len(cards)} caption cards ({caption_source}) + hook banner")

        out = run_dir / "final_vertical.mp4"
        self.log(run, "Rendering 1080x1920: jump cuts, center crop, burned captions, loudness -14 LUFS…")
        renderer = media.render_vertical(src, segments, ass_file, out, info["has_audio"], cards, hook)
        self.log(run, f"Captions burned in with {renderer}")
        thumb = run_dir / "thumbnail.jpg"
        media.thumbnail(out, thumb, at=min(1.0, kept / 2))
        final = media.probe(out)

        run.edit = EditResult(
            source_file=src.name,
            output_file=out.name,
            thumbnail_file=thumb.name,
            captions_file=ass_file.name,
            input_duration_s=round(info["duration"], 2),
            output_duration_s=round(final["duration"], 2),
            silence_removed_s=round(info["duration"] - final["duration"], 2),
            cuts=max(0, len(segments) - 1),
            resolution=f"{final['width']}x{final['height']}",
            caption_source=caption_source,
        )
        self.log(run, f"Edit done: {run.edit.input_duration_s}s → {run.edit.output_duration_s}s, {run.edit.cuts} jump cuts")


def claim_footage(run_dir: Path, incoming: Path) -> str:
    """Move a dropped clip from the inbox into the run folder."""
    target = run_dir / f"raw{incoming.suffix.lower() or '.mp4'}"
    incoming.replace(target)
    return target.name
