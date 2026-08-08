"""The part of the server that decides who is allowed to talk to it."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from osuforge.server.security import (
    DEFAULT_PORT,
    RESERVED_PORTS,
    Access,
    SessionToken,
    runtime_file,
    write_runtime_file,
)


@pytest.fixture
def access() -> Access:
    return Access(port=DEFAULT_PORT, token=SessionToken("secret-value"))


class TestHost:
    def test_the_loopback_literal_and_localhost_are_the_server(self, access: Access) -> None:
        assert access.host_allowed(f"127.0.0.1:{DEFAULT_PORT}")
        assert access.host_allowed(f"localhost:{DEFAULT_PORT}")
        assert access.host_allowed(f"[::1]:{DEFAULT_PORT}")

    def test_a_name_an_attacker_controls_is_refused(self, access: Access) -> None:
        # DNS rebinding defeats the loopback bind: a name they own is pointed at
        # 127.0.0.1 and the browser connects for them. What they cannot do is
        # forge the Host header, because the browser sends the name it resolved.
        # This is the layer that stops it.
        assert not access.host_allowed(f"evil.example.com:{DEFAULT_PORT}")
        assert not access.host_allowed(f"127.0.0.1.nip.io:{DEFAULT_PORT}")

    def test_the_right_name_on_the_wrong_port_is_refused(self, access: Access) -> None:
        assert not access.host_allowed("127.0.0.1:24050")

    def test_a_missing_host_is_refused(self, access: Access) -> None:
        assert not access.host_allowed(None)
        assert not access.host_allowed("")

    def test_a_bare_name_without_a_port_is_refused(self, access: Access) -> None:
        assert not access.host_allowed("localhost")


class TestOrigin:
    def test_our_own_pages_are_allowed(self, access: Access) -> None:
        assert access.origin_allowed(f"http://127.0.0.1:{DEFAULT_PORT}")
        assert access.origin_allowed(f"http://localhost:{DEFAULT_PORT}")

    def test_any_website_is_not(self, access: Access) -> None:
        # The threat is a page in another tab, not another computer. A tool that
        # answers `Access-Control-Allow-Origin: *` here lets every site the user
        # visits read live game state.
        assert not access.origin_allowed("https://example.com")
        assert not access.origin_allowed("null")
        assert not access.origin_allowed("*")

    def test_https_on_our_own_port_is_still_not_us(self, access: Access) -> None:
        assert not access.origin_allowed(f"https://127.0.0.1:{DEFAULT_PORT}")

    def test_a_client_that_is_not_a_browser_is_allowed_through_to_the_token(
        self, access: Access
    ) -> None:
        # A browser always sends Origin on a WebSocket handshake, so its absence
        # means something else — a script the user ran, or OBS, whose CEF build
        # has not been checked. The token is what stands behind this.
        assert access.origin_allowed(None)


class TestToken:
    def test_the_right_value_matches(self) -> None:
        token = SessionToken("abc")
        assert token.matches("abc")

    def test_anything_else_does_not(self) -> None:
        token = SessionToken("abc")
        assert not token.matches("abd")
        assert not token.matches("")
        assert not token.matches(None)
        assert not token.matches("abc ")

    def test_generated_tokens_are_long_and_never_repeat(self) -> None:
        first = SessionToken.generate()
        second = SessionToken.generate()
        assert first.value != second.value
        assert len(first.value) >= 32

    def test_a_token_is_not_reused_between_runs(self) -> None:
        # A token stored on disk from last week is a token that leaked last
        # week. There is deliberately no way to load one.
        assert not hasattr(SessionToken, "load")


class TestPorts:
    def test_the_default_avoids_the_tools_that_already_exist(self) -> None:
        # Colliding would mean an overlay talking to the wrong program, which is
        # worse than failing to start.
        assert DEFAULT_PORT not in RESERVED_PORTS
        assert set(RESERVED_PORTS) == {24050, 20727}


class TestRuntimeFile:
    def test_it_carries_the_port_and_token(self, tmp_path: Path) -> None:
        access = Access(port=24080, token=SessionToken("s3cret"))
        path = write_runtime_file(access, path=tmp_path / "runtime.json")
        data = json.loads(path.read_text("utf-8"))
        assert data["port"] == 24080
        assert data["token"] == "s3cret"
        assert data["url"] == "http://127.0.0.1:24080/"

    @pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX mode; see below")
    def test_it_is_readable_only_by_its_owner(self, tmp_path: Path) -> None:
        # It holds a live credential. The mode is set at creation rather than
        # tightened afterwards, because tightening leaves a window.
        path = write_runtime_file(
            Access(port=24080, token=SessionToken("s3cret")), path=tmp_path / "runtime.json"
        )
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert not mode & stat.S_IROTH
        assert not mode & stat.S_IRGRP

    @pytest.mark.skipif(os.name != "nt", reason="the Windows case")
    def test_on_windows_the_mode_is_not_the_protection(self, tmp_path: Path) -> None:
        # Asserted rather than assumed, because it is the opposite of what the
        # code appears to do. `os.open` accepts the mode and Windows ignores
        # everything but the read-only flag, so the file comes out 0o666. What
        # protects it there is its location: %LOCALAPPDATA% is inside the user
        # profile, whose ACL already excludes other users.
        path = write_runtime_file(
            Access(port=24080, token=SessionToken("s3cret")), path=tmp_path / "runtime.json"
        )
        assert stat.S_IMODE(os.stat(path).st_mode) & stat.S_IROTH
        assert "AppData" in str(runtime_file()) or "Local" in str(runtime_file())

    def test_rewriting_replaces_rather_than_appends(self, tmp_path: Path) -> None:
        target = tmp_path / "runtime.json"
        write_runtime_file(Access(port=1, token=SessionToken("old")), path=target)
        write_runtime_file(Access(port=2, token=SessionToken("new")), path=target)
        data = json.loads(target.read_text("utf-8"))
        assert data == {
            "schema_version": 1,
            "port": 2,
            "token": "new",
            "url": "http://127.0.0.1:2/",
        }

    def test_the_default_location_is_outside_the_osu_install(self) -> None:
        assert "osu!" not in str(runtime_file())
        assert runtime_file().name == "runtime.json"
