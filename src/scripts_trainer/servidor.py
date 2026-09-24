import http.server
import os
import pathlib
import socketserver
import threading
import time
import webbrowser

HOST = "127.0.0.1"
PORT = 8000
TIMEOUT = 6.0

last_heartbeat = time.monotonic()
lock = threading.Lock()
closing = False


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args) -> None:
        pass

    def do_POST(self) -> None:
        global last_heartbeat, closing
        if self.path == "/__heartbeat":
            with lock:
                last_heartbeat = time.monotonic()
            self.send_response(204)
            self.end_headers()
            return

        if self.path == "/__close":
            with lock:
                last_heartbeat = time.monotonic() - TIMEOUT - 1
            self.send_response(204)
            self.end_headers()
            return

        self.send_error(404)

    def do_GET(self) -> None:
        if self.path.startswith("/__"):
            self.send_error(404)
            return
        super().do_GET()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


os.chdir(pathlib.Path(pathlib.Path(__file__).resolve()).parent)

server = Server((HOST, PORT), Handler)


def watchdog() -> None:
    global closing
    while not closing:
        time.sleep(1)
        with lock:
            stale = time.monotonic() - last_heartbeat > TIMEOUT
        if stale:
            closing = True
            threading.Thread(target=server.shutdown, daemon=True).start()
            return


threading.Thread(target=watchdog, daemon=True).start()

# Abrir después de que el socket ya esté escuchando.
webbrowser.open_new_tab(f"http://{HOST}:{PORT}/index.html")

try:
    server.serve_forever()
finally:
    closing = True
    server.server_close()
