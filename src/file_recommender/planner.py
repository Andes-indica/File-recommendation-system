"""Small, transparent retrieval planner for the initial search increment."""

from dataclasses import dataclass
import re


METADATA_FILTER = re.compile(
    r"\b(?:type|ext):[\w.]+|\b(?:after|before):\d{4}-\d{2}-\d{2}\b",
    re.IGNORECASE,
)
FILENAME_INTENT = re.compile(r"\b(?:file|filename|named)\b", re.IGNORECASE)
SEMANTIC_INTENT = re.compile(
    r"\b(?:similar to|related to|about|conceptually|meaning of|in other words)\b",
    re.IGNORECASE,
)
TOKEN = re.compile(r"[\w.-]+", re.UNICODE)


@dataclass(frozen=True)
class RetrievalPlan:
    strategy: str
    reason: str
    terms: tuple[str, ...]


def plan_query(query: str, semantic_available: bool = False) -> RetrievalPlan:
    """Select a low-cost retrieval strategy from query shape and explicit filters."""
    cleaned = query.strip()
    terms = tuple(TOKEN.findall(cleaned))

    if METADATA_FILTER.search(cleaned):
        return RetrievalPlan(
            "metadata",
            "The query contains an explicit file type or date filter.",
            terms,
        )

    if FILENAME_INTENT.search(cleaned) or any("." in term for term in terms):
        return RetrievalPlan(
            "filename",
            "The query points to a specific name or file extension.",
            terms,
        )

    if semantic_available and SEMANTIC_INTENT.search(cleaned):
        return RetrievalPlan(
            "semantic",
            "The query asks for related meaning rather than exact wording.",
            terms,
        )

    if len(terms) >= 3:
        return RetrievalPlan(
            "hybrid",
            "A multi-term query benefits from combining filename and full-text matches.",
            terms,
        )

    return RetrievalPlan(
        "keyword",
        "A short query can be answered with a single full-text search.",
        terms,
    )