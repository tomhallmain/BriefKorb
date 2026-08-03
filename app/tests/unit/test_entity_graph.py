"""Tests for the entity_graph module.

Isolation guarantees
--------------------
- All file I/O is confined to pytest's ``tmp_path`` fixture.
- spaCy is unconditionally stubbed out (``_no_spacy`` autouse fixture) so
  tests run without any model installed.
- No email server config, app_info_cache, token storage, or database is
  touched.  EntityGraphManager has no dependency on those subsystems.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from entity_graph import EntityGraphManager, ExtractedJobPosting
from entity_graph.extractors.job_posting import JobPostingExtractor
from entity_graph.extractors.text_utils import html_to_text, normalize_whitespace, truncate_for_nlp
from entity_graph.graph import EntityGraph, _slugify
from entity_graph.namespaces import BK, SCHEMA
from entity_graph.resolution import EntityResolutionQueue, ResolutionCandidate
from email_server import EmailMessage
from rdflib import Literal, URIRef
from rdflib.namespace import OWL, RDF


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _msg(
    subject: str = "Test",
    body: str = "",
    sender: str = "Test Sender <test@example.com>",
    received_date: datetime | None = None,
    msg_id: str = "msg-001",
) -> EmailMessage:
    return EmailMessage(
        id=msg_id,
        subject=subject,
        sender=sender,
        recipients=["me@example.com"],
        received_date=received_date or datetime.now(timezone.utc),
        body=body,
        is_read=False,
        provider="test",
    )


def _job_body(
    *,
    company: str = "TechCorp",
    title: str = "Senior Engineer",
    location: str = "Remote",
    salary: str = "$130,000",
    apply_url: str | None = None,
) -> str:
    link = f'<a href="{apply_url}">Apply Now</a>' if apply_url else "Apply now."
    return (
        f"Company: {company}\n"
        f"Position: {title}\n"
        f"Location: {location}\n"
        f"Salary: {salary}\n"
        f"{link} Equal opportunity employer."
    )


# ---------------------------------------------------------------------------
# Autouse fixtures — applied to every test in this file
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_spacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub spaCy completely so tests run without an installed model."""
    import entity_graph.extractors.job_posting as jp
    monkeypatch.setattr(jp, "_nlp", None)
    monkeypatch.setattr(jp, "_nlp_load_attempted", False)
    monkeypatch.setattr(jp, "_get_nlp", lambda: None)


@pytest.fixture
def mgr(tmp_path: Path) -> EntityGraphManager:
    return EntityGraphManager(str(tmp_path / "eg"))


# ===========================================================================
# text_utils
# ===========================================================================

class TestHtmlToText:
    def test_strips_tags(self):
        assert "Hello" in html_to_text("<p>Hello <b>world</b></p>")
        assert "<" not in html_to_text("<p>Hello</p>")

    def test_skips_script_content(self):
        result = html_to_text("<p>visible</p><script>var secret = 1;</script>")
        assert "visible" in result
        assert "secret" not in result

    def test_skips_style_content(self):
        result = html_to_text("<style>body{color:red}</style><p>text</p>")
        assert "color" not in result
        assert "text" in result

    def test_block_tags_add_whitespace(self):
        result = html_to_text("<div>A</div><div>B</div>")
        assert "A" in result
        assert "B" in result

    def test_plain_text_passthrough(self):
        assert html_to_text("just text") == "just text"

    def test_empty_string(self):
        assert html_to_text("") == ""


def test_normalize_whitespace_strips_ends():
    assert normalize_whitespace("  hello world  ") == "hello world"


def test_normalize_whitespace_collapses_inner():
    # normalize_whitespace collapses all runs of spaces/tabs to a single space
    assert normalize_whitespace("a  b") == "a b"


def test_truncate_for_nlp_respects_limit():
    assert len(truncate_for_nlp("x" * 200, max_chars=50)) == 50


def test_truncate_for_nlp_short_text_unchanged():
    assert truncate_for_nlp("short") == "short"


# ===========================================================================
# JobPostingExtractor — detection
# ===========================================================================

