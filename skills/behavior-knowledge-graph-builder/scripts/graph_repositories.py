"""Replaceable graph/read and human-review/write ports for the packaged app.

The HTTP server depends on these ports, not on SQLite or filesystem paths. Graph
IDs and revisions are opaque strings. A revision must identify an immutable graph
snapshot within its graph ID; review responses point to that exact revision.

Review adapters own atomic compare-and-swap, annotation audit/tombstones, and the
approval state machine. Every mutation must reject a stale expected_version with
ReviewError(status=409), invalidate obsolete approval, and return the complete
review DTO. There is intentionally no unrestricted save/PUT of review state.

The default adapters share a local session catalog and delegate domain operations
to Store. An API, S3, database, or filesystem integration may supply either port
independently; it need not inherit from these implementations or use SQLite.
"""

from dataclasses import dataclass
import importlib
import importlib.util
from pathlib import Path
import sys
from typing import Protocol

from graph_model import Store, ReviewError, digest, parse_json


class GraphRepository(Protocol):
    def list_graphs(self) -> list[dict]: ...
    def get_graph(self, graph_id: str, revision: str | None = None) -> dict: ...


class HumanReviewRepository(Protocol):
    def get_review(self, graph_id: str) -> dict: ...
    def list_annotations(self, graph_id: str, include_deleted: bool = False) -> list[dict]: ...
    def get_annotation(self, graph_id: str, annotation_id: str, include_deleted: bool = False) -> dict: ...
    def create_annotation(self, graph_id: str, annotation: dict, expected_version: int) -> dict: ...
    def update_annotation(self, graph_id: str, annotation_id: str, changes: dict, expected_version: int) -> dict: ...
    def delete_annotation(self, graph_id: str, annotation_id: str, expected_version: int) -> dict: ...
    def resolve_annotation(self, graph_id: str, annotation_id: str, disposition: str,
                           text: str, expected_version: int) -> dict: ...
    def reopen_annotation(self, graph_id: str, annotation_id: str, expected_version: int) -> dict: ...
    def request_review(self, graph_id: str, expected_version: int) -> dict: ...
    def approve_review(self, graph_id: str, text: str, acknowledge_limits: bool, expected_version: int) -> dict: ...


@dataclass(frozen=True)
class Repositories:
    graphs: GraphRepository
    reviews: HumanReviewRepository


def review_dto(state):
    """Separate graph data from human review state; keep legacy review fields."""
    return {**{key: value for key, value in state.items() if key != "graph"},
            "graph_id": state["session_id"], "graph_revision": str(state["round"]),
            "rounds": [{**item, "graph_revision": str(item["round"])}
                       for item in state["rounds"]]}


class SessionCatalog:
    """Resolve stable IDs to initialized local sessions without exposing paths.

    Workspace discovery is deliberately one directory deep and ignores symlinks.
    No review is created by a read. Bad/duplicate initialized sessions fail visibly
    rather than disappearing from the catalog. New direct children appear on the
    next read; moving a session retains its ID.
    """

    def __init__(self, session=None, workspace=None):
        if (session is None) == (workspace is None):
            raise ReviewError("Choose exactly one local session or workspace.")
        self.session = session if isinstance(session, Store) else (Store(session) if session is not None else None)
        self.workspace = Path(workspace).expanduser().resolve() if workspace is not None else None
        if self.workspace is not None and not self.workspace.is_dir():
            raise ReviewError("The review workspace must be an existing directory.", 404)
        self.entries()

    def entries(self):
        if self.session is not None:
            stores = [self.session]
        else:
            stores = [Store(path) for path in sorted(self.workspace.iterdir())
                      if path.is_dir() and not path.is_symlink()
                      and (path / "review.sqlite3").is_file()
                      and not (path / "review.sqlite3").is_symlink()]
        result = {}
        for store in stores:
            state = store.state()
            identifier = state["session_id"]
            if identifier in result:
                raise ReviewError("Duplicate graph IDs in the review workspace.", 409)
            result[identifier] = (store, state)
        return result

    def find(self, graph_id):
        entry = self.entries().get(graph_id)
        if entry is None:
            raise ReviewError("Graph not found.", 404)
        return entry


class SessionGraphRepository:
    def __init__(self, catalog):
        self.catalog = catalog

    def list_graphs(self):
        return [{"id": identifier, "title": state["title"], "revision": str(state["round"]),
                 "digest": state["graph_digest"], "node_count": len((state["graph"] or {}).get("nodes", []))}
                for identifier, (_, state) in self.catalog.entries().items()]

    def get_graph(self, graph_id, revision=None):
        store, state = self.catalog.find(graph_id)
        revision = str(state["round"]) if revision is None else revision
        if (not isinstance(revision, str) or len(revision) > 18 or not revision.isascii()
                or not revision.isdecimal() or str(int(revision)) != revision):
            raise ReviewError("Local graph revision must be a canonical nonnegative round number.")
        number = int(revision)
        if number == 0:
            graph, graph_digest = None, None
        else:
            snapshot = store.round_snapshot(number)
            graph, graph_digest = snapshot["graph"], snapshot["digest"]
            if digest(graph) != graph_digest:
                raise ReviewError("Stored graph revision does not match its digest.", 409)
        return {"id": graph_id, "title": state["title"], "revision": revision,
                "digest": graph_digest, "graph": graph}


