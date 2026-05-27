"""
app/data/news_fetcher.py
=========================
Fetch live financial news for Indian markets from FREE RSS feeds.

NO API KEY REQUIRED.
All sources used here are publicly available RSS/XML feeds.

NEWS SOURCES:
-------------
1. Economic Times Markets RSS
   → https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms
   Covers: NSE, BSE, Nifty, Sensex, stocks, FII/DII activity

2. Moneycontrol News RSS
   → https://www.moneycontrol.com/rss/latestnews.xml
   Covers: general market news, IPOs, earnings, macro

3. Livemint Markets RSS
   → https://www.livemint.com/rss/markets
   Covers: Indian equities, F&O, technical analysis

CACHING STRATEGY:
-----------------
News is cached in Redis for 5 minutes (CACHE_TTL_SECONDS).
This means:
  - First call in 5 min: fetch from RSS, store in Redis
  - Subsequent calls in same 5 min: return cached data
  - After 5 min: fetch fresh news again

This prevents hammering news sites with requests every cycle
(cycles can run every few seconds) while still keeping news fresh.

USAGE FROM MarketContext:
--------------------------
    from app.data.news_fetcher import fetch_news

    news = await fetch_news(symbol="NIFTY 50", max_articles=10)
    # Returns: [{"title": "Nifty hits all-time high", "source": "ET"}, ...]

    market_context = MarketContext(
        symbol       = "NIFTY 50",
        recent_news  = news,
        ...
    )
"""

import asyncio
import json
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx
from loguru import logger

from app.db.redis import redis_client


# ============================================================
# CONFIGURATION
# ============================================================

# Cache news for 5 minutes
CACHE_TTL_SECONDS = 300

# HTTP request timeout
REQUEST_TIMEOUT = 10.0

# Free RSS feed URLs for Indian financial news
RSS_FEEDS = [
    {
        "name":  "Economic Times Markets",
        "url":   "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "short": "ET",
    },
    {
        "name":  "Moneycontrol Latest",
        "url":   "https://www.moneycontrol.com/rss/latestnews.xml",
        "short": "MC",
    },
    {
        "name":  "Livemint Markets",
        "url":   "https://www.livemint.com/rss/markets",
        "short": "Mint",
    },
]


# ============================================================
# MAIN FETCH FUNCTION
# ============================================================

async def fetch_news(
    symbol:       str  = "NIFTY 50",
    max_articles: int  = 15,
    use_cache:    bool = True,
) -> list[dict]:
    """
    Fetch recent financial news for Indian markets.

    Returns a list of article dicts like:
    [
        {
            "title":       "Nifty hits 24,000 on strong FII buying",
            "source":      "ET",
            "published":   "2024-01-15T10:30:00",
        },
        ...
    ]

    Parameters:
    -----------
    symbol      : trading symbol (used as cache key namespace)
    max_articles: maximum articles to return
    use_cache   : if True, check Redis cache first (recommended)

    Returns empty list on failure — news fetch failure is non-fatal.
    """

    cache_key = f"futureedge:news:{symbol.replace(' ', '_')}"

    # --------------------------------------------------------
    # CHECK CACHE FIRST
    # --------------------------------------------------------

    if use_cache:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                articles = json.loads(cached)
                logger.debug(
                    f"News from cache | symbol={symbol} | "
                    f"count={len(articles)}"
                )
                return articles[:max_articles]

        except Exception as e:
            logger.warning(f"News cache read failed: {e}")

    # --------------------------------------------------------
    # FETCH FROM RSS FEEDS
    # --------------------------------------------------------

    all_articles = []

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        for feed in RSS_FEEDS:
            try:
                articles = await _fetch_rss(client, feed["url"], feed["short"])
                all_articles.extend(articles)

            except Exception as e:
                logger.warning(
                    f"RSS fetch failed | source={feed['short']} | {e}"
                )

    # --------------------------------------------------------
    # DEDUPLICATE BY TITLE
    # --------------------------------------------------------

    seen_titles = set()
    unique_articles = []

    for article in all_articles:
        title_key = article["title"].lower()[:60]   # first 60 chars
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_articles.append(article)

    # --------------------------------------------------------
    # CACHE THE RESULT
    # --------------------------------------------------------

    if unique_articles:
        try:
            await redis_client.setex(
                cache_key,
                CACHE_TTL_SECONDS,
                json.dumps(unique_articles),
            )
        except Exception as e:
            logger.warning(f"News cache write failed: {e}")

    result = unique_articles[:max_articles]

    logger.info(
        f"News fetched | symbol={symbol} | count={len(result)} | "
        f"sources={len([f for f in RSS_FEEDS])}"
    )

    return result


# ============================================================
# RSS PARSER
# ============================================================

async def _fetch_rss(
    client:     httpx.AsyncClient,
    url:        str,
    source_tag: str,
) -> list[dict]:
    """
    Fetch and parse a single RSS feed URL.

    RSS is a standard XML format for news feeds.
    We parse the <item> elements and extract:
      - <title>      : headline
      - <pubDate>    : publication date

    Returns a list of article dicts.
    """

    response = await client.get(url, follow_redirects=True)
    response.raise_for_status()

    # Parse XML
    root = ET.fromstring(response.text)

    articles = []

    # RSS items are inside <channel><item> elements
    for item in root.findall(".//item"):
        title_elem   = item.find("title")
        pub_date_elem = item.find("pubDate")

        if title_elem is None or not title_elem.text:
            continue

        title = title_elem.text.strip()

        # Skip very short or generic titles
        if len(title) < 10:
            continue

        # Parse publication date (best effort)
        published = ""
        if pub_date_elem is not None and pub_date_elem.text:
            published = pub_date_elem.text.strip()

        articles.append({
            "title":     title,
            "source":    source_tag,
            "published": published,
        })

    return articles