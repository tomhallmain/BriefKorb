import os

from .graph import EntityGraph


class EntityStorage:
    """N-Quads persistence for the entity graph."""

    def __init__(self, path: str) -> None:
        self._path = path

    def load(self, graph: EntityGraph) -> None:
        if os.path.exists(self._path):
            graph.parse(self._path, fmt="nquads")

    def save(self, graph: EntityGraph) -> None:
        graph.serialize(self._path, fmt="nquads")
