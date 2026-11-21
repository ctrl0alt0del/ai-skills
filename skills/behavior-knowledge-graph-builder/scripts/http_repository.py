"""Server-side repository adapters for the graph review HTTP API.

The configured base URL identifies a trusted review service, not a generic JSON
or S3 endpoint. Graph IDs/revisions stay opaque, mutations carry the review
version and the service's CSRF token, and redirects never forward credentials.
"""

from http.client import HTTPException
import ipaddress
import math
import os
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from graph_contract import ReviewError, canonical, parse_json


MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_REQUEST_BYTES = 128 * 1024


def _base_url(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(char) < 33 for char in value) or "\\" in value):
        raise ReviewError("HTTP repository base_url must be an absolute HTTPS URL.")
    try:
        parsed = urlsplit(value)
        hostname, port = parsed.hostname, parsed.port
    except ValueError:
        raise ReviewError("HTTP repository base_url is invalid.") from None
    if (not hostname or parsed.username is not None or parsed.password is not None
            or "?" in value or "#" in value or (port is not None and not 1 <= port <= 65535)):
        raise ReviewError("HTTP repository base_url cannot include credentials, a query, or a fragment.")
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname.lower() == "localhost"
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ReviewError("HTTP repositories require HTTPS; HTTP is permitted only for loopback services.")
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    return value.rstrip("/"), origin


def _segment(value, label):
    if not isinstance(value, str) or not value or len(value) > 8192:
        raise ReviewError(f"{label} must be a nonempty opaque string of at most 8192 characters.")
    try:
        encoded = quote(value, safe="", encoding="utf-8", errors="strict")
    except UnicodeError:
        raise ReviewError(f"{label} must be valid UTF-8.") from None
    # quote deliberately leaves dots alone. Entire dot segments must still be
    # encoded so intermediaries cannot interpret an opaque ID as traversal.
    return encoded.replace(".", "%2E") if encoded in {".", ".."} else encoded


def _graph_path(graph_id):
    return "/api/graphs/" + _segment(graph_id, "graph_id")


def _deleted_query(include_deleted):
    if type(include_deleted) is not bool:
        raise ReviewError("include_deleted must be a boolean.")
    return "?include_deleted=true" if include_deleted else ""


def _version_payload(expected_version, values=None):
    if type(expected_version) is not int or expected_version < 0:
        raise ReviewError("expected_version must be a nonnegative integer.")
    if values is not None and not isinstance(values, dict):
        raise ReviewError("Repository mutation data must be a JSON object.")
    return {**(values or {}), "expected_version": expected_version}


