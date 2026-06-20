#!/usr/bin/env python3
"""Interactive SPARQL REPL for the BriefKorb entity graph.

Loads the live entity_graph.nq from the configured storage directory and
presents a readline-based prompt.  Empty line submits the buffered query.
Ctrl-C clears the current buffer.  Ctrl-D exits.

Usage
-----
  python scripts/sparql_repl.py
  python scripts/sparql_repl.py /path/to/entity_graph

Prefix shortcuts (pre-defined)
-------------------------------
  schema:   https://schema.org/
  bk:       https://briefkorb.local/schema#
  owl:      http://www.w3.org/2002/07/owl#
  rdfs:     http://www.w3.org/2000/01/rdf-schema#
  xsd:      http://www.w3.org/2001/XMLSchema#

Example queries
---------------
  # All active job postings
  SELECT ?post ?title ?org WHERE {
    GRAPH <https://briefkorb.local/graph/email-derived> {
      ?post a schema:JobPosting ;
            schema:title ?title .
      OPTIONAL { ?post schema:hiringOrganization/schema:name ?org }
    }
  }

  # All orgs in the graph
  SELECT ?name (COUNT(?post) AS ?n) WHERE {
    ?org a schema:Organization ; schema:name ?name .
    OPTIONAL { ?post schema:hiringOrganization ?org }
  } GROUP BY ?name ORDER BY DESC(?n)

  # Inspect a single node
  DESCRIBE <https://briefkorb.local/entity/org/acme-corp>
"""

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import readline  # noqa: F401 — enables line editing on Unix/macOS
except ImportError:
    pass

_PREFIXES = """
PREFIX schema: <https://schema.org/>
PREFIX bk:     <https://briefkorb.local/schema#>
PREFIX owl:    <http://www.w3.org/2002/07/owl#>
PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>
""".strip()

_HELP = textwrap.dedent("""
    Commands
    --------
    \\prefixes     show the auto-prepended prefix block
    \\triples      show total triple count
    \\graphs       list named graphs
    \\clear        clear the buffer without running
    \\help         this message
    \\quit / \\exit exit the REPL

    Input
    -----
    Type SPARQL across multiple lines.
    Press Enter on a blank line to submit.
    Ctrl-C clears the current buffer.
    Ctrl-D exits.
""")


def resolve_dir(override: str | None) -> Path:
    if override:
        return Path(override)
    config_path = Path(__file__).parent.parent / "email_server" / "config.yaml"
    from email_server.config import EmailServerConfig
    cfg = EmailServerConfig.from_file(str(config_path))
    return Path(cfg.token_storage_path) / "entity_graph"


def load_graph(storage_dir: Path):
    from entity_graph import EntityGraphManager
    mgr = EntityGraphManager(str(storage_dir))
    return mgr


def _fmt_value(v) -> str:
    if v is None:
        return "(unbound)"
    s = str(v)
    # Shorten well-known URIs
    s = s.replace("https://schema.org/", "schema:")
    s = s.replace("https://briefkorb.local/schema#", "bk:")
    s = s.replace("http://www.w3.org/2002/07/owl#", "owl:")
    return s


def run_query(mgr, raw: str) -> None:
    # Prepend prefixes unless the query already declares them
    query = raw.strip()
    if not query.lower().startswith("prefix"):
        query = _PREFIXES + "\n" + query

    try:
        from rdflib.query import ResultRow
        results = mgr._graph.query(query)

        result_type = results.type  # "SELECT", "CONSTRUCT", "ASK", "DESCRIBE"

        if result_type == "ASK":
            print("Result:", results.askAnswer)
            return

        if result_type == "SELECT":
            rows = list(results)
            if not rows:
                print("(no results)")
                return
            vars_ = [str(v) for v in results.vars]
            col_w = [max(len(v), 4) for v in vars_]
            for row in rows:
                for i, v in enumerate(row):
                    col_w[i] = max(col_w[i], len(_fmt_value(v)))
            header = "  ".join(v.ljust(col_w[i]) for i, v in enumerate(vars_))
            sep = "  ".join("-" * w for w in col_w)
            print(header)
            print(sep)
            for row in rows:
                vals = [_fmt_value(row[i]) for i in range(len(vars_))]
                print("  ".join(v.ljust(col_w[i]) for i, v in enumerate(vals)))
            print(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")
            return

        # DESCRIBE / CONSTRUCT — print as Turtle
        from rdflib import Graph
        g = Graph()
        for triple in results:
            g.add(triple)
        print(g.serialize(format="turtle"))

    except Exception as e:
        print(f"Error: {e}")


def _show_graphs(mgr) -> None:
    for ctx in mgr._graph.contexts():
        n = sum(1 for _ in ctx)
        print(f"  {ctx.identifier}  ({n:,} triples)")


def main() -> None:
    storage_dir = resolve_dir(sys.argv[1] if len(sys.argv) > 1 else None)

    nq_path = storage_dir / "entity_graph.nq"
    if not nq_path.exists():
        print(f"No entity graph found at {storage_dir}")
        print("Run the app and process some emails first, or pass a different path.")
        sys.exit(1)

    print(f"Loading entity graph from {storage_dir} …")
    mgr = load_graph(storage_dir)

    triple_count = sum(1 for _ in mgr._graph)
    print(f"Loaded {triple_count:,} triples.  Type \\help for usage.\n")

    buffer: list[str] = []

    while True:
        prompt = "    ... " if buffer else "sparql> "
        try:
            line = input(prompt)
        except KeyboardInterrupt:
            if buffer:
                print("  (buffer cleared)")
                buffer.clear()
            else:
                print()
        except EOFError:
            print("\nBye.")
            break
        else:
            stripped = line.strip()

            # Backslash commands (only when buffer is empty)
            if not buffer and stripped.startswith("\\"):
                cmd = stripped.lower()
                if cmd in ("\\quit", "\\exit", "\\q"):
                    print("Bye.")
                    break
                elif cmd == "\\help":
                    print(_HELP)
                elif cmd == "\\prefixes":
                    print(_PREFIXES)
                elif cmd == "\\triples":
                    print(f"{sum(1 for _ in mgr._graph):,} triples")
                elif cmd == "\\graphs":
                    _show_graphs(mgr)
                elif cmd == "\\clear":
                    buffer.clear()
                else:
                    print(f"Unknown command: {stripped}  (try \\help)")
                continue

            # Blank line submits
            if stripped == "":
                if buffer:
                    run_query(mgr, "\n".join(buffer))
                    buffer.clear()
            else:
                buffer.append(line)


if __name__ == "__main__":
    main()
