"""entity_graph — RDF-backed entity extraction for BriefKorb.

Public API
----------
    mgr = EntityGraphManager("/path/to/storage/dir")
    postings = mgr.process_message(email_message)
    pending  = mgr.get_pending_resolutions()
    mgr.approve_resolutions(ids)          # mark approved in queue
    mgr.apply_approved_resolutions()      # write owl:sameAs to graph
    mgr.inject_external(triples)          # add data from outside emails
    rows     = mgr.query_job_postings()
    mgr.query(sparql_string)              # raw SPARQL

Named graphs
------------
    bk:graph/email-derived  — everything extracted from emails
    bk:graph/external       — externally injected triples
    bk:graph/inferred       — owl:sameAs and other computed assertions
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from rdflib import Literal, URIRef
from rdflib.namespace import RDF, FOAF, XSD

from .extractors import ExtractedJobPosting, JobPostingExtractor
from .graph import EntityGraph
from .namespaces import BK, SCHEMA
from .resolution import EntityResolutionQueue, ResolutionCandidate
from .storage import EntityStorage

if TYPE_CHECKING:
    from email_server import EmailMessage

__all__ = [
    "EntityGraphManager",
    "ExtractedJobPosting",
    "ResolutionCandidate",
]

# Per-stage type URIs used in SPARQL VALUES clauses
_STAGE_TYPES = {
    "active":     "schema:JobPosting",
    "historical": "bk:HistoricalJobPosting",
    "signal":     "bk:HiringSignal",
}

_JOB_QUERY = """\
PREFIX schema: <https://schema.org/>
PREFIX bk:     <https://briefkorb.local/schema#>
PREFIX foaf:   <http://xmlns.com/foaf/0.1/>

SELECT ?job ?stage ?title ?orgName ?location ?url ?salary ?date ?senderEmail
WHERE {{
    VALUES ?type {{ {type_values} }}
    ?job a ?type .
    BIND(STR(?type) AS ?stage)
    OPTIONAL {{ ?job schema:title       ?title    }}
    OPTIONAL {{ ?job schema:jobLocation ?location }}
    OPTIONAL {{ ?job schema:url         ?url      }}
    OPTIONAL {{ ?job bk:salary          ?salary   }}
    OPTIONAL {{ ?job schema:datePosted  ?date     }}
    OPTIONAL {{
        ?job schema:hiringOrganization ?org .
        ?org schema:name ?orgName
    }}
    OPTIONAL {{
        ?job bk:foundIn ?msg .
        ?msg bk:fromSender ?sender .
        ?sender foaf:mbox ?senderEmail
    }}
    {filter}
}}
ORDER BY DESC(?date)
LIMIT {limit}
"""


class EntityGraphManager:
    """Coordinates extraction, graph storage, and entity resolution."""

    TTL_ACTIVE_DAYS = 7
    TTL_HISTORICAL_DAYS = 120   # ~4 months
    TTL_ARCHIVAL_DAYS = 730     # ~2 years

    def __init__(
        self,
        storage_dir: str,
        ttl_active_days: int = TTL_ACTIVE_DAYS,
        ttl_historical_days: int = TTL_HISTORICAL_DAYS,
        ttl_archival_days: int = TTL_ARCHIVAL_DAYS,
    ) -> None:
        os.makedirs(storage_dir, exist_ok=True)
        self._ttl_active = ttl_active_days
        self._ttl_historical = ttl_historical_days
        self._ttl_archival = ttl_archival_days
        self._graph = EntityGraph()
        self._storage = EntityStorage(os.path.join(storage_dir, "entity_graph.nq"))
        self._queue = EntityResolutionQueue(os.path.join(storage_dir, "resolution_queue.json"))
        self._extractor = JobPostingExtractor()
        self._storage.load(self._graph)
        self.age_postings()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def age_postings(
        self,
        ttl_active_days: int | None = None,
        ttl_historical_days: int | None = None,
        ttl_archival_days: int | None = None,
    ) -> dict[str, int]:
        """Transition job postings through their lifecycle stages.

        Stages
        ------
        schema:JobPosting        active    (< ttl_active_days)
            → all fields: title, url, location, salary, org, date

        bk:HistoricalJobPosting  recent    (ttl_active .. ttl_historical)
            → org + date + title + location
            → apply URL and salary stripped (links are stale; salary is ephemeral)
            → bk:foundIn link dropped; orphaned EmailMessage nodes removed

        bk:HiringSignal          archival  (ttl_historical .. ttl_archival)
            → org + date only
            → useful for "this org tends to hire in Q1" style inference

        [removed]                (> ttl_archival_days)

        Organization and sender nodes are never removed — they accumulate
        history across posting cycles and are the basis for long-term inference.

        Returns counts of postings transitioned at each stage.
        """
        active_cutoff = datetime.now(timezone.utc) - timedelta(
            days=ttl_active_days if ttl_active_days is not None else self._ttl_active
        )
        historical_cutoff = datetime.now(timezone.utc) - timedelta(
            days=ttl_historical_days if ttl_historical_days is not None else self._ttl_historical
        )
        archival_cutoff = datetime.now(timezone.utc) - timedelta(
            days=ttl_archival_days if ttl_archival_days is not None else self._ttl_archival
        )
        now_lit = Literal(datetime.now(timezone.utc).isoformat(), datatype=XSD.dateTime)

        g = self._graph._g
        eg = self._graph.email_graph
        counts = {"to_historical": 0, "to_signal": 0, "removed": 0}

        def _parse_date(raw) -> Optional[datetime]:
            if raw is None:
                return None
            try:
                dt = datetime.fromisoformat(str(raw))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                return None

        # ------------------------------------------------------------------
        # Stage 1 → 2: schema:JobPosting → bk:HistoricalJobPosting
        # ------------------------------------------------------------------
        for row in list(g.query("""\
