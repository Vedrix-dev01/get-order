import shutil
import time

import pytest

from content_agents.config import Settings
from content_agents.demo_llm import DemoLLM
from content_agents.pipeline import Pipeline, PipelineError

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def wait_for(pipeline, run_id, status, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = pipeline.store.get_run(run_id)
        if run.status == status and not pipeline.is_busy(run_id):
            return run
        assert run.status != "failed", run.error
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {status}, at {run.status}")


@pytest.fixture
def pipeline(tmp_path):
    p = Pipeline(Settings(workspace=tmp_path), llm=DemoLLM(latency_s=0))
    p.start_watcher(interval_s=0.2)
    yield p
    p.shutdown()


def test_full_run_with_checkpoints(pipeline):
    run = pipeline.start(["Why most Instagram Reels flop in the first 3 seconds"])
    run = wait_for(pipeline, run.id, "awaiting_angle_approval")
    assert run.angles and pipeline.store.angle_library()

    with pytest.raises(PipelineError):
        pipeline.approve_publish(run.id, ["instagram"])  # wrong stage

    pipeline.approve_angle(run.id, 0)
    run = wait_for(pipeline, run.id, "awaiting_footage")
    assert run.script.word_count > 50
    assert (pipeline.settings.run_dir(run.id) / "shoot_brief.md").exists()

    pipeline.simulate_footage(run.id)  # lands in the inbox; the watcher claims it
    run = wait_for(pipeline, run.id, "awaiting_final_approval")
    assert run.edit.resolution == "1080x1920"
    assert run.edit.output_duration_s < run.edit.input_duration_s
    assert run.edit.cuts >= 3
    assert {p.platform for p in run.copy_pack.posts} == {"instagram", "tiktok", "youtube_shorts"}
    assert all(d.status == "queued_as_draft" and d.dry_run for d in run.drafts)

    run = pipeline.approve_publish(run.id, ["youtube_shorts", "instagram"])
    statuses = {d.platform: d.status for d in run.drafts}
    assert statuses == {"youtube_shorts": "published", "instagram": "published", "tiktok": "skipped"}


def test_inbox_ignores_clip_for_unknown_run(pipeline):
    (pipeline.settings.inbox_dir / "run-abcdef_take.mp4").write_bytes(b"x")
    sizes = {}
    pipeline.scan_inbox(sizes)
    pipeline.scan_inbox(sizes)
    assert (pipeline.settings.inbox_dir / "run-abcdef_take.mp4").exists()