class TestDetection:
    @pytest.fixture(autouse=True)
    def extractor(self):
        self.ex = JobPostingExtractor()

    def _detect(self, subject="", body=""):
        is_job, score, signals = self.ex._detect(_msg(subject=subject, body=body))
        return is_job, score, signals

    def test_job_alert_subject_detected(self):
        is_job, _, signals = self._detect(subject="Job Alert: new openings", body="apply now")
        assert is_job
        assert any("subject:" in s for s in signals)

    def test_apply_now_body_detected(self):
        # "apply now" alone (0.30) is below the single-signal threshold;
        # combine with "requirements:" so two signals are present
        body = "We have an opening. Apply now. Requirements: 3+ years experience."
        is_job, _, signals = self._detect(subject="Hi", body=body)
        assert is_job
        assert any("body:apply now" in s for s in signals)

    def test_apply_now_alone_not_detected(self):
        # A lone "apply now" in promo copy must not trigger extraction
        is_job, _, _ = self._detect(subject="Special offer", body="Apply now for 20% off.")
        assert not is_job

    def test_equal_opportunity_employer_strong_signal(self):
        # Weight raised to 0.60 so this signal passes the single-signal guard
        is_job, score, _ = self._detect(subject="Role", body="We are an equal opportunity employer.")
        assert is_job
        assert score >= 0.55

    def test_negative_signal_suppresses_application_status_email(self):
        body = (
            "Thank you for applying. We have received your application and "
            "will be in touch. Apply now to browse more roles."
        )
        is_job, _, _ = self._detect(subject="Your application", body=body)
        assert not is_job

    def test_negative_signal_suppresses_order_confirmation(self):
        is_job, _, _ = self._detect(
            subject="Order confirmation",
            body="Your order has been received. Order number: 12345. Apply now for rewards.",
        )
        assert not is_job

    def test_plain_conversation_not_detected(self):
        is_job, _, _ = self._detect(subject="Lunch?", body="Are you free tomorrow?")
        assert not is_job

    def test_multiple_signals_produce_higher_score(self):
        _, score_multi, _ = self._detect(
            subject="We're hiring a developer",
            body="Apply now. Requirements: 3+ years. Equal opportunity employer.",
        )
        _, score_single, _ = self._detect(subject="We're hiring", body="")
        assert score_multi > score_single

    def test_were_hiring_subject_high_weight(self):
        is_job, score, _ = self._detect(subject="We're hiring!", body="")
        assert is_job
        assert score >= 0.60


# ===========================================================================
# JobPostingExtractor — field extraction
# ===========================================================================

