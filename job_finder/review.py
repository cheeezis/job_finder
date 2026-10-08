"""Start the review: the FastAPI app (job_finder/review_app.py) on uvicorn.

Locally it opens the browser on loopback; in Azure the container runs the
same command with --host 0.0.0.0 behind the Entra sign-in.
"""

import argparse
import errno
import os
import socket
import threading
import time
import webbrowser

import uvicorn

from job_finder.persistence.database import transaction
from job_finder.review_app import create_app
from job_finder.telemetry import start_tracing

# Set when deployed behind a real hostname (e.g. Azure Container Apps): the
# app then allows exactly this hostname over HTTPS instead of localhost.
DEPLOYED_HOST_ENV = "JOBFINDER_REVIEW_HOST"


def parse_args():
    """Parse local server options."""
    parser = argparse.ArgumentParser(prog="job-finder-review", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def address_is_in_use(error):
    """Recognize the cross-platform error for an already running server."""
    return error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == 10048


def bind_exclusively(host, port):
    """Bind the port so that a second review cannot share it, especially on Windows.

    Windows lets a second process bind a reused address unless the socket
    asks for exclusive use; then two reviews would answer at random.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Windows only; getattr instead of hasattr, so type checks pass on every platform.
    option = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
    if option is not None:
        sock.setsockopt(socket.SOL_SOCKET, option, 1)
    try:
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    return sock


def main():
    """Start the review on the configured address, or point the browser to the one already running."""
    args = parse_args()
    url = f"http://{args.host}:{args.port}"
    try:
        sock = bind_exclusively(args.host, args.port)
    except OSError as error:
        if not address_is_in_use(error):
            raise
        print(f"Job Finder läuft bereits unter {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    print(f"Job Finder geoeffnet unter {url}")
    print("Dieses Fenster schliessen, um den Job Finder zu beenden.")
    app = create_app(deployed_host=os.environ.get(DEPLOYED_HOST_ENV, ""))
    tracing = start_tracing(service="jobfinder-review")
    # Signing in to the database first takes seconds after a cold start; do it while uvicorn starts.
    threading.Thread(target=warm_up_database, daemon=True).start()
    try:
        uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False)).run(sockets=[sock])
    finally:
        if tracing is not None:
            # Scaling to zero ends the process: send the remaining spans first.
            tracing.shutdown()


def warm_up_database():
    """Open one database connection right away, so the first request finds the sign-in done."""
    started = time.monotonic()
    try:
        with transaction() as connection:
            connection.execute("SELECT 1")
    except Exception as error:
        # The requests report a real database problem themselves.
        print(f"Datenbank-Vorbereitung fehlgeschlagen: {type(error).__name__}")
        return
    print(f"Datenbankanmeldung vorbereitet in {time.monotonic() - started:.1f} s")


if __name__ == "__main__":
    main()
