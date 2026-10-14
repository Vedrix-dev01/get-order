"""Agent 5 - Publisher: final video + copy -> drafts on each platform, held for
the creator's final approval, then published on approve.

Each platform has a different notion of "draft"; the adapters below use the
official API route that keeps the post unpublished until approval:
  YouTube   - upload as a Private video, flip to Public on approval
  Instagram - create a Reels media container, call media_publish on approval
  TikTok    - upload to the creator's TikTok inbox (drafts), finish in the app

The demo runs in dry-run: it builds the exact API requests and records them
(shown in the dashboard) instead of sending them. Live sending is enabled per
platform once that platform's OAuth app and tokens are set up.
"""

from __future__ import annotations

import os

from ..models import ApiRequest, DraftRecord, Platform, RunState, now_iso
from .base import Agent
from .copywriter import LIMITS, compose

GRAPH_VERSION = os.environ.get("IG_GRAPH_VERSION", "v23.0")


def youtube_requests(run: RunState, size: int) -> tuple[list[ApiRequest], list[ApiRequest], str]:
    post = run.copy_pack.for_platform("youtube_shorts")
    meta = {
        "snippet": {"title": post.title, "description": compose(post), "tags": post.hashtags, "categoryId": "22"},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
    }
    draft = [
        ApiRequest(step="Start resumable upload (as Private)", method="POST",
                   url="https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                   body=meta),
        ApiRequest(step="Upload video bytes", method="PUT", url="{upload session URL from step 1}",
                   body={"file": run.edit.output_file, "bytes": size}),
    ]
    publish = [
        ApiRequest(step="Make video Public", method="PUT",
                   url="https://www.googleapis.com/youtube/v3/videos?part=status",
                   body={"id": "{video id}", "status": {"privacyStatus": "public"}}),
    ]
    return draft, publish, "Uploaded as a Private video; switched to Public when you approve"


def instagram_requests(run: RunState, size: int) -> tuple[list[ApiRequest], list[ApiRequest], str]:
    post = run.copy_pack.for_platform("instagram")
    base = f"https://graph.facebook.com/{GRAPH_VERSION}/{{ig-user-id}}"
    draft = [
        ApiRequest(step="Create Reels media container", method="POST", url=f"{base}/media",
                   body={"media_type": "REELS", "video_url": f"{{public URL of {run.edit.output_file}}}",
                         "caption": compose(post), "share_to_feed": True}),
    ]
    publish = [
        ApiRequest(step="Publish container", method="POST", url=f"{base}/media_publish",
                   body={"creation_id": "{container id}"}),
    ]
    return draft, publish, "Media container created and held unpublished until you approve"


def tiktok_requests(run: RunState, size: int) -> tuple[list[ApiRequest], list[ApiRequest], str]:
    draft = [
        ApiRequest(step="Init upload to creator inbox", method="POST",
                   url="https://open.tiktokapis.com/v2/post/publish/inbox/video/init/",
                   body={"source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size,
                                         "total_chunk_count": 1}}),
        ApiRequest(step="Upload video bytes", method="PUT", url="{upload_url from step 1}",
                   body={"file": run.edit.output_file, "Content-Range": f"bytes 0-{size - 1}/{size}"}),
    ]
    return draft, [], "Sent to your TikTok inbox as a draft; caption is copied for you to paste when you post"


ADAPTERS = {"youtube_shorts": youtube_requests, "instagram": instagram_requests, "tiktok": tiktok_requests}


class Publisher(Agent):
    name = "publisher"
    label = "Publisher"

    def queue_drafts(self, run: RunState) -> None:
        assert run.edit and run.copy_pack
        size = (self.ctx.settings.run_dir(run.id) / run.edit.output_file).stat().st_size
        run.drafts = []
        for platform, build in ADAPTERS.items():
            draft_reqs, publish_reqs, mechanism = build(run, size)
            run.drafts.append(DraftRecord(
                platform=platform, status="queued_as_draft", dry_run=True, draft_mechanism=mechanism,
                requests=draft_reqs, publish_requests=publish_reqs, remote_id=f"dryrun-{platform}-{run.id}",
            ))
            self.log(run, f"{LIMITS[platform]['label']}: draft queued (dry run). {mechanism}")
        self.ctx.notifier.send(run.id, self.name,
                               "3 drafts are queued and waiting for your final approval in the dashboard.")

    def publish(self, run: RunState, platforms: list[Platform]) -> None:
        for draft in run.drafts:
            if draft.status != "queued_as_draft":
                continue
            label = LIMITS[draft.platform]["label"]
            if draft.platform in platforms:
                draft.status = "published"
                how = "published" if draft.publish_requests else "ready in your TikTok inbox to post"
                self.log(run, f"{label}: approved → {how} (dry run)")
            else:
                draft.status = "skipped"
                self.log(run, f"{label}: not approved, left as draft")
            draft.updated_at = now_iso()
