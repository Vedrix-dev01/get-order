"""ffmpeg-based video tooling for the editor agent.

Pipeline for one raw clip:
  1. detect silences          (ffmpeg silencedetect)
  2. keep only speech         (trim + concat = jump cuts)
  3. crop to 9:16, 1080x1920  (scale + center crop)
  4. burn captions            (styled ASS subtitles via libass, or Pillow-drawn
                               overlays on ffmpeg builds without libass)
  5. normalize loudness       (loudnorm to -14 LUFS, the short-form standard)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

Segment = tuple[float, float]

OUT_W, OUT_H = 1080, 1920
CAPTION_FONT = os.environ.get("CA_CAPTION_FONT", "Inter Black")


class MediaError(RuntimeError):
    pass


def ffmpeg(args: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", *args],
        cwd=cwd, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise MediaError(f"ffmpeg failed:\n{tail}")
    return proc.stderr


@lru_cache(maxsize=1)
def available_filters() -> frozenset[str]:
    """Filters this ffmpeg build has. Slim builds (e.g. current Homebrew) lack
    libass (ass), freetype (drawtext) and flite, so those steps have fallbacks.
    CA_DISABLE_FILTERS=ass,flite simulates a slim build."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    names = set(re.findall(r"^\s*[A-Z.|]{2,4}\s+(\w+)\s+\S+->\S+", proc.stdout, re.MULTILINE))
    disabled = {f.strip() for f in os.environ.get("CA_DISABLE_FILTERS", "").split(",") if f.strip()}
    return frozenset(names - disabled)


