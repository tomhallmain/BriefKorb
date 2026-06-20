"""Fuzzy entity resolution with a batchable manual-review queue.

Flow
----
1. `resolve_or_queue()` is called for each candidate org name extracted from an email.
   - score >= HIGH_THRESHOLD  → confident match, return existing URI immediately.
   - LOW_THRESHOLD <= score < HIGH_THRESHOLD → ambiguous; mint new URI for now, queue for review.
   - score < LOW_THRESHOLD    → no match; mint new URI, no review needed.
2. The UI calls `get_pending()` to show the review list.
3. The user calls `approve(ids)` / `reject(ids)` (or the _all variants) in batch.
4. `EntityGraphManager.apply_approved_resolutions()` reads approved items and writes
   owl:sameAs triples, then persists the graph.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import re

from rapidfuzz import fuzz, process
from rdflib import URIRef

HIGH_THRESHOLD = 90
LOW_THRESHOLD = 70

# Maps common legal-entity suffix variants to a canonical abbreviation.
# Multi-word entries must be substituted before single-word ones.
_SUFFIX_MULTI: list[tuple[str, str]] = [
    ("limited liability company", "llc"),
    ("limited liability partnership", "llp"),
    ("limited partnership", "lp"),
    ("public limited company", "plc"),
]
_SUFFIX_SINGLE: dict[str, str] = {
    "corporation": "corp", "corp.": "corp",
    "incorporated": "inc", "inc.": "inc",
    "limited": "ltd",      "ltd.": "ltd",
    "company": "co",       "co.": "co",
    "l.l.c.": "llc",      "llc.": "llc",
    "p.l.c.": "plc",      "plc.": "plc",
    "l.l.p.": "llp",      "llp.": "llp",
    "&": "and",
}


def _canonical_org_name(name: str) -> str:
    """Normalise org name for fuzzy comparison.

    Collapses legal-entity suffix variants so that names differing only in
    how they abbreviate a suffix ("Acme Corporation" vs "Acme Corp.") score
    as identical before the fuzzy scorer runs.
    """
    s = name.lower().strip()
    for phrase, canon in _SUFFIX_MULTI:
        s = re.sub(r'\b' + re.escape(phrase) + r'\b', canon, s)
    tokens = s.split()
    out = []
    for tok in tokens:
        bare = tok.rstrip(".,;:")
        out.append(_SUFFIX_SINGLE.get(tok, _SUFFIX_SINGLE.get(bare, bare)))
    return " ".join(t for t in out if t)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ResolutionCandidate:
    id: str
    candidate_uri: str
    candidate_name: str
    match_uri: str
    match_name: str
    score: float
    source_email_id: str
    status: str = "pending"         # pending | approved | rejected
    created_at: str = field(default_factory=_now)
    resolved_at: Optional[str] = None
    note: Optional[str] = None


class EntityResolutionQueue:
    """Persistent queue for org-name matches that need human review."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._items: List[ResolutionCandidate] = []
        self._load()

    # ------------------------------------------------------------------
    # Resolution logic
    # ------------------------------------------------------------------

    def resolve_or_queue(
        self,
        candidate_name: str,
        candidate_uri: URIRef,
        existing_orgs: List[Tuple[URIRef, str]],
        source_email_id: str = "",
    ) -> Tuple[URIRef, bool]:
        """Return (uri_to_use, was_queued).

        uri_to_use is the existing URI on a confident match, otherwise candidate_uri.
        was_queued is True when the item was added to the review queue.
        """
        if not existing_orgs:
            return candidate_uri, False

        norm_candidate = _canonical_org_name(candidate_name)
        norm_names = [_canonical_org_name(name) for _, name in existing_orgs]
        result = process.extractOne(norm_candidate, norm_names, scorer=fuzz.token_sort_ratio)
        if result is None:
            return candidate_uri, False

        _, score, idx = result
        best_name = existing_orgs[idx][1]  # original (un-normalised) name for the queue record
        best_uri = existing_orgs[idx][0]

        if score >= HIGH_THRESHOLD:
            return best_uri, False

        if score >= LOW_THRESHOLD:
            item = ResolutionCandidate(
                id=str(uuid.uuid4()),
                candidate_uri=str(candidate_uri),
                candidate_name=candidate_name,
                match_uri=str(best_uri),
                match_name=best_name,
                score=round(score, 1),
                source_email_id=source_email_id,
            )
            self._items.append(item)
            self._save()
            return candidate_uri, True

        return candidate_uri, False

    # ------------------------------------------------------------------
    # Batch review
    # ------------------------------------------------------------------

    def get_pending(self) -> List[ResolutionCandidate]:
        return [i for i in self._items if i.status == "pending"]

    def get_all(self) -> List[ResolutionCandidate]:
        return list(self._items)

    def approve(self, ids: List[str]) -> List[ResolutionCandidate]:
        """Mark candidates approved. Returns the approved items (caller writes sameAs)."""
        id_set = set(ids)
        approved = []
        for item in self._items:
            if item.id in id_set and item.status == "pending":
                item.status = "approved"
                item.resolved_at = _now()
                approved.append(item)
        self._save()
        return approved

    def reject(self, ids: List[str]) -> int:
        """Mark candidates rejected (they keep their minted URI). Returns count."""
        id_set = set(ids)
        count = 0
        for item in self._items:
            if item.id in id_set and item.status == "pending":
                item.status = "rejected"
                item.resolved_at = _now()
                count += 1
        self._save()
        return count

    def approve_all_pending(self) -> List[ResolutionCandidate]:
        return self.approve([i.id for i in self.get_pending()])

    def reject_all_pending(self) -> int:
        return self.reject([i.id for i in self.get_pending()])

    def set_note(self, id: str, note: str) -> bool:
        for item in self._items:
            if item.id == id:
                item.note = note
                self._save()
                return True
        return False

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._items = [ResolutionCandidate(**r) for r in json.load(f)]
        except FileNotFoundError:
            self._items = []

    def _save(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump([asdict(i) for i in self._items], f, ensure_ascii=False, indent=2)
