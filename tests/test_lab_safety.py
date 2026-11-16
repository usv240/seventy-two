"""The Lab's safety on a visitor's link, and a clock for one day of a window of several days."""
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import api  # noqa: E402
import ingest  # noqa: E402
import rules  # noqa: E402


def test_a_link_that_connects_to_a_private_address_is_refused():
    """Even if the name was public when it was checked, the connected address is checked again."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"internal")

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        assert ingest.fetch(url) == "internal"                       # the board's own sources are not restricted
        with pytest.raises(Exception, match="not a public web page"):
            ingest.fetch(url, check=lambda u: None)                  # a check that passed is not enough
    finally:
        srv.shutdown()


def test_public_and_private_addresses():
    assert ingest.public_ip("93.184.216.34") and not ingest.public_ip("10.0.0.5")
    assert not ingest.public_ip("169.254.169.254") and not ingest.public_ip("::1")


def test_a_clock_for_one_day_of_a_three_week_window():
    w = {"start_utc": "2025-01-17T05:00:00Z", "end_utc": "2025-02-10T04:59:00Z", "timezone": "America/New_York"}
    first, last = api.day_within(w, "2025-02-09")
    assert rules.iso(first) == "2025-02-09T05:00:00Z" and rules.iso(last) == "2025-02-10T04:59:00Z"
    hep = rules.for_domain("hepatitis_a")
    d = hep.deadlines(first, last)
    assert rules.iso(d["hepa_vaccine_deadline"]) == "2025-02-23T05:00:00Z"   # 2 weeks from that day, not from Jan 17
    with pytest.raises(ValueError):
        api.day_within(w, "2025-03-01")
