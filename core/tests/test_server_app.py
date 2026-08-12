"""Who the server answers, and who it does not.

These are the checks that matter most in this package. A local server is
reachable by every page the user visits, so the tests that assert it refuses
them are the ones standing between a replay analyser and a way to read someone's
game from a web page they happened to open.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from osuforge.server.app import build_app
from osuforge.server.assets import load_site
from osuforge.server.security import Access, SessionToken

BUILT_SITE = Path(__file__).resolve().parents[2] / "web" / "dist"

PORT = 24080
TOKEN = "test-token-value"
PAGE = "<!doctype html><title>t</title><script>const T='__TOKEN__';</script>"
SECOND = "<!doctype html><title>ping</title><script>const P='__TOKEN__';</script>"

# What `load_site` produces for a directory-format build of two pages: the
# second one keyed by both forms of its URL, because a browser following a link
# written `/ping` may ask for either.
PAGES = {"/": PAGE, "/ping/": SECOND, "/ping": SECOND}


class _Payload:
    def __init__(self) -> None:
        self.header: dict[str, Any] = {"schema_version": 1, "replay": "a.osr"}
        self.frames = b"\x01\x02\x03\x04"


@pytest.fixture
def access() -> Access:
    return Access(port=PORT, token=SessionToken(TOKEN))


@pytest.fixture
def client(access: Access) -> TestClient:
    app = build_app(access, pages=PAGES, payloads={"a.osr": _Payload()})  # type: ignore[dict-item]
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


class TestPages:
    """More than one page, each substituted and each reachable by navigation."""

    def test_a_second_page_is_served_with_its_token_and_no_header(self, client: TestClient) -> None:
        # No Authorization, because a browser navigating to a URL cannot attach
        # one — the same reason `/` is reachable without it.
        response = client.get("/ping/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert TOKEN in response.text
        assert "__TOKEN__" not in response.text, (
            "an unsubstituted page 401s on every call it makes, and says nothing about why"
        )

    def test_the_second_page_is_reached_by_either_form_of_its_url(self, client: TestClient) -> None:
        assert client.get("/ping").text == client.get("/ping/").text

    def test_the_pages_are_told_apart(self, client: TestClient) -> None:
        # Both hold the placeholder, so a lookup that fell back to the root page
        # would still substitute and still look right.
        assert "<title>ping</title>" in client.get("/ping/").text
        assert "<title>t</title>" in client.get("/").text

    def test_the_api_still_refuses_without_a_token(self, client: TestClient) -> None:
        # The point of the exemption being membership of the page table: adding
        # pages does not move the data behind it.
        assert client.get("/api/replays").status_code == 401
        assert client.get("/api/replays/a.osr/frames").status_code == 401

    def test_a_path_that_reads_two_ways_is_decided_by_the_one_that_routes(
        self, client: TestClient
    ) -> None:
        # `request.url.path` is rebuilt from the scope and re-split, so an
        # encoded `?` decodes into a delimiter and everything after it is
        # dropped: this path reads as `/` there and as itself to the router.
        # Whichever handler ends up running, the exemption has to have been
        # decided about the same string, or a request reaches one on the
        # strength of another's exemption.
        response = client.get("/%3F../api/replays")
        assert response.status_code == 401, (
            "the request was waved through on the root page's exemption"
        )

    def test_a_page_under_a_differently_cased_api_is_refused_too(self, access: Access) -> None:
        # The router matches case-sensitively, so `/API/x` would never reach the
        # `/api/x` handler and this is not reachable today. It is refused anyway:
        # this check is what decides who is served without a token, and resting
        # that on a second component's case sensitivity is a coupling nobody
        # would write down on purpose.
        with pytest.raises(ValueError, match="without a token"):
            build_app(access, pages={"/": PAGE, "/API/sneak/": PAGE}, payloads={})

    def test_an_unknown_path_is_a_404_for_a_request_that_has_the_token(
        self, client: TestClient
    ) -> None:
        assert client.get("/ping/nested", headers=auth()).status_code == 404

    def test_a_page_inside_the_api_namespace_is_refused_before_the_socket_opens(
        self, access: Access
    ) -> None:
        # It cannot come out of an Astro build, and if it ever did it would be an
        # exemption granted inside the data surface. Refused at construction,
        # where it is one check, rather than per request, where it is a filter
        # that has to be right about every path a browser can encode.
        for table in ({"/": PAGE, "/api/replays": PAGE}, {"/": PAGE, "/ws": PAGE}):
            with pytest.raises(ValueError, match="without a token"):
                build_app(access, pages=table, payloads={})

    def test_an_asset_inside_the_api_namespace_is_refused_too(self, access: Access) -> None:
        with pytest.raises(ValueError, match="without a token"):
            build_app(
                access,
                pages={"/": PAGE},
                payloads={},
                assets={"/api/replays": (b"{}", "application/json")},
            )


@pytest.mark.skipif(
    not (BUILT_SITE / "index.html").is_file(),
    reason=f"no built site at {BUILT_SITE}; run `npm run build` in web/",
)
class TestTheRealBuild:
    """The whole chain on real output: Astro's config, the loader, the server.

    The fixtures above assert what the server does with a page table; this
    asserts that the table a real build produces is the one it does that to. The
    two can only drift apart here, where nothing is written by hand.
    """

    def client(self, access: Access) -> TestClient:
        site = load_site(BUILT_SITE)
        app = build_app(
            access,
            pages=site.pages,
            payloads={},
            assets=site.assets,
            policy=site.policy,
        )
        return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")

    def test_the_second_page_arrives_substituted_and_unauthenticated(self, access: Access) -> None:
        # No Authorization header, because a browser navigating to a URL has no
        # way to attach one. Before this change the same file was served out of
        # the asset table as bytes, placeholder intact, and every call the page
        # then made was answered 401 with nothing saying why.
        response = self.client(access).get("/ping/")
        assert response.status_code == 200
        assert TOKEN in response.text
        assert "__TOKEN__" not in response.text

    def test_either_form_of_its_url_reaches_it(self, access: Access) -> None:
        client = self.client(access)
        assert client.get("/ping").text == client.get("/ping/").text

    def test_the_api_still_refuses_a_request_with_no_token(self, access: Access) -> None:
        assert self.client(access).get("/api/replays").status_code == 401

    def test_a_page_is_sent_only_the_hashes_of_its_own_scripts(self, access: Access) -> None:
        # The built root page carries Astro's hydration script and the built
        # second page carries none, so a union policy would hand `/ping/`
        # permission to run a script it never shipped.
        client = self.client(access)
        root = client.get("/").headers["content-security-policy"]
        second = client.get("/ping/").headers["content-security-policy"]
        assert "'sha256-" in root
        assert "'sha256-" not in second
        assert "'unsafe-inline'" not in root.split("style-src")[0]


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
        app = build_app(access, pages=PAGES, payloads={"a.osr": payload})
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
        app = build_app(access, pages=PAGES, payloads={"a.osr": payload})
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
            pages=PAGES,
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

    def test_the_policy_is_asked_for_the_path_it_is_sent_with(self, access: Access) -> None:
        # Per page, and this is the wiring that makes it so: each page ships its
        # own inline script, and one policy naming every page's hashes would
        # admit each of those scripts on all the other pages.
        app = build_app(
            access,
            pages=PAGES,
            payloads={},
            policy=lambda path: f"script-src 'self' 'hash-of{path}'",
        )
        client = TestClient(app, base_url=f"http://127.0.0.1:{PORT}")
        for path in ("/", "/ping/", "/ping"):
            header = client.get(path).headers["content-security-policy"]
            assert header == f"script-src 'self' 'hash-of{path}'"
