"""Declarative, independent graph and review repository bindings.

This is trusted server configuration. Only the separately validated ``client``
object may be sent to the browser; paths, SDK settings, and token environment
variable names stay in the server process. Loading configuration never provisions
storage or creates a review. Only selected bindings instantiate their adapters.
"""

from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from graph_model import ReviewError, parse_json, text_value
from graph_repositories import Repositories, check_repositories, local_repositories


CONFIG_SCHEMA = "behavior-review-config/1"
CLIENT_SCHEMA = "behavior-review-client-config/1"
DEFAULT_CLIENT_CONFIG = {
    "schema_version": CLIENT_SCHEMA,
    "repositories": {"local": {"type": "http", "base_url": ""}},
    "bindings": {"graphs": "local", "reviews": "local"},
}
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_RESERVED_NAMES = {"constructor", "prototype", "__proto__", "toString", "toLocaleString",
                   "valueOf", "hasOwnProperty", "isPrototypeOf", "propertyIsEnumerable"}
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _fields(value, required, optional=(), label="configuration"):
    if (not isinstance(value, dict) or set(required) - value.keys()
            or value.keys() - set(required) - set(optional)):
        raise ReviewError(f"Invalid {label} fields. Check the documented config.json schema.")


def _string(value, label, maximum=8000):
    text_value(value, label, maximum)
    return value


