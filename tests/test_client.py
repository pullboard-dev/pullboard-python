import unittest

from pullboard import PullboardClient, PullboardError, anon_provision, assert_safe_base_url


def make_transport(responses):
    """A fake transport that records calls and replays canned (status, payload) pairs."""
    calls = []

    def transport(method, url, headers, body):
        calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return responses.pop(0)

    transport.calls = calls
    return transport


class ClientTests(unittest.TestCase):
    def test_claim_supplies_defaults_and_generated_request_id(self):
        transport = make_transport([(200, {"leaseId": "lease"})])
        client = PullboardClient(
            "https://pullboard.test/", "secret",
            request_id=lambda: "generated-id", transport=transport,
        )
        client.claim("work")
        call = transport.calls[0]
        self.assertEqual(call["url"], "https://pullboard.test/api/claim")
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["headers"]["authorization"], "Bearer secret")
        self.assertEqual(
            call["body"],
            {"workId": "work", "role": "builder", "ttl": 3600, "requestId": "generated-id"},
        )

    def test_get_item_unwraps_the_envelope_and_encodes_the_id(self):
        transport = make_transport([(200, {"item": {"workId": "a/b"}})])
        client = PullboardClient("https://pullboard.test", "secret", transport=transport)
        self.assertEqual(client.get_item("a/b"), {"workId": "a/b"})
        self.assertEqual(transport.calls[0]["url"], "https://pullboard.test/api/items/a%2Fb")

    def test_comment_posts_only_text_append_only(self):
        transport = make_transport([(200, {"workId": "note-1", "comments": [{"commentId": "c1", "text": "hi"}]})])
        client = PullboardClient(
            "https://pullboard.test", "secret",
            request_id=lambda: "unused", transport=transport,
        )
        result = client.comment("note-1", "hi")
        call = transport.calls[0]
        self.assertEqual(call["url"], "https://pullboard.test/api/items/note-1/comments")
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["body"], {"text": "hi"})  # append-only: text only, no requestId
        self.assertEqual(len(result["comments"]), 1)

    def test_error_response_carries_stable_status_and_code(self):
        transport = make_transport([(409, {"error": "WORK_TAKEN", "message": "held"})])
        client = PullboardClient("https://pullboard.test", "secret", transport=transport)
        with self.assertRaises(PullboardError) as ctx:
            client.claim("work")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.code, "WORK_TAKEN")
        self.assertEqual(str(ctx.exception), "held")

    def test_requires_base_url_and_token(self):
        with self.assertRaises(ValueError):
            PullboardClient("", "secret")
        with self.assertRaises(ValueError):
            PullboardClient("https://pullboard.test", "")

    def test_patch_item_reads_current_version_then_patches(self):
        transport = make_transport([
            (200, {"item": {"updatedAt": "version-7"}}),
            (200, {"item": {"title": "Clearer"}}),
        ])
        client = PullboardClient(
            "https://pullboard.test", "secret",
            request_id=lambda: "patch-id", transport=transport,
        )
        client.patch_item("work/one", {"title": "Clearer"})
        self.assertEqual(transport.calls[0]["url"], "https://pullboard.test/api/items/work%2Fone")
        self.assertEqual(
            transport.calls[1]["body"],
            {"title": "Clearer", "expectedUpdatedAt": "version-7", "requestId": "patch-id"},
        )


class AnonProvisionTests(unittest.TestCase):
    def test_provisions_without_auth_and_returns_token_and_workspace_id(self):
        transport = make_transport([(200, {"token": "tok-1", "workspace": {"workspaceId": "ws-1"}})])
        result = anon_provision(base_url="https://pullboard.test/", label="my-agent", transport=transport)
        call = transport.calls[0]
        self.assertEqual(call["url"], "https://pullboard.test/api/accounts/anon-provision")
        self.assertNotIn("authorization", call["headers"])
        self.assertEqual(call["body"], {"label": "my-agent"})
        self.assertEqual(result, {"token": "tok-1", "workspace_id": "ws-1"})

    def test_raises_stable_metadata_when_refused(self):
        transport = make_transport([(429, {"error": "RATE_LIMITED", "message": "slow down"})])
        with self.assertRaises(PullboardError) as ctx:
            anon_provision(base_url="https://pullboard.test", transport=transport)
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(ctx.exception.code, "RATE_LIMITED")


class BaseUrlDestinationGuardTests(unittest.TestCase):
    """Finding #4 — the bearer token must never be sent to an insecure destination."""

    def _never_called(self, *args, **kwargs):
        raise AssertionError("transport must never run for a rejected base_url")

    def test_client_rejects_http_remote_so_token_is_never_sent_in_cleartext(self):
        with self.assertRaises(ValueError) as ctx:
            PullboardClient("http://evil.example", "secret", transport=self._never_called)
        self.assertIn("refuses to send your bearer token", str(ctx.exception))
        self.assertIn("evil.example", str(ctx.exception))

    def test_client_rejects_non_web_scheme(self):
        with self.assertRaises(ValueError):
            PullboardClient("file:///etc/passwd", "secret", transport=self._never_called)

    def test_client_rejects_malformed_url(self):
        with self.assertRaises(ValueError) as ctx:
            PullboardClient("not a url", "secret", transport=self._never_called)
        self.assertIn("absolute http(s) URL", str(ctx.exception))

    def test_client_allows_https_and_loopback_http(self):
        # https anywhere is fine, and a real request goes through.
        transport = make_transport([(200, {"leaseId": "lease"})])
        PullboardClient("https://pullboard.dev", "secret", transport=transport).claim("work")
        self.assertEqual(transport.calls[0]["url"], "https://pullboard.dev/api/claim")
        # http is tolerated only for loopback dev hosts.
        for dev in ("http://localhost:8787", "http://127.0.0.1:8787", "http://[::1]:8787"):
            local = make_transport([(200, {"leaseId": "lease"})])
            PullboardClient(dev, "secret", transport=local).claim("work")
            self.assertEqual(local.calls[0]["url"], "{}/api/claim".format(dev))

    def test_anon_provision_applies_the_same_guard_and_never_calls_transport(self):
        with self.assertRaises(ValueError) as ctx:
            anon_provision(base_url="http://evil.example", transport=self._never_called)
        self.assertIn("refuses to send your bearer token", str(ctx.exception))
        # https onboarding still works.
        ok = make_transport([(200, {"token": "tok-1", "workspace": {"workspaceId": "ws-1"}})])
        self.assertEqual(
            anon_provision(base_url="https://pullboard.dev", transport=ok),
            {"token": "tok-1", "workspace_id": "ws-1"},
        )

    def test_assert_safe_base_url_returns_the_url_when_safe(self):
        self.assertEqual(assert_safe_base_url("https://pullboard.dev"), "https://pullboard.dev")


if __name__ == "__main__":
    unittest.main()