class SQLiteHumanReviewRepository:
    """Local adapter preserving Store's transactional domain operations."""

    def __init__(self, catalog):
        self.catalog = catalog

    def get_review(self, graph_id):
        _, state = self.catalog.find(graph_id)
        return review_dto(state)

    def list_annotations(self, graph_id, include_deleted=False):
        notes = self.get_review(graph_id)["annotations"]
        return [note for note in notes if include_deleted or note["status"] != "deleted"]

    def get_annotation(self, graph_id, annotation_id, include_deleted=False):
        for note in self.list_annotations(graph_id, include_deleted):
            if note["id"] == annotation_id:
                return note
        raise ReviewError("Annotation not found.", 404)

    def create_annotation(self, graph_id, annotation, expected_version):
        store, _ = self.catalog.find(graph_id)
        return review_dto(store.annotate(annotation["target_type"], annotation["target_id"],
                                        annotation["kind"], annotation["text"], "ui", None, expected_version))

    def update_annotation(self, graph_id, annotation_id, changes, expected_version):
        store, _ = self.catalog.find(graph_id)
        return review_dto(store.edit_annotation(annotation_id, changes, "ui", expected_version))

    def delete_annotation(self, graph_id, annotation_id, expected_version):
        store, _ = self.catalog.find(graph_id)
        return review_dto(store.delete_annotation(annotation_id, "ui", expected_version))

    def resolve_annotation(self, graph_id, annotation_id, disposition, text, expected_version):
        store, _ = self.catalog.find(graph_id)
        return review_dto(store.resolve(annotation_id, disposition, text, "ui", expected_version))

    def reopen_annotation(self, graph_id, annotation_id, expected_version):
        store, _ = self.catalog.find(graph_id)
        return review_dto(store.reopen(annotation_id, expected_version, "ui"))

    def request_review(self, graph_id, expected_version):
        store, _ = self.catalog.find(graph_id)
        return review_dto(store.request_review(expected_version))

    def approve_review(self, graph_id, text, acknowledge_limits, expected_version):
        store, _ = self.catalog.find(graph_id)
        return review_dto(store.approve(text, "ui", None, acknowledge_limits, expected_version))


def local_repositories(session=None, workspace=None):
    catalog = SessionCatalog(session=session, workspace=workspace)
    return Repositories(SessionGraphRepository(catalog), SQLiteHumanReviewRepository(catalog))


def check_repositories(value):
    if isinstance(value, dict) and set(value) == {"graphs", "reviews"}:
        value = Repositories(value["graphs"], value["reviews"])
    elif isinstance(value, (tuple, list)) and len(value) == 2:
        value = Repositories(*value)
    if not isinstance(value, Repositories):
        raise ReviewError("Repository factory must return Repositories, a (graphs, reviews) pair, or a graphs/reviews mapping.")
    for name, methods in (("graphs", ("list_graphs", "get_graph")),
                          ("reviews", ("get_review", "list_annotations", "get_annotation", "create_annotation",
                                       "update_annotation", "delete_annotation", "resolve_annotation",
                                       "reopen_annotation", "request_review", "approve_review"))):
        for method in methods:
            if not callable(getattr(getattr(value, name), method, None)):
                raise ReviewError(f"{name} repository is missing callable {method}.")
    return value


def load_repositories(factory, config_path=None):
    """Load an explicitly trusted Python module:factory or /path/plugin.py:factory.

    Configuration is a JSON object passed as the factory's single argument. This
    is a startup extension point, not an HTTP endpoint or an untrusted graph field.
    Credentials stay in the server process; never include them in browser DTOs.
    """
    if not isinstance(factory, str) or ":" not in factory:
        raise ReviewError("Use --repository-factory module:callable or /path/plugin.py:callable.")
    module_name, callable_name = factory.rsplit(":", 1)
    if not module_name or not callable_name.isidentifier():
        raise ReviewError("Repository factory must name one module and callable.")
    config = {} if config_path is None else parse_json(Path(config_path).read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ReviewError("Repository configuration must be a JSON object.")
    try:
        if module_name.endswith(".py"):
            path = Path(module_name).expanduser().resolve()
            name = "_behavior_review_adapter_" + digest(str(path))[:16]
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise ImportError("Adapter cannot be loaded")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        else:
            module = importlib.import_module(module_name)
        builder = getattr(module, callable_name)
        if not callable(builder):
            raise TypeError("Factory is not callable")
        return check_repositories(builder(config))
    except ReviewError:
        raise
    except Exception as error:
        raise ReviewError("Cannot load the configured repository factory. Check its module, callable, and configuration.") from error
