"""
app/agents/business_agent.py
===============================
Stage 1 of the fundamental analysis method — the 18 qualitative questions from
`fundamental_analysis_steps.md`, answered from the company's own filings.

WHAT CHANGED THE DESIGN OF THIS FILE:
---------------------------------------
Stage 1 was originally assumed to be mostly manual, on the grounds that these
answers are not in any ratio table. They are not — they are in the annual
report, and the annual report turns out to be machine-readable. So the system
reads it, and manual entry became the exception rather than the norm.

HOW AN ANSWER IS PRODUCED:
----------------------------
    curated queries -> retrieve passages from this company's documents
                    -> LLM drafts an answer FROM THOSE PASSAGES ONLY
                    -> answer carries the page citations it was drawn from

The queries are curated per question and phrased the way annual reports phrase
things, because that is what retrieval matches on. A question asked in the
reader's words ("how many staff?") retrieves worse than one asked in the
document's words ("total number of permanent employees on rolls"). Several
queries per question, because one phrasing rarely covers a topic.

THREE KINDS OF QUESTION, AND WHY THEY ARE MARKED DIFFERENTLY:
--------------------------------------------------------------
  RETRIEVAL — answerable from the filings. Most of them.
  STRUCTURED — already scraped as numbers (shareholding), so retrieval would
               be a worse route to something we hold exactly.
  EXTERNAL  — not in any company-published document. Q2's promoter background
              is the case: no annual report says its promoter has a criminal
              record. Answering it needs SEBI/MCA sources, and until those are
              wired the question reports itself unanswered rather than being
              quietly answered from a company's own self-description.
  JUDGMENT  — Q17 is an opinion, not a lookup. It is produced, and it is
              LABELLED as an opinion so it is never read as a finding.

STAGE 1 IS NOT A GATE:
------------------------
The method says "list every red flag found in one place", not "stop". All
three stages always run. A MISSING answer never blocks; a BAD answer does —
because ingestion is fallible and silence must not read as evidence of health.

USAGE:
------
    from app.agents.business_agent import run_business_checklist

    report = await run_business_checklist("RELIANCE", raw)
    report.answers[0].citations   # ["AR FY2026, p.12", ...]
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger

from app.core.config import settings


class Kind(str, Enum):
    RETRIEVAL = "RETRIEVAL"
    STRUCTURED = "STRUCTURED"
    EXTERNAL = "EXTERNAL"
    JUDGMENT = "JUDGMENT"


class AnswerStatus(str, Enum):
    ANSWERED = "ANSWERED"
    PARTIAL = "PARTIAL"          # passages found, but thin
    NOT_FOUND = "NOT_FOUND"      # nothing retrieved
    NEEDS_EXTERNAL = "NEEDS_EXTERNAL"


@dataclass
class QuestionSpec:
    n: int
    question: str
    kind: Kind
    queries: list[str] = field(default_factory=list)
    doc_types: list[str] | None = None
    red_flag_hint: str = ""


@dataclass
class Answer:
    n: int
    question: str
    kind: str
    status: str
    answer: str = ""
    citations: list[str] = field(default_factory=list)
    passages: list[dict] = field(default_factory=list)
    is_opinion: bool = False
    red_flag: str | None = None
    # Which tier the answer actually came from. Recorded per answer rather than
    # per question, because the same question resolves differently depending on
    # whether this company's report happens to be ingested — and a reader must
    # be able to tell an audited filing from a page a model found.
    source: str = "DOCUMENTS"           # DOCUMENTS | WEB | SHAREHOLDING | NONE
    sources: list[dict] = field(default_factory=list)   # web only: {title, url, domain}


@dataclass
class _SearchBudget:
    """
    Caps paid web searches for one analysis.

    A company with no ingested documents fails every retrieval, so without a
    ceiling one run would fire a search for all 18 questions and a mis-wired
    loop could do it repeatedly. The counter is shared across the fan-out, and
    guarded because the 18 questions are answered concurrently.
    """
    remaining: int
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def take(self) -> bool:
        async with self._lock:
            if self.remaining <= 0:
                return False
            self.remaining -= 1
            return True


@dataclass
class BusinessReportResult:
    symbol: str
    answers: list[Answer] = field(default_factory=list)
    red_flags: list[dict] = field(default_factory=list)
    gate: str = "CLEAR"          # BLOCK | FLAG | CLEAR
    answered: int = 0
    completeness: float = 0.0


# ============================================================
# THE 18 QUESTIONS
#
# Queries are written in the document's language, not the reader's. Verified
# against a live RELIANCE FY2026 ingest: keyword-style phrasings scored ~0.36
# and returned unrelated prose, while phrasings that echo the report's own
# section headings scored ~0.57 and landed on the right table.
#
# The strongest phrasings are lifted from MANDATORY disclosures, because their
# wording is fixed by regulation and therefore near-identical across companies:
#   - BRSR (SEBI, top 1000 listed) — employees, plant locations, suppliers
#   - Ind AS 108 segment reporting — segment revenue, major-customer
#     concentration ("10 per cent or more")
#   - Companies Act Rule 8(3) — technology absorption, energy conservation
#   - MD&A standard headings — "Industry structure and developments",
#     "Risks and concerns", "Opportunities and threats"
# Those beat any natural phrasing of the same question.
# ============================================================

QUESTIONS: list[QuestionSpec] = [
    QuestionSpec(1, "What does the company do?", Kind.RETRIEVAL, [
        "the Company is engaged in the business of",
        "our business overview principal activities and operations",
    ]),
    QuestionSpec(2, "Who are the promoters and what is their background?", Kind.EXTERNAL, [
        "promoter and promoter group shareholding pledged shares",
        "directors of the company background experience appointment",
    ], red_flag_hint="criminal history, SEBI orders, pledging, falling promoter holding"),
    QuestionSpec(3, "What do they manufacture or provide?", Kind.RETRIEVAL, [
        "products manufactured and services rendered by the Company",
        "our product portfolio and service offerings",
    ]),
    QuestionSpec(4, "How many plants or locations, and where?", Kind.RETRIEVAL, [
        # BRSR carries this as a fixed-format table.
        "number of locations where plants and offices of the entity are situated",
        "manufacturing divisions and plant locations of the Company",
        "national and international locations plants offices total",
    ]),
    QuestionSpec(5, "Are the facilities running at full capacity?", Kind.RETRIEVAL, [
        "capacity utilisation during the year installed capacity",
        "utilisation levels ramp up of new capacity",
    ], doc_types=["CONCALL", "ANNUAL_REPORT"],
       red_flag_hint="persistently low or falling utilisation"),
    QuestionSpec(6, "What raw materials are required?", Kind.RETRIEVAL, [
        "cost of materials consumed notes to the financial statements",
        "value of imports calculated on CIF basis raw materials components",
        "consumption of raw materials imported and indigenous percentage of total",
    ], red_flag_hint="import dependence or government-regulated inputs"),
    QuestionSpec(7, "Who are the clients or end-users?", Kind.RETRIEVAL, [
        # Ind AS 108 wording — a mandatory disclosure when concentration exists.
        "information about major customers revenue from a single external customer "
        "amounting to 10 per cent or more of total revenue",
        "customers and end user industries served by the Company",
    ], red_flag_hint="a single customer being a large share of revenue"),
    QuestionSpec(8, "Who are the competitors, and how concentrated is the market?", Kind.RETRIEVAL, [
        # Standard MD&A headings, mandated by Schedule V.
        "industry structure and developments opportunities and threats",
        "competitive intensity and market share in the industry",
    ], doc_types=["ANNUAL_REPORT", "CONCALL"]),
    QuestionSpec(9, "Who are the major shareholders and what are the holding trends?",
                 Kind.STRUCTURED, red_flag_hint="falling promoter holding, rising pledge"),
    QuestionSpec(10, "Are any new products planned?", Kind.RETRIEVAL, [
        "new product launches and pipeline during the year",
        "research and development new offerings under development",
    ], doc_types=["CONCALL", "ANNUAL_REPORT"],
       red_flag_hint="expansion into a genuinely unrelated business"),
    QuestionSpec(11, "Is geographic expansion planned?", Kind.RETRIEVAL, [
        "geographic expansion into new markets and international operations",
        "exports and overseas subsidiaries presence",
    ], doc_types=["CONCALL", "ANNUAL_REPORT"]),
    QuestionSpec(12, "What is the revenue mix across segments?", Kind.RETRIEVAL, [
        "segment revenue and segment results reportable segments",
        "revenue from operations by business segment contribution",
    ]),
    QuestionSpec(13, "Is this a heavily regulated environment?", Kind.RETRIEVAL, [
        "risks and concerns regulatory and policy changes affecting operations",
        "regulatory environment licences approvals and statutory compliances",
        "government policy and tariff changes impact on the business",
    ], red_flag_hint="regulation acting as a constraint rather than a barrier to entry"),
    QuestionSpec(14, "Who are the bankers and auditors?", Kind.RETRIEVAL, [
        "statutory auditors of the Company appointment reappointment tenure",
        "resignation of auditors and reasons thereof qualification in audit report",
    ], red_flag_hint="auditor change or resignation"),
    QuestionSpec(15, "Any headcount or labour issues?", Kind.RETRIEVAL, [
        # BRSR fixed-format table, then the Directors' Report disclosure.
        "details of employees and workers permanent and other than permanent total",
        "particulars of employees remuneration rule 5 of the Companies Rules",
        "industrial relations remained cordial employee strength during the year",
    ], red_flag_hint="strikes, unrest, dependence on scarce skills"),
    QuestionSpec(16, "What are the entry barriers for new competitors?", Kind.RETRIEVAL, [
        "our competitive strengths scale integration and cost leadership",
        "barriers to entry capital intensity and regulatory approvals required",
        "outlook and strategy differentiation from competitors",
    ], doc_types=["ANNUAL_REPORT", "CONCALL"]),
    QuestionSpec(17, "Could the product be replicated in a cheap-labour country?",
                 Kind.JUDGMENT, [
        # Companies Act Rule 8(3) requires a technology-absorption disclosure.
        "technology absorption adaptation and innovation efforts made",
        "research and development expenditure patents and proprietary technology",
        "process know how and engineering capability developed in house",
    ]),
    QuestionSpec(18, "Does the company have too many subsidiaries?", Kind.RETRIEVAL, [
        "list of subsidiaries associates and joint ventures with proportion of ownership",
        "statement containing salient features of the financial statement of subsidiaries",
    ], red_flag_hint="a large number of subsidiaries, or any with unexplained purpose"),
]


# ============================================================
# ENTRY POINT
# ============================================================

async def run_business_checklist(symbol: str, raw=None) -> BusinessReportResult:
    """
    Answer all 18 questions, fanning out with asyncio.gather.

    The fan-out happens INSIDE this node rather than as 18 graph nodes: graph
    nodes are checkpoint units, and 18 of them would make the run history
    unreadable for no resumability benefit.
    """
    result = BusinessReportResult(symbol=symbol)
    budget = _SearchBudget(remaining=settings.STAGE1_WEB_SEARCH_MAX_QUESTIONS)

    answers = await asyncio.gather(
        *(_answer(symbol, spec, raw, budget) for spec in QUESTIONS),
        return_exceptions=True,
    )

    for spec, answer in zip(QUESTIONS, answers):
        if isinstance(answer, Exception):
            logger.warning(f"Stage 1 Q{spec.n} failed for {symbol}: {answer}")
            answer = Answer(spec.n, spec.question, spec.kind.value,
                            AnswerStatus.NOT_FOUND.value,
                            answer=f"Could not be answered: {answer}")
        result.answers.append(answer)
        if answer.red_flag:
            result.red_flags.append({
                "question": spec.n, "flag": answer.red_flag, "citations": answer.citations,
            })

    result.answered = sum(
        1 for a in result.answers if a.status == AnswerStatus.ANSWERED.value
    )
    result.completeness = result.answered / len(QUESTIONS)

    # Nothing here BLOCKs yet: a block must rest on a Tier-1 regulatory source
    # with a confirmed identity match, and those fetchers are not built. Until
    # then, findings are surfaced for a human to read — which is the correct
    # default anyway, since a false block is invisible when it happens.
    result.gate = "FLAG" if result.red_flags else "CLEAR"

    # Counted among ANSWERED only — a web search that came back empty is still
    # tagged WEB, and including it would make the split add up to more than the
    # answered count.
    from_web = sum(1 for a in result.answers
                   if a.source == "WEB" and a.status == AnswerStatus.ANSWERED.value)
    logger.info(
        f"Stage 1 | {symbol} | answered {result.answered}/{len(QUESTIONS)} "
        f"({result.answered - from_web} from filings, {from_web} from web) | "
        f"red flags {len(result.red_flags)} | gate {result.gate}"
    )
    return result


# ============================================================
# ONE QUESTION
# ============================================================

async def _answer(symbol: str, spec: QuestionSpec, raw, budget: "_SearchBudget") -> Answer:
    if spec.kind is Kind.STRUCTURED:
        return _answer_from_shareholding(spec, raw)

    # Q2's promoter background is not in any company-published document — no
    # annual report discloses that its own promoter has a regulatory history.
    # Web search is its PRIMARY route, not its fallback; filings are only ever
    # context for it. What web search still cannot do is confirm that an order
    # names THIS promoter rather than a namesake, so it may inform a FLAG and
    # must never produce a BLOCK.
    if spec.kind is Kind.EXTERNAL:
        web = await _web_answer(symbol, spec, budget)
        if web is not None:
            return web

    from app.memory.document_store import retrieve

    # Several phrasings per question, merged and de-duplicated. One phrasing
    # rarely covers a topic that an annual report spreads across sections.
    seen: set[tuple] = set()
    passages: list[dict] = []
    for query in spec.queries:
        try:
            for hit in await retrieve(symbol, query, top_k=settings.STAGE1_TOP_K,
                                      doc_types=spec.doc_types):
                key = (hit["citation"], hit["text"][:80])
                if key not in seen:
                    seen.add(key)
                    passages.append(hit)
        except Exception as e:
            logger.warning(f"Retrieval failed for {symbol} Q{spec.n} ({query!r}): {e}")

    # Best-scoring passages across all phrasings, capped at STAGE1_TOP_K.
    # Keeping twice as many was tried and measurably hurt — see the comment on
    # STAGE1_TOP_K in config.py.
    passages.sort(key=lambda h: h["score"], reverse=True)
    passages = passages[: settings.STAGE1_TOP_K]
    citations = list(dict.fromkeys(p["citation"] for p in passages))

    if not passages:
        # The common cause is an empty shelf, not an unanswerable question:
        # a symbol whose annual report was never ingested fails every retrieval
        # and used to score 1/18 with every answer reading "no relevant passage
        # found" — which reads as "this is unknowable" rather than "nothing was
        # downloaded". The web answers it instead.
        web = await _web_answer(symbol, spec, budget)
        if web is not None:
            return web
        return Answer(spec.n, spec.question, spec.kind.value,
                      AnswerStatus.NOT_FOUND.value,
                      answer="No relevant passage found in the ingested documents, "
                             "and web search is unavailable.",
                      source="NONE")

    if spec.kind is Kind.EXTERNAL:
        # Only reached when web search is off or failed — it is tried first for
        # this kind. Passages are still shown as context, but the status says
        # plainly that they do not settle the question.
        return Answer(
            spec.n, spec.question, spec.kind.value, AnswerStatus.NEEDS_EXTERNAL.value,
            answer=("This question cannot be settled from company-published documents. "
                    "It needs SEBI orders, the MCA disqualified-directors list and exchange "
                    "insider-trading disclosures. Passages below are context only."),
            citations=citations, passages=passages, source="DOCUMENTS",
        )

    try:
        drafted = await _synthesise(symbol, spec, passages)
    except SynthesisUnavailable as e:
        # The passages are still worth showing — they are the evidence. What
        # must not happen is counting this as ANSWERED, because `answered`
        # feeds completeness and completeness gates the verdict.
        return Answer(
            spec.n, spec.question, spec.kind.value, AnswerStatus.PARTIAL.value,
            answer=(f"Retrieved passages could not be summarised ({e}). "
                    f"The source text follows.\n\n" + "\n\n".join(
                        f"[{p['citation']}] {p['text']}" for p in passages)),
            citations=citations, passages=passages, source="DOCUMENTS",
        )

    # Status reflects whether the question was actually ANSWERED, not whether
    # passages were retrieved. Keying it off passage count (as this first did)
    # scored RELIANCE 17/18 while eight of those answers said "the excerpts do
    # not provide this" — and completeness feeds the verdict gate downstream,
    # so an inflated count silently buys a verdict the evidence does not support.
    if _is_non_answer(drafted):
        # Passages were retrieved but did not answer — a different failure from
        # retrieving nothing, and one the web can still rescue. The documents
        # were given their turn first, which is the precedence that matters.
        web = await _web_answer(symbol, spec, budget)
        if web is not None:
            return web
        status = AnswerStatus.NOT_FOUND.value
    elif len(passages) >= 2:
        status = AnswerStatus.ANSWERED.value
    else:
        status = AnswerStatus.PARTIAL.value

    return Answer(
        spec.n, spec.question, spec.kind.value, status,
        answer=drafted, citations=citations, passages=passages,
        is_opinion=spec.kind is Kind.JUDGMENT,
        red_flag=_detect_red_flag(spec, drafted),
    )


class SynthesisUnavailable(Exception):
    """
    Synthesis was meant to run and could not (API down, out of credits, rate
    limited).

    Distinct from synthesis being switched OFF, which is a supported mode: with
    it disabled the retrieved passages ARE the answer, and reading three cited
    paragraphs is a legitimate Stage 1. When it fails instead, returning those
    same passages silently reports the outage as a full answer — observed live,
    where an exhausted API key produced a clean 18/18 whose "answers" were raw
    passage dumps. Completeness gates the verdict downstream, so that is an
    outage buying a grade.
    """


# ============================================================
# WEB FALLBACK
# ============================================================

async def _web_answer(symbol: str, spec: QuestionSpec, budget: "_SearchBudget") -> Answer | None:
    """
    Answer one question from the open web, with citations.

    Returns None — rather than an empty Answer — when search is unavailable or
    the budget is spent, so the caller can fall through to its own wording. A
    None here means "not attempted"; an Answer with NOT_FOUND means "searched
    and found nothing", and those are different facts about the company.
    """
    from app.data.web_search import is_available, search_answer

    if not is_available() or not await budget.take():
        return None

    instruction = (
        f"Research the Indian listed company whose NSE ticker is {symbol}.\n"
        f"Question: {spec.question}\n\n"
        f"Answer from what you find by searching. Prefer the company's own "
        f"filings and exchange disclosures, then regulators, then established "
        f"financial press; treat aggregator and content-farm pages as weak.\n"
        f"If the search does not settle the question, reply with exactly "
        f"'NOT ANSWERED:' followed by one short sentence on what is missing. "
        f"That is a useful answer; a guess is not.\n"
        f"Be specific — names, numbers and dates rather than generalities. "
        f"3-5 sentences.\n"
    )
    if spec.red_flag_hint:
        instruction += (
            f"\nIf what you find shows evidence of: {spec.red_flag_hint} — append a "
            f"final line starting exactly 'RED FLAG:' followed by what the evidence is, "
            f"naming the source. Only if you actually found it; absence of coverage is "
            f"not evidence of absence, and must not be reported as a flag.\n"
        )
    if spec.kind is Kind.EXTERNAL:
        instruction += (
            "\nFor any regulatory action, penalty or criminal matter, state explicitly "
            "whether the source confirms it concerns THIS company's promoter rather than "
            "a person of a similar name. If you cannot confirm the identity, say so — an "
            "unverified name match is not a finding.\n"
        )
    if spec.kind is Kind.JUDGMENT:
        instruction += (
            "\nThis question calls for an opinion rather than a fact. Give your reasoning "
            "and begin the answer with 'Opinion:' so it is not read as a finding.\n"
        )

    hit = await search_answer(instruction)
    if hit is None:
        return None

    drafted = hit.text
    status = (AnswerStatus.NOT_FOUND.value if _is_non_answer(drafted)
              else AnswerStatus.ANSWERED.value)

    return Answer(
        spec.n, spec.question, spec.kind.value, status,
        answer=drafted,
        citations=hit.citations,
        is_opinion=spec.kind is Kind.JUDGMENT,
        red_flag=_detect_red_flag(spec, drafted),
        source="WEB",
        sources=hit.sources,
    )


def _answer_from_shareholding(spec: QuestionSpec, raw) -> Answer:
    """
    Q9 comes from the scraped shareholding table, not retrieval — we hold the
    numbers exactly, and retrieving prose about them would be a worse route to
    a worse answer.
    """
    if raw is None or not getattr(raw, "shareholding", None):
        return Answer(spec.n, spec.question, spec.kind.value,
                      AnswerStatus.NOT_FOUND.value,
                      answer="Shareholding table not available.")

    periods = list(raw.shareholding.keys())
    recent = periods[-4:]
    lines, red_flag = [], None

    for holder in ("Promoters", "FIIs", "DIIs", "Public"):
        series = {p: raw.shareholding[p][holder] for p in recent if holder in raw.shareholding[p]}
        if not series:
            continue
        values = list(series.values())
        lines.append(f"{holder}: {values[0]:.1f}% -> {values[-1]:.1f}% over {len(values)} quarters")
        if holder == "Promoters" and len(values) >= 2 and values[-1] < values[0] - 1.0:
            red_flag = (f"Promoter holding fell from {values[0]:.1f}% to {values[-1]:.1f}% "
                        f"over the last {len(values)} quarters")

    return Answer(
        spec.n, spec.question, spec.kind.value,
        AnswerStatus.ANSWERED.value if lines else AnswerStatus.NOT_FOUND.value,
        answer="; ".join(lines) or "No shareholding rows parsed.",
        citations=["Screener shareholding pattern"], red_flag=red_flag,
        source="SHAREHOLDING",
    )


# Phrasings a model reaches for when the passages do not answer. The explicit
# 'NOT ANSWERED:' marker is what it is asked for; the rest are the fallbacks it
# uses anyway, kept because a silent non-answer counted as an answer is the
# failure this exists to prevent.
_NON_ANSWER_MARKERS = (
    "not answered:",
    "do not provide", "does not provide",
    "do not specify", "does not specify",
    "do not mention", "does not mention",
    "do not explicitly state", "does not explicitly state",
    "cannot answer", "cannot provide", "cannot determine",
    "no relevant passage",
)

# What a model appends when asked for a red flag and there is none.
_EMPTY_FLAGS = {"none", "none.", "n/a", "no", "no red flag", "not applicable", ""}


def _first_sentence(text: str) -> str:
    """The first sentence, for deciding whether an answer answers."""
    head = text.strip().split("\n", 1)[0]
    cut = head.find(". ")
    return (head[: cut + 1] if cut != -1 else head).strip()


def _is_non_answer(drafted: str) -> bool:
    """
    True when the answer does not answer.

    Only the FIRST SENTENCE is tested, because that is where an answer either
    delivers or declines. Two narrower rules were tried and both were wrong:

      - matching anywhere in the text threw away real answers that hedged at
        the end ("...has 235 subsidiaries. The passages do not provide each
        one's purpose.")
      - a fixed character window did too, since the hedge often begins well
        inside it.

    Getting this wrong in the other direction is worse still: keying status off
    "did we retrieve passages" scored RELIANCE 17/18 while eight of those
    answers declined to answer. Completeness gates the verdict downstream, so
    an inflated count buys a verdict the evidence does not support.
    """
    if not drafted:
        return True
    lead = _first_sentence(drafted).lower()
    return any(marker in lead for marker in _NON_ANSWER_MARKERS)


def _detect_red_flag(spec: QuestionSpec, drafted: str) -> str | None:
    """
    A red flag is raised only when the drafted answer says so explicitly.

    Deliberately not keyword matching over the source passages: an annual
    report contains the word "litigation" on many pages that describe nothing
    adverse, and a checklist that cries wolf gets ignored exactly when it
    matters.
    """
    if not spec.red_flag_hint or not drafted:
        return None
    marker = "RED FLAG:"
    if marker not in drafted:
        return None

    flag = drafted.split(marker, 1)[1].strip().split("\n")[0][:300]
    # Asked "flag this if present", a model will often answer "RED FLAG: None."
    # Recording that as a finding puts a red flag on a clean company.
    if flag.strip().lower().rstrip(".") in {f.rstrip(".") for f in _EMPTY_FLAGS}:
        return None
    return flag


# ============================================================
# SYNTHESIS — draft from the passages, never beyond them
# ============================================================

async def _synthesise(symbol: str, spec: QuestionSpec, passages: list[dict]) -> str:
    """
    Draft an answer from the retrieved passages.

    With synthesis disabled the passages themselves are returned, which is a
    usable Stage 1 in its own right: reading three cited paragraphs beats
    reading a 190-page annual report, and every claim is sourced by
    construction because you read the source.
    """
    context = "\n\n".join(
        f"[{p['citation']}] {p['text']}" for p in passages
    )

    if not settings.STAGE1_SYNTHESIS_ENABLED or not settings.OPENAI_API_KEY:
        return context

    instruction = (
        f"Question about {symbol}: {spec.question}\n\n"
        f"Answer ONLY from the passages below. Do not use outside knowledge.\n"
        f"If the passages do NOT answer the question, reply with exactly "
        f"'NOT ANSWERED:' followed by one short sentence on what is missing. "
        f"That is a useful answer; a guess is not.\n"
        f"Otherwise cite the page in brackets for each claim, exactly as it "
        f"appears in the passage label. 2-4 sentences.\n"
    )
    if spec.red_flag_hint:
        instruction += (
            f"\nIf the passages show evidence of: {spec.red_flag_hint} — append a final "
            f"line starting exactly 'RED FLAG:' followed by what the evidence is. "
            f"Only if the passages actually show it. Absence of mention is not evidence.\n"
        )
    if spec.kind is Kind.JUDGMENT:
        instruction += (
            "\nThis question calls for an opinion rather than a fact. Give your reasoning "
            "and begin the answer with 'Opinion:' so it is not read as a finding.\n"
        )

    try:
        from openai import OpenAI

        def _call():
            client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=60.0)
            response = client.chat.completions.create(
                model=settings.STAGE1_SYNTHESIS_MODEL,
                messages=[
                    {"role": "system", "content":
                        "You answer questions about Indian listed companies strictly from "
                        "supplied filing excerpts. You never add facts of your own, and you "
                        "say when the excerpts do not answer the question."},
                    {"role": "user", "content": instruction + "\nPASSAGES:\n" + context},
                ],
                max_tokens=400,
                temperature=0.1,
            )
            return response.choices[0].message.content.strip()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _call)

    except Exception as e:
        logger.warning(f"Stage 1 synthesis failed for {symbol} Q{spec.n}: {e}")
        raise SynthesisUnavailable(str(e)) from e
