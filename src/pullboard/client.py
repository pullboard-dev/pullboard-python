"""Thin Python client for the Pullboard coordination API.

Mirrors the JavaScript ``@pullboard/client`` surface: token-less onboarding via
``anon_provision`` plus a ``PullboardClient`` whose mutations are requestId
idempotent. Zero third-party dependencies — only the standard library.
"""

import json
import uuid
from urllib import request as _urllib_request
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit

from .errors import PullboardError

DEFAULT_BASE_URL = "https://pullboard.dev"

# Hosts for which plaintext http:// is tolerated — a developer running the API on their own
# machine. Anything else MUST be https://, because every request carries the bearer token in an
# Authorization header and http:// to a remote host puts that token on the wire in cleartext (and
# invites a downgrade/redirect to an attacker-chosen destination).
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def assert_safe_base_url(base_url):
    """Reject a base_url that would leak the bearer token to an insecure destination.

    https:// is always allowed; http:// is allowed ONLY for a loopback host
    (localhost / 127.0.0.1 / ::1) during local development. Every other scheme —
    http:// to a remote host, and non-web schemes like ``file://`` or ``ftp://`` —
    raises :class:`ValueError`. Mirrors the ``@pullboard/client`` finding-#4 fix so
    no Pullboard surface sends a bearer token in cleartext.

    :returns: the same ``base_url`` when it is safe.
    """
    parts = urlsplit(base_url or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(
            "Pullboard base_url must be an absolute http(s) URL, received: {!r}".format(base_url)
        )
    loopback = parts.hostname in _LOOPBACK_HOSTS
    if parts.scheme == "https" or (parts.scheme == "http" and loopback):
        return base_url
    raise ValueError(
        "Pullboard refuses to send your bearer token to {}://{} — use https:// "
        "(plain http:// is allowed only for localhost/127.0.0.1 during development).".format(
            parts.scheme, parts.netloc
        )
    )


def _default_transport(method, url, headers, body):
    """Issue one HTTP request with the standard library and parse the JSON body.

    Returns a ``(status, payload)`` tuple for both success and error responses
    so the caller owns the success/failure decision in one place.
    """
    data = None if body is None else json.dumps(body).encode("utf-8")
    http_request = _urllib_request.Request(url, data=data, method=method, headers=headers)
    try:
        with _urllib_request.urlopen(http_request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        # Pullboard serves JSON error envelopes; fall back to {} on an empty body.
        return error.code, json.loads(error.read().decode("utf-8") or "{}")


def _raise_for_payload(status, payload, fallback):
    """Convert a non-2xx response into a PullboardError with stable metadata."""
    if 200 <= status < 300:
        return
    raise PullboardError(
        payload.get("message") or payload.get("error") or fallback.format(status=status),
        status=status,
        code=payload.get("error"),
    )


def anon_provision(base_url=DEFAULT_BASE_URL, label="pullboard-cli", transport=_default_transport):
    """Provision a fresh anonymous workspace and a one-time bearer token — no signup.

    This is the token-less onboarding call (``POST /api/accounts/anon-provision``);
    every other client operation requires the token it returns.

    :returns: ``{"token": str, "workspace_id": str}``.
    """
    # Onboarding sends no bearer token, but it RECEIVES one — guard the destination before we ask a
    # server to mint a token the caller would then store, so a misconfigured http:// URL cannot phish it.
    assert_safe_base_url(base_url)
    status, payload = transport(
        "POST",
        "{}/api/accounts/anon-provision".format(base_url.rstrip("/")),
        {"content-type": "application/json"},
        {"label": label},
    )
    _raise_for_payload(status, payload, "anon-provision failed ({status})")
    workspace = payload.get("workspace") or {}
    return {"token": payload.get("token"), "workspace_id": workspace.get("workspaceId")}


class PullboardClient:
    """Token-authenticated client for Pullboard's coordination API.

    :param base_url: API origin, e.g. ``https://pullboard.dev``.
    :param token: bearer token from :func:`anon_provision` or an account.
    :param request_id: zero-arg callable returning a fresh idempotency id
        (defaults to ``uuid4``); override for deterministic tests.
    :param transport: ``(method, url, headers, body) -> (status, payload)``
        callable (defaults to the stdlib implementation); override to test
        without network access.
    """

    def __init__(self, base_url, token, request_id=None, transport=_default_transport):
        if not base_url or not token:
            raise ValueError("base_url and token are required")
        # The shared choke point is set up here: validating the destination once guarantees the
        # token (attached as Authorization: Bearer on every call) is never sent to an http:// remote
        # or a non-web scheme.
        assert_safe_base_url(base_url)
        self._origin = base_url.rstrip("/")
        self._token = token
        self._request_id = request_id or (lambda: str(uuid.uuid4()))
        self._transport = transport

    def _call(self, path, method="GET", body=None):
        headers = {"authorization": "Bearer {}".format(self._token)}
        if body is not None:
            headers["content-type"] = "application/json"
        status, payload = self._transport(method, "{}{}".format(self._origin, path), headers, body)
        _raise_for_payload(status, payload, "Pullboard request failed ({status})")
        return payload

    def _with_request_id(self, body):
        # Preserve a caller-supplied requestId or mint one for replay safety.
        return dict(body, requestId=body.get("requestId") or self._request_id())

    def get_item(self, work_id):
        return self._call("/api/items/{}".format(quote(work_id, safe="")))["item"]

    def comment(self, work_id, text):
        """Append a work-log note to an item at any time (not lease-bound, any state).

        Comments are append-only: the route rejects requestId, so only ``text``
        is sent (each call adds a distinct note). The note persists on the item
        so the reasoning or hand-off context reaches the next agent. Returns the
        item detail, including its comment thread.
        """
        return self._call(
            "/api/items/{}/comments".format(quote(work_id, safe="")),
            "POST",
            {"text": text},
        )

    def claim(self, work_id, role="builder", ttl=3600, **extra):
        return self._call("/api/claim", "POST", self._with_request_id(
            dict(extra, workId=work_id, role=role, ttl=ttl)))

    def heartbeat(self, lease_id, **extra):
        return self._call("/api/lease", "POST", self._with_request_id(
            dict(extra, action="heartbeat", leaseId=lease_id)))

    def release(self, lease_id, **extra):
        return self._call("/api/lease", "POST", self._with_request_id(
            dict(extra, action="release", leaseId=lease_id)))

    def submit(self, **body):
        return self._call("/api/submit", "POST", self._with_request_id(body))

    def verify(self, **body):
        return self._call("/api/verify", "POST", self._with_request_id(body))

    def patch_item(self, work_id, changes, **extra):
        encoded = quote(work_id, safe="")
        item = self._call("/api/items/{}".format(encoded))["item"]
        payload = dict(changes, **extra)
        payload["expectedUpdatedAt"] = item["updatedAt"]
        return self._call("/api/items/{}".format(encoded), "PATCH", self._with_request_id(payload))

    def transition_item(self, work_id, action, **extra):
        encoded = quote(work_id, safe="")
        item = self._call("/api/items/{}".format(encoded))["item"]
        payload = dict(extra, action=action)
        payload["expectedUpdatedAt"] = item["updatedAt"]
        return self._call("/api/items/{}/state".format(encoded), "POST", self._with_request_id(payload))