PREFIX schema: <https://schema.org/>
PREFIX bk:     <https://briefkorb.local/schema#>
SELECT ?job ?date ?msg WHERE {
    ?job a schema:JobPosting .
    OPTIONAL { ?job schema:datePosted ?date }
    OPTIONAL { ?job bk:foundIn ?msg }
}""")):
            date = _parse_date(row.date)
            # No date or undatable → treat as immediately stale
            if date is not None and date >= active_cutoff:
                continue
            g.remove((row.job, RDF.type, SCHEMA.JobPosting))
            eg.add((row.job, RDF.type, BK.HistoricalJobPosting))
            # Strip ephemeral fields
            g.remove((row.job, SCHEMA.url, None))
            g.remove((row.job, BK.salary, None))
            # Drop the message link; remove orphaned EmailMessage node
            if row.msg:
                g.remove((row.job, BK.foundIn, None))
                if not any(True for _ in g.subjects(BK.foundIn, row.msg)):
                    g.remove((row.msg, None, None))
                    g.remove((None, None, row.msg))
            eg.add((row.job, BK.transitionedAt, now_lit))
            counts["to_historical"] += 1

        # ------------------------------------------------------------------
        # Stage 2 → 3: bk:HistoricalJobPosting → bk:HiringSignal
        # ------------------------------------------------------------------
        for row in list(g.query("""\
PREFIX schema: <https://schema.org/>
PREFIX bk:     <https://briefkorb.local/schema#>
SELECT ?job ?date WHERE {
    ?job a bk:HistoricalJobPosting .
    OPTIONAL { ?job schema:datePosted ?date }
}""")):
            date = _parse_date(row.date)
            if date is not None and date >= historical_cutoff:
                continue
            g.remove((row.job, RDF.type, BK.HistoricalJobPosting))
            eg.add((row.job, RDF.type, BK.HiringSignal))
            # Strip title and location; keep only org + date
            g.remove((row.job, SCHEMA.title, None))
            g.remove((row.job, SCHEMA.jobLocation, None))
            g.remove((row.job, BK.transitionedAt, None))
            eg.add((row.job, BK.transitionedAt, now_lit))
            counts["to_signal"] += 1

        # ------------------------------------------------------------------
        # Stage 3 → removed: bk:HiringSignal beyond archival window
        # ------------------------------------------------------------------
        for row in list(g.query("""\
