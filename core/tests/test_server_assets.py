"""The built site, and the policy served with it.

Every case here came from a browser console rather than from imagination, which
is why they are worth keeping: each one was a real refusal on a real page.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from osuforge.server.assets import (
    SiteMissingError,
    default_site_root,
    inline_script_hashes,
    load_site,
)


def build_site(root: Path, index: str = "<html>__TOKEN__</html>") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(index, encoding="utf-8")
    return root


class TestLoading:
    def test_a_missing_build_names_the_command_that_produces_it(self, tmp_path: Path) -> None:
        with pytest.raises(SiteMissingError, match="npm run build"):
            load_site(tmp_path / "nothing")

    def test_assets_are_read_by_url_path(self, tmp_path: Path) -> None:
        root = build_site(tmp_path / "dist")
        (root / "_astro").mkdir()
        (root / "_astro" / "app.js").write_text("export {};", encoding="utf-8")
        site = load_site(root)
        assert "/_astro/app.js" in site.assets
        assert site.assets["/_astro/app.js"][1].startswith("text/javascript")

    def test_an_unknown_extension_is_skipped_and_counted(self, tmp_path: Path) -> None:
        # Served as a guess, a file the browser expected to execute produces a
        # blank page and an hour of looking in the wrong place.
        root = build_site(tmp_path / "dist")
        (root / "notes.bin").write_bytes(b"\x00")
        site = load_site(root)
        assert "/notes.bin" not in site.assets
        assert site.skipped[".bin"] == 1

    def test_the_token_placeholder_is_substituted_per_request(self, tmp_path: Path) -> None:
        site = load_site(build_site(tmp_path / "dist"))
        assert site.carries_token_placeholder
        assert site.page("secret") == "<html>secret</html>"
        assert "secret" not in site.index, "the token never lives in the loaded page"

    def test_a_build_without_the_placeholder_is_detectable(self, tmp_path: Path) -> None:
        # Such a page fetches without a token and 401s on every request, which
        # reads as a server fault rather than a build fault.
        site = load_site(build_site(tmp_path / "dist", "<html>no placeholder</html>"))
        assert not site.carries_token_placeholder

    def test_discovery_finds_the_checkout(self, monkeypatch) -> None:
        monkeypatch.delenv("OSU_FORGE_SITE", raising=False)
        assert default_site_root().name == "dist"
        assert default_site_root().parent.name == "web"


class TestInlineScriptHashes:
    """Astro emits an inline script to start hydration, and a policy of
    `default-src 'self'` blocks it — correctly, because the policy cannot tell
    it apart from an injected one. Naming the exact contents keeps the policy
    meaning what it says; `'unsafe-inline'` would turn it off for every script.
    """

    def expect(self, body: str) -> str:
        digest = hashlib.sha256(body.encode("utf-8")).digest()
        return f"sha256-{base64.b64encode(digest).decode('ascii')}"

    def test_an_inline_script_is_hashed(self) -> None:
        assert inline_script_hashes("<script>alert(1)</script>") == (self.expect("alert(1)"),)

    def test_a_script_with_a_src_is_not(self) -> None:
        # It is fetched, so 'self' already covers it. Hashing the empty body
        # would add a directive that permits an empty inline script.
        assert inline_script_hashes('<script src="/app.js"></script>') == ()

    def test_attributes_do_not_change_the_hash(self) -> None:
        # The hash covers the element's contents, not its tag.
        plain = inline_script_hashes("<script>go()</script>")
        typed = inline_script_hashes('<script type="module">go()</script>')
        assert plain == typed == (self.expect("go()"),)

    def test_two_identical_scripts_produce_one_directive(self) -> None:
        assert len(inline_script_hashes("<script>a()</script><script>a()</script>")) == 1

    def test_several_are_kept_in_order(self) -> None:
        found = inline_script_hashes("<script>a()</script><script>b()</script>")
        assert found == (self.expect("a()"), self.expect("b()"))

    def test_a_page_with_no_scripts_has_none(self) -> None:
        assert inline_script_hashes("<html><body>hi</body></html>") == ()


class TestPolicy:
    def test_it_carries_the_hashes_it_computed(self, tmp_path: Path) -> None:
        site = load_site(
            build_site(tmp_path / "dist", "<html>__TOKEN__<script>go()</script></html>")
        )
        policy = site.policy()
        assert site.script_hashes
        for value in site.script_hashes:
            assert f"'{value}'" in policy
        assert "'unsafe-inline'" not in policy.split("style-src")[0], (
            "scripts are allowed by hash, never by turning the protection off"
        )

    def test_frame_ancestors_is_present(self, tmp_path: Path) -> None:
        # The directive that stops the page being framed, and the one a browser
        # ignores when it arrives in a meta element — which is why the policy is
        # a header.
        assert "frame-ancestors 'none'" in load_site(build_site(tmp_path / "dist")).policy()

    def test_the_backdrop_can_be_painted(self, tmp_path: Path) -> None:
        # The background endpoint is authenticated, so the page fetches it with
        # the token and hands the result to an <img> as an object URL. A policy
        # that omits blob: blocks every backdrop after a successful download,
        # which reads on the page as a map that has no background.
        assert "img-src 'self' data: blob:" in load_site(build_site(tmp_path / "dist")).policy()

    def test_nothing_off_this_machine_is_reachable(self, tmp_path: Path) -> None:
        policy = load_site(build_site(tmp_path / "dist")).policy()
        assert "default-src 'self'" in policy
        assert "connect-src 'self'" in policy
        assert "font-src 'self'" in policy
        assert "object-src 'none'" in policy
