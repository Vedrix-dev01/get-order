"""Trend signal sources for the researcher.

Live feeds are public endpoints that need no API key. Each source fails soft:
if a feed is unreachable the researcher carries on with whatever it has, and
falls back to the bundled sample feed so a run never stalls on the network.
"""

from __future__ import annotations

import json
import urllib.request
import xml.etree.ElementTree as ET

from .config import PROJECT_DIR, Settings
from .models import TrendSignal

USER_AGENT = "content-agents-demo/0.1 (trend research)"
SAMPLE_FEED = PROJECT_DIR / "sample_data" / "trends.json"


def _get(url: str, timeout: float = 8.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def reddit_top(subreddit: str, limit: int = 8) -> list[TrendSignal]:
    data = json.loads(_get(f"https://www.reddit.com/r/{subreddit}/top.json?t=week&limit={limit}"))
    out = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        out.append(TrendSignal(
            source=f"r/{subreddit}",
            title=post.get("title", ""),
            metric=f"{post.get('ups', 0):,} upvotes · {post.get('num_comments', 0):,} comments",
            url=f"https://www.reddit.com{post.get('permalink', '')}",
        ))
    return out


def google_trends(geo: str = "US", limit: int = 10) -> list[TrendSignal]:
    root = ET.fromstring(_get(f"https://trends.google.com/trending/rss?geo={geo}"))
    ns = {"ht": "https://trends.google.com/trending/rss"}
    out = []
    for item in root.iter("item"):
        traffic = item.findtext("ht:approx_traffic", default="", namespaces=ns)
        out.append(TrendSignal(source=f"Google Trends {geo}", title=item.findtext("title", ""),
                               metric=f"{traffic} searches" if traffic else ""))
        if len(out) >= limit:
            break
    return out


def sample_feed() -> list[TrendSignal]:
    return [TrendSignal(**s) for s in json.loads(SAMPLE_FEED.read_text(encoding="utf-8"))]


def gather_signals(settings: Settings, log=lambda msg: None) -> list[TrendSignal]:
    signals: list[TrendSignal] = []
    if settings.live_trend_feeds:
        fetchers = [(f"r/{s}", lambda s=s: reddit_top(s)) for s in settings.subreddits]
        fetchers.append(("Google Trends", google_trends))
        for name, fetch in fetchers:
            try:
                got = fetch()
                signals.extend(got)
                log(f"Pulled {len(got)} signals from {name}")
            except Exception as exc:  # network/feed errors must not stop the pipeline
                log(f"Skipped {name}: {exc.__class__.__name__}")
    if not signals:
        signals = sample_feed()
        log(f"Using bundled sample trend feed ({len(signals)} signals)")
    return signals
