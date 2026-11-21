"""Durable, trusted repository locators shared by the review app and agent CLI.

A connection is configuration, never content supplied by a graph or annotation.
It contains paths and an optional factory reference, not credentials. Relative
paths are resolved beside the connection file so a local review can be moved.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile

from graph_model import ReviewError, parse_json, text_value
from graph_repositories import Repositories, local_repositories, load_repositories


CONNECTION_SCHEMA = "behavior-review-connection/1"


def _graph_id(value):
    text_value(value, "graph_id", 2000)
    return value  # IDs are opaque; do not trim or reinterpret their characters.


@dataclass(frozen=True)
class ReviewConnection:
    path: Path | None
    repositories: Repositories
    graph_id: str | None
    kind: str
    client_config: dict | None = None
    config_path: Path | None = None

    def select_graph(self, override=None):
        identifier = override if override is not None else self.graph_id
        if identifier is None:
            raise ReviewError("Select a graph with --graph-id, or save graph_id in the connection. Use feedback-graphs to list available IDs.")
        return _graph_id(identifier)


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ReviewError("Invalid review connection fields. Use the documented behavior-review-connection/1 format.")


def _relative_path(value, parent, label):
    value = text_value(value, label, 8000)
    path = Path(value).expanduser()
    return (parent / path).resolve() if not path.is_absolute() else path.resolve()


def _factory_reference(value, parent):
    value = text_value(value, "repository factory", 8000)
    if ":" not in value:
        raise ReviewError("Repository factory must use module:callable or /path/plugin.py:callable.")
    module, name = value.rsplit(":", 1)
    if not module or not name.isidentifier():
        raise ReviewError("Repository factory must name one module and callable.")
    if module.endswith(".py"):
        module = str(_relative_path(module, parent, "factory path"))
    return module + ":" + name


def _load_value(value, path):
    _keys(value, ("schema_version", "repository"), ("graph_id",))
    if value["schema_version"] != CONNECTION_SCHEMA:
        raise ReviewError("Unsupported review connection schema.")
    graph_id = value.get("graph_id")
    if graph_id is not None:
        graph_id = _graph_id(graph_id)
    repository = value["repository"]
    if not isinstance(repository, dict):
        raise ReviewError("Review connection repository must be an object.")
    kind = repository.get("kind")
    client_config = None
    config_path = None
    if kind in {"session", "workspace"}:
        _keys(repository, ("kind", "path"))
        location = _relative_path(repository["path"], path.parent, "repository path")
        repositories = local_repositories(**{kind: location})
    elif kind == "factory":
        _keys(repository, ("kind", "factory"), ("config",))
        factory = _factory_reference(repository["factory"], path.parent)
        config = (_relative_path(repository["config"], path.parent, "repository config")
                  if "config" in repository else None)
        repositories = load_repositories(factory, config)
    elif kind == "config":
        from repository_config import load_repository_config
        _keys(repository, ("kind", "path"))
        configured = load_repository_config(_relative_path(repository["path"], path.parent, "repository config"))
        repositories = configured.repositories
        client_config, config_path = configured.client_config, configured.path
        if graph_id is None:
            graph_id = configured.graph_id
    else:
        raise ReviewError("Review connection repository kind must be session, workspace, factory, or config.")
    return ReviewConnection(path, repositories, graph_id, kind, client_config, config_path)


def read_connection(path):
    path = Path(path).expanduser().resolve()
    try:
        value = parse_json(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReviewError("Review connection cannot be read. Use its saved path or create one with connect.", 404) from error
    return _load_value(value, path)


def session_connection(session):
    """Explicit --session remains a convenience, without a write during reads."""
    repositories = local_repositories(session=session)
    identifier = repositories.graphs.list_graphs()[0]["id"]
    return ReviewConnection(None, repositories, identifier, "session")


def config_connection(path):
    from repository_config import load_repository_config
    configured = load_repository_config(path)
    return ReviewConnection(None, configured.repositories, configured.graph_id, "config",
                            configured.client_config, configured.path)


def write_connection(path, *, session=None, workspace=None, factory=None, config=None,
                     graph_id=None, repository_config_path=None):
    """Create or reuse an identical locator; never overwrite another connection."""
    if sum(item is not None for item in (session, workspace, factory, repository_config_path)) != 1:
        raise ReviewError("Choose exactly one session, workspace, repository factory, or config.json.")
    if config is not None and factory is None:
        raise ReviewError("--repository-config requires --repository-factory.")
    path = Path(path).expanduser().absolute()
    path = path.parent.resolve() / path.name

    def stored_path(value):
        location = Path(value).expanduser().resolve()
        try:
            return os.path.relpath(location, path.parent)
        except ValueError:  # Different drives on Windows.
            return str(location)

    if session is not None:
        repository = {"kind": "session", "path": stored_path(session)}
        if graph_id is None:
            graph_id = session_connection(session).graph_id
    elif workspace is not None:
        repository = {"kind": "workspace", "path": stored_path(workspace)}
    elif repository_config_path is not None:
        repository = {"kind": "config", "path": stored_path(repository_config_path)}
    else:
        resolved = _factory_reference(factory, Path.cwd())
        module, name = resolved.rsplit(":", 1)
        if module.endswith(".py"):
            resolved = stored_path(module) + ":" + name
        repository = {"kind": "factory", "factory": resolved}
        if config is not None:
            repository["config"] = stored_path(config)
    value = {"schema_version": CONNECTION_SCHEMA, "repository": repository}
    if graph_id is not None:
        value["graph_id"] = _graph_id(graph_id)
    connection = _load_value(value, path)
    if connection.graph_id is not None:
        if not any(item.get("id") == connection.graph_id for item in connection.repositories.graphs.list_graphs()):
            raise ReviewError("Selected graph is not in this repository's catalog.", 404)
    path.parent.mkdir(parents=True, exist_ok=True)
    def check_existing():
        if path.is_symlink():
            raise ReviewError("Refusing to replace a symbolic-link connection file.", 409)
        existing = parse_json(path.read_text(encoding="utf-8"))
        if existing != value:
            raise ReviewError("This connection file already has different settings. Choose a new path; no settings were overwritten.", 409)

    if path.exists() or path.is_symlink():
        check_existing()
        return connection
    descriptor, temporary_name = tempfile.mkstemp(prefix=".review-connection-", suffix=".json", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        try:
            stream = os.fdopen(descriptor, "w", encoding="utf-8")
        except Exception:
            os.close(descriptor)
            raise
        with stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Publish a complete file atomically without replacing a competing writer.
        # Both paths are on the same filesystem. Failure leaves the old locator
        # untouched and never exposes an incomplete connection.json.
        try:
            os.link(temporary, path)
        except FileExistsError:
            check_existing()
    finally:
        temporary.unlink(missing_ok=True)
    return connection