class TestFieldExtraction:
    @pytest.fixture(autouse=True)
    def extractor(self):
        self.ex = JobPostingExtractor()

    def _extract(self, body: str, subject: str = "Job Alert") -> list[ExtractedJobPosting]:
        return self.ex.extract(_msg(subject=subject, body=body))

    def test_apply_url_from_anchor(self):
        body = '<a href="https://jobs.example.com/42">Apply Now</a> Equal opportunity employer.'
        postings = self._extract(body)
        assert len(postings) == 1
        assert postings[0].apply_url == "https://jobs.example.com/42"

    def test_multiple_apply_links_give_multiple_postings(self):
        body = (
            '<a href="https://jobs.example.com/1">Apply Now</a>'
            '<a href="https://jobs.example.com/2">Apply Here</a>'
            " Equal opportunity employer."
        )
        assert len(self._extract(body)) == 2

    def test_title_from_label(self):
        body = "Position: Senior Python Engineer\nApply now. Equal opportunity employer."
        postings = self._extract(body)
        assert postings
        assert "Python" in (postings[0].title or "")

    def test_title_falls_back_to_subject(self):
        body = "We are an equal opportunity employer. Apply now."
        postings = self._extract(body, subject="Data Scientist at Acme")
        assert postings
        assert postings[0].title == "Data Scientist at Acme"

    def test_remote_location_extracted(self):
        body = "This is a fully remote role. Apply now. Equal opportunity employer."
        postings = self._extract(body)
        assert postings
        assert "remote" in (postings[0].location or "").lower()

    def test_location_label_extracted(self):
        body = "Location: New York, NY\nApply now. Equal opportunity employer."
        postings = self._extract(body)
        assert postings
        assert "New York" in (postings[0].location or "")

    def test_salary_extracted(self):
        body = "Salary: $120,000 - $150,000. Apply now. Equal opportunity employer."
        postings = self._extract(body)
        assert postings
        assert "$" in (postings[0].salary or "")

    def test_org_from_company_label(self):
        body = "Company: Acme Corp\nApply now. Equal opportunity employer."
        postings = self._extract(body)
        assert postings
        assert "Acme" in (postings[0].hiring_org or "")

    def test_non_job_email_returns_empty(self):
        assert self.ex.extract(_msg(subject="Newsletter", body="Here is the news.")) == []

    def test_confidence_in_unit_range(self):
        body = "Job opening available. Apply now. Equal opportunity employer."
        postings = self._extract(body)
        assert postings
        assert 0.0 <= postings[0].confidence <= 1.0

    def test_signals_tuple_non_empty(self):
        body = "Apply now. Equal opportunity employer."
        postings = self._extract(body, subject="We're hiring")
        assert postings
        assert len(postings[0].signals) > 0

    # -- Apply link robustness -----------------------------------------------

    def test_nested_span_apply_link_detected(self):
        # <a><span>Apply Now</span></a> — inner tag must be stripped before matching
        body = (
            '<a href="https://jobs.example.com/99"><span>Apply Now</span></a>'
            " Equal opportunity employer."
        )
        postings = self._extract(body)
        assert len(postings) == 1
        assert postings[0].apply_url == "https://jobs.example.com/99"

    def test_submit_application_anchor_text_detected(self):
        body = (
            '<a href="https://careers.example.com/apply">Submit Application</a>'
            " Equal opportunity employer."
        )
        postings = self._extract(body)
        assert postings
        assert postings[0].apply_url == "https://careers.example.com/apply"

    def test_tracking_url_unwrapped(self):
        tracking = "https://click.email.example.com/r?url=https%3A%2F%2Fjobs.acme.com%2F42"
        body = f'<a href="{tracking}">Apply Now</a> Equal opportunity employer.'
        postings = self._extract(body)
        assert postings
        assert postings[0].apply_url == "https://jobs.acme.com/42"

    def test_non_tracking_url_unchanged(self):
        real_url = "https://jobs.acme.com/senior-engineer"
        body = f'<a href="{real_url}">Apply Now</a> Equal opportunity employer.'
        postings = self._extract(body)
        assert postings
        assert postings[0].apply_url == real_url

    def test_digest_postings_use_windowed_context(self):
        # Each listing section contains its own company label before its apply link
        body = (
            "<p>Company: Alpha Inc</p>"
            '<a href="https://jobs.example.com/1">Apply Now</a>'
            "<p>Company: Beta Ltd</p>"
            '<a href="https://jobs.example.com/2">Apply Now</a>'
            "<p>Equal opportunity employer.</p>"
        )
        postings = self._extract(body)
        assert len(postings) == 2
        orgs = {p.hiring_org for p in postings if p.hiring_org}
        # Each posting should carry its own org, not the same one for both
        assert len(orgs) == 2

    def test_digest_subject_not_used_as_title(self):
        # Multi-posting email subject should not bleed into individual posting titles
        body = (
            "<h3>Backend Engineer</h3>"
            '<a href="https://jobs.example.com/1">Apply Now</a>'
            "<h3>Data Scientist</h3>"
            '<a href="https://jobs.example.com/2">Apply Now</a>'
            "<p>Equal opportunity employer.</p>"
        )
        postings = self._extract(body, subject="5 jobs matching your search")
        assert len(postings) == 2
        for p in postings:
            # Subject ("5 jobs matching your search") must not be used as title
            assert (p.title or "") != "5 jobs matching your search"

    # -- Sender domain extraction --------------------------------------------

    def test_careers_subdomain_stripped(self):
        from entity_graph.extractors.job_posting import _org_from_sender
        assert _org_from_sender("Hiring <careers@microsoft.com>") == "Microsoft"

    def test_jobs_subdomain_stripped(self):
        from entity_graph.extractors.job_posting import _org_from_sender
        assert _org_from_sender("alerts@jobs.google.com") == "Google"

    def test_job_board_sender_returns_none(self):
        from entity_graph.extractors.job_posting import _org_from_sender
        assert _org_from_sender("alerts@indeed.com") is None
        assert _org_from_sender("noreply@click.linkedin.com") is None

    def test_simple_domain_capitalised(self):
        from entity_graph.extractors.job_posting import _org_from_sender
        assert _org_from_sender("jobs@acme.com") == "Acme"

    # -- Prep pattern blocklist ----------------------------------------------

    def test_prep_pattern_ignores_remote(self):
        # "from Remote" must not extract "Remote" as an org name
        body = "This role is available from Remote locations. Apply now. Equal opportunity employer."
        postings = self._extract(body, subject="We're hiring")
        # Org should be None or come from sender domain, not "Remote"
        assert (postings[0].hiring_org or "").lower() != "remote"


# ===========================================================================
# Org name canonicalisation
# ===========================================================================

from entity_graph.resolution import _canonical_org_name


