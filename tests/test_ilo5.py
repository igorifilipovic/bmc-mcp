"""Regression tests for HPE iLO 5 Redfish compatibility."""
import unittest

import httpx

from bmc_mcp.config import BmcHost
from bmc_mcp.redfish import Redfish, _normalize, severity_rank


class Ilo5CompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.host = BmcHost(
            name="ilo5",
            vendor="ilo",
            address="ilo5.example.test",
            username="mcp-readonly",
            password="secret",
        )

    def test_caution_is_warning_level(self):
        self.assertEqual(severity_rank("Caution"), severity_rank("Warning"))

    def test_hpe_oem_fields_are_preserved(self):
        event = {
            "Id": "42",
            "Created": "2026-10-15T12:30:00Z",
            "Message": "Drive status changed",
            "Oem": {
                "Hpe": {
                    "Severity": "Caution",
                    "RecommendedAction": "Check the affected drive.",
                    "Categories": ["Hardware", "Storage"],
                    "Repaired": False,
                    "Count": 2,
                    "LearnMoreLink": "https://example.test/help/42",
                }
            },
        }
        svc = {"id": "IML", "scope": "Systems/1"}

        normalized = _normalize(event, svc)

        self.assertEqual(normalized["severity"], "Caution")
        self.assertEqual(normalized["hpe_severity"], "Caution")
        self.assertEqual(
            normalized["recommended_action"], "Check the affected drive."
        )
        self.assertEqual(normalized["categories"], ["Hardware", "Storage"])
        self.assertFalse(normalized["repaired"])
        self.assertEqual(normalized["count"], 2)
        self.assertEqual(normalized["learn_more"], "https://example.test/help/42")

    async def test_redirect_is_followed_on_configured_bmc(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.path == "/redfish/v1/Systems":
                return httpx.Response(
                    308,
                    headers={
                        "Location": "https://other-host.invalid/redfish/v1/Systems/"
                    },
                    request=request,
                )
            if request.url.path == "/redfish/v1/Systems/":
                return httpx.Response(
                    200,
                    json={"Members": []},
                    request=request,
                )
            return httpx.Response(404, request=request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            base_url="https://ilo5.example.test",
            transport=transport,
            follow_redirects=False,
        ) as client:
            rf = Redfish(client, self.host)
            members = await rf._members("/redfish/v1/Systems")

        self.assertEqual(members, [])
        self.assertEqual(
            requests,
            [
                "https://ilo5.example.test/redfish/v1/Systems",
                "https://ilo5.example.test/redfish/v1/Systems/",
            ],
        )

    async def test_remote_protocol_error_is_retried_once(self):
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.RemoteProtocolError(
                    "Server disconnected without sending a response.",
                    request=request,
                )
            return httpx.Response(
                200,
                json={"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]},
                request=request,
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(
            base_url="https://ilo5.example.test",
            transport=transport,
            follow_redirects=False,
        ) as client:
            rf = Redfish(client, self.host)
            members = await rf._members("/redfish/v1/Systems")

        self.assertEqual(members, ["/redfish/v1/Systems/1"])
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
