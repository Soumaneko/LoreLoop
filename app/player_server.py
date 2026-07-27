from __future__ import annotations

import hmac
import html
from http import HTTPStatus
from urllib.parse import urlparse

from app.debug_server import DebugHandler, PLAYER_STATIC_DIR


TOKEN_META = '<meta name="loreloop-session-token" content="" />'


class PlayerHandler(DebugHandler):
    """Desktop HTTP surface: player assets and authenticated player APIs only."""

    session_token = ""

    def do_GET(self) -> None:
        if not self._valid_host():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        path = urlparse(self.path).path
        if path == "/":
            self._player_index()
            return
        if path.startswith("/assets/"):
            super().do_GET()
            return
        if path.startswith("/api/player/"):
            if not self._valid_token():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            super().do_GET()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._valid_host():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        path = urlparse(self.path).path
        if not path.startswith("/api/player/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not self._valid_token():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        super().do_POST()

    def log_message(self, format: str, *args) -> None:
        return

    def _valid_host(self) -> bool:
        address = self.server.server_address
        expected = f"127.0.0.1:{address[1]}"
        return hmac.compare_digest(self.headers.get("Host", ""), expected)

    def _valid_token(self) -> bool:
        supplied = self.headers.get("X-LoreLoop-Token", "")
        return bool(self.session_token) and hmac.compare_digest(
            supplied,
            self.session_token,
        )

    def _player_index(self) -> None:
        template = (PLAYER_STATIC_DIR / "index.html").read_text(encoding="utf-8")
        if TOKEN_META not in template:
            raise RuntimeError("player index is missing the desktop session token marker")
        token_meta = (
            '<meta name="loreloop-session-token" content="'
            + html.escape(self.session_token, quote=True)
            + '" />'
        )
        payload = template.replace(TOKEN_META, token_meta, 1).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def make_player_handler(session_token: str) -> type[PlayerHandler]:
    clean_token = str(session_token).strip()
    if not clean_token:
        raise ValueError("desktop session token cannot be empty")

    class ConfiguredPlayerHandler(PlayerHandler):
        session_token = clean_token

    return ConfiguredPlayerHandler
