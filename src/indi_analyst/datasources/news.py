"""Headlines + sentiment from Google News RSS (free, no key)."""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from time import mktime

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from indi_analyst.models import NewsItem

_ANALYZER = SentimentIntensityAnalyzer()
_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"


class GoogleNewsSource:
    def news(self, name_or_symbol: str, max_items: int = 8) -> list[NewsItem]:
        query = urllib.parse.quote(f"{name_or_symbol} stock")
        url = _GOOGLE_NEWS_RSS.format(q=query)
        try:
            feed = feedparser.parse(url)
        except Exception:
            return []

        items: list[NewsItem] = []
        for entry in feed.entries[:max_items]:
            title = getattr(entry, "title", "").strip()
            if not title:
                continue
            published = None
            if getattr(entry, "published_parsed", None):
                published = datetime.fromtimestamp(mktime(entry.published_parsed), tz=timezone.utc)
            source = None
            if getattr(entry, "source", None):
                source = getattr(entry.source, "title", None)
            sentiment = _ANALYZER.polarity_scores(title)["compound"]
            items.append(
                NewsItem(
                    title=title,
                    link=getattr(entry, "link", None),
                    published=published,
                    source=source,
                    sentiment=sentiment,
                )
            )
        return items
