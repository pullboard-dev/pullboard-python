import unittest

from pullboard import PullboardClient, PullboardError, anon_provision


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
            "http://pullboard.test/", "secret",
            request_id=lambda: "generated-id", transport=transport,
        )
        client.claim("work")
        call = transport.calls[0]
        self.assertEqual(call["url"], "http://pullboard.test/api/claim")
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["headers"]["authorization"], "Bearer secret")
        self.assertEqual(
            call["body"],
            {"workId": "work", "role": "builder", "ttl": 3600, "requestId": "generated-id"},
        )

    def test_get_item_unwraps_the_envelope_and_encodes_the_id(self):
        transport = make_transport([(200, {"item": {"workId": "a/b"}})])
        client = PullboardClient("http://pullboard.test", "secret", transport=transport)
        self.assertEqual(client.get_item("a/b"), {"workId": "a/b"})
        self.assertEqual(transport.calls[0]["url"], "http://pullboard.test/api/items/a%2Fb")

    def test_error_response_carries_stable_status_and_code(self):
        transport = make_transport([(409, {"error": "WORK_TAKEN", "message": "held"})])
        client = PullboardClient("http://pullboard.test", "secret", transport=transport)
        with self.assertRaises(PullboardError) as ctx:
            client.claim("work")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.code, "WORK_TAKEN")
        self.assertEqual(str(ctx.exception), "held")

    def test_requires_base_url_and_token(self):
        with self.assertRaises(ValueError):
            PullboardClient("", "secret")
        with self.assertRaises(ValueError):
            PullboardClient("http://pullboard.test", "")

    def test_patch_item_reads_current_version_then_patches(self):
        transport = make_transport([
            (200, {"item": {"updatedAt": "version-7"}}),
            (200, {"item": {"title": "Clearer"}}),
        ])
        client = PullboardClient(
            "http://pullboard.test", "secret",
            request_id=lambda: "patch-id", transport=transport,
        )
        client.patch_item("work/one", {"title": "Clearer"})
        self.assertEqual(transport.calls[0]["url"], "http://pullboard.test/api/items/work%2Fone")
        self.assertEqual(
            transport.calls[1]["body"],
            {"title": "Clearer", "expectedUpdatedAt": "version-7", "requestId": "patch-id"},
        )


class AnonProvisionTests(unittest.TestCase):
    def test_provisions_without_auth_and_returns_token_and_workspace_id(self):
        transport = make_transport([(200, {"token": "tok-1", "workspace": {"workspaceId": "ws-1"}})])
        result = anon_provision(base_url="http://pullboard.test/", label="my-agent", transport=transport)
        call = transport.calls[0]
        self.assertEqual(call["url"], "http://pullboard.test/api/accounts/anon-provision")
        self.assertNotIn("authorization", call["headers"])
        self.assertEqual(call["body"], {"label": "my-agent"})
        self.assertEqual(result, {"token": "tok-1", "workspace_id": "ws-1"})

    def test_raises_stable_metadata_when_refused(self):
        transport = make_transport([(429, {"error": "RATE_LIMITED", "message": "slow down"})])
        with self.assertRaises(PullboardError) as ctx:
            anon_provision(base_url="http://pullboard.test", transport=transport)
        self.assertEqual(ctx.exception.status, 429)
        self.assertEqual(ctx.exception.code, "RATE_LIMITED")


if __name__ == "__main__":
    unittest.main()
