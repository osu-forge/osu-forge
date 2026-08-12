"""The built site, and the policy served with it.

Every case here came from a browser console rather than from imagination, which
is why they are worth keeping: each one was a real refusal on a real page.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import pytest

from osuforge.server.assets import (
    SiteMissingError,
    default_site_root,
    inline_script_hashes,
    load_site,
    page_urls,
)

REPO = Path(__file__).resolve().parents[2]
BUILT_SITE = REPO / "web" / "dist"


def build_site(root: Path, index: str = "<html>__TOKEN__</html>") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(index, encoding="utf-8")
    return root


def add_page(root: Path, relative: str, html: str = "<html>__TOKEN__</html>") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


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

    def test_the_loaded_page_still_holds_the_placeholder(self, tmp_path: Path) -> None:
        # Substitution belongs to the response, not to the load: the token is
        # never resident in the table, and one loaded site can be served by a run
        # with a different token.
        site = load_site(build_site(tmp_path / "dist"))
        assert site.carries_token_placeholder
        assert site.pages["/"] == "<html>__TOKEN__</html>"

    def test_a_build_without_the_placeholder_is_detectable(self, tmp_path: Path) -> None:
        # Such a page fetches without a token and 401s on every request, which
        # reads as a server fault rather than a build fault.
        site = load_site(build_site(tmp_path / "dist", "<html>no placeholder</html>"))
        assert not site.carries_token_placeholder

    def test_a_second_page_missing_the_placeholder_is_detectable_too(self, tmp_path: Path) -> None:
        # The page that lost it is the one nobody thought to open, so checking
        # the root page only would report the build as fine.
        root = build_site(tmp_path / "dist")
        add_page(root, "corpus/index.html", "<html>no placeholder</html>")
        assert not load_site(root).carries_token_placeholder

    def test_discovery_finds_the_checkout(self, monkeypatch) -> None:
        monkeypatch.delenv("OSU_FORGE_SITE", raising=False)
        assert default_site_root().name == "dist"
        assert default_site_root().parent.name == "web"


class TestPages:
    """Every `.html` is a page and no page is an asset.

    An asset is served as the bytes on disk. A page arriving that way still
    carries `__TOKEN__`, so every call it makes presents the literal placeholder
    and is answered 401 — a page that loads, looks finished, and fails at
    everything without saying why.
    """

    def test_a_second_page_is_a_page_and_not_an_asset(self, tmp_path: Path) -> None:
        root = build_site(tmp_path / "dist")
        add_page(root, "corpus/index.html", "<html>__TOKEN__ corpus</html>")
        site = load_site(root)
        assert site.pages["/corpus/"] == "<html>__TOKEN__ corpus</html>"
        assert not [url for url in site.assets if url.endswith(".html")]
        assert ".html" not in site.skipped, "a page is served, not counted as unservable"

    def test_both_forms_of_the_directory_url_answer(self, tmp_path: Path) -> None:
        # A link written `/corpus` is followed as `/corpus` by one browser and
        # `/corpus/` by another, and the server answers whichever arrives rather
        # than spending a redirect on correcting a slash.
        root = build_site(tmp_path / "dist")
        add_page(root, "corpus/index.html")
        site = load_site(root)
        assert site.pages["/corpus"] == site.pages["/corpus/"]

    def test_a_file_format_build_is_reachable_by_the_link_that_names_it(
        self, tmp_path: Path
    ) -> None:
        # `build.format: "file"` writes `corpus.html` for the link `/corpus`. The
        # config says `directory`, and this is what stops the other setting being
        # a page served as bytes.
        root = build_site(tmp_path / "dist")
        add_page(root, "corpus.html")
        site = load_site(root)
        assert "/corpus.html" in site.pages
        assert "/corpus" in site.pages

    def test_two_shapes_of_one_route_keep_their_own_urls(self, tmp_path: Path) -> None:
        # `build.format: "preserve"` can emit both for one route. Each stays
        # reachable at the URL its own file names, and the form they share is
        # settled the same way every load rather than by whatever the directory
        # was walked in.
        root = build_site(tmp_path / "dist")
        add_page(root, "corpus.html", "<html>__TOKEN__ file form</html>")
        add_page(root, "corpus/index.html", "<html>__TOKEN__ directory form</html>")
        site = load_site(root)
        assert site.pages["/corpus.html"] == "<html>__TOKEN__ file form</html>"
        assert site.pages["/corpus/"] == "<html>__TOKEN__ directory form</html>"
        assert site.pages["/corpus"] == "<html>__TOKEN__ directory form</html>"

    def test_a_file_without_an_extension_is_not_taken_for_a_page(self, tmp_path: Path) -> None:
        # It would be served with no content type to give it, and a page the
        # browser is not told is HTML is a page it shows the source of.
        root = build_site(tmp_path / "dist")
        (root / "LICENSE").write_text("...", encoding="utf-8")
        site = load_site(root)
        assert "/LICENSE" not in site.pages
        assert site.skipped["(none)"] == 1

    def test_the_root_page_is_the_one_a_missing_build_is_reported_by(self, tmp_path: Path) -> None:
        # A dist with pages but no root is a build that half happened, and the
        # honest answer is still the command that produces one.
        root = tmp_path / "dist"
        add_page(root, "corpus/index.html")
        with pytest.raises(SiteMissingError, match="npm run build"):
            load_site(root)

    def test_the_extensionless_root_is_the_bare_slash(self) -> None:
        assert page_urls("index.html") == ("/", None)


@pytest.mark.skipif(
    not (BUILT_SITE / "index.html").is_file(),
    reason=f"no built site at {BUILT_SITE}; run `npm run build` in web/",
)
class TestTheRealBuild:
    """The two sides of the URL shape, checked against each other.

    Astro's `build.format` decides where a page lands on disk and this module
    decides what URL that becomes. A fixture cannot notice those two drifting
    apart, because a fixture is written to whatever this module already does.
    """

    def test_the_second_page_arrives_where_the_config_says(self) -> None:
        site = load_site(BUILT_SITE)
        assert "/" in site.pages
        assert "/corpus/" in site.pages, sorted(site.pages)
        assert "/corpus" in site.pages

    def test_no_built_page_is_served_as_bytes(self) -> None:
        site = load_site(BUILT_SITE)
        assert not [url for url in site.assets if url.endswith(".html")]

    def test_every_built_page_carries_the_placeholder(self) -> None:
        assert load_site(BUILT_SITE).carries_token_placeholder

    def test_every_built_page_names_only_absolute_urls(self) -> None:
        # `/corpus` is answered with the page rather than a redirect to `/corpus/`,
        # which is only safe while nothing in the HTML is relative: `href="a.css"`
        # would resolve against `/` from one form and `/corpus/` from the other.
        #
        # Every URL-shaped attribute, not `href` and `src` alone: Astro puts the
        # island's script URLs on `component-url` and `renderer-url`, and those
        # are the ones a bundler is most likely to emit relative. Both quote
        # styles, because which one appears is the serialiser's choice and not a
        # property of the page.
        site = load_site(BUILT_SITE)
        pattern = re.compile(r"""\b[a-zA-Z-]*(?:href|src|url)\s*=\s*("[^"]*"|'[^']*')""")
        for url, html in site.pages.items():
            found = 0
            for raw in pattern.findall(html):
                value = raw[1:-1]
                if not value:
                    continue
                found += 1
                assert value.startswith(("/", "http://", "https://", "data:", "blob:", "#")), (
                    f"{url} names {value!r}, which resolves differently per URL form"
                )
            assert found, f"{url} names no URLs at all, so this asserted nothing about it"


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
        policy = site.policy("/")
        assert site.script_hashes["/"]
        for value in site.script_hashes["/"]:
            assert f"'{value}'" in policy
        assert "'unsafe-inline'" not in policy.split("style-src")[0], (
            "scripts are allowed by hash, never by turning the protection off"
        )

    def test_a_pages_script_is_not_allowed_on_another_page(self, tmp_path: Path) -> None:
        # The reason the policy is per page rather than one naming the union.
        # Astro emits a different inline script per page, and a union would be
        # the smallest policy admitting them all — while also admitting an
        # injected copy of one page's script on every other page, which is the
        # hole naming hashes was there to close.
        root = build_site(tmp_path / "dist", "<html>__TOKEN__<script>home()</script></html>")
        add_page(root, "corpus/index.html", "<html>__TOKEN__<script>corpus()</script></html>")
        site = load_site(root)
        home, second = site.script_hashes["/"], site.script_hashes["/corpus/"]
        assert home and second and home != second
        assert f"'{second[0]}'" not in site.policy("/")
        assert f"'{home[0]}'" not in site.policy("/corpus/")
        assert site.policy("/corpus/") == site.policy("/corpus"), (
            "one page, one policy, whichever form of its URL was asked for"
        )

    def test_a_path_with_no_page_is_allowed_no_script(self, tmp_path: Path) -> None:
        # An asset response and a 404 have no document for a script to run in,
        # so neither carries permission for one.
        site = load_site(
            build_site(tmp_path / "dist", "<html>__TOKEN__<script>go()</script></html>")
        )
        assert "script-src 'self';" in site.policy("/_astro/app.js")
        assert "script-src 'self';" in site.policy("/nothing/here")

    def test_frame_ancestors_is_present(self, tmp_path: Path) -> None:
        # The directive that stops the page being framed, and the one a browser
        # ignores when it arrives in a meta element — which is why the policy is
        # a header.
        assert "frame-ancestors 'none'" in load_site(build_site(tmp_path / "dist")).policy("/")

    def test_the_backdrop_can_be_painted(self, tmp_path: Path) -> None:
        # The background endpoint is authenticated, so the page fetches it with
        # the token and hands the result to an <img> as an object URL. A policy
        # that omits blob: blocks every backdrop after a successful download,
        # which reads on the page as a map that has no background.
        assert "img-src 'self' data: blob:" in load_site(build_site(tmp_path / "dist")).policy("/")

    def test_the_song_can_be_played(self, tmp_path: Path) -> None:
        # The same shape as the backdrop, and the failure is quieter: with no
        # media-src the policy falls back to default-src 'self', which admits no
        # blob:, and a play that downloaded its track sits there in silence.
        assert "media-src 'self' blob:" in load_site(build_site(tmp_path / "dist")).policy("/")

    def test_nothing_off_this_machine_is_reachable(self, tmp_path: Path) -> None:
        policy = load_site(build_site(tmp_path / "dist")).policy("/")
        assert "default-src 'self'" in policy
        assert "connect-src 'self'" in policy
        assert "font-src 'self'" in policy
        assert "object-src 'none'" in policy
