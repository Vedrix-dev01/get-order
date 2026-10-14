"""Dashboard + JSON API (standard library only).

GET  /api/config                     mode, model, inbox folder
GET  /api/runs                       recent runs
GET  /api/runs/<id>?after=<event id> run state + new activity events
GET  /api/library                    the content-angle database
POST /api/runs                       {"topics": [...], "auto_approve": bool}
POST /api/runs/<id>/angle            {"index": n}
POST /api/runs/<id>/simulate-footage
POST /api/runs/<id>/footage          raw video bytes, X-Filename header
POST /api/runs/<id>/publish          {"platforms": [...]}
POST /api/runs/<id>/retry
GET  /media/<id>/<file>              run outputs (video supports Range)
"""

from __future__ import annotations

import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .agents.copywriter import LIMITS, compose
from .config import PACKAGE_DIR, Settings
from .pipeline import Pipeline, PipelineError

STATIC = PACKAGE_DIR / "static"
MAX_UPLOAD = 2 * 1024**3


def run_payload(pipeline: Pipeline, run_id: str, after: int) -> dict | None:
    run = pipeline.store.get_run(run_id)
    if run is None:
        return None
    data = run.model_dump(mode="json")
    if run.script:
        data["script_meta"] = {"word_count": run.script.word_count, "est_duration_s": run.script.est_duration_s}
    if run.copy_pack:
        data["copy_final"] = {p.platform: {"text": compose(p), "limit": LIMITS[p.platform]["text"],
                                           "title_limit": LIMITS[p.platform]["title"]}
                              for p in run.copy_pack.posts}
    if run.shoot_brief_file:
        brief = pipeline.settings.run_dir(run.id) / run.shoot_brief_file
        data["shoot_brief"] = brief.read_text(encoding="utf-8") if brief.exists() else None
    data["busy"] = pipeline.is_busy(run.id)
    data["events"] = pipeline.store.events(run.id, after)
    return data


def make_handler(pipeline: Pipeline):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ContentAgents/0.1"

        def log_message(self, fmt, *args):  # quieter console
            if not self.path.startswith("/api/runs/") or self.command != "GET":
                super().log_message(fmt, *args)

        # --- helpers ---
        def _json(self, payload, status: int = 200) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, message: str, status: int = 400) -> None:
            self._json({"error": message}, status)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}") if length else {}

        def _file(self, path: Path) -> None:
            if not path.is_file():
                return self._error("not found", 404)
            size = path.stat().st_size
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)):
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else end
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with path.open("rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0 and (chunk := f.read(min(1 << 16, remaining))):
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    remaining -= len(chunk)

        # --- routes ---
        def do_GET(self) -> None:
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            if not parts:
                return self._file(STATIC / "index.html")
            if parts[0] == "static" and len(parts) == 2:
                return self._file(STATIC / Path(parts[1]).name)
            if parts[0] == "media" and len(parts) == 3:
                if not re.fullmatch(r"[0-9a-f]{6}", parts[1]):
                    return self._error("not found", 404)
                return self._file(pipeline.settings.runs_dir / parts[1] / Path(parts[2]).name)
            if parts == ["api", "config"]:
                s = pipeline.settings
                return self._json({"llm_mode": pipeline.llm.mode, "model": s.model if pipeline.llm.mode == "live" else None,
                                   "inbox_dir": str(s.inbox_dir), "slack": bool(s.slack_webhook_url),
                                   "live_trends": s.live_trend_feeds})
            if parts == ["api", "runs"]:
                return self._json([{"id": r.id, "created_at": r.created_at, "status": r.status, "topics": r.topics,
                                    "title": (r.angle.title if r.angle else None)}
                                   for r in pipeline.store.list_runs()])
            if parts == ["api", "library"]:
                return self._json(pipeline.store.angle_library())
            if len(parts) == 3 and parts[:2] == ["api", "runs"]:
                after = int(parse_qs(url.query).get("after", ["0"])[0])
                payload = run_payload(pipeline, parts[2], after)
                return self._json(payload) if payload else self._error("no such run", 404)
            return self._error("not found", 404)

        def do_POST(self) -> None:
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            try:
                if parts == ["api", "runs"]:
                    body = self._body()
                    run = pipeline.start(body.get("topics") or [], bool(body.get("auto_approve")))
                    return self._json({"id": run.id}, 201)
                if len(parts) == 4 and parts[:2] == ["api", "runs"]:
                    run_id, action = parts[2], parts[3]
                    if action == "angle":
                        pipeline.approve_angle(run_id, int(self._body().get("index", -1)))
                    elif action == "simulate-footage":
                        pipeline.simulate_footage(run_id)
                    elif action == "footage":
                        self._upload(run_id)
                    elif action == "publish":
                        pipeline.approve_publish(run_id, list(self._body().get("platforms") or []))
                    elif action == "retry":
                        pipeline.retry(run_id)
                    else:
                        return self._error("unknown action", 404)
                    return self._json({"ok": True})
            except PipelineError as exc:
                return self._error(str(exc), 409)
            except (ValueError, json.JSONDecodeError) as exc:
                return self._error(str(exc), 400)
            return self._error("not found", 404)

        def _upload(self, run_id: str) -> None:
            """Browser upload of a real clip; written into the Drive inbox so it
            goes through exactly the same path as a phone upload."""
            pipeline._require(run_id, "awaiting_footage")
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_UPLOAD:
                raise ValueError("upload must be between 1 byte and 2 GB")
            suffix = Path(self.headers.get("X-Filename", "clip.mp4")).suffix.lower() or ".mp4"
            if not re.fullmatch(r"\.[a-z0-9]{2,4}", suffix):
                suffix = ".mp4"
            tmp = pipeline.settings.inbox_dir / f".uploading-{run_id}{suffix}"
            with tmp.open("wb") as f:
                remaining = length
                while remaining > 0 and (chunk := self.rfile.read(min(1 << 20, remaining))):
                    f.write(chunk)
                    remaining -= len(chunk)
            tmp.replace(pipeline.settings.inbox_dir / f"run-{run_id}_upload{suffix}")
            pipeline.store.log(run_id, "you", f"Uploaded {self.headers.get('X-Filename', 'clip')} to the Drive inbox")

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    pipeline = Pipeline(Settings())
    pipeline.resume_active()
    pipeline.start_watcher()
    httpd = ThreadingHTTPServer((host, port), make_handler(pipeline))
    mode = f"live Claude ({pipeline.settings.model})" if pipeline.llm.mode == "live" else "demo mode (no API key)"
    print(f"Content agents dashboard: http://{host}:{port}  ·  {mode}")
    print(f"Drive inbox folder being watched: {pipeline.settings.inbox_dir}")
    from shutil import which

    from .media import available_filters
    filters = available_filters()
    voice = "ffmpeg flite" if "flite" in filters else ("macOS say" if which("say") else "none (upload a real clip)")
    print(f"Captions: {'libass' if 'ass' in filters else 'Pillow'} · simulated-take voice: {voice}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.shutdown()
        httpd.server_close()
