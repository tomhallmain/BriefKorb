import re
import unicodedata
from typing import List, Tuple

from rdflib import ConjunctiveGraph, Graph, URIRef, Literal
from rdflib.namespace import RDF, OWL, XSD, FOAF

from .namespaces import BK, SCHEMA, EMAIL_GRAPH_URI, EXTERNAL_GRAPH_URI, INFERRED_GRAPH_URI


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-") or "unknown"


class EntityGraph:
    """ConjunctiveGraph with named graphs for email-derived, external, and inferred data."""

    def __init__(self) -> None:
        self._g = ConjunctiveGraph()
        self._g.bind("bk", BK)
        self._g.bind("schema", SCHEMA)
        self._g.bind("foaf", FOAF)
        self._g.bind("owl", OWL)

    # ------------------------------------------------------------------
    # Named-graph accessors
    # ------------------------------------------------------------------

    @property
    def email_graph(self) -> Graph:
        return self._g.get_context(EMAIL_GRAPH_URI)

    @property
    def external_graph(self) -> Graph:
        return self._g.get_context(EXTERNAL_GRAPH_URI)

    @property
    def inferred_graph(self) -> Graph:
        return self._g.get_context(INFERRED_GRAPH_URI)

    # ------------------------------------------------------------------
    # URI minting
    # ------------------------------------------------------------------

    def org_uri(self, name: str) -> URIRef:
        return BK[f"org/{_slugify(name)}"]

    def sender_uri(self, email: str) -> URIRef:
        return BK[f"sender/{email.lower().strip()}"]

    def domain_uri(self, domain: str) -> URIRef:
        return BK[f"domain/{domain.lower().strip()}"]

    def message_uri(self, email_id: str) -> URIRef:
        return BK[f"email/{email_id}"]

    def job_uri(self, email_id: str, index: int) -> URIRef:
        return BK[f"job/{email_id}/{index}"]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_all_org_names(self) -> List[Tuple[URIRef, str]]:
        """Return (uri, name) for every Organization node across all graphs."""
        return [
            (uri, str(name))
            for uri, name in self._g.subject_objects(SCHEMA.name)
            if (uri, RDF.type, SCHEMA.Organization) in self._g
        ]

    def add_same_as(self, uri_a: URIRef, uri_b: URIRef) -> None:
        """Write a bidirectional owl:sameAs assertion into the inferred graph."""
        self.inferred_graph.add((uri_a, OWL.sameAs, uri_b))
        self.inferred_graph.add((uri_b, OWL.sameAs, uri_a))

    def add_triple(self, s: URIRef, p: URIRef, o, graph: Graph | None = None) -> None:
        target = graph if graph is not None else self.email_graph
        target.add((s, p, o))

    # ------------------------------------------------------------------
    # Query / serialization
    # ------------------------------------------------------------------

    def query(self, sparql: str):
        return self._g.query(sparql)

    def serialize(self, path: str, fmt: str = "nquads") -> None:
        self._g.serialize(destination=path, format=fmt)

    def parse(self, path: str, fmt: str = "nquads") -> None:
        self._g.parse(source=path, format=fmt)

    def __len__(self) -> int:
        return len(self._g)