class TestCanonicalOrgName:
    def test_corporation_expands_to_corp(self):
        assert _canonical_org_name("Acme Corporation") == _canonical_org_name("Acme Corp")

    def test_corp_dot_normalised(self):
        assert _canonical_org_name("Acme Corp.") == _canonical_org_name("Acme Corp")

    def test_incorporated_normalised(self):
        assert _canonical_org_name("Widgets Inc.") == _canonical_org_name("Widgets Inc")
        assert _canonical_org_name("Widgets Incorporated") == _canonical_org_name("Widgets Inc")

    def test_limited_normalised(self):
        assert _canonical_org_name("Horizon Limited") == _canonical_org_name("Horizon Ltd")
        assert _canonical_org_name("Horizon Ltd.") == _canonical_org_name("Horizon Ltd")

    def test_company_normalised(self):
        assert _canonical_org_name("Acme Company") == _canonical_org_name("Acme Co.")

    def test_llc_variants_normalised(self):
        assert _canonical_org_name("Horizon LLC") == _canonical_org_name("Horizon L.L.C.")
        assert _canonical_org_name("Horizon Limited Liability Company") == _canonical_org_name("Horizon LLC")

    def test_ampersand_to_and(self):
        assert _canonical_org_name("Smith & Jones") == _canonical_org_name("Smith and Jones")

    def test_different_companies_still_differ(self):
        # "Inc" and "Corp" are different suffixes and should NOT collapse to the same thing
        assert _canonical_org_name("Acme Inc") != _canonical_org_name("Acme Corp")

    def test_case_insensitive(self):
        assert _canonical_org_name("ACME CORP") == _canonical_org_name("acme corp")

    def test_unrelated_names_unchanged_in_substance(self):
        assert _canonical_org_name("Google") == "google"
        assert _canonical_org_name("OpenAI") == "openai"


# ===========================================================================
# EntityResolutionQueue
# ===========================================================================

@pytest.fixture
def queue(tmp_path: Path) -> EntityResolutionQueue:
    return EntityResolutionQueue(str(tmp_path / "queue.json"))


@pytest.fixture
def existing_orgs() -> list[tuple[URIRef, str]]:
    return [(URIRef("https://briefkorb.local/schema#org/acme-corp"), "Acme Corp")]


class TestResolutionQueue:
    def test_confident_match_returns_existing_uri_without_queuing(self, queue, existing_orgs):
        # "Acme Corporation" → canonical "acme corp"; "Acme Corp" → "acme corp" → 100%
        candidate = URIRef("https://briefkorb.local/schema#org/acme-corporation")
        resolved, was_queued = queue.resolve_or_queue("Acme Corporation", candidate, existing_orgs)
        assert resolved == existing_orgs[0][0]
        assert not was_queued
        assert queue.get_pending() == []

    def test_ambiguous_match_queues_for_review(self, queue, existing_orgs):
        candidate = URIRef("https://briefkorb.local/schema#org/acme-co")
        resolved, was_queued = queue.resolve_or_queue(
            "Acme Co", candidate, existing_orgs, source_email_id="msg-1"
        )
        assert resolved == candidate   # uses new URI until resolved
        assert was_queued
        pending = queue.get_pending()
        assert len(pending) == 1
        assert pending[0].candidate_name == "Acme Co"
        assert pending[0].source_email_id == "msg-1"
        assert pending[0].status == "pending"

    def test_no_match_returns_candidate_without_queuing(self, queue, existing_orgs):
        candidate = URIRef("https://briefkorb.local/schema#org/totally-different")
        resolved, was_queued = queue.resolve_or_queue("Totally Different Ltd", candidate, existing_orgs)
        assert resolved == candidate
        assert not was_queued
        assert queue.get_pending() == []

    def test_empty_existing_orgs_never_queues(self, queue):
        candidate = URIRef("bk:org/new")
        resolved, was_queued = queue.resolve_or_queue("New Org", candidate, [])
        assert resolved == candidate
        assert not was_queued

    def test_approve_marks_item_approved(self, queue, existing_orgs):
        queue.resolve_or_queue("Acme Co", URIRef("bk:org/acme-co"), existing_orgs)
        item_id = queue.get_pending()[0].id
        approved = queue.approve([item_id])
        assert len(approved) == 1
        assert approved[0].status == "approved"
        assert queue.get_pending() == []
        assert queue.get_all()[0].resolved_at is not None

    def test_reject_marks_item_rejected(self, queue, existing_orgs):
        queue.resolve_or_queue("Acme Co", URIRef("bk:org/acme-co"), existing_orgs)
        item_id = queue.get_pending()[0].id
        count = queue.reject([item_id])
        assert count == 1
        assert queue.get_pending() == []
        assert queue.get_all()[0].status == "rejected"

    def test_approve_all_pending_clears_queue(self, queue, existing_orgs):
        for name, slug in [("Acme Co", "acme-co"), ("Acme LLC", "acme-llc")]:
            queue.resolve_or_queue(name, URIRef(f"bk:org/{slug}"), existing_orgs)
        approved = queue.approve_all_pending()
        assert len(approved) >= 1
        assert queue.get_pending() == []

    def test_reject_all_pending_clears_queue(self, queue, existing_orgs):
        for name, slug in [("Acme Co", "acme-co"), ("Acme LLC", "acme-llc")]:
            queue.resolve_or_queue(name, URIRef(f"bk:org/{slug}"), existing_orgs)
        queue.reject_all_pending()
        assert queue.get_pending() == []
        assert all(i.status == "rejected" for i in queue.get_all())

    def test_set_note_persists(self, queue, existing_orgs):
        queue.resolve_or_queue("Acme Co", URIRef("bk:org/acme-co"), existing_orgs)
        item_id = queue.get_pending()[0].id
        assert queue.set_note(item_id, "Confirmed same company")
        assert queue.get_all()[0].note == "Confirmed same company"

    def test_set_note_unknown_id_returns_false(self, queue):
        assert not queue.set_note("nonexistent-id", "note")

    def test_persists_to_disk_and_reloads(self, tmp_path, existing_orgs):
        path = str(tmp_path / "q.json")
        q1 = EntityResolutionQueue(path)
        q1.resolve_or_queue("Acme Co", URIRef("bk:org/acme-co"), existing_orgs)
        q2 = EntityResolutionQueue(path)
        assert len(q2.get_pending()) == 1
        assert q2.get_pending()[0].candidate_name == "Acme Co"

    def test_fresh_queue_has_no_pending(self, tmp_path):
        q = EntityResolutionQueue(str(tmp_path / "fresh.json"))
        assert q.get_pending() == []
        assert q.get_all() == []


