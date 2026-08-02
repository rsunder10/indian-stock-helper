"""Headlines + sentiment from Google News RSS (free, no key).

Coverage is widened with a couple of query phrasings, near-duplicate headlines are collapsed,
and results are returned freshest-first. Aggregate sentiment is recency-weighted so a week-old
headline counts for less than this morning's — see :func:`aggregate_sentiment`.
"""

from __future__ import annotations

import urllib.parse
from datetime import UTC, datetime
from statistics import mean
from time import mktime

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from indi_analyst.models import NewsItem

_ANALYZER = SentimentIntensityAnalyzer()
_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"

# A few phrasings of the same subject pull slightly different Google News result sets; dedupe
# collapses the overlap, netting broader coverage than a single query.
_QUERY_TEMPLATES = ("{term} stock", "{term} share price NSE")


def _norm_title(title: str) -> str:
    return " ".join(title.lower().split())


def _fetch(query: str) -> list[NewsItem]:
    """Fetch and score one RSS query. Returns [] on any parse failure (never raises)."""
    url = _GOOGLE_NEWS_RSS.format(q=urllib.parse.quote(query))
    try:
        feed = feedparser.parse(url)
    except Exception:
        return []

    items: list[NewsItem] = []
    for entry in getattr(feed, "entries", []):
        title = getattr(entry, "title", "").strip()
        if not title:
            continue
        published = None
        if getattr(entry, "published_parsed", None):
            published = datetime.fromtimestamp(mktime(entry.published_parsed), tz=UTC)
        source = None
        if getattr(entry, "source", None):
            source = getattr(entry.source, "title", None)
        items.append(
            NewsItem(
                title=title,
                link=getattr(entry, "link", None),
                published=published,
                source=source,
                sentiment=_ANALYZER.polarity_scores(title)["compound"],
            )
        )
    return items


def _recency_key(item: NewsItem) -> datetime:
    return item.published or datetime.min.replace(tzinfo=UTC)


def aggregate_sentiment(
    news: list[NewsItem],
    *,
    halflife_days: float = 7.0,
    now: datetime | None = None,
) -> float | None:
    """Recency-weighted mean of per-headline VADER compound scores, or None if none are scored.

    Each headline's weight decays by half every ``halflife_days``; undated headlines get unit
    weight. With ``halflife_days <= 0`` (or no usable timestamps) this reduces to a plain mean.
    """
    # Pair each scored headline's (non-None) compound score with its timestamp; the walrus keeps
    # the score typed as a plain float after the None filter.
    scored = [(s, n.published) for n in news if (s := n.sentiment) is not None]
    if not scored:
        return None
    if halflife_days <= 0:
        return round(mean(s for s, _ in scored), 3)

    now = now or datetime.now(UTC)
    weights: list[float] = []
    for _s, published in scored:
        if published is not None:
            age_days = max(0.0, (now - published).total_seconds() / 86400.0)
            weights.append(0.5 ** (age_days / halflife_days))
        else:
            weights.append(1.0)
    total = sum(weights)
    if total <= 0:  # every headline decayed to ~0 weight — fall back to a plain mean
        return round(mean(s for s, _ in scored), 3)
    agg = sum(w * s for w, (s, _) in zip(weights, scored, strict=False)) / total
    return round(agg, 3)


class GoogleNewsSource:
    def news(self, name_or_symbol: str, max_items: int = 8) -> list[NewsItem]:
        term = name_or_symbol.strip()
        if not term:
            return []

        collected: list[NewsItem] = []
        for template in _QUERY_TEMPLATES:
            collected.extend(_fetch(template.format(term=term)))

        # Dedupe by normalized title, keeping the first (best-scored duplicates are identical).
        seen: set[str] = set()
        deduped: list[NewsItem] = []
        for item in collected:
            key = _norm_title(item.title)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        # Freshest first; undated items sort last. Keep the top `max_items`.
        deduped.sort(key=_recency_key, reverse=True)
        return deduped[:max_items]