def _http_error(status):
    messages = {
        400: "Remote repository rejected the request. Check its API contract and input.",
        401: "Remote repository authentication failed. Check its configured token environment variable.",
        403: "Remote repository denied the request. Check access and refresh the review before retrying.",
        404: "Remote graph, review, or API operation was not found.",
        405: "Remote repository does not accept this operation's HTTP method.",
        409: "Remote review changed. Read the latest state before retrying.",
        413: "Remote repository rejected an oversized request.",
        429: "Remote repository rate limit reached. Retry later.",
        501: "Remote repository does not support this operation. Upgrade its adapter or service.",
    }
    if not isinstance(status, int) or not 400 <= status <= 599:
        return ReviewError("Remote repository redirected or returned an unexpected HTTP status; redirects are disabled.", 502)
    return ReviewError(messages.get(status, "Remote repository operation failed. Check service availability and retry."), status)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class HttpRepositoryClient:
    """Bounded UTF-8 JSON transport with HTTPS and optional environment auth.

    base_url may contain a reverse-proxy path prefix, but never ``/api`` itself.
    The bearer token is read from auth_token_env at request time. No literal
    credentials are accepted in URLs or repository configuration.
    """

    def __init__(self, base_url, auth_token_env=None, timeout=15, max_bytes=MAX_RESPONSE_BYTES):
        self.base_url, self.origin = _base_url(base_url)
        if auth_token_env is not None and (not isinstance(auth_token_env, str)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", auth_token_env)):
            raise ReviewError("auth_token_env must name an environment variable.")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0 or timeout > 300:
            raise ReviewError("HTTP repository timeout must be between 0 and 300 seconds, excluding 0.")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ReviewError("HTTP repository max_bytes must be a positive integer.")
        self.auth_token_env = auth_token_env
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._opener = build_opener(_NoRedirects())
        self._csrf_token = None
        self._token_lock = threading.RLock()

    def _headers(self):
        headers = {"Accept": "application/json", "Cache-Control": "no-cache"}
        if self.auth_token_env is not None:
            token = os.environ.get(self.auth_token_env)
            if not token or len(token) > 16384 or any(ord(char) < 33 or ord(char) > 126 for char in token):
                raise ReviewError("HTTP repository authentication token is missing or invalid in the configured environment variable.", 401)
            headers["Authorization"] = "Bearer " + token
        return headers

    def _review_token(self):
        with self._token_lock:
            if self._csrf_token is None:
                token = self.request("GET", "/api/bootstrap").get("csrf_token")
                if (not isinstance(token, str) or not token or len(token) > 4096
                        or any(ord(char) < 33 or ord(char) > 126 for char in token)):
                    raise ReviewError("Remote repository did not provide a valid review token.", 502)
                self._csrf_token = token
            return self._csrf_token

    def request(self, method, path, body=None):
        """Call a path within this service; return a JSON object or ReviewError."""
        if method not in {"GET", "POST", "PATCH", "DELETE"} or not isinstance(path, str) or not path.startswith("/api/"):
            raise ReviewError("Invalid repository HTTP operation.")
        headers = self._headers()
        raw = None
        if method != "GET":
            if not isinstance(body, dict):
                raise ReviewError("Repository mutation data must be a JSON object.")
            try:
                raw = canonical(body).encode("utf-8")
            except (ValueError, TypeError, UnicodeError, RecursionError):
                raise ReviewError("Repository mutation is not valid UTF-8 JSON.") from None
            if len(raw) > MAX_REQUEST_BYTES:
                raise ReviewError("Repository mutation exceeds the 128 KiB request limit.", 413)
            headers.update({"Content-Type": "application/json; charset=utf-8",
                            "Origin": self.origin, "X-Review-Token": self._review_token()})
        try:
            request = Request(self.base_url + path, data=raw, headers=headers, method=method)
            with self._opener.open(request, timeout=self.timeout) as response:
                if not 200 <= response.status <= 299:
                    raise _http_error(response.status)
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        length = int(length)
                    except ValueError:
                        raise ReviewError("Remote repository returned an invalid response length.", 502) from None
                    if length < 0:
                        raise ReviewError("Remote repository returned an invalid response length.", 502)
                    if length > self.max_bytes:
                        raise ReviewError("Remote repository response exceeds the configured byte limit.", 502)
                raw = response.read(self.max_bytes + 1)
                if len(raw) > self.max_bytes:
                    raise ReviewError("Remote repository response exceeds the configured byte limit.", 502)
        except HTTPError as error:
            status = error.code
            error.close()
            if status == 403:
                with self._token_lock:
                    self._csrf_token = None
            raise _http_error(status) from None
        except TimeoutError:
            raise ReviewError("Remote repository request timed out. Read the latest state before retrying a mutation.", 504) from None
        except (URLError, HTTPException, OSError, ValueError):
            raise ReviewError("Could not reach the remote repository. Check its URL, TLS certificate, and service availability.", 502) from None
        try:
            document = parse_json(raw.decode("utf-8"))
        except (ReviewError, UnicodeError):
            raise ReviewError("Remote repository response is not valid UTF-8 JSON.", 502) from None
        if not isinstance(document, dict):
            raise ReviewError("Remote repository response must be a JSON object.", 502)
        return document


class HttpGraphRepository:
    def __init__(self, client):
        self.client = client

    def list_graphs(self):
        graphs = self.client.request("GET", "/api/graphs").get("graphs")
        if not isinstance(graphs, list) or not all(isinstance(graph, dict) for graph in graphs):
            raise ReviewError("Remote graph catalog must contain a graphs array of objects.", 502)
        return graphs

    def get_graph(self, graph_id, revision=None):
        path = _graph_path(graph_id)
        if revision is not None:
            _segment(revision, "revision")
            path += "?" + urlencode({"revision": revision}, encoding="utf-8", errors="strict")
        return self.client.request("GET", path)


class HttpHumanReviewRepository:
    def __init__(self, client):
        self.client = client

    def get_review(self, graph_id):
        return self.client.request("GET", _graph_path(graph_id) + "/review")

    def list_annotations(self, graph_id, include_deleted=False):
        annotations = self.client.request("GET", _graph_path(graph_id) + "/annotations" + _deleted_query(include_deleted)).get("annotations")
        if not isinstance(annotations, list) or not all(isinstance(note, dict) for note in annotations):
            raise ReviewError("Remote annotation response must contain an annotations array of objects.", 502)
        return annotations

    def get_annotation(self, graph_id, annotation_id, include_deleted=False):
        path = _graph_path(graph_id) + "/annotations/" + _segment(annotation_id, "annotation_id")
        return self.client.request("GET", path + _deleted_query(include_deleted))

    def create_annotation(self, graph_id, annotation, expected_version):
        return self.client.request("POST", _graph_path(graph_id) + "/annotations", _version_payload(expected_version, annotation))

    def update_annotation(self, graph_id, annotation_id, changes, expected_version):
        path = _graph_path(graph_id) + "/annotations/" + _segment(annotation_id, "annotation_id")
        return self.client.request("PATCH", path, _version_payload(expected_version, changes))

    def delete_annotation(self, graph_id, annotation_id, expected_version):
        path = _graph_path(graph_id) + "/annotations/" + _segment(annotation_id, "annotation_id")
        return self.client.request("DELETE", path, _version_payload(expected_version))

    def resolve_annotation(self, graph_id, annotation_id, disposition, text, expected_version):
        return self.client.request("POST", _graph_path(graph_id) + "/resolve", _version_payload(expected_version, {
            "annotation_id": annotation_id, "disposition": disposition, "text": text,
        }))

    def reopen_annotation(self, graph_id, annotation_id, expected_version):
        return self.client.request("POST", _graph_path(graph_id) + "/reopen", _version_payload(expected_version, {
            "annotation_id": annotation_id,
        }))

    def request_review(self, graph_id, expected_version):
        return self.client.request("POST", _graph_path(graph_id) + "/request-review", _version_payload(expected_version))

    def approve_review(self, graph_id, text, acknowledge_limits, expected_version):
        return self.client.request("POST", _graph_path(graph_id) + "/approve", _version_payload(expected_version, {
            "text": text, "acknowledge_limits": acknowledge_limits,
        }))

    def list_pending_feedback(self, graph_id):
        return self.client.request("GET", _graph_path(graph_id) + "/pending-feedback")

    def mark_feedback_processed(self, graph_id, annotation_id, feedback_revision, outcome, summary,
                                processor, expected_version):
        return self.client.request("POST", _graph_path(graph_id) + "/mark-processed", _version_payload(expected_version, {
            "annotation_id": annotation_id, "feedback_revision": feedback_revision,
            "outcome": outcome, "summary": summary, "processor": processor,
        }))
