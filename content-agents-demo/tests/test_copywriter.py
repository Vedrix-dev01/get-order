from content_agents.agents.copywriter import LIMITS, compose, enforce_limits
from content_agents.models import CopyPack, PlatformCopy


def test_enforce_limits_normalizes_and_trims():
    pack = CopyPack(posts=[
        PlatformCopy(platform="youtube_shorts", title="x" * 140, caption="desc", hashtags=["#Shorts", "shorts", "AI Tools"]),
        PlatformCopy(platform="instagram", title="hook", caption="word " * 600, hashtags=[f"tag{i}" for i in range(40)]),
    ])
    fixed, notes = enforce_limits(pack)
    yt = fixed.for_platform("youtube_shorts")
    ig = fixed.for_platform("instagram")
    assert len(yt.title) <= 100
    assert yt.hashtags == ["shorts", "aitools"]
    assert len(ig.hashtags) == 30
    assert len(compose(ig)) <= LIMITS["instagram"]["text"]
    assert any("TikTok: no copy generated" in n for n in notes)
    assert any("trimmed hashtags" in n for n in notes)


def test_enforce_limits_leaves_good_copy_alone():
    posts = [PlatformCopy(platform=p, title="Title", caption="Caption", hashtags=["a"])
             for p in ("instagram", "tiktok", "youtube_shorts")]
    fixed, notes = enforce_limits(CopyPack(posts=posts))
    assert notes == []
    assert fixed == CopyPack(posts=posts)
