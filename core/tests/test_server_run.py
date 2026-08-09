"""Starting the server, and refusing to start it somewhere unexpected."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from osuforge.server.run import INTERFACE, ServerError, prepare
from osuforge.server.security import DEFAULT_PORT, RESERVED_PORTS


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((INTERFACE, 0))
        return int(probe.getsockname()[1])


class TestPortPolicy:
    def test_a_taken_port_fails_loudly_rather_than_moving(self, tmp_path: Path) -> None:
        # Moving is the wrong convenience. A client with the old port cached
        # would reach whatever else is there, and the neighbouring ports belong
        # to other tools of exactly this kind.
        port = free_port()
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind((INTERFACE, port))
        holder.listen(1)
        try:
            with (
                pytest.raises(ServerError, match="already in use"),
                prepare(port=port, runtime_path=tmp_path / "runtime.json"),
            ):
                pass
        finally:
            holder.close()

    def test_the_error_says_why_it_did_not_move(self, tmp_path: Path) -> None:
        port = free_port()
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind((INTERFACE, port))
        holder.listen(1)
        try:
            with (
                pytest.raises(ServerError) as caught,
                prepare(port=port, runtime_path=tmp_path / "runtime.json"),
            ):
                pass
            assert "cached" in str(caught.value)
            assert "belong to other tools" in str(caught.value)
        finally:
            holder.close()

    @pytest.mark.parametrize("port", sorted(RESERVED_PORTS))
    def test_another_tools_port_is_refused_before_it_is_probed(
        self, port: int, tmp_path: Path
    ) -> None:
        # Refused whether or not anything is listening. An overlay talking to
        # the wrong program looks like it worked, which is worse than silence.
        with (
            pytest.raises(ServerError, match="belongs to another tool"),
            prepare(port=port, runtime_path=tmp_path / "runtime.json"),
        ):
            pass

    @pytest.mark.parametrize("port", [0, 80, 1023, 70000, -1])
    def test_ports_outside_the_range_are_refused(self, port: int, tmp_path: Path) -> None:
        with pytest.raises(ServerError), prepare(port=port, runtime_path=tmp_path / "runtime.json"):
            pass

    def test_the_default_is_not_one_of_the_reserved_ones(self) -> None:
        assert DEFAULT_PORT not in RESERVED_PORTS


class TestRuntimeFile:
    def test_it_is_published_while_running_and_removed_afterwards(self, tmp_path: Path) -> None:
        # A stale file sends the next client to a port that is no longer
        # answering, carrying a token that no longer means anything. "Nothing
        # is running" is the honest failure.
        path = tmp_path / "runtime.json"
        with prepare(port=free_port(), runtime_path=path) as access:
            assert path.is_file()
            written = json.loads(path.read_text(encoding="utf-8"))
            assert written["port"] == access.port
            assert written["token"] == access.token.value
            assert written["url"].startswith(f"http://{INTERFACE}:")
        assert not path.exists()

    def test_it_is_removed_even_when_the_body_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "runtime.json"
        with pytest.raises(ZeroDivisionError), prepare(port=free_port(), runtime_path=path):
            raise ZeroDivisionError
        assert not path.exists()

    def test_each_run_gets_its_own_token(self, tmp_path: Path) -> None:
        port = free_port()
        with prepare(port=port, runtime_path=tmp_path / "a.json") as first:
            first_token = first.token.value
        with prepare(port=port, runtime_path=tmp_path / "b.json") as second:
            assert second.token.value != first_token


class TestInterface:
    def test_the_interface_is_loopback_and_not_a_parameter(self) -> None:
        # A flag that made this reachable from another machine would be the one
        # line in the module that undid the rest of it.
        import inspect

        from osuforge.server import run

        assert INTERFACE == "127.0.0.1"
        for name in ("prepare", "serve"):
            signature = inspect.signature(getattr(run, name))
            assert "host" not in signature.parameters
            assert "interface" not in signature.parameters