def probe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffprobe could not read {path.name}: {proc.stderr.strip()}")
    import json

    info = json.loads(proc.stdout)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError(f"{path.name} has no video stream")
    return {
        "duration": float(info["format"]["duration"]),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


# --- Silence detection & cut list -------------------------------------------


def parse_silencedetect(stderr: str, duration: float) -> list[Segment]:
    silences: list[Segment] = []
    start: float | None = None
    for line in stderr.splitlines():
        if m := re.search(r"silence_start: (-?[\d.]+)", line):
            start = max(0.0, float(m.group(1)))
        elif (m := re.search(r"silence_end: ([\d.]+)", line)) and start is not None:
            silences.append((start, float(m.group(1))))
            start = None
    if start is not None:  # clip ended while silent
        silences.append((start, duration))
    return silences


def detect_silences(path: Path, threshold_db: float, min_silence_s: float, duration: float) -> list[Segment]:
    stderr = ffmpeg(["-i", str(path), "-af", f"silencedetect=noise={threshold_db}dB:d={min_silence_s}",
                     "-vn", "-f", "null", "-"])
    return parse_silencedetect(stderr, duration)


def speech_segments(duration: float, silences: list[Segment], pad: float = 0.12,
                    min_keep: float = 0.2) -> list[Segment]:
    """Complement of the silences, padded so words aren't clipped."""
    segments: list[Segment] = []
    cursor = 0.0
    for s_start, s_end in sorted(silences):
        if s_start > cursor:
            segments.append((cursor, s_start))
        cursor = max(cursor, s_end)
    if cursor < duration:
        segments.append((cursor, duration))

    padded = [(max(0.0, a - pad), min(duration, b + pad)) for a, b in segments]
    merged: list[Segment] = []
    for a, b in padded:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    kept = [(round(a, 3), round(b, 3)) for a, b in merged if b - a >= min_keep]
    return kept or [(0.0, duration)]


def map_time(t: float, segments: list[Segment]) -> float | None:
    """Map a time in the raw clip onto the cut (output) timeline."""
    offset = 0.0
    for a, b in segments:
        if a <= t <= b:
            return offset + (t - a)
        offset += b - a
    return None


# --- Captions ----------------------------------------------------------------


@dataclass
class Card:
    start: float
    end: float
    text: str


def align_sentences(sentences: list[str], segments: list[Segment]) -> list[Card]:
    """Place script sentences on the output timeline.

    When the take has one speech run per sentence (the usual case: creators
    pause between lines) each sentence gets its own segment. Otherwise the
    script is spread across total speech time in proportion to word count.
    """
    durations = [b - a for a, b in segments]
    out: list[Card] = []
    if len(sentences) == len(segments):
        offset = 0.0
        for sentence, d in zip(sentences, durations):
            out.append(Card(offset, offset + d, sentence))
            offset += d
        return out
    total = sum(durations)
    weights = [max(1, len(s.split())) for s in sentences]
    t = 0.0
    for sentence, w in zip(sentences, weights):
        span = total * w / sum(weights)
        out.append(Card(t, t + span, sentence))
        t += span
    return out


def chunk_cards(lines: list[Card], max_words: int = 3) -> list[Card]:
    """Split each line into short, punchy caption cards (short-form style)."""
    cards: list[Card] = []
    for line in lines:
        words = line.text.split()
        if not words:
            continue
        groups: list[list[str]] = [[]]
        for word in words:
            groups[-1].append(word)
            if len(groups[-1]) >= max_words or re.search(r"[.,!?;:]$", word):
                groups.append([])
        groups = [g for g in groups if g]
        weights = [sum(len(w) for w in g) + 2 for g in groups]
        t = line.start
        for group, weight in zip(groups, weights):
            span = (line.end - line.start) * weight / sum(weights)
            cards.append(Card(round(t, 3), round(t + span, 3), " ".join(group)))
            t += span
    return cards


def cards_from_words(words: list[tuple[float, float, str]], segments: list[Segment],
                     max_words: int = 3) -> list[Card]:
    """Caption cards from word timestamps (Whisper), mapped onto the cut timeline."""
    mapped = []
    for start, end, word in words:
        s, e = map_time(start, segments), map_time(end, segments)
        if s is not None and e is not None and word:
            mapped.append((s, e, word))
    cards: list[Card] = []
    for i in range(0, len(mapped), max_words):
        group = mapped[i:i + max_words]
        cards.append(Card(group[0][0], group[-1][1], " ".join(w for _, _, w in group)))
    return cards


def _ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _highlight(text: str) -> str:
    """Color the most important-looking word yellow, TikTok-caption style."""
    words = text.split()
    key = max(range(len(words)), key=lambda i: len(re.sub(r"\W", "", words[i])))
    words[key] = r"{\c&H00D7FF&}" + words[key] + r"{\c&HFFFFFF&}"
    return " ".join(words)


def write_ass(cards: list[Card], hook: str | None, path: Path, hook_seconds: float = 3.0) -> None:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {OUT_W}
PlayResY: {OUT_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{CAPTION_FONT},96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,3,2,80,80,560,1
Style: Hook,{CAPTION_FONT},68,&H00141414,&H00141414,&H0000D7FF,&H0000D7FF,-1,0,0,0,100,100,0,0,3,22,0,8,110,110,250,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if hook:
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(hook_seconds)},Hook,,0,0,0,,"
                     r"{\fad(120,200)}" + _ass_escape(hook))
    pop = r"{\fscx82\fscy82\t(0,90,\fscx100\fscy100)}"
    for card in cards:
        text = _highlight(_ass_escape(card.text.upper()))
        lines.append(f"Dialogue: 0,{_ass_time(card.start)},{_ass_time(card.end)},Caption,,0,0,0,,{pop}{text}")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def transcribe_words(path: Path) -> list[tuple[float, float, str]] | None:
    """Word-level timestamps via faster-whisper, if installed (optional extra)."""
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError:
        return None
    model = WhisperModel(os.environ.get("CA_WHISPER_MODEL", "base"), compute_type="int8")
    segments, _ = model.transcribe(str(path), word_timestamps=True)
    return [(w.start, w.end, w.word.strip()) for s in segments for w in (s.words or [])]


# --- Render ------------------------------------------------------------------


def render_vertical(src: Path, segments: list[Segment], ass_file: Path, out: Path, has_audio: bool,
                    cards: list[Card], hook: str | None) -> str:
    """Jump-cut to the speech segments, crop to 9:16, burn captions, normalize
    audio. Returns the caption renderer used ("libass" or "pillow")."""
    if "ass" in available_filters():
        try:
            _render(src, segments, ass_file, out, has_audio, cards, hook, use_ass=True)
            return "libass"
        except MediaError:
            pass  # filter listed but unusable on this build: fall back below
    _render(src, segments, ass_file, out, has_audio, cards, hook, use_ass=False)
    return "pillow"