PREFIX schema: <https://schema.org/>
PREFIX bk:     <https://briefkorb.local/schema#>
SELECT ?job ?date WHERE {
    ?job a bk:HiringSignal .
    OPTIONAL { ?job schema:datePosted ?date }
}""")):
            date = _parse_date(row.date)
            if date is not None and date >= archival_cutoff:
                continue
            g.remove((row.job, None, None))
            g.remove((None, None, row.job))
            counts["removed"] += 1

        if any(counts.values()):
            self._storage.save(self._graph)

        return counts

    def process_message(self, message: "EmailMessage") -> List[ExtractedJobPosting]:
        """Extract job postings from one email and persist. Returns found postings."""
        postings = self._extractor.extract(message)
        if postings:
            for idx, posting in enumerate(postings):
                self._ingest_posting(message, posting, idx)
            self._storage.save(self._graph)
        return postings

    def process_messages(self, messages: List["EmailMessage"]) -> int:
        """Batch-process a list of emails. Ages postings first, then saves once
        at the end. Returns count of new job postings found."""
        self.age_postings()
        total = 0
        for message in messages:
            postings = self._extractor.extract(message)
            for idx, posting in enumerate(postings):
                self._ingest_posting(message, posting, idx)
            total += len(postings)
        if total:
            self._storage.save(self._graph)
        return total

    def _ingest_posting(
        self, message: "EmailMessage", posting: ExtractedJobPosting, index: int
    ) -> None:
        g = self._graph.email_graph

        # Sender + domain
        _, sender_email = parseaddr(message.sender or "")
        sender_email = sender_email.lower().strip()
        sender_uri = self._graph.sender_uri(sender_email)
        g.add((sender_uri, RDF.type, BK.EmailSender))
        g.add((sender_uri, FOAF.mbox, Literal(sender_email)))

        if "@" in sender_email:
            domain = sender_email.split("@", 1)[1]
            domain_uri = self._graph.domain_uri(domain)
            g.add((domain_uri, RDF.type, BK.Domain))
            g.add((sender_uri, BK.senderDomain, domain_uri))

        # Email message node
        msg_uri = self._graph.message_uri(message.id)
        g.add((msg_uri, RDF.type, BK.EmailMessage))
        g.add((msg_uri, BK.fromSender, sender_uri))
        g.add((msg_uri, SCHEMA.name, Literal(message.subject or "")))
        received = message.received_date
        if isinstance(received, datetime):
            g.add((msg_uri, SCHEMA.dateReceived, Literal(received.isoformat(), datatype=XSD.dateTime)))

        # Organization (with fuzzy resolution)
        org_uri: Optional[URIRef] = None
        if posting.hiring_org:
            candidate_uri = self._graph.org_uri(posting.hiring_org)
            existing = self._graph.get_all_org_names()
            resolved_uri, _ = self._queue.resolve_or_queue(
                posting.hiring_org, candidate_uri, existing, source_email_id=message.id
            )
            g.add((candidate_uri, RDF.type, SCHEMA.Organization))
            g.add((candidate_uri, SCHEMA.name, Literal(posting.hiring_org)))
            org_uri = resolved_uri

        # Job posting node
        job_uri = self._graph.job_uri(message.id, index)
        g.add((job_uri, RDF.type, SCHEMA.JobPosting))
        if posting.title:
            g.add((job_uri, SCHEMA.title, Literal(posting.title)))
        if posting.apply_url:
            g.add((job_uri, SCHEMA.url, Literal(posting.apply_url)))
        if posting.location:
            g.add((job_uri, SCHEMA.jobLocation, Literal(posting.location)))
        if posting.salary:
            g.add((job_uri, BK.salary, Literal(posting.salary)))
        if isinstance(received, datetime):
            g.add((job_uri, SCHEMA.datePosted, Literal(received.isoformat(), datatype=XSD.dateTime)))
        if org_uri is not None:
            g.add((job_uri, SCHEMA.hiringOrganization, org_uri))
        g.add((job_uri, BK.foundIn, msg_uri))

    # ------------------------------------------------------------------
    # Entity resolution queue
    # ------------------------------------------------------------------

    def get_pending_resolutions(self) -> List[ResolutionCandidate]:
        return self._queue.get_pending()

    def get_all_resolutions(self) -> List[ResolutionCandidate]:
        return self._queue.get_all()

    def approve_resolutions(self, ids: List[str]) -> List[ResolutionCandidate]:
        """Mark items approved. Call apply_approved_resolutions() to write sameAs."""
        return self._queue.approve(ids)

    def reject_resolutions(self, ids: List[str]) -> int:
        return self._queue.reject(ids)

    def approve_all_resolutions(self) -> List[ResolutionCandidate]:
        return self._queue.approve_all_pending()

    def reject_all_resolutions(self) -> int:
        return self._queue.reject_all_pending()

    def apply_approved_resolutions(self) -> int:
        """Write owl:sameAs to the inferred graph for all approved queue items."""
        approved = [i for i in self._queue.get_all() if i.status == "approved"]
        for item in approved:
            self._graph.add_same_as(URIRef(item.candidate_uri), URIRef(item.match_uri))
        if approved:
            self._storage.save(self._graph)
        return len(approved)

    # ------------------------------------------------------------------
    # External data injection
    # ------------------------------------------------------------------

    def inject_external(
        self,
        triples: List[tuple],
        graph_uri: Optional[URIRef] = None,
    ) -> None:
        """Add (s, p, o) triples to the external graph (or a custom named graph)."""
        target = (
            self._graph._g.get_context(graph_uri)
            if graph_uri is not None
            else self._graph.external_graph
        )
        for s, p, o in triples:
            target.add((s, p, o))
        self._storage.save(self._graph)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def query_job_postings(
        self,
        org_name: Optional[str] = None,
        sender_email: Optional[str] = None,
        stages: Optional[List[str]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Return job postings as plain dicts, optionally filtered.

        stages: subset of ["active", "historical", "signal"]; defaults to all three.
        """
        _STAGE_URIS = {
            "active":     "<https://schema.org/JobPosting>",
            "historical": "<https://briefkorb.local/schema#HistoricalJobPosting>",
            "signal":     "<https://briefkorb.local/schema#HiringSignal>",
        }
        wanted = stages if stages else list(_STAGE_URIS)
        type_uris = " ".join(_STAGE_URIS[s] for s in wanted if s in _STAGE_URIS)

        filters: List[str] = []
        if org_name:
            escaped = org_name.replace('"', '\\"')
            filters.append(f'FILTER(CONTAINS(LCASE(STR(?orgName)), LCASE("{escaped}")))')
        if sender_email:
            escaped = sender_email.replace('"', '\\"')
            filters.append(f'FILTER(STR(?senderEmail) = "{escaped}")')

        sparql = _JOB_QUERY.format(
            type_values=type_uris,
            filter="\n    ".join(filters),
            limit=limit,
        )
        rows = []
        for row in self._graph.query(sparql):
            stage_uri = str(row.stage)
            if "HistoricalJobPosting" in stage_uri:
                stage = "historical"
            elif "HiringSignal" in stage_uri:
                stage = "signal"
            else:
                stage = "active"
            rows.append({
                "job_uri": str(row.job),
                "stage": stage,
                "title": str(row.title) if row.title else None,
                "org_name": str(row.orgName) if row.orgName else None,
                "location": str(row.location) if row.location else None,
                "apply_url": str(row.url) if row.url else None,
                "salary": str(row.salary) if row.salary else None,
                "date_posted": str(row.date) if row.date else None,
                "sender_email": str(row.senderEmail) if row.senderEmail else None,
            })
        return rows

    def query_orgs(self) -> List[Dict[str, Any]]:
        """Return all known organizations with posting counts across all lifecycle stages."""
        sparql = """\
PREFIX schema: <https://schema.org/>
PREFIX bk:     <https://briefkorb.local/schema#>

SELECT ?org ?name
    (COUNT(?active)     AS ?activeCount)
    (COUNT(?historical) AS ?historicalCount)
    (COUNT(?signal)     AS ?signalCount)
WHERE {
    ?org a schema:Organization ;
         schema:name ?name .
    OPTIONAL { ?active     a schema:JobPosting        ; schema:hiringOrganization ?org }
    OPTIONAL { ?historical a bk:HistoricalJobPosting  ; schema:hiringOrganization ?org }
    OPTIONAL { ?signal     a bk:HiringSignal          ; schema:hiringOrganization ?org }
}
GROUP BY ?org ?name
ORDER BY DESC(?activeCount) DESC(?historicalCount) DESC(?signalCount) ?name
"""
        return [
            {
                "uri": str(r.org),
                "name": str(r.name),
                "active_count": int(r.activeCount),
                "historical_count": int(r.historicalCount),
                "signal_count": int(r.signalCount),
                "total_count": int(r.activeCount) + int(r.historicalCount) + int(r.signalCount),
            }
            for r in self._graph.query(sparql)
        ]

    def query(self, sparql: str):
        """Raw SPARQL query against the full conjunctive graph."""
        return self._graph.query(sparql)

    def triple_count(self) -> int:
        return len(self._graph)