# ===========================================================================
# EntityGraph
# ===========================================================================

class TestSlugify:
    def test_basic(self):
        assert _slugify("Acme Corp") == "acme-corp"

    def test_unicode_transliterated(self):
        assert _slugify("Ünïcödé Corp") == "unicode-corp"

    def test_punctuation_stripped(self):
        assert _slugify("A & B, LLC.") == "a-b-llc"

    def test_empty_gives_unknown(self):
        assert _slugify("") == "unknown"

    def test_multiple_hyphens_collapsed(self):
        slug = _slugify("A -- B")
        assert "--" not in slug


class TestEntityGraphStructure:
    @pytest.fixture
    def g(self) -> EntityGraph:
        return EntityGraph()

    def test_org_uri_is_stable(self, g):
        assert g.org_uri("Acme Corp") == g.org_uri("Acme Corp")

    def test_different_names_give_different_uris(self, g):
        assert g.org_uri("Acme") != g.org_uri("Beta")

    def test_sender_uri_normalises_case(self, g):
        assert g.sender_uri("TEST@EXAMPLE.COM") == g.sender_uri("test@example.com")

    def test_add_same_as_is_bidirectional(self, g):
        a, b = URIRef("bk:org/a"), URIRef("bk:org/b")
        g.add_same_as(a, b)
        assert (a, OWL.sameAs, b) in g.inferred_graph
        assert (b, OWL.sameAs, a) in g.inferred_graph

    def test_email_graph_isolated_from_external(self, g):
        uri = URIRef("bk:test/node")
        g.email_graph.add((uri, RDF.type, SCHEMA.Organization))
        assert (uri, RDF.type, SCHEMA.Organization) in g.email_graph
        assert (uri, RDF.type, SCHEMA.Organization) not in g.external_graph
        assert (uri, RDF.type, SCHEMA.Organization) not in g.inferred_graph

    def test_external_graph_isolated_from_email(self, g):
        uri = URIRef("bk:ext/node")
        g.external_graph.add((uri, RDF.type, SCHEMA.Organization))
        assert (uri, RDF.type, SCHEMA.Organization) not in g.email_graph

    def test_get_all_org_names_returns_named_orgs(self, g):
        g.email_graph.add((URIRef("bk:org/beta"), RDF.type, SCHEMA.Organization))
        g.email_graph.add((URIRef("bk:org/beta"), SCHEMA.name, Literal("Beta Inc")))
        names = dict(g.get_all_org_names())
        assert URIRef("bk:org/beta") in names
        assert names[URIRef("bk:org/beta")] == "Beta Inc"

    def test_triple_count_increments(self, g):
        assert len(g) == 0
        g.email_graph.add((URIRef("bk:a"), RDF.type, SCHEMA.Organization))
        assert len(g) == 1


# ===========================================================================
# EntityGraphManager — age_postings lifecycle
# ===========================================================================