def _url(value, label, *, relative=False):
    if (not isinstance(value, str) or len(value) > 8000
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in "<>\"'`" for c in value)):
        raise ReviewError(f"{label} must be a valid URL.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ReviewError(f"{label} must be a valid URL.") from error
    if (parsed.username is not None or parsed.password is not None or "?" in value or "#" in value
            or "\\" in value or (port is not None and not 1 <= port <= 65535)):
        raise ReviewError(f"{label} cannot contain credentials, a query, or a fragment.")
    if relative and not parsed.scheme and not parsed.netloc and not value.startswith("//"):
        return value
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ReviewError(f"{label} must use HTTP or HTTPS.")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ReviewError(f"{label} requires HTTPS except for loopback development servers.")
    return value


def _registry(value):
    if not isinstance(value, dict) or not value:
        raise ReviewError("repositories must be a nonempty object of named repository definitions.")
    for name, spec in value.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in _RESERVED_NAMES:
            raise ReviewError("Repository names must begin with a letter and contain only letters, digits, underscores, or hyphens.")
        if not isinstance(spec, dict) or not isinstance(spec.get("type"), str):
            raise ReviewError("Each repository definition requires a type.")


def _bindings(value, definitions):
    _fields(value, ("graphs", "reviews"), label="repository bindings")
    for role in ("graphs", "reviews"):
        name = value[role]
        if not isinstance(name, str) or name not in definitions:
            raise ReviewError(f"The {role} binding must name a defined repository.")


def validate_client_config(value):
    """Return an isolated public configuration with no server-only options."""
    _fields(value, ("schema_version", "repositories", "bindings"), label="client configuration")
    if value["schema_version"] != CLIENT_SCHEMA:
        raise ReviewError("Unsupported browser repository configuration schema.")
    definitions = value["repositories"]
    _registry(definitions)
    _bindings(value["bindings"], definitions)
    for spec in definitions.values():
        kind = spec["type"]
        if kind == "http":
            _fields(spec, ("type",), ("base_url",), label="public HTTP repository")
            _url(spec.get("base_url", ""), "Client HTTP base_url", relative=True)
        elif kind == "json":
            _fields(spec, ("type", "url"), label="public JSON repository")
            _string(spec["url"], "Client JSON catalog URL")
            _url(spec["url"], "Client JSON catalog URL", relative=True)
        elif kind == "memory":
            _fields(spec, ("type", "catalog", "snapshots"), label="public memory repository")
            if not isinstance(spec["catalog"], dict) or not isinstance(spec["snapshots"], list):
                raise ReviewError("A client memory repository requires a catalog object and snapshots array.")
        else:
            raise ReviewError("Browser repository type must be http, json, or memory. Configure storage credentials only on the server.")
    if definitions[value["bindings"]["reviews"]]["type"] != "http":
        raise ReviewError("The browser reviews binding requires an HTTP repository with durable review operations.")
    return deepcopy(value)


def client_connect_origins(value):
    """CSP sources from explicit public endpoints, never wildcard permissions."""
    origins = set()
    for spec in validate_client_config(value)["repositories"].values():
        address = spec.get("base_url", spec.get("url", ""))
        parsed = urlsplit(address)
        if parsed.scheme and parsed.netloc:
            origins.add(parsed.scheme + "://" + parsed.netloc)
    return sorted(origins)


def _positive_number(value, label, maximum):
    if type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= maximum:
        raise ReviewError(f"{label} must be a positive number no greater than {maximum}.")


def validate_config(value):
    _fields(value, ("schema_version", "repositories", "bindings"), ("graph_id", "client"), "server configuration")
    if value["schema_version"] != CONFIG_SCHEMA:
        raise ReviewError("Unsupported server repository configuration schema.")
    definitions = value["repositories"]
    _registry(definitions)
    _bindings(value["bindings"], definitions)
    if (definitions[value["bindings"]["reviews"]]["type"] == "session"
            and value["bindings"]["graphs"] != value["bindings"]["reviews"]):
        raise ReviewError("A legacy session review must use the same named repository for graphs and reviews. Import the session into document repositories for independent storage.")
    if "graph_id" in value:
        _string(value["graph_id"], "graph_id", 2000)
    for spec in definitions.values():
        kind = spec["type"]
        if kind in {"filesystem", "sqlite"}:
            _fields(spec, ("type", "path"), label=kind + " repository")
            _string(spec["path"], kind + " path")
        elif kind == "session":
            _fields(spec, ("type",), ("session", "workspace"), "session repository")
            if ("session" in spec) == ("workspace" in spec):
                raise ReviewError("A session repository requires exactly one session or workspace path.")
            _string(spec.get("session", spec.get("workspace")), "session repository path")
        elif kind == "s3":
            _fields(spec, ("type", "bucket"), ("prefix", "region", "endpoint_url", "profile", "max_bytes"), "S3 repository")
            _string(spec["bucket"], "S3 bucket", 255)
            for key in ("region", "profile"):
                if key in spec:
                    _string(spec[key], "S3 " + key, 255)
            if "prefix" in spec and (not isinstance(spec["prefix"], str) or len(spec["prefix"]) > 2000):
                raise ReviewError("S3 prefix must be a string no longer than 2000 characters.")
            if "endpoint_url" in spec:
                _url(spec["endpoint_url"], "S3 endpoint_url")
        elif kind == "http":
            _fields(spec, ("type", "base_url"), ("auth_token_env", "timeout", "max_bytes"), "HTTP repository")
            _url(spec["base_url"], "HTTP base_url")
            if "auth_token_env" in spec and (not isinstance(spec["auth_token_env"], str)
                                              or not _ENV.fullmatch(spec["auth_token_env"])):
                raise ReviewError("auth_token_env must name an environment variable, not contain a token.")
            if "timeout" in spec:
                _positive_number(spec["timeout"], "HTTP timeout", 120)
        else:
            raise ReviewError("Server repository type must be filesystem, sqlite, s3, http, or session.")
        if "max_bytes" in spec and (type(spec["max_bytes"]) is not int or not 1024 <= spec["max_bytes"] <= 128 * 1024 * 1024):
            raise ReviewError("max_bytes must be an integer between 1024 and 134217728.")
    client = validate_client_config(value.get("client", DEFAULT_CLIENT_CONFIG))
    return {**deepcopy(value), "client": client}


@dataclass(frozen=True)
class ConfiguredRepositories:
    path: Path
    repositories: Repositories
    graph_id: str | None
    client_config: dict
    bindings: dict
    types: dict


def read_config(path):
    path = Path(path).expanduser().resolve()
    try:
        value = parse_json(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ReviewError("Repository config.json cannot be read.", 404) from error
    return path, validate_config(value)


def load_repository_config(path):
    path, value = read_config(path)
    definitions = value["repositories"]
    instances = {}

    def location(raw):
        candidate = Path(raw).expanduser()
        return (path.parent / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()

    def instance(name):
        if name in instances:
            return instances[name]
        spec = definitions[name]
        kind = spec["type"]
        if kind == "filesystem":
            from filesystem_repository import FileSystemDocumentStore
            created = FileSystemDocumentStore(location(spec["path"]))
        elif kind == "sqlite":
            from sqlite_repository import SQLiteDocumentStore
            created = SQLiteDocumentStore(location(spec["path"]))
        elif kind == "s3":
            from s3_repository import S3DocumentStore
            created = S3DocumentStore(**{key: item for key, item in spec.items() if key != "type"})
        elif kind == "http":
            from http_repository import HttpRepositoryClient
            created = HttpRepositoryClient(**{key: item for key, item in spec.items() if key != "type"})
        else:
            selected = "session" if "session" in spec else "workspace"
            created = local_repositories(**{selected: location(spec[selected])})
        instances[name] = created
        return created

    def graph_repository(name):
        kind = definitions[name]["type"]
        backing = instance(name)
        if kind == "session":
            return backing.graphs
        if kind == "http":
            from http_repository import HttpGraphRepository
            return HttpGraphRepository(backing)
        from document_repositories import DocumentGraphRepository
        return DocumentGraphRepository(backing)

    def review_repository(name, graphs):
        kind = definitions[name]["type"]
        backing = instance(name)
        if kind == "session":
            return backing.reviews
        if kind == "http":
            from http_repository import HttpHumanReviewRepository
            return HttpHumanReviewRepository(backing)
        from document_repositories import DocumentHumanReviewRepository
        return DocumentHumanReviewRepository(backing, graphs)

    graphs = graph_repository(value["bindings"]["graphs"])
    reviews = review_repository(value["bindings"]["reviews"], graphs)
    return ConfiguredRepositories(path, check_repositories(Repositories(graphs, reviews)),
                                  value.get("graph_id"), value["client"], value["bindings"],
                                  {name: spec["type"] for name, spec in definitions.items()})
