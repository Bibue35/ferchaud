"""Vercel deployment disabled — app moved to Railway."""
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "https://ferchaud.up.railway.app")
        self.end_headers()

    do_POST = do_GET
    do_PUT = do_GET
    do_DELETE = do_GET