def _aged_msg(days_ago: int, msg_id: str = "msg-001") -> EmailMessage:
    return _msg(
        msg_id=msg_id,
        subject="We're hiring a Senior Engineer",
        body=_job_body(),
        received_date=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


class TestAgePostings:
    def test_fresh_posting_stays_active(self, mgr):
        mgr.process_messages([_aged_msg(1)])
        assert len(mgr.query_job_postings(stages=["active"])) == 1

    def test_posting_transitions_to_historical(self, mgr):
        mgr.process_messages([_aged_msg(1)])
        counts = mgr.age_postings(ttl_active_days=0, ttl_historical_days=999, ttl_archival_days=9999)
        assert counts["to_historical"] == 1
        assert counts["to_signal"] == 0
        assert counts["removed"] == 0
        assert mgr.query_job_postings(stages=["active"]) == []
        assert len(mgr.query_job_postings(stages=["historical"])) == 1

    def test_historical_strips_url_and_salary(self, mgr):
        body = (
            '<a href="https://jobs.example.com/apply">Apply Now</a>'
            " Salary: $130,000. Equal opportunity employer."
        )
        mgr.process_messages([_msg(subject="We're hiring", body=body,
                                   received_date=datetime.now(timezone.utc) - timedelta(days=1))])
        mgr.age_postings(ttl_active_days=0, ttl_historical_days=999, ttl_archival_days=9999)
        results = mgr.query_job_postings(stages=["historical"])
        assert results
        assert results[0]["apply_url"] is None
        assert results[0]["salary"] is None

    def test_historical_retains_org_and_date(self, mgr):
        mgr.process_messages([_aged_msg(1)])
        mgr.age_postings(ttl_active_days=0, ttl_historical_days=999, ttl_archival_days=9999)
        results = mgr.query_job_postings(stages=["historical"])
        assert results
        assert results[0]["org_name"] is not None
        assert results[0]["date_posted"] is not None

    def test_posting_transitions_to_signal(self, mgr):
        mgr.process_messages([_aged_msg(1)])
        counts = mgr.age_postings(ttl_active_days=0, ttl_historical_days=0, ttl_archival_days=9999)
        assert counts["to_historical"] == 1
        assert counts["to_signal"] == 1
        assert len(mgr.query_job_postings(stages=["signal"])) == 1

    def test_signal_strips_title_and_location(self, mgr):
        mgr.process_messages([_aged_msg(1)])
        mgr.age_postings(ttl_active_days=0, ttl_historical_days=0, ttl_archival_days=9999)
        results = mgr.query_job_postings(stages=["signal"])
        assert results
        assert results[0]["title"] is None
        assert results[0]["location"] is None

    def test_signal_retains_org_and_date(self, mgr):
        mgr.process_messages([_aged_msg(1)])
        mgr.age_postings(ttl_active_days=0, ttl_historical_days=0, ttl_archival_days=9999)
        results = mgr.query_job_postings(stages=["signal"])
        assert results
        assert results[0]["org_name"] is not None
        assert results[0]["date_posted"] is not None

    def test_signal_removed_after_archival_ttl(self, mgr):
        mgr.process_messages([_aged_msg(1)])
        counts = mgr.age_postings(ttl_active_days=0, ttl_historical_days=0, ttl_archival_days=0)
        assert counts["removed"] == 1
        assert mgr.query_job_postings() == []

    def test_org_node_survives_full_removal(self, mgr):
        mgr.process_messages([_aged_msg(1)])
        mgr.age_postings(ttl_active_days=0, ttl_historical_days=0, ttl_archival_days=0)
        orgs = mgr.query_orgs()
        assert any("TechCorp" in o["name"] for o in orgs)

    def test_no_changes_returns_zero_counts(self, mgr):
        mgr.process_messages([_aged_msg(1)])
        counts = mgr.age_postings(ttl_active_days=999)
        assert counts == {"to_historical": 0, "to_signal": 0, "removed": 0}

    def test_message_node_removed_on_active_to_historical(self, mgr):
        mgr.process_messages([_aged_msg(1)])
        msg_uri = mgr._graph.message_uri("msg-001")
        assert (msg_uri, RDF.type, BK.EmailMessage) in mgr._graph.email_graph
        mgr.age_postings(ttl_active_days=0, ttl_historical_days=999, ttl_archival_days=9999)
        # Message node should be orphaned and removed now that foundIn link is gone
        assert (msg_uri, RDF.type, BK.EmailMessage) not in mgr._graph._g


# ===========================================================================
# EntityGraphManager — queries and external injection
# ===========================================================================

@pytest.fixture
def mgr_two_postings(mgr: EntityGraphManager) -> EntityGraphManager:
    msgs = [
        _msg(
            msg_id="msg-001",
            subject="We're hiring a Backend Engineer",
            sender="Acme Careers <jobs@acme.com>",
            body=_job_body(company="Acme Corp", title="Backend Engineer", location="Remote"),
        ),
        _msg(
            msg_id="msg-002",
            subject="Join us as a Data Scientist",
            sender="Beta Talent <talent@beta.io>",
            body=_job_body(company="Beta Inc", title="Data Scientist", location="New York"),
        ),
    ]
    mgr.process_messages(msgs)
    return mgr


@pytest.fixture
def mgr_two_postings_aged(mgr: EntityGraphManager) -> EntityGraphManager:
    """Same two postings as mgr_two_postings, but backdated a day so aging
    with ttl_active_days=0 doesn't race age_postings()'s own now()-based
    cutoff against received_date=now() -- same idea as TestAgePostings'
    _aged_msg helper."""
    msgs = [
        _msg(
            msg_id="msg-001",
            subject="We're hiring a Backend Engineer",
            sender="Acme Careers <jobs@acme.com>",
            body=_job_body(company="Acme Corp", title="Backend Engineer", location="Remote"),
            received_date=datetime.now(timezone.utc) - timedelta(days=1),
        ),
        _msg(
            msg_id="msg-002",
            subject="Join us as a Data Scientist",
            sender="Beta Talent <talent@beta.io>",
            body=_job_body(company="Beta Inc", title="Data Scientist", location="New York"),
            received_date=datetime.now(timezone.utc) - timedelta(days=1),
        ),
    ]
    mgr.process_messages(msgs)
    return mgr


class TestQueries:
    def test_query_all_postings(self, mgr_two_postings):
        assert len(mgr_two_postings.query_job_postings()) == 2

    def test_query_by_org_filters_correctly(self, mgr_two_postings):
        results = mgr_two_postings.query_job_postings(org_name="Acme")
        assert len(results) == 1
        assert "Acme" in results[0]["org_name"]

    def test_query_by_org_case_insensitive(self, mgr_two_postings):
        upper = mgr_two_postings.query_job_postings(org_name="ACME")
        lower = mgr_two_postings.query_job_postings(org_name="acme")
        assert len(upper) == len(lower) == 1

    def test_stage_filter_active_only(self, mgr_two_postings):
        assert len(mgr_two_postings.query_job_postings(stages=["active"])) == 2
        assert mgr_two_postings.query_job_postings(stages=["historical"]) == []

    def test_stage_active_has_stage_field(self, mgr_two_postings):
        results = mgr_two_postings.query_job_postings(stages=["active"])
        assert all(r["stage"] == "active" for r in results)

    def test_query_orgs_returns_all(self, mgr_two_postings):
        names = {o["name"] for o in mgr_two_postings.query_orgs()}
        assert "Acme Corp" in names
        assert "Beta Inc" in names

    def test_query_orgs_active_count(self, mgr_two_postings):
        orgs = {o["name"]: o for o in mgr_two_postings.query_orgs()}
        assert orgs["Acme Corp"]["active_count"] == 1
        assert orgs["Acme Corp"]["total_count"] == 1

    def test_query_orgs_counts_across_stages(self, mgr_two_postings_aged):
        mgr_two_postings_aged.age_postings(ttl_active_days=0, ttl_historical_days=999, ttl_archival_days=9999)
        orgs = {o["name"]: o for o in mgr_two_postings_aged.query_orgs()}
        assert orgs["Acme Corp"]["historical_count"] == 1
        assert orgs["Acme Corp"]["active_count"] == 0
        assert orgs["Acme Corp"]["total_count"] == 1

    def test_inject_external_goes_to_external_graph(self, mgr):
        org_uri = mgr._graph.org_uri("Acme Corp")
        mgr.inject_external([
            (org_uri, RDF.type, SCHEMA.Organization),
            (org_uri, SCHEMA.name, Literal("Acme Corp")),
            (org_uri, SCHEMA["numberOfEmployees"], Literal(5000)),
        ])
        assert (org_uri, SCHEMA["numberOfEmployees"], Literal(5000)) in mgr._graph.external_graph
        assert (org_uri, SCHEMA["numberOfEmployees"], Literal(5000)) not in mgr._graph.email_graph

    def test_apply_approved_resolutions_writes_same_as(self, mgr):
        a = mgr._graph.org_uri("Acme Corp")
        b = mgr._graph.org_uri("Acme Corporation")
        mgr._queue._items.append(ResolutionCandidate(
            id="test-resolution-id",
            candidate_uri=str(b),
            candidate_name="Acme Corporation",
            match_uri=str(a),
            match_name="Acme Corp",
            score=85.0,
            source_email_id="msg-001",
            status="approved",
        ))
        mgr._queue._save()
        count = mgr.apply_approved_resolutions()
        assert count == 1
        assert (b, OWL.sameAs, a) in mgr._graph.inferred_graph
        assert (a, OWL.sameAs, b) in mgr._graph.inferred_graph

    def test_raw_sparql_query(self, mgr_two_postings):
        results = list(mgr_two_postings.query("""\
PREFIX schema: <https://schema.org/>
SELECT (COUNT(?j) AS ?n) WHERE { ?j a schema:JobPosting }
"""))
        assert int(results[0][0]) == 2

    def test_triple_count_nonzero_after_ingest(self, mgr_two_postings):
        assert mgr_two_postings.triple_count() > 0


# ===========================================================================
# Integration — full pipeline with persistence round-trip
# ===========================================================================

_INTEGRATION_HTML = """\
<html><body>
<h2>Senior Backend Engineer</h2>
<p>Company: Horizon Tech</p>
<p>Location: Remote (US)</p>
<p>Salary: $140,000 - $170,000 per year</p>
<p>We need a senior backend engineer to work on distributed systems.</p>
<p>Requirements:</p>
<ul>
  <li>5+ years Python or Go</li>
  <li>Cloud infrastructure experience (AWS/GCP)</li>
</ul>
<p><a href="https://horizon.tech/careers/senior-backend-123">Apply Now</a></p>
<p>Horizon Tech is an equal opportunity employer.</p>
</body></html>
"""


@pytest.fixture
def integration_msg() -> EmailMessage:
    return _msg(
        msg_id="integration-001",
        subject="Job Alert: Senior Backend Engineer at Horizon Tech",
        sender="Horizon Careers <careers@horizon.tech>",
        body=_INTEGRATION_HTML,
        received_date=datetime.now(timezone.utc) - timedelta(hours=2),
    )


class TestIntegration:
    def test_ingest_returns_posting_count(self, mgr, integration_msg):
        count = mgr.process_messages([integration_msg])
        assert count == 1

    def test_posting_queryable_after_ingest(self, mgr, integration_msg):
        mgr.process_messages([integration_msg])
        postings = mgr.query_job_postings()
        assert len(postings) == 1
        p = postings[0]
        assert p["stage"] == "active"
        assert p["apply_url"] == "https://horizon.tech/careers/senior-backend-123"
        assert p["location"] is not None
        assert p["salary"] is not None

    def test_org_queryable_after_ingest(self, mgr, integration_msg):
        mgr.process_messages([integration_msg])
        orgs = mgr.query_orgs()
        assert any("Horizon" in o["name"] for o in orgs)

    def test_graph_persists_across_manager_instances(self, tmp_path, integration_msg):
        storage = str(tmp_path / "eg")
        EntityGraphManager(storage).process_messages([integration_msg])
        postings = EntityGraphManager(storage).query_job_postings()
        assert len(postings) == 1
        assert postings[0]["apply_url"] == "https://horizon.tech/careers/senior-backend-123"

    def test_aging_state_persists_across_instances(self, tmp_path, integration_msg):
        storage = str(tmp_path / "eg")
        mgr1 = EntityGraphManager(storage)
        mgr1.process_messages([integration_msg])
        mgr1.age_postings(ttl_active_days=0, ttl_historical_days=999, ttl_archival_days=9999)

        mgr2 = EntityGraphManager(storage)
        assert mgr2.query_job_postings(stages=["active"]) == []
        assert len(mgr2.query_job_postings(stages=["historical"])) == 1

    def test_full_lifecycle_preserves_org(self, mgr, integration_msg):
        mgr.process_messages([integration_msg])
        mgr.age_postings(ttl_active_days=0, ttl_historical_days=0, ttl_archival_days=0)
        assert mgr.query_job_postings() == []
        orgs = mgr.query_orgs()
        assert any("Horizon" in o["name"] for o in orgs)

    def test_idempotent_ingest(self, mgr, integration_msg):
        """Processing the same message twice should not duplicate postings."""
        mgr.process_messages([integration_msg])
        mgr.process_messages([integration_msg])
        # Both job URIs are identical (same msg id + index), so deduped by RDF
        postings = mgr.query_job_postings()
        assert len(postings) == 1

    def test_no_writes_outside_storage_dir(self, tmp_path, integration_msg):
        """Verify all files written during a full cycle are inside tmp_path."""
        import os
        storage = str(tmp_path / "eg")
        mgr = EntityGraphManager(storage)
        mgr.process_messages([integration_msg])
        mgr.age_postings(ttl_active_days=0, ttl_historical_days=999, ttl_archival_days=9999)

        for dirpath, _, files in os.walk(tmp_path):
            for fname in files:
                full = os.path.join(dirpath, fname)
                assert str(tmp_path) in full, f"Unexpected file outside tmp_path: {full}"
                assert fname.endswith(".nq") or fname.endswith(".json"), (
                    f"Unexpected file type: {fname}"
                )
