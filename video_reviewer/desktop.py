"""Native desktop entrypoint used by the packaged Scout macOS app."""
from __future__ import annotations

import socket
import threading
from pathlib import Path

from video_reviewer.gui import _choose_port, create_app


def _wait_for_server(host: str, port: int, server, thread: threading.Thread) -> None:
    for _ in range(50):
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return
        except OSError:
            thread.join(0.1)
            if not thread.is_alive():
                raise RuntimeError("Scout stopped before its desktop window could open.")
    server.should_exit = True
    thread.join(1)
    raise RuntimeError("Scout did not start within 5 seconds.")


def launch_desktop(manifest_path: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run Scout in a native WebView window instead of opening a browser tab."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Scout desktop mode can only bind to localhost.")

    import uvicorn
    import webview

    selected_port = _choose_port(host, port)
    app = create_app(manifest_path)
    config = uvicorn.Config(app, host=host, port=selected_port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="scout-server", daemon=True)
    thread.start()
    _wait_for_server(host, selected_port, server, thread)

    window = webview.create_window(
        "🐾 Scout",
        f"http://{host}:{selected_port}/",
        width=1440,
        height=960,
        min_size=(960, 680),
    )
    try:
        webview.start(debug=False)
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def main() -> int:
    try:
        launch_desktop(Path.home() / ".video-renamer" / "manifest.csv")
    except Exception as exc:  # noqa: BLE001 - show a useful desktop error
        print(f"Scout could not start: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
