"""
Serve the app so you can open it on this computer and on your phone
(same Wi-Fi):

    python serve.py

Only the app/ folder is served (the page and its data files), plus the daily
price files under /daily/ for the charts; nothing else from the project.
"""
import functools
import http.server
import socket
from pathlib import Path

PORT = 8000
APP_DIR = Path(__file__).parent / "app"
DAILY_DIR = Path(__file__).parent / "data" / "daily"


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def translate_path(self, path):
        # /daily/OGDC.csv -> data/daily/OGDC.csv (online, the workflow copies them)
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean.startswith("/daily/"):
            name = Path(clean).name
            if name.endswith(".csv") and (DAILY_DIR / name).is_file():
                return str(DAILY_DIR / name)
        return super().translate_path(path)

    def log_message(self, *args):
        pass


def main():
    handler = functools.partial(QuietHandler, directory=str(APP_DIR))
    with http.server.ThreadingHTTPServer(("0.0.0.0", PORT), handler) as server:
        print(f"KMI Scanner running.\n  On this computer: http://localhost:{PORT}\n"
              f"  On your phone (same Wi-Fi): http://{lan_ip()}:{PORT}\n"
              "Press Ctrl+C to stop.")
        server.serve_forever()


if __name__ == "__main__":
    main()
