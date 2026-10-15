"""Tiny fake Redfish BMC (HTTPS, basic auth) for local smoke tests.

Usage: python mock_bmc.py <port> <cert> <key>
Account: mcp-readonly / secret
"""
import base64
import json
import ssl
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT, CERT, KEY = int(sys.argv[1]), sys.argv[2], sys.argv[3]
EXPECTED = "Basic " + base64.b64encode(b"mcp-readonly:secret").decode()

DATA = {
    "/redfish/v1/Systems": {"Members": [{"@odata.id": "/redfish/v1/Systems/System.Embedded.1"}]},
    "/redfish/v1/Systems/System.Embedded.1": {
        "Id": "System.Embedded.1", "Name": "System", "Manufacturer": "Dell Inc.",
        "Model": "PowerEdge R650", "PowerState": "On", "BiosVersion": "1.9.2",
        "Status": {"State": "Enabled", "Health": "Warning", "HealthRollup": "Critical"},
    },
    "/redfish/v1/Managers": {"Members": [{"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1"}]},
    "/redfish/v1/Managers/iDRAC.Embedded.1": {
        "Id": "iDRAC.Embedded.1",
        "LogServices": {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices"},
    },
    "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices": {
        "Members": [{"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel"}]
    },
    "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel": {
        "Id": "Sel", "Name": "System Event Log",
        "Entries": {"@odata.id": "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries"},
    },
    # page 1 (ascending order, like many SELs) with a nextLink to page 2
    "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries": {
        "Members": [
            {"Id": "1", "Created": "2020-01-01T10:00:00+00:00", "Severity": "OK",
             "Message": "Old informational entry", "MessageId": "SYS1000"},
            {"Id": "2", "Created": "2026-10-15T08:00:00+00:00", "Severity": "Warning",
             "Message": "Fan 3 speed below threshold", "MessageId": "FAN0001"},
        ],
        "Members@odata.nextLink": "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries?$skip=2",
    },
    "/redfish/v1/Managers/iDRAC.Embedded.1/LogServices/Sel/Entries?$skip=2": {
        "Members": [
            {"Id": "3", "Created": "2026-10-15T12:30:00Z", "Severity": "Critical",
             "Message": "Power supply 2 failed", "MessageId": "PSU0003"},
        ]
    },
}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.headers.get("Authorization") != EXPECTED:
            self.send_response(401); self.end_headers(); return
        doc = DATA.get(self.path)
        if doc is None:
            self.send_response(404); self.end_headers(); return
        body = json.dumps(doc).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT, KEY)
srv = HTTPServer(("127.0.0.1", PORT), H)
srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
