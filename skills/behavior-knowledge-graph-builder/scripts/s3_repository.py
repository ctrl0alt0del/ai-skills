"""Bounded JSON document storage with S3's atomic conditional writes.

Graph/review rules live in document_repositories.py. This adapter only persists
their documents. boto3 is optional until this backend is selected; credentials
come from its normal environment/profile/role provider chain.
"""

import ipaddress
import re
from urllib.parse import urlsplit

from document_repositories import MAX_DOCUMENT_BYTES, document_bytes, document_key
from graph_contract import ReviewError, parse_json


def _endpoint(value):
    if value is None:
        return None
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(ord(char) < 33 for char in value) or "\\" in value):
        raise ReviewError("S3 endpoint_url must be an absolute HTTPS URL.")
    try:
        parsed = urlsplit(value)
        hostname, port = parsed.hostname, parsed.port
    except ValueError:
        raise ReviewError("S3 endpoint_url is invalid.") from None
    if (not hostname or parsed.username is not None or parsed.password is not None
            or "?" in value or "#" in value or (port is not None and not 1 <= port <= 65535)):
        raise ReviewError("S3 endpoint_url cannot include credentials, a query, or a fragment.")
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname.lower() == "localhost"
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ReviewError("S3 endpoints require HTTPS; HTTP is permitted only for loopback test services.")
    return value.rstrip("/")


def _relative_path(value, label):
    if (not isinstance(value, str) or not value or "\\" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ReviewError(f"S3 {label} must be a relative path without empty or traversal components.")
    return value


def _etag(value):
    # Keep the token unchanged. Reject wildcard/list header expressions, which
    # could turn an exact comparison into a weaker precondition.
    if (not isinstance(value, str) or not value or len(value) > 1024
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise ReviewError("S3 did not provide a usable object version token.", 502)
    quoted = len(value) >= 2 and value[0] == value[-1] == '"' and '"' not in value[1:-1]
    unquoted = not any(char in value for char in ('"', "*", ","))
    if not (quoted or unquoted):
        raise ReviewError("S3 did not provide a usable object version token.", 502)
    return value


def _storage_error(error, *, writing=False):
    """Never surface SDK messages: endpoints and credentials can occur in them."""
    response = getattr(error, "response", {})
    if not isinstance(response, dict):
        response = {}
    metadata = response.get("ResponseMetadata", {})
    detail = response.get("Error", {})
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
    code = detail.get("Code") if isinstance(detail, dict) else None
    status = status if type(status) is int else None
    code = code if isinstance(code, str) else None
    if code == "NoSuchBucket":
        return ReviewError("The configured S3 bucket does not exist or is unavailable. Create/configure it before using this repository.", 502)
    if status in {409, 412} or code in {"PreconditionFailed", "ConditionalRequestConflict", "409", "412"}:
        return ReviewError("S3 document changed or already exists. Read the latest state before retrying.", 409)
    if status == 404 or code in {"NoSuchKey", "NotFound", "404"}:
        if writing:
            return ReviewError("S3 document disappeared before the write. Read the latest state before retrying.", 409)
        return ReviewError("S3 document not found.", 404)
    if status in {401, 403} or code in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"}:
        return ReviewError("S3 access was denied. Check the configured SDK credentials and bucket permissions.", 403)
    if error.__class__.__name__ == "ParamValidationError":
        return ReviewError("The S3 SDK rejected this operation. Install a current boto3 release with conditional PutObject support.", 502)
    return ReviewError("S3 repository operation failed. Check its configuration, credentials, and service availability.", 502)


class S3DocumentStore:
    """Store JSON objects under one bucket/prefix with ETag compare-and-swap.

    There are no unconditional writes or read/then-overwrite fallbacks. A custom
    S3-compatible service must implement IfNoneMatch and IfMatch atomically.
    ``client`` is a dependency-injection hook for protocol tests.
    """

    def __init__(self, bucket, prefix="", region=None, endpoint_url=None, profile=None,
                 *, client=None, max_bytes=MAX_DOCUMENT_BYTES):
        if not isinstance(bucket, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,253}[A-Za-z0-9])?", bucket):
            raise ReviewError("S3 bucket must be a bucket name, without a URL or path.")
        if not isinstance(prefix, str) or prefix.startswith("/"):
            raise ReviewError("S3 prefix must be a relative string.")
        prefix = prefix[:-1] if prefix.endswith("/") else prefix
        if prefix:
            _relative_path(prefix, "prefix")
        for value, label in ((region, "region"), (profile, "profile")):
            if value is not None and (not isinstance(value, str) or not value.strip()
                                      or any(ord(char) < 32 for char in value)):
                raise ReviewError(f"S3 {label} must be a nonempty string.")
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ReviewError("S3 max_bytes must be a positive integer.")
        self.bucket = bucket
        self.prefix = prefix + "/" if prefix else ""
        self.max_bytes = max_bytes
        endpoint_url = _endpoint(endpoint_url)
        if client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError:
                raise ReviewError("The S3 repository requires optional boto3. Install boto3 in the server's Python environment.", 501) from None
            try:
                session = boto3.Session(profile_name=profile, region_name=region)
                client = session.client("s3", endpoint_url=endpoint_url, config=Config(
                    signature_version="s3v4", connect_timeout=10, read_timeout=30,
                    retries={"mode": "standard", "total_max_attempts": 1},
                ))
            except Exception:
                raise ReviewError("Could not initialize S3. Check the configured SDK profile, region, and credentials.", 502) from None
        self.client = client

    def _key(self, key):
        key = self.prefix + document_key(key)
        try:
            size = len(key.encode("utf-8"))
        except UnicodeError:
            raise ReviewError("S3 object key is not valid UTF-8.") from None
        if size > 1024:
            raise ReviewError("S3 object key exceeds 1024 UTF-8 bytes.")
        return key

    def read(self, key):
        key = self._key(key)
        body = None
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response["Body"]
            length = response.get("ContentLength")
            if isinstance(length, int) and length > self.max_bytes:
                raise ReviewError("S3 document exceeds the configured byte limit.", 413)
            raw = body.read(self.max_bytes + 1)
            if not isinstance(raw, bytes):
                raise ReviewError("S3 returned an invalid document body.", 502)
            if len(raw) > self.max_bytes:
                raise ReviewError("S3 document exceeds the configured byte limit.", 413)
            token = _etag(response.get("ETag"))
        except ReviewError:
            raise
        except Exception as error:
            raise _storage_error(error) from None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    pass
        try:
            document = parse_json(raw.decode("utf-8"))
        except (ReviewError, UnicodeError):
            raise ReviewError("S3 document is not valid UTF-8 JSON.", 502) from None
        if not isinstance(document, dict):
            raise ReviewError("S3 document must contain a JSON object.", 502)
        return document, token

    def create(self, key, document):
        self._write(key, document, {"IfNoneMatch": "*"})

    def compare_and_swap(self, key, document, expected_token):
        self._write(key, document, {"IfMatch": _etag(expected_token)})

    def _write(self, key, document, condition):
        key, raw = self._key(key), document_bytes(document, self.max_bytes)
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=raw,
                                   ContentType="application/json; charset=utf-8", **condition)
        except Exception as error:
            raise _storage_error(error, writing=True) from None
