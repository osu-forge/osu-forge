"""Playback, end to end, through everything that actually renders it.

The bugs this exists to catch were all invisible from here: a Double Time clock
running at two thirds speed, a ball snapped to a five-pixel grid, sliders drawn
complete before they were reached. Every one of them passed the unit suite and
every one of them was caught by driving a real map and a real replay through the
real server into a real browser and counting pixels. That verification lived in
a scratchpad once and died with the session; this is it, committed.

Nothing is asserted against a stored image. Headless rendering goes through
SwiftShader, which is deterministic within one browser build and not across
them, so a golden screenshot would fail on the day the browser was bumped and
say nothing about playback. What is asserted is *relative*: two seeks apart must
move thousands of pixels, and the same paused frame captured twice must not
move at all.

The server is ``forge serve`` itself, a real process on a real socket, started
the way a player starts it. That is worth more than the rendering it exists to
check: the token-placeholder guard, the osu!.db read, the analysis cache, the
journal, the corpus and the replay watcher are on this path and on no other one
the suite drives.

Opt in with ``pytest -m e2e``. Missing prerequisites skip by default and fail
when ``OSU_FORGE_E2E_REQUIRE=1``, because a CI job that goes green by silently
skipping is worse than no CI job.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from osuforge.replay.model import Keys

from .fixtures import build_cfg
from .replay_fixtures import build_replay

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "web"
SITE = WEB / "dist"
DRIVER = WEB / "e2e" / "driver.mjs"

REQUIRE_VAR = "OSU_FORGE_E2E_REQUIRE"
BROWSER_VAR = "OSU_FORGE_E2E_BROWSER"
PACKAGED_BROWSER = Path("/opt/pw-browsers/chromium")

K1 = int(Keys.K1 | Keys.M1)
MARKERS: list[tuple[int, float, float, int]] = [(0, 256.0, -500.0, 0), (-1, 256.0, -500.0, 0)]

# Two circles and a curved slider that repeats. Small on purpose and not
# arbitrary: the slider is a perfect-circle arc whose declared length (300 px at
# one slider multiplier, a 500 ms beat, so three beats a span) is shorter than
# the curve through its anchors, so the path has to be trimmed; two repeats mean
# a reverse arrow and a ball that turns around. Between them these three objects
# exercise the paths buffer, snake-in, ball interpolation, ticks and hit state.
MAP_TEXT = (
    "osu file format v14\n"
    "[General]\nMode: 0\nStackLeniency: 0.7\n"
    "[Metadata]\nArtist:A\nTitle:E2E\nVersion:V\n"
    "[Difficulty]\nHPDrainRate:5\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\n"
    "SliderMultiplier:1\nSliderTickRate:1\n"
    "[TimingPoints]\n0,500,4,2,0,100,1,0\n"
    "[HitObjects]\n"
    "100,100,1000,5,0\n"
    "200,140,1500,1,0\n"
    "120,260,2200,2,0,P|260:150|400:260,2,300\n"
)

SLIDER_START = 2200
SLIDER_SPAN = 1500
SLIDER_REPEATS = 2
SLIDER_END = SLIDER_START + SLIDER_SPAN * SLIDER_REPEATS
RECORDING_END = SLIDER_END + 500
SAMPLE_MS = 30
"""Cursor sampling period. `Period` steps one recorded sample, so this is also
the map time one keypress buys."""

STEPS = 40
"""Presses of `Period` per capture, about 1.2 s of the recording."""

_ANCHORS = ((120.0, 260.0), (260.0, 150.0), (400.0, 260.0))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _between(
    first: tuple[float, float], second: tuple[float, float], at: float
) -> tuple[float, float]:
    return (
        first[0] + (second[0] - first[0]) * at,
        first[1] + (second[1] - first[1]) * at,
    )


def _along_slider(progress: float) -> tuple[float, float]:
    """The two anchor legs, walked at constant parameter. Close enough to the
    arc to keep the slider tracked, and exact enough to be reproducible."""
    if progress < 0.5:
        return _between(_ANCHORS[0], _ANCHORS[1], progress * 2.0)
    return _between(_ANCHORS[1], _ANCHORS[2], (progress - 0.5) * 2.0)


def _cursor(time_ms: int) -> tuple[float, float, int]:
    """Where the hand was, and what it was holding, at one instant."""
    if time_ms < 1000:
        return (100.0, 100.0, 0)
    if time_ms < 1040:
        return (100.0, 100.0, K1)
    if time_ms < 1500:
        return (*_between((100.0, 100.0), (200.0, 140.0), (time_ms - 1040) / 460.0), 0)
    if time_ms < 1540:
        return (200.0, 140.0, K1)
    if time_ms < SLIDER_START:
        return (*_between((200.0, 140.0), _ANCHORS[0], (time_ms - 1540) / 660.0), 0)
    if time_ms < SLIDER_END:
        span, into = divmod(time_ms - SLIDER_START, SLIDER_SPAN)
        travelled = into / SLIDER_SPAN
        # Odd spans run backwards: that is what a repeat is.
        return (*_along_slider(travelled if span % 2 == 0 else 1.0 - travelled), K1)
    return (*_ANCHORS[0], 0)


def _replay_frames() -> list[tuple[int, float, float, int]]:
    body = list(MARKERS)
    previous = -1
    for time_ms in range(0, RECORDING_END + 1, SAMPLE_MS):
        x, y, keys = _cursor(time_ms)
        body.append((time_ms - previous, x, y, keys))
        previous = time_ms
    return body


def _build_world(root: Path) -> tuple[Path, Path, Path]:
    """A whole osu! install, small enough to fit in a temporary directory."""
    install = root / "osu!"
    songs = install / "Songs" / "1 A - E2E"
    replays = install / "Data" / "r"
    songs.mkdir(parents=True)
    replays.mkdir(parents=True)

    # `forge serve` starts from a config rather than from a folder, and the
    # folder holding it is the install every path it does not receive is read
    # from. No osu!.db beside it: an install without one is a case the server
    # has to answer, and answering it is part of what this now covers.
    config = install / "osu!.tester.cfg"
    config.write_bytes(build_cfg({"BeatmapDirectory": "Songs"}))

    beatmap = songs / "map.osu"
    beatmap.write_text(MAP_TEXT, encoding="utf-8")
    digest = hashlib.md5(beatmap.read_bytes()).hexdigest()

    (replays / "e2e.osr").write_bytes(
        build_replay(
            beatmap_hash=digest,
            count_300=3,
            count_100=0,
            count_50=0,
            count_geki=1,
            count_katu=0,
            count_miss=0,
            max_combo=5,
            score=100_000,
            frames=_replay_frames(),
        )
    )
    return config, install / "Songs", replays


def _chromium() -> Path | None:
    """The browser to drive: an explicit one, playwright-core's own, or the
    image's. First that exists wins; nothing is downloaded from a test."""
    override = os.environ.get(BROWSER_VAR)
    if override:
        return Path(override) if Path(override).exists() else None
    probe = subprocess.run(
        [
            "node",
            "-e",
            "process.stdout.write(require('playwright-core').chromium.executablePath())",
        ],
        cwd=WEB,
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode == 0 and (installed := Path(probe.stdout.strip())).exists():
        return installed
    return PACKAGED_BROWSER if PACKAGED_BROWSER.exists() else None


def _prerequisites() -> tuple[Path, Path]:
    """Resolve everything the run needs, or stop with the reason by name."""
    missing: list[str] = []

    if not (SITE / "index.html").is_file():
        missing.append(f"no built page at {SITE} (cd web && npm run build)")
    node = shutil.which("node")
    if node is None:
        missing.append("node is not on PATH")
    if not (WEB / "node_modules" / "playwright-core").is_dir():
        missing.append("playwright-core is not installed (cd web && npm ci)")
    browser = _chromium() if node else None
    if browser is None:
        missing.append(f"no chromium found (set {BROWSER_VAR}, or npx playwright install chromium)")

    if missing:
        reason = "e2e prerequisites missing: " + "; ".join(missing)
        if os.environ.get(REQUIRE_VAR) == "1":
            pytest.fail(reason)
        pytest.skip(reason)

    assert node is not None and browser is not None
    return Path(node), browser


@contextmanager
def _served(*, config: Path, songs: Path, replays: Path, root: Path) -> Iterator[str]:
    """A real process on a real socket, for the life of the block."""
    port = _free_port()
    log = root / "server.log"
    # `-m` rather than the installed `forge` script, so the server runs on the
    # same interpreter as the test. `--backfill 0` because one replay leaves
    # nothing to backfill and the slow path should not be able to open.
    command = [
        sys.executable,
        "-m",
        "osuforge.cli",
        "serve",
        "--config",
        str(config),
        "--site",
        str(SITE),
        "--songs",
        str(songs),
        "--replays",
        str(replays),
        "--journal",
        str(root / "journal.jsonl"),
        "--cache",
        str(root / "cache.db"),
        "--backfill",
        "0",
        "--port",
        str(port),
    ]
    # The token file the page authenticates with is placed from LOCALAPPDATA and
    # is not a flag, so this is what keeps a test run out of the home directory
    # of whoever is running it.
    env = {**os.environ, "LOCALAPPDATA": str(root / "appdata")}
    with log.open("w", encoding="utf-8") as sink:
        process = subprocess.Popen(command, stdout=sink, stderr=subprocess.STDOUT, env=env)
        url = f"http://127.0.0.1:{port}/"
        try:
            # Startup is an index, a judged replay, the analysis and a corpus
            # pass, not just a bind: seconds rather than the fraction the
            # launcher this replaced took.
            deadline = time.monotonic() + 120.0
            while True:
                if process.poll() is not None:
                    raise AssertionError(f"the server exited early:\n{log.read_text()}")
                try:
                    with urllib.request.urlopen(url, timeout=2) as response:
                        if response.status == 200:
                            break
                except urllib.error.URLError, OSError, TimeoutError:
                    pass
                if time.monotonic() > deadline:
                    raise AssertionError(f"the server never answered:\n{log.read_text()}")
                time.sleep(0.2)
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)


