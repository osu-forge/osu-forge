"""Who the server answers, and who it does not.

These are the checks that matter most in this package. A local server is
reachable by every page the user visits, so the tests that assert it refuses
them are the ones standing between a replay analyser and a way to read someone's
game from a web page they happened to open.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from osuforge.server.app import build_app
from osuforge.server.security import Access, SessionToken

PORT = 24080
TOKEN = "test-token-value"
PAGE = "<!doctype html><title>t</title><script>const T='__TOKEN__';</script>"


class _Payload:
    def __init__(self) -> None:
        self.header: dict[str, Any] = {"schema_version": 1, "replay": "a.osr"}
        self.frames = b"\x01\x02\x03\x04"


@pytest.fixture
def access() -> Access:
    return Access(port=PORT, token=SessionToken(TOKEN))


@pytest.fixture
def client(access: Access) -> TestClient:
    app = build_app(access, page=PAGE, payloads={"a.osr": _Payload()})  # type: ignore[dict-item]
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")


def auth(**extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", **extra}


class TestHostChecking:
    def test_the_loopback_host_is_served(self, client: TestClient) -> None:
        assert client.get("/").status_code == 200

    def test_a_rebound_name_is_refused(self, client: TestClient) -> None:
        # The attack the loopback bind does not stop: a name the attacker owns,
        # pointed at 127.0.0.1, so the browser connects on their behalf. They
        # cannot forge the Host header, which is why this check is the one that
        # actually works.
        response = client.get("/", headers={"Host": "evil.example.com"})
        assert response.status_code == 403

    def test_a_rebound_name_is_refused_before_the_token_is_considered(
        self, client: TestClient
    ) -> None:
        # Order matters. A rebound request must not learn whether its token was
        # right, or wrong, or missing.
        response = client.get("/api/replays", headers={"Host": "evil.example.com"})
        assert response.status_code == 403
        assert "unauthorised" not in response.text


class TestOriginChecking:
    def test_a_website_cannot_call_the_api(self, client: TestClient) -> None:
        response = client.get("/api/replays", headers=auth(**{"Origin": "https://example.com"}))
        assert response.status_code == 403

    def test_our_own_page_can(self, client: TestClient) -> None:
        response = client.get(
            "/api/replays", headers=auth(**{"Origin": f"http://127.0.0.1:{PORT}"})
        )
        assert response.status_code == 200

    def test_the_response_never_permits_another_origin_to_read_it(self, client: TestClient) -> None:
        # The header tosu sets to `*`. Its only function is to let other origins
        # read the body, which is precisely what is being prevented.
        response = client.get("/api/replays", headers=auth())
        assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


class TestToken:
    def test_the_api_needs_one(self, client: TestClient) -> None:
        assert client.get("/api/replays").status_code == 401

    def test_a_wrong_one_is_refused(self, client: TestClient) -> None:
        response = client.get("/api/replays", headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401

    def test_the_page_itself_does_not(self, client: TestClient) -> None:
        # A browser navigating to a URL cannot send a header, so this has to be
        # reachable without one. It is safe because a cross-site page can cause
        # the navigation but cannot read the response.
        assert client.get("/").status_code == 200

    def test_the_page_carries_the_token_for_its_own_use(self, client: TestClient) -> None:
        assert TOKEN in client.get("/").text
        assert "__TOKEN__" not in client.get("/").text


class TestData:
    def test_a_header_comes_back_as_json(self, client: TestClient) -> None:
        response = client.get("/api/replays/a.osr/header", headers=auth())
        assert response.status_code == 200
        assert response.json()["replay"] == "a.osr"

    def test_frames_come_back_as_bytes(self, client: TestClient) -> None:
        response = client.get("/api/replays/a.osr/frames", headers=auth())
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.content == b"\x01\x02\x03\x04"

    def test_an_unknown_replay_is_a_404_not_a_guess(self, client: TestClient) -> None:
        assert client.get("/api/replays/nope.osr/header", headers=auth()).status_code == 404

    def test_a_path_cannot_reach_outside_what_was_registered(self, client: TestClient) -> None:
        # Names are keys in a dictionary the server was handed, never paths it
        # resolves, so there is nothing for traversal to traverse.
        for attempt in ("..%2f..%2fsecret", "....//secret"):
            assert client.get(f"/api/replays/{attempt}/header", headers=auth()).status_code == 404


class TestBackground:
    def build(self, access: Access, payload: Any) -> TestClient:
        app = build_app(access, page=PAGE, payloads={"a.osr": payload})
        return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")

    def test_a_background_comes_back_with_its_kind(self, access: Access, tmp_path: Any) -> None:
        image = tmp_path / "bg.jpg"
        image.write_bytes(b"\xff\xd8\xff\xdbjpeg-ish")
        payload = _Payload()
        payload.background = image
        response = self.build(access, payload).get("/api/replays/a.osr/background", headers=auth())
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content.startswith(b"\xff\xd8")

    def test_a_map_without_one_is_a_404(self, access: Access) -> None:
        client = self.build(access, _Payload())
        assert client.get("/api/replays/a.osr/background", headers=auth()).status_code == 404

    def test_a_file_deleted_since_startup_is_a_404_not_a_crash(
        self, access: Access, tmp_path: Any
    ) -> None:
        payload = _Payload()
        payload.background = tmp_path / "gone.png"
        client = self.build(access, payload)
        assert client.get("/api/replays/a.osr/background", headers=auth()).status_code == 404

    def test_it_needs_the_token_like_everything_under_api(
        self, access: Access, tmp_path: Any
    ) -> None:
        image = tmp_path / "bg.png"
        image.write_bytes(b"png")
        payload = _Payload()
        payload.background = image
        assert self.build(access, payload).get("/api/replays/a.osr/background").status_code == 401


class TestAudio:
    def build(self, access: Access, payload: Any) -> TestClient:
        app = build_app(access, page=PAGE, payloads={"a.osr": payload})
        return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")

    def test_a_song_comes_back_with_its_kind(self, access: Access, tmp_path: Any) -> None:
        # The kind is what decides whether the element will play it, so it is
        # mapped from the suffix rather than guessed from the bytes.
        for name, expected in (("a.mp3", "audio/mpeg"), ("a.ogg", "audio/ogg")):
            song = tmp_path / name
            song.write_bytes(b"not really encoded")
            payload = _Payload()
            payload.audio = song
            response = self.build(access, payload).get("/api/replays/a.osr/audio", headers=auth())
            assert response.status_code == 200
            assert response.headers["content-type"] == expected
            assert response.content == b"not really encoded"

    def test_a_map_without_one_is_a_404(self, access: Access) -> None:
        client = self.build(access, _Payload())
        assert client.get("/api/replays/a.osr/audio", headers=auth()).status_code == 404

    def test_a_file_deleted_since_startup_is_a_404_not_a_crash(
        self, access: Access, tmp_path: Any
    ) -> None:
        payload = _Payload()
        payload.audio = tmp_path / "gone.mp3"
        client = self.build(access, payload)
        assert client.get("/api/replays/a.osr/audio", headers=auth()).status_code == 404

    def test_it_needs_the_token_like_everything_under_api(
        self, access: Access, tmp_path: Any
    ) -> None:
        song = tmp_path / "a.mp3"
        song.write_bytes(b"mp3")
        payload = _Payload()
        payload.audio = song
        assert self.build(access, payload).get("/api/replays/a.osr/audio").status_code == 401


class TestCorpus:
    def build(self, access: Access, corpus: Any) -> TestClient:
        app = build_app(
            access,
            page=PAGE,
            payloads={"a.osr": _Payload()},
            corpus=corpus,  # type: ignore[dict-item]
        )
        return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")

    def test_the_answer_is_served_when_there_is_one(self, access: Access) -> None:
        client = self.build(access, lambda: {"replays": 12, "insufficient": None})
        response = client.get("/api/corpus", headers=auth())
        assert response.status_code == 200
        assert response.json()["replays"] == 12

    def test_no_provider_is_a_404_not_an_empty_corpus(self, access: Access) -> None:
        # An empty object would read as "a corpus with nothing in it", which is
        # a conclusion. No answer existing is a different statement.
        client = self.build(access, None)
        assert client.get("/api/corpus", headers=auth()).status_code == 404

    def test_a_provider_with_no_answer_yet_is_also_a_404(self, access: Access) -> None:
        client = self.build(access, lambda: None)
        assert client.get("/api/corpus", headers=auth()).status_code == 404

    def test_the_corpus_needs_the_token_like_everything_under_api(self, access: Access) -> None:
        client = self.build(access, lambda: {"replays": 12})
        assert client.get("/api/corpus").status_code == 401


class TestWebSocket:
    def protocols(self, token: str = TOKEN) -> list[str]:
        return [f"osu-forge-token.{token}"]

    def headers(self, **extra: str) -> dict[str, str]:
        # The test client sends `Host: testserver` on a WebSocket handshake
        # whatever its base URL says, and the guard correctly refuses that. A
        # realistic Host is supplied so these tests exercise the token and origin
        # checks rather than stopping at the first one.
        return {"Host": f"127.0.0.1:{PORT}", **extra}

    def test_a_socket_opens_with_the_right_token(self, client: TestClient) -> None:
        with client.websocket_connect(
            "/ws", subprotocols=self.protocols(), headers=self.headers()
        ) as socket:
            socket.send_json({"replay": "a.osr"})
            assert socket.receive_json()["header"]["replay"] == "a.osr"
            assert socket.receive_bytes() == b"\x01\x02\x03\x04"

    def test_a_socket_with_the_wrong_host_is_closed(self, client: TestClient) -> None:
        from starlette.websockets import WebSocketDisconnect

        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                "/ws",
                subprotocols=self.protocols(),
                headers={"Host": "evil.example.com"},
            ) as socket,
        ):
            socket.receive_json()

    def test_a_socket_without_a_token_is_closed(self, client: TestClient) -> None:
        from starlette.websockets import WebSocketDisconnect

        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("/ws", headers=self.headers()) as socket,
        ):
            socket.receive_json()

    def test_a_socket_from_a_website_is_closed(self, client: TestClient) -> None:
        from starlette.websockets import WebSocketDisconnect

        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(
                "/ws",
                subprotocols=self.protocols(),
                headers=self.headers(Origin="https://example.com"),
            ) as socket,
        ):
            socket.receive_json()

    def test_an_unknown_replay_is_reported_rather_than_closing_the_socket(
        self, client: TestClient
    ) -> None:
        with client.websocket_connect(
            "/ws", subprotocols=self.protocols(), headers=self.headers()
        ) as socket:
            socket.send_json({"replay": "nope.osr"})
            assert socket.receive_json()["error"] == "no such replay"
            # Still usable afterwards.
            socket.send_json({"replay": "a.osr"})
            assert "header" in socket.receive_json()


class TestHeaders:
    def test_responses_say_not_to_sniff_or_cache(self, client: TestClient) -> None:
        response = client.get("/api/replays", headers=auth())
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"

    def test_there_is_no_api_documentation_surface(self, client: TestClient) -> None:
        # An interactive docs page on a local server is another origin's way in
        # and another thing to keep correct.
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path, headers=auth()).status_code == 404
