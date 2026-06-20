"""Job posting extraction from email messages.

Two-stage approach
------------------
1. Detection — score subject and body against positive signal lists; subtract
   negative signals for application-status and transactional emails; require
   either ≥ 2 positive signals or a single strong one (≥ 0.50) to reduce
   false positives from promotional emails that happen to say "Apply now".

2. Extraction — for each apply link found (one posting per link):
   - Apply links are discovered by stripping inner HTML from every <a> tag
     and checking the visible text, so <a><span>Apply Now</span></a> patterns
     are caught just like direct-text anchors.
   - Tracking/redirect URLs (e.g. click.indeed.com?url=…) are unwrapped to
     their canonical destination via query-parameter inspection.
   - For single-posting emails the full body is used for field extraction.
   - For multi-posting digest emails (multiple apply links) the HTML is
     windowed between successive apply links so each posting's title/org/
     location comes from its own context, not the full digest body.
   - The subject line is used as a title fallback for single-posting emails
     only; for digests the overall subject is not a useful job title.
   - Sender-domain org fallback strips known generic subdomain prefixes
     (careers., jobs., mail., …) and refuses to guess org from known job-board
     domains (indeed.com, linkedin.com, …).
   - The prep-pattern fallback ("at Acme Corp") ignores a blocklist of words
     (Remote, Home, Online, …) that are locations, not org names.

spaCy model is lazy-loaded on first use.  If `en_core_web_sm` is not installed
the extractor continues in regex-only mode and logs a warning once.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from email.utils import parseaddr
from typing import List, Optional, Tuple, TYPE_CHECKING

from .base import BaseExtractor, ExtractedJobPosting
from .text_utils import html_to_text, normalize_whitespace, truncate_for_nlp

if TYPE_CHECKING:
    from email_server import EmailMessage

logger = logging.getLogger(__name__)

# ── Detection signals ─────────────────────────────────────────────────────────

_SUBJECT_SIGNALS: List[Tuple[str, float]] = [
    ("job alert", 0.55),
    ("job opportunity", 0.55),
    ("job opening", 0.55),
    ("job listing", 0.50),
    ("career opportunity", 0.55),
    ("career alert", 0.50),
    ("open role", 0.55),
    ("open position", 0.55),
    ("new opening", 0.45),
    ("we're hiring", 0.65),
    ("we are hiring", 0.65),
    ("now hiring", 0.55),
    ("is hiring", 0.45),
    ("join our team", 0.40),
    ("position available", 0.55),
    ("employment opportunity", 0.50),
    ("work with us", 0.35),
]

_BODY_SIGNALS: List[Tuple[str, float]] = [
    ("apply now", 0.30),
    ("apply here", 0.30),
    ("view job", 0.30),
    ("job description", 0.30),
    ("requirements:", 0.20),
    ("qualifications:", 0.20),
    ("responsibilities:", 0.20),
    ("about the role", 0.35),
    ("about the position", 0.35),
    ("years of experience", 0.25),
    # "Equal Opportunity Employer" is US EEO legal boilerplate that appears
    # almost exclusively in actual job postings; weight it as a near-definitive
    # signal so it passes the single-signal threshold on its own.
    ("equal opportunity employer", 0.60),
    ("salary range", 0.30),
    ("full-time", 0.15),
    ("part-time", 0.10),
]

# Negative signals subtract from score.  These catch the most common
# false-positive families: application-status notifications, order
# confirmations, and account/finance emails.
_NEGATIVE_BODY_SIGNALS: List[Tuple[str, float]] = [
    # Application status — you *applied*, this email is not a posting
    ("your application has been received", 0.60),
    ("we've received your application", 0.60),
    ("we have received your application", 0.60),
    ("your application was received", 0.60),
    ("thank you for applying", 0.50),
    ("application status", 0.35),
    ("we regret to inform", 0.50),
    ("not selected", 0.40),
    ("not moving forward", 0.40),
    # E-commerce / transactional
    ("your order", 0.50),
    ("order confirmation", 0.50),
    ("order number", 0.40),
    ("track your order", 0.50),
    # Finance / account
    ("payment received", 0.40),
    ("account statement", 0.40),
]

_DETECTION_THRESHOLD = 0.30
# A single weak signal (e.g. "apply now" alone at 0.30) is insufficient.
# Require either ≥ 2 positive signals or one signal that already exceeds this.
_SINGLE_SIGNAL_MIN = 0.50

# ── HTML helpers ──────────────────────────────────────────────────────────────

# Captures href and ALL inner content of every <a> tag, including nested elements
_ANCHOR_RE = re.compile(
    r'<a\s[^>]*href=["\']([^"\']{10,})["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

# Strips remaining HTML tags from anchor inner content
_TAG_RE = re.compile(r'<[^>]+>')

# Matches the visible CTA text that identifies an apply link after tag stripping
_APPLY_TEXT_RE = re.compile(
    r'\b(?:'
    r'apply(?:\s+now|\s+here|\s+today|\s+online)?'
    r'|submit\s+(?:application|resume|cv)'
    r'|view\s+(?:job|opening|position|role|opportunity)'
    r'|see\s+(?:job|details?|opening)'
    r'|full\s+details?'
    r')\b',
    re.IGNORECASE,
)

# HTML headings used for title extraction
_HEADING_RE = re.compile(r'<h[1-3][^>]*>([^<]{5,120})</h[1-3]>', re.IGNORECASE)

# ── Field-extraction patterns ─────────────────────────────────────────────────

_TITLE_LABEL_RE = re.compile(
    r'(?:^|\n)(?:position|role|job\s+title|title|opening|vacancy)\s*[:\-]\s*(.{5,120})',
    re.IGNORECASE | re.MULTILINE,
)

_LOCATION_LABEL_RE = re.compile(
    r'(?:^|\n)(?:location|where|office|city|cities)\s*[:\-]\s*(.{2,80})',
    re.IGNORECASE | re.MULTILINE,
)

_WORK_MODE_RE = re.compile(
    r'\b(remote|fully\s+remote|hybrid|on[- ]?site|in[- ]?office)\b',
    re.IGNORECASE,
)

_SALARY_RE = re.compile(
    r'(?:'
    r'\$\s*[\d,]+(?:k)?\s*(?:[-–]\s*\$?\s*[\d,]+(?:k)?)?'
    r'(?:\s*(?:per\s+year|\/year|\/yr|annually|per\s+hour|\/hour|\/hr))?'
    r'|£\s*[\d,]+(?:[-–]£?\s*[\d,]+)?'
    r'|€\s*[\d,]+(?:[-–]€?\s*[\d,]+)?'
    r'|(?:salary|compensation|pay)\s*[:\-]\s*[^\n<]{3,60}'
    r')',
    re.IGNORECASE,
)

_ORG_LABEL_RE = re.compile(
    r'(?:^|\n)(?:company|organization|employer)\s*[:\-]\s*([A-Z][A-Za-z0-9 &.,\']{1,60})',
    re.IGNORECASE | re.MULTILINE,
)

_ORG_PREP_RE = re.compile(
    r'\b(?:at|for|with|from)\s+([A-Z][A-Za-z0-9 &.\']{1,50}?)(?:[,.\n]|$)',
)

# Words that look like org names after a preposition but are actually locations
# or generic terms; blocking them prevents "from Remote" → org = "Remote" etc.
_ORG_PREP_BLOCKLIST = frozenset({
    "remote", "home", "online", "hybrid", "office", "headquarters",
    "us", "least", "most", "all", "this", "that",
    "our", "your", "the", "an", "a", "you", "me", "we", "they",
})

# ── Sender-domain helpers ─────────────────────────────────────────────────────

_GENERIC_SUBDOMAINS = frozenset({
    "jobs", "careers", "hr", "talent", "recruiting", "recruitment",
    "hiring", "apply", "email", "mail", "noreply", "no-reply",
    "notifications", "alerts", "info", "hello", "team",
    "bounce", "reply", "smtp", "send", "news", "newsletter",
    "updates", "marketing", "promo", "go", "click", "track",
})

_COMMON_TLDS = frozenset({
    "com", "org", "net", "io", "co", "ai", "app", "dev",
    "uk", "ca", "au", "de", "fr", "jp", "in", "eu",
})

# Sending domain belongs to the job board, not the hiring company; any
# org hint extracted from these domains would be meaningless or wrong.
_JOB_BOARD_DOMAINS = frozenset({
    "indeed.com", "linkedin.com", "glassdoor.com", "monster.com",
    "ziprecruiter.com", "dice.com", "simplyhired.com", "careerbuilder.com",
    "handshake.com", "lever.co", "greenhouse.io", "workday.com",
    "jobvite.com", "ashbyhq.com", "myworkdayjobs.com",
    "smartrecruiters.com", "icims.com", "taleo.net", "successfactors.com",
    "recruitee.com", "bamboohr.com", "workable.com",
})

# ── URL helpers ───────────────────────────────────────────────────────────────

# Query-parameter names used by email tracking services to embed the real URL
_REDIRECT_PARAMS = (
    "url", "dest", "destination", "redirect", "to",
    "target", "link", "u", "ref_url", "r",
)


def _resolve_tracking_url(url: str) -> str:
    """Unwrap tracking/redirect URLs that embed the destination as a query param.

    Email service providers commonly replace apply links with a tracking
    redirect (e.g. https://click.indeed.com/r?url=https%3A%2F%2Fjobs.acme.com).
    Where the destination is encoded in the query string we return it directly
    so the stored apply_url points at the real job posting.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
        for param in _REDIRECT_PARAMS:
            if param in qs:
                dest = urllib.parse.unquote(qs[param][0])
                if dest.startswith(("http://", "https://")):
                    return dest
    except Exception:
        pass
    return url


def _org_from_sender(sender: str) -> Optional[str]:
    """Extract a best-guess org name from the sender email address.

    Strips known generic subdomain prefixes (careers., jobs., mail., …) and
    returns None for known job-board domains (indeed.com, linkedin.com, …)
    where the sender domain reveals nothing about the hiring organisation.

    Examples
    --------
    careers@microsoft.com  → "Microsoft"
    jobs@google.com        → "Google"
    noreply@acme.com       → "Acme"
    alerts@indeed.com      → None   (job board)
    click.email.linkedin.com → None (job board)
    """
    if "@" not in sender:
        return None
    _, addr = parseaddr(sender)
    if not addr or "@" not in addr:
        addr = sender
    domain = addr.split("@")[-1].rstrip(">").strip().lower()
    if not domain:
        return None

    # Job boards: the sender domain is the board, not the hiring company
    if any(domain == bd or domain.endswith("." + bd) for bd in _JOB_BOARD_DOMAINS):
        return None

    parts = domain.split(".")
    # Strip TLD(s) from the right
    while len(parts) > 1 and parts[-1] in _COMMON_TLDS:
        parts.pop()
    # Strip generic subdomain labels from the left
    while len(parts) > 1 and parts[0] in _GENERIC_SUBDOMAINS:
        parts.pop(0)

    return parts[-1].capitalize() if parts else None


# ── spaCy lazy loader ─────────────────────────────────────────────────────────

_nlp = None
_nlp_load_attempted = False


def _get_nlp():
    global _nlp, _nlp_load_attempted
    if _nlp_load_attempted:
        return _nlp
    _nlp_load_attempted = True
    try:
        import spacy  # noqa: PLC0415
        _nlp = spacy.load("en_core_web_sm")
    except Exception:
        logger.warning(
            "spaCy model 'en_core_web_sm' not available; "
            "run `python -m spacy download en_core_web_sm` to enable NER enrichment. "
            "Falling back to regex-only extraction."
        )
    return _nlp


# ── Extractor ─────────────────────────────────────────────────────────────────


class JobPostingExtractor(BaseExtractor):

    def extract(self, message: "EmailMessage") -> List[ExtractedJobPosting]:
        is_job, confidence, signals = self._detect(message)
        if not is_job:
            return []

        html_body = message.body or ""
        plain_text = html_to_text(html_body) if "<" in html_body else html_body

        nlp = _get_nlp()
        doc = nlp(truncate_for_nlp(plain_text)) if nlp else None

        apply_links = self._find_apply_links(html_body)

        if not apply_links:
            return [self._build_posting(
                plain_text, html_body, doc, None,
                confidence, signals, message,
            )]

        if len(apply_links) == 1:
            url = _resolve_tracking_url(apply_links[0][0])
            return [self._build_posting(
                plain_text, html_body, doc, url,
                confidence, signals, message,
            )]

        # Digest email: window the HTML between successive apply links so
        # each posting's fields are extracted from its own context rather
        # than the full digest body.  The email subject is not used as a
        # title fallback here because it describes the digest, not any
        # individual listing.
        postings: List[ExtractedJobPosting] = []
        prev_end = 0
        for url, _start, end in apply_links:
            seg_html = html_body[prev_end:end]
            seg_text = html_to_text(seg_html)
            seg_doc = nlp(truncate_for_nlp(seg_text)) if nlp else None
            postings.append(self._build_posting(
                seg_text, seg_html, seg_doc,
                _resolve_tracking_url(url),
                confidence, signals, message,
                use_subject_fallback=False,
            ))
            prev_end = end

        return postings

    # ── Detection ─────────────────────────────────────────────────────────────

    def _detect(
        self, message: "EmailMessage"
    ) -> Tuple[bool, float, Tuple[str, ...]]:
        subject = (message.subject or "").lower()
        html_body = message.body or ""
        plain_text = (
            html_to_text(html_body)[:4000].lower()
            if "<" in html_body
            else html_body[:4000].lower()
        )

        score = 0.0
        signals: List[str] = []

        for token, weight in _SUBJECT_SIGNALS:
            if token in subject:
                score += weight
                signals.append(f"subject:{token}")

        for token, weight in _BODY_SIGNALS:
            if token in plain_text:
                score += weight
                signals.append(f"body:{token}")

        for token, weight in _NEGATIVE_BODY_SIGNALS:
            if token in plain_text:
                score -= weight

        score = min(max(score, 0.0), 1.0)

        # Guard: a single weak signal is not enough — require either ≥ 2
        # positive signals or a signal strong enough to be unambiguous on
        # its own (e.g. "we're hiring" at 0.65, "equal opportunity employer"
        # at 0.60).  This prevents single-keyword false positives from promo
        # emails that happen to say "Apply now" or "Requirements:".
        is_job = (
            score >= _DETECTION_THRESHOLD
            and (len(signals) >= 2 or score >= _SINGLE_SIGNAL_MIN)
        )
        return is_job, score, tuple(signals)

    # ── Apply link discovery ───────────────────────────────────────────────────

    def _find_apply_links(self, html_body: str) -> List[Tuple[str, int, int]]:
        """Return [(url, match_start, match_end), ...] for unique apply links.

        Strips inner HTML from each anchor's content before matching the CTA
        text, so patterns like <a href="…"><span>Apply Now</span></a> are
        handled the same as plain <a href="…">Apply Now</a>.
        """
        seen: set[str] = set()
        results: List[Tuple[str, int, int]] = []
        for m in _ANCHOR_RE.finditer(html_body):
            href = m.group(1)
            inner_text = _TAG_RE.sub("", m.group(2)).strip()
            if _APPLY_TEXT_RE.search(inner_text) and href not in seen:
                seen.add(href)
                results.append((href, m.start(), m.end()))
        return results

    def _find_apply_urls(self, html_body: str) -> List[str]:
        """Convenience wrapper returning just the URL strings."""
        return [url for url, _, _ in self._find_apply_links(html_body)]

    # ── Posting builder ────────────────────────────────────────────────────────

    def _build_posting(
        self,
        plain_text: str,
        html_body: str,
        doc,
        apply_url: Optional[str],
        confidence: float,
        signals: Tuple[str, ...],
        message: "EmailMessage",
        *,
        use_subject_fallback: bool = True,
    ) -> ExtractedJobPosting:
        subject = (message.subject or "") if use_subject_fallback else ""
        return ExtractedJobPosting(
            title=self._extract_title(plain_text, html_body, subject),
            hiring_org=self._extract_org(plain_text, doc, message.sender or ""),
            location=self._extract_location(plain_text, doc),
            apply_url=apply_url,
            salary=self._extract_salary(plain_text),
            confidence=round(confidence, 3),
            signals=signals,
        )

    # ── Field extractors ───────────────────────────────────────────────────────

    def _extract_title(self, plain_text: str, html_body: str, subject: str) -> Optional[str]:
        m = _TITLE_LABEL_RE.search(plain_text)
        if m:
            return normalize_whitespace(m.group(1).split("\n")[0])

        for m in _HEADING_RE.finditer(html_body):
            candidate = normalize_whitespace(m.group(1))
            if len(candidate) >= 8 and candidate.lower() not in {
                "about us", "about the company", "apply now", "job description",
                "overview", "requirements", "qualifications", "responsibilities",
            }:
                return candidate

        if subject and len(subject) < 100 and subject.lower() not in (
            "job alert", "career opportunity", "new job opening"
        ):
            return normalize_whitespace(subject)

        return None

    def _extract_org(self, plain_text: str, doc, sender: str) -> Optional[str]:
        # 1. Explicit structured label
        m = _ORG_LABEL_RE.search(plain_text)
        if m:
            return normalize_whitespace(m.group(1))

        # 2. spaCy ORG entities — most-frequent mention wins
        if doc is not None:
            orgs = [ent.text for ent in doc.ents if ent.label_ == "ORG"]
            if orgs:
                return normalize_whitespace(max(set(orgs), key=orgs.count))

        # 3. Prep pattern "at Acme Corp" — with blocklist to suppress false
        #    positives like "from Remote" or "at least 5 years"
        m = _ORG_PREP_RE.search(plain_text)
        if m:
            candidate = m.group(1)
            first_word = candidate.strip().split()[0].lower().rstrip(".,")
            if first_word not in _ORG_PREP_BLOCKLIST:
                return normalize_whitespace(candidate)

        # 4. Sender domain — strips generic subdomains, refuses job-board domains
        return _org_from_sender(sender)

    def _extract_location(self, plain_text: str, doc) -> Optional[str]:
        m = _LOCATION_LABEL_RE.search(plain_text)
        if m:
            return normalize_whitespace(m.group(1).split("\n")[0])

        m = _WORK_MODE_RE.search(plain_text)
        if m:
            return m.group(1).capitalize()

        if doc is not None:
            gpes = [ent.text for ent in doc.ents if ent.label_ == "GPE"]
            if gpes:
                return normalize_whitespace(gpes[0])

        return None

    def _extract_salary(self, plain_text: str) -> Optional[str]:
        m = _SALARY_RE.search(plain_text)
        return normalize_whitespace(m.group(0)) if m else None
