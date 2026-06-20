from rdflib import Namespace, URIRef

BK = Namespace("https://briefkorb.local/schema#")
SCHEMA = Namespace("https://schema.org/")

EMAIL_GRAPH_URI = URIRef("https://briefkorb.local/graph/email-derived")
EXTERNAL_GRAPH_URI = URIRef("https://briefkorb.local/graph/external")
INFERRED_GRAPH_URI = URIRef("https://briefkorb.local/graph/inferred")
