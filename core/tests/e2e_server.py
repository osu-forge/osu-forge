"""Serve one folder of replays, for the end-to-end test to point a browser at.

This is not a second implementation of ``forge serve``. It is the shortest path
from a replay on disk to a real socket that speaks the real protocol: index the
songs, judge the replay, build the payload the page decodes, hand it to
:func:`osuforge.server.run.serve`. Everything the playback regression could hide
in — the simulation, the payload encoding, the token, the CSP, the WebSocket —
is the production code, called the production way.

It exists at all because ``osuforge.cli`` cannot be imported off Windows: the
rule engine pulls in probes built on ``ctypes.WINFUNCTYPE`` at module scope, by
design, since the tool advises on a Windows game. The browser side of this test
runs on Linux, so the command line is the one link in the chain the test cannot
traverse. See ``test_e2e_playback.py``.

Run as a script, not imported:
    python tests/e2e_server.py --songs <dir> --replays <dir> --site <dir> --port N
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from osuforge.live.watch import BeatmapIndex
from osuforge.replay.source import judge
from osuforge.server import ReplayPayload, load_site, serve


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--songs", type=Path, required=True)
    parser.add_argument("--replays", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    site = load_site(args.site)
    index = BeatmapIndex(args.songs)

    payloads: dict[str, Any] = {}
    for path in sorted(args.replays.glob("*.osr")):
        judged = judge(path, index)
        if isinstance(judged, str):
            print(f"skipped {path.name}: {judged}")
            continue
        payloads[path.name] = ReplayPayload.build(
            replay_name=path.name,
            beatmap=judged.played,
            simulation=judged.simulation,
            rate=judged.replay.rate,
            mods=int(judged.replay.mods),
        )

    if not payloads:
        print("nothing to serve")
        return 2

    print(f"serving {len(payloads)} replay(s) on {args.port}", flush=True)
    serve(site=site, payloads=payloads, port=args.port, runtime_path=args.runtime)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