def _render(src: Path, segments: list[Segment], ass_file: Path, out: Path, has_audio: bool,
            cards: list[Card], hook: str | None, use_ass: bool) -> None:
    inputs = ["-i", str(src.resolve())]
    if not use_ass:
        playlist = render_caption_frames(cards, hook, out.parent / "_captions")
        inputs += ["-f", "concat", "-safe", "0", "-i", str(playlist.resolve())]
    parts, labels = [], []
    for i, (a, b) in enumerate(segments):
        parts.append(f"[0:v]trim=start={a}:end={b},setpts=PTS-STARTPTS[v{i}]")
        labels.append(f"[v{i}]")
        if has_audio:
            parts.append(f"[0:a]atrim=start={a}:end={b},asetpts=PTS-STARTPTS[a{i}]")
            labels.append(f"[a{i}]")
    n = len(segments)
    if has_audio:
        parts.append(f"{''.join(labels)}concat=n={n}:v=1:a=1[vc][ac]")
        parts.append("[ac]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")
    else:
        parts.append(f"{''.join(labels)}concat=n={n}:v=1:a=0[vc]")
    frame = f"[vc]fps=30,scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},setsar=1"
    if use_ass:
        parts.append(f"{frame},ass={ass_file.name}[v]")
    else:
        parts.append(f"{frame}[base]")
        parts.append("[base][1:v]overlay=0:0:format=auto:eof_action=pass[v]")
    graph = ";".join(parts)
    # Kept for debugging; passed inline because ffmpeg 8+ removed -filter_complex_script.
    (out.parent / "edit.filtergraph").write_text(graph.replace(";", ";\n"), encoding="utf-8")
    maps = ["-map", "[v]"] + (["-map", "[a]", "-c:a", "aac", "-b:a", "160k"] if has_audio else ["-an"])
    ffmpeg([*inputs, "-filter_complex", graph, *maps,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", out.name], cwd=out.parent)


FONT_CANDIDATES = [
    os.environ.get("CA_CAPTION_FONT_FILE", ""),
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",  # macOS
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/ariblk.ttf",
    "/usr/share/fonts/opentype/inter/Inter-Black.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size: int):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _wrap(draw, words: list[str], font, max_w: int) -> list[list[str]]:
    lines: list[list[str]] = [[]]
    for word in words:
        trial = " ".join(lines[-1] + [word])
        if lines[-1] and draw.textlength(trial, font=font) > max_w:
            lines.append([word])
        else:
            lines[-1].append(word)
    return lines


def _draw_frame(card: Card | None, hook: str | None, path: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if hook:
        font = _font(68)
        y = 250
        for line in _wrap(draw, hook.split(), font, OUT_W - 260):
            text = " ".join(line)
            w = draw.textlength(text, font=font)
            x = (OUT_W - w) / 2
            draw.rectangle((x - 22, y - 14, x + w + 22, y + 82), fill=(255, 215, 0, 255))
            draw.text((x, y), text, font=font, fill=(20, 20, 20, 255))
            y += 92
    if card:
        font = _font(96)
        words = card.text.upper().split()
        key = max(range(len(words)), key=lambda i: len(re.sub(r"\W", "", words[i])))
        lines = _wrap(draw, words, font, OUT_W - 160)
        y = OUT_H - 560 - 112 * len(lines)
        index = 0
        for line in lines:
            space = draw.textlength(" ", font=font)
            x = (OUT_W - draw.textlength(" ".join(line), font=font)) / 2
            for word in line:
                color = (255, 215, 0, 255) if index == key else (255, 255, 255, 255)
                draw.text((x, y), word, font=font, fill=color, stroke_width=7, stroke_fill=(0, 0, 0, 255))
                x += draw.textlength(word, font=font) + space
                index += 1
            y += 112
    img.save(path)


def render_caption_frames(cards: list[Card], hook: str | None, workdir: Path,
                          hook_seconds: float = 3.0) -> Path:
    """Captions as a timed sequence of transparent PNGs (an ffconcat playlist)
    for overlaying, used when ffmpeg has no libass."""
    workdir.mkdir(parents=True, exist_ok=True)
    cuts = sorted({0.0, *(c.start for c in cards), *(c.end for c in cards), hook_seconds if hook else 0.0})
    entries: list[tuple[str, float]] = []
    rendered: dict[tuple, str] = {}
    for t0, t1 in zip(cuts, cuts[1:]):
        if t1 - t0 < 0.01:
            continue
        mid = (t0 + t1) / 2
        card = next((c for c in cards if c.start <= mid < c.end), None)
        show_hook = hook if hook and mid < hook_seconds else None
        key = (card.text if card else None, show_hook)
        if key not in rendered:
            rendered[key] = f"cap{len(rendered):04d}.png"
            _draw_frame(card, show_hook, workdir / rendered[key])
        entries.append((rendered[key], t1 - t0))
    if not entries:
        rendered[(None, None)] = "cap_blank.png"
        _draw_frame(None, None, workdir / "cap_blank.png")
        entries.append(("cap_blank.png", 0.1))
    lines = ["ffconcat version 1.0"]
    for name, duration in entries:
        lines += [f"file '{name}'", f"duration {duration:.3f}"]
    lines.append(f"file '{entries[-1][0]}'")  # concat demuxer needs the last file repeated
    playlist = workdir / "captions.ffconcat"
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return playlist


def thumbnail(video: Path, out: Path, at: float = 1.0) -> None:
    ffmpeg(["-ss", str(at), "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out)])


# --- Demo footage ------------------------------------------------------------


def synthesize_footage(sentences: list[str], out: Path, workdir: Path) -> Path:
    """Fake a raw phone take: 16:9, a presenter reading the script with the
    awkward pauses real takes have (lead-in, gaps between lines, tail).

    Speech comes from ffmpeg's flite TTS, or macOS `say` on builds without
    flite, so the editor agent has something real to cut, crop and caption
    without anyone filming.
    """
    filters = available_filters()
    use_flite = "flite" in filters
    has_say = shutil.which("say") is not None
    if not use_flite and not has_say:
        raise MediaError("No text-to-speech available to simulate a take (needs ffmpeg with flite, "
                         "or macOS). Use 'Upload a real clip' instead.")
    workdir.mkdir(parents=True, exist_ok=True)
    gaps = [1.2, 0.9, 1.7, 0.8, 1.4, 1.0, 1.9, 0.9, 1.3]
    lead, tail = 1.8, 2.2
    inputs, chains = [], []
    for i, sentence in enumerate(sentences):
        (workdir / f"line{i}.txt").write_text(sentence, encoding="utf-8")
        if use_flite:
            try:
                ffmpeg(["-f", "lavfi", "-i", f"flite=textfile=line{i}.txt:voice=slt",
                        "-af", "aresample=48000,volume=2.2", "-ac", "1", f"line{i}.wav"], cwd=workdir)
            except MediaError:
                if not has_say:
                    raise
                use_flite = False  # flite listed but unusable: switch to macOS say
        if not use_flite:
            proc = subprocess.run(["say", "-o", f"line{i}.aiff", "-f", f"line{i}.txt"], cwd=workdir,
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                raise MediaError(f"macOS say failed: {proc.stderr.strip()}")
            ffmpeg(["-i", f"line{i}.aiff", "-af", "aresample=48000", "-ac", "1", f"line{i}.wav"], cwd=workdir)
        inputs += ["-i", f"line{i}.wav"]
        pad = tail if i == len(sentences) - 1 else gaps[i % len(gaps)]
        delay = f"adelay={int(lead * 1000)}:all=1," if i == 0 else ""
        chains.append(f"[{i}:a]{delay}apad=pad_dur={pad}[s{i}]")
    n = len(sentences)
    graph = ";".join(chains) + ";" + "".join(f"[s{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[speech]"
    graph += ";anoisesrc=a=0.004:c=pink:r=48000[noise];[speech][noise]amix=inputs=2:duration=first:normalize=0[a]"
    ffmpeg([*inputs, "-filter_complex", graph, "-map", "[a]", "-ac", "1", "take.wav"], cwd=workdir)
    duration = probe_audio_duration(workdir / "take.wav")

    font = "fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:" if Path(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf").exists() else ""
    # Everything outside the centre 608px column is cut by the 9:16 crop.
    video_graph = (
        f"gradients=s=1920x1080:c0=0x1b2a41:c1=0x3b5368:c2=0x22313f:nb_colors=3:speed=0.015:d={duration}:r=30,"
        "drawbox=x=(iw-520)/2:y=210:w=520:h=870:color=0xf2c14e@0.22:t=fill,"
        "drawbox=x=(iw-220)/2:y=260:w=220:h=220:color=0xf2c14e@0.35:t=fill"
    )
    if "drawtext" in filters:  # labels are decoration; slim builds skip them
        video_graph += (
        f",drawtext={font}text='SUBJECT':fontcolor=white@0.55:fontsize=30:x=(w-tw)/2:y=520,"
        f"drawtext={font}text='REC':fontcolor=0xff4d4d:fontsize=40:x=60:y=50,"
        f"drawtext={font}text='%{{pts\\:hms}}':fontcolor=white:fontsize=40:x=w-tw-60:y=50,"
        f"drawtext={font}text='raw 16\\:9 take':fontcolor=white@0.5:fontsize=30:x=60:y=h/2-20,"
        f"drawtext={font}text='(cropped away)':fontcolor=white@0.35:fontsize=26:x=60:y=h/2+24"
        )
    ffmpeg(["-f", "lavfi", "-i", video_graph, "-i", "take.wav", "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-shortest", str(out.resolve())], cwd=workdir)
    return out


def probe_audio_duration(path: Path) -> float:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                           str(path)], capture_output=True, text=True)
    return float(proc.stdout.strip())