def test_a_replay_plays_back_in_a_real_browser(tmp_path: Path) -> None:
    node, browser = _prerequisites()
    config, songs, replays = _build_world(tmp_path)
    shots = tmp_path / "shots"

    with _served(config=config, songs=songs, replays=replays, root=tmp_path) as url:
        driver = subprocess.run(
            [
                str(node),
                str(DRIVER),
                # The viewer's own page. `/` is the overview now, and it has no
                # playfield on it by design — the whole point of the dashboard
                # split is that the front page answers without loading a replay.
                "--url",
                f"{url}replays/",
                "--out",
                str(shots),
                "--executable",
                str(browser),
                "--steps",
                str(STEPS),
            ],
            cwd=WEB,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    assert driver.returncode == 0, f"driver failed:\n{driver.stderr}"
    verdict = json.loads(driver.stdout.strip().splitlines()[-1])
    print(f"e2e verdict: {json.dumps(verdict['diffs'])} painted={json.dumps(verdict['painted'])}")

    # An uncaught exception in the page is the single most informative signal
    # here: WebGL2 unavailable, a payload the decoder does not recognise, a
    # shader that will not compile all arrive as one of these.
    assert verdict["pageerrors"] == []

    diffs = verdict["diffs"]
    # Stillness first: it is the noise floor the two motion numbers are read
    # against, and it is what would fail if rendering were not a pure function
    # of the clock.
    assert diffs["paused_repeat"] < 25, "the same paused frame rendered differently twice"
    assert diffs["start_to_mid"] > 1000, "seeking forward changed almost nothing"
    assert diffs["mid_to_end"] > 1000, "seeking forward again changed almost nothing"

    # A canvas that failed to draw is still a canvas, and every diff above it
    # would read as motion if the failure happened between captures.
    assert verdict["painted"]["s1a"] > 2000, "the playfield is empty where the map should be"
