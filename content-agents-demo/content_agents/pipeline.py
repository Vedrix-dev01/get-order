"""Orchestrator: moves a run through the five agents.

Each stage's output is saved before the next stage starts, so a run survives
restarts and any stage can be retried on its own. Two human checkpoints stop
the run until the creator acts (unless auto_approve is on):
  - pick the angle to produce          (after the researcher)
  - approve the drafts for publishing  (after the publisher queues them)
and one real-world wait: the creator filming and uploading the take.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import media
from .agents.base import AgentContext
from .agents.copywriter import Copywriter
from .agents.publisher import Publisher
from .agents.researcher import Researcher
from .agents.scriptwriter import Scriptwriter
from .agents.video_editor import VideoEditor, claim_footage
from .config import Settings
from .llm import LLM, make_llm
from .models import PLATFORMS, Platform, RunState
from .notify import Notifier
from .store import Store

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
ACTIVE = {"researching", "scripting", "editing", "writing_copy", "queuing_drafts"}


class PipelineError(RuntimeError):
    pass


class Pipeline:
    def __init__(self, settings: Settings | None = None, llm: LLM | None = None) -> None:
        self.settings = settings or Settings()
        self.store = Store(self.settings.db_path)
        self.llm = llm or make_llm(self.settings)
        self.notifier = Notifier(self.settings, self.store)
        ctx = AgentContext(self.settings, self.store, self.llm, self.notifier)
        self.researcher = Researcher(ctx)
        self.scriptwriter = Scriptwriter(ctx)
        self.editor = VideoEditor(ctx)
        self.copywriter = Copywriter(ctx)
        self.publisher = Publisher(ctx)
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pipeline")
        self._locks: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)
        self._locks_guard = threading.Lock()
        self._watcher: threading.Thread | None = None
        self._stop = threading.Event()
        self._busy: set[str] = set()

    # --- lifecycle -----------------------------------------------------------

    def lock(self, run_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks[run_id]

    def start(self, topics: list[str], auto_approve: bool = False) -> RunState:
        topics = [t.strip() for t in topics if t.strip()]
        if not topics:
            raise PipelineError("Give at least one topic idea")
        run = RunState(id=secrets.token_hex(3), topics=topics, auto_approve=auto_approve, llm_mode=self.llm.mode)
        self.store.save_run(run)
        mode = "Claude (live)" if self.llm.mode == "live" else "demo mode (offline agent outputs)"
        self.store.log(run.id, "orchestrator", f"Run {run.id} started · {mode}")
        self._submit(run.id)
        return run

    def resume_active(self) -> None:
        """After a restart, pick up runs that were mid-stage."""
        for run in self.store.list_runs(200):
            if run.status in ACTIVE:
                self._submit(run.id)

    def shutdown(self) -> None:
        self._stop.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _submit(self, run_id: str) -> None:
        self.executor.submit(self._drive, run_id)

    def is_busy(self, run_id: str) -> bool:
        return run_id in self._busy

    # --- the state machine ---------------------------------------------------

    def _drive(self, run_id: str) -> None:
        with self.lock(run_id):
            run = self.store.get_run(run_id)
            if run is None:
                return
            self._busy.add(run_id)
            try:
                while (step := getattr(self, f"_step_{run.status}", None)) is not None:
                    step(run)
                    self.store.save_run(run)
            except Exception as exc:
                run.status = "failed"
                run.error = f"{exc.__class__.__name__}: {exc}"
                self.store.log(run.id, "orchestrator", f"Run failed: {run.error}", level="error")
                self.store.save_run(run)
            finally:
                self._busy.discard(run_id)

    def _step_researching(self, run: RunState) -> None:
        self.researcher.run(run)
        if run.auto_approve:
            run.selected_angle = 0
            self.store.mark_angle_selected(run.id, run.angles[0].title)
            self.store.log(run.id, "orchestrator", "Auto-approve: using the top-ranked angle")
            run.status = "scripting"
        else:
            run.status = "awaiting_angle_approval"
            self.notifier.send(run.id, "researcher", f"{len(run.angles)} angles ready. Pick one to produce.")

    def _step_scripting(self, run: RunState) -> None:
        self.scriptwriter.run(run)
        run.status = "awaiting_footage"

    def _step_editing(self, run: RunState) -> None:
        self.editor.run(run)
        run.status = "writing_copy"

    def _step_writing_copy(self, run: RunState) -> None:
        self.copywriter.run(run)
        run.status = "queuing_drafts"

    def _step_queuing_drafts(self, run: RunState) -> None:
        self.publisher.queue_drafts(run)
        if run.auto_approve:
            self.publisher.publish(run, list(PLATFORMS))
            run.status = "published"
        else:
            run.status = "awaiting_final_approval"

    # --- human actions -------------------------------------------------------

    def _require(self, run_id: str, status: str) -> RunState:
        run = self.store.get_run(run_id)
        if run is None:
            raise PipelineError(f"No run {run_id}")
        if run.status != status:
            raise PipelineError(f"Run {run_id} is '{run.status}', not '{status}'")
        return run

    def approve_angle(self, run_id: str, index: int) -> RunState:
        with self.lock(run_id):
            run = self._require(run_id, "awaiting_angle_approval")
            if not 0 <= index < len(run.angles):
                raise PipelineError("No such angle")
            run.selected_angle = index
            run.status = "scripting"
            self.store.mark_angle_selected(run.id, run.angles[index].title)
            self.store.log(run.id, "you", f"Approved angle: “{run.angles[index].title}”")
            self.store.save_run(run)
        self._submit(run_id)
        return run

    def attach_footage(self, run_id: str, path: Path) -> RunState:
        with self.lock(run_id):
            run = self._require(run_id, "awaiting_footage")
            run.footage_file = claim_footage(self.settings.run_dir(run_id), path)
            run.status = "editing"
            self.store.log(run.id, "video_editor", f"New footage detected in Drive inbox: {path.name}")
            self.store.save_run(run)
        self._submit(run_id)
        return run

    def simulate_footage(self, run_id: str) -> None:
        """Stand-in for the creator filming: synthesize a raw take of the script
        (TTS + realistic pauses) and drop it in the inbox folder."""
        run = self._require(run_id, "awaiting_footage")
        if run.script is None:
            raise PipelineError("No script yet")
        self.store.log(run_id, "you", "Filming… (simulated phone take of the script, with real pauses)")

        def work() -> None:
            try:
                workdir = self.settings.run_dir(run_id) / "_synth"
                tmp = self.settings.inbox_dir / f".uploading-{run_id}.mp4"
                media.synthesize_footage(run.script.sentences, tmp, workdir)
                # Rename into place only when complete, like a finished Drive sync.
                tmp.replace(self.settings.inbox_dir / f"run-{run_id}_phone_take.mp4")
                self.store.log(run_id, "you", "Uploaded run-%s_phone_take.mp4 to the Drive inbox" % run_id)
            except Exception as exc:
                self.store.log(run_id, "you", f"Simulated filming failed: {exc}", level="error")

        self.executor.submit(work)

    def approve_publish(self, run_id: str, platforms: list[Platform]) -> RunState:
        with self.lock(run_id):
            run = self._require(run_id, "awaiting_final_approval")
            self.store.log(run.id, "you", "Final approval: " + (", ".join(platforms) or "none"))
            self.publisher.publish(run, platforms)
            run.status = "published"
            self.store.save_run(run)
        return run

    def retry(self, run_id: str) -> RunState:
        """Re-run a failed run from the stage it failed in."""
        with self.lock(run_id):
            run = self._require(run_id, "failed")
            if run.script is None:
                run.status = "researching" if not run.angles else "scripting"
            elif run.edit is None:
                run.status = "editing" if run.footage_file else "awaiting_footage"
            elif run.copy_pack is None:
                run.status = "writing_copy"
            else:
                run.status = "queuing_drafts"
            run.error = None
            self.store.log(run.id, "orchestrator", f"Retrying from '{run.status}'")
            self.store.save_run(run)
        self._submit(run_id)
        return run

    # --- Drive inbox watcher -------------------------------------------------

    def start_watcher(self, interval_s: float = 1.5) -> None:
        if self._watcher is None:
            self._watcher = threading.Thread(target=self._watch, args=(interval_s,), daemon=True,
                                             name="drive-watcher")
            self._watcher.start()

    def _watch(self, interval_s: float) -> None:
        sizes: dict[Path, int] = {}
        while not self._stop.is_set():
            try:
                self.scan_inbox(sizes)
            except Exception as exc:  # keep watching whatever happens
                print(f"[watcher] {exc}")
            time.sleep(interval_s)

    def scan_inbox(self, sizes: dict[Path, int] | None = None) -> None:
        """Claim new, fully-written clips. A clip named with a run id
        (run-<id>...) goes to that run; otherwise to the oldest waiting run."""
        sizes = {} if sizes is None else sizes
        for path in sorted(self.settings.inbox_dir.iterdir()):
            if path.name.startswith(".") or path.suffix.lower() not in VIDEO_EXTS or not path.is_file():
                continue
            size = path.stat().st_size
            if sizes.get(path) != size:  # still syncing; check again next tick
                sizes[path] = size
                continue
            waiting = self.store.runs_with_status("awaiting_footage")
            if named := re.search(r"run-([0-9a-f]{6})", path.name):
                target = next((r for r in waiting if r.id == named.group(1)), None)
            else:
                target = waiting[0] if waiting else None
            if target is None:
                continue
            sizes.pop(path, None)
            self.attach_footage(target.id, path)
