"""
app/data/web_search.py
=========================
Web search with citations, via the OpenAI Responses API's `web_search` tool.

WHY THIS EXISTS:
-------------------
Stage 1 reads the company's own filings, which is the right primary source but
cannot answer everything. Three kinds of question fall outside them:

  - things no company writes about itself (promoter regulatory history)
  - things stated only from the company's own point of view (competitors,
    market concentration, entry barriers)
  - anything at all, for a company whose annual report has not been ingested

That last case is the common one, and it used to fail silently: a symbol with
no documents in the store scored 1/18 and every answer read "no relevant
passage found", which looks like the information does not exist rather than
like the shelf is empty.

THE TOOL CALL MUST BE FORCED, AND THIS IS THE WHOLE POINT:
------------------------------------------------------------
Passing `tools=[{"type": "web_search"}]` only OFFERS the tool. Measured against
the live API, the model frequently declines it and answers from parametric
memory instead — fluent, plausible, and carrying ZERO citations. For a research
system that is the worst possible failure: unsourced recall that reads exactly
like sourced research.

So `tool_choice` forces the search on every call. If a response comes back with
no `web_search_call` item, it is rejected rather than returned, because it was
not research — it was recollection.

WHAT A WEB CITATION IS AND IS NOT:
------------------------------------
It is a URL a model found and drew from. It is NOT a Tier-1 regulatory
confirmation: this cannot verify that a SEBI order names THIS company's
promoter rather than someone of a similar name. Identity matching is what a
BLOCK would require, so nothing here may ever produce one. Every source keeps
its domain visible precisely so the reader can weigh a SEBI circular
differently from a content-farm summary.

USAGE:
------
    from app.data.web_search import search_answer

    hit = await search_answer("Who audits Asian Paints (NSE: ASIANPAINT)?")
    hit.text                      # prose, inline link markup stripped
    hit.sources                   # [{"title", "url", "domain"}, ...]
"""

import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from loguru import logger

from app.core.config import settings


@dataclass
class WebAnswer:
    text: str
    sources: list[dict] = field(default_factory=list)   # {title, url, domain}

    @property
    def citations(self) -> list[str]:
        """Display strings, de-duplicated, domain first so provenance leads."""
        seen, out = set(), []
        for s in self.sources:
            label = f"{s['domain']} — {s['title']}" if s.get("title") else s["domain"]
            if label not in seen:
                seen.add(label)
                out.append(label)
        return out


# The model writes its own inline markdown citations, e.g.
#   "...auditors ([economictimes.indiatimes.com](https://…?utm_source=openai))"
# The same URLs arrive structurally in the annotations, so the inline copies are
# stripped: keeping both leaves the prose unreadable and duplicates every source.
_INLINE_CITE = re.compile(r"\s*\(\[[^\]]+\]\(https?://[^)]+\)\)")

# One shared limiter. Stage 1 fans out over 18 questions at once, and without
# this a single analysis would open 18 simultaneous search calls — enough to hit
# rate limits, and enough to make one company's run starve another's.
#
# KEYED BY EVENT LOOP, NOT JUST BY SIZE: an asyncio.Semaphore binds to the loop
# that first awaits it, and awaiting it from a second loop raises "bound to a
# different event loop". A plain module-level singleton therefore works in the
# server (one long-lived loop) and breaks in anything that calls asyncio.run
# more than once — the test suite, and any script or job that does the same.
_semaphores: "dict[int, tuple[asyncio.AbstractEventLoop, int, asyncio.Semaphore]]" = {}


def _limiter() -> asyncio.Semaphore:
    size = max(1, settings.STAGE1_WEB_SEARCH_CONCURRENCY)
    loop = asyncio.get_running_loop()
    key = id(loop)

    cached = _semaphores.get(key)
    if cached is not None and cached[0] is loop and cached[1] == size:
        return cached[2]

    # Drop entries for loops that have closed, so a long-lived process that
    # creates loops repeatedly does not accumulate them. `id()` can be reused
    # after a loop is collected, which is why the loop itself is stored and
    # compared identically above rather than trusted by key alone.
    for stale in [k for k, (lp, _, _) in _semaphores.items() if lp.is_closed()]:
        _semaphores.pop(stale, None)

    semaphore = asyncio.Semaphore(size)
    _semaphores[key] = (loop, size, semaphore)
    return semaphore


def is_available() -> bool:
    return bool(settings.STAGE1_WEB_SEARCH_ENABLED and settings.OPENAI_API_KEY)


async def search_answer(
    instruction: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 500,
) -> WebAnswer | None:
    """
    Run one forced web search and return the answer with its sources.

    Returns None when search is disabled, when the call fails, or when the
    model answered WITHOUT searching — see the module docstring on why that
    last case is treated as a failure rather than as a cheap success.
    """
    if not is_available():
        return None

    model = model or settings.STAGE1_WEB_SEARCH_MODEL
    system = system or (
        "You research Indian listed companies using web search. You cite what you "
        "find, you never present recollection as a finding, and you say plainly "
        "when the search does not settle the question."
    )

    def _call() -> WebAnswer | None:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY,
                        timeout=float(settings.STAGE1_WEB_SEARCH_TIMEOUT_S))
        response = client.responses.create(
            model=model,
            tools=[{"type": "web_search"}],
            # Forced, not offered. See the module docstring.
            tool_choice={"type": "web_search"},
            instructions=system,
            input=instruction,
            max_output_tokens=max_tokens,
        )

        searched = any(getattr(i, "type", None) == "web_search_call"
                       for i in (response.output or []))
        if not searched:
            logger.warning("Web search returned an unsourced answer (no search performed) — discarding")
            return None

        sources: list[dict] = []
        for item in response.output or []:
            for content in (getattr(item, "content", None) or []):
                for ann in (getattr(content, "annotations", None) or []):
                    if getattr(ann, "type", None) != "url_citation":
                        continue
                    url = getattr(ann, "url", "") or ""
                    if not url:
                        continue
                    sources.append({
                        "title": (getattr(ann, "title", "") or "").strip(),
                        "url": url,
                        "domain": urlparse(url).netloc.removeprefix("www."),
                    })

        text = _INLINE_CITE.sub("", response.output_text or "").strip()
        if not text:
            return None
        return WebAnswer(text=text, sources=sources)

    try:
        async with _limiter():
            return await asyncio.get_running_loop().run_in_executor(None, _call)
    except Exception as e:
        logger.warning(f"Web search failed: {type(e).__name__}: {e}")
        return None
