# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial repository scaffolding: Apache-2.0 license, dependency-license policy
  with `cargo-deny` enforcement, CI workflows, issue templates.
- `docs/architecture.md` — three-tier extension model, event bus, capability model.
- `docs/licensing.md` — provenance policy for memory-layout data and the clean-room
  procedure for signature discovery.
- `docs/privacy.md` — live input logger disclosure.
- `osuforge.models` — the `Finding` contract shared by every subsystem.
- `osuforge.config` — read-only `osu!.<user>.cfg` parser with a byte-exact
  round trip, credential redaction applied at the parser boundary, config
  discovery, and locale-invariant typed accessors.
- `osuforge.config.keybinds` — ruleset-aware conflict detection. Two bindings
  conflict only when they are live in an overlapping context, so the four
  apparent collisions in a real config resolve to the one that is real.
- `osuforge.probes.base` — the `ProbeResult` contract. Probes return failure as
  a value and never raise into the rule engine.
- `osuforge.rules` — declarative rule engine plus the first five rules, all
  config-only so they run with no probes, no elevation, and osu! not running.
  A rule whose probe is unavailable emits a visible skip rather than vanishing,
  and a rule that raises costs its own finding rather than the whole report.
- `osuforge.probes` — read-only Windows measurements: monitors via
  `EnumDisplaySettingsExW`, pointer settings, accessibility shortcut state,
  timer resolution, power scheme, Game Bar, Fullscreen Optimizations, osu!
  process detection, and the monitor osu!'s window is actually on.
- Nine system rules built on those probes, and `forge doctor` / `forge scan`
  with `--json`, `--only-facts`, `--severity` and `--fail-on`.
- `osu_forge_diffcalc::beatmap` — `.osu` parser with timing points, slider
  durations, and the version-dependent `ApproachRate` fallback. Shared by
  difficulty calculation and the replay hit simulator so the two cannot
  disagree about what a beatmap says.
- `SliderPath` — slider geometry for all four curve types, with arc-length
  sampling and the declared-length trimming osu! applies. Checked against the
  length field of every slider in a real collection.
- `osuforge.replay` — `.osr` parser: header, judgement counts, mods, and the
  LZMA frame stream with delta accumulation and the trailing seed frame
  separated out.
- `osuforge.replay.frames` — marker stripping, key press/release extraction
  with device attribution, and a timing uncertainty on every event. Unknown
  uncertainty is reported as unknown rather than collapsed to zero.
- Stack leniency, so an object's position is where it is drawn and judged rather
  than where the file says. Files older than format v6 stacked by a different
  algorithm; that one is not implemented and those files are reported as
  unstacked instead of being stacked by the wrong rules.
- Difficulty derivations on the beatmap: approach rate to preempt time, circle
  size to object scale and radius, overall difficulty to judgement windows. The
  windows are returned unrounded — which rounding stable applies is a decision
  for whatever does the comparing.
- Mod handling for Hard Rock and Easy. Double Time and Half Time deliberately
  leave the beatmap alone: they change the clock, and applying them here as well
  would count the same speed increase twice.
- `osu_forge_diffcalc` — PyO3 bindings, so the replay hit simulator reads
  beatmaps through the same parser as difficulty calculation instead of through
  a second one written in Python.
- Slider parts — ticks, repeat points and the tail, with the position of the
  ball at each. A slider's grade comes from how many of them the player
  collected, so judgement counts cannot be reproduced without them.
- `osuforge.replay.simulate` — the hit simulator. Presses are matched to objects
  by timing *and* by where the cursor was, and sliders are scored part by part
  against the follow circle.
- `osuforge.replay.validate` — the screen that decides whether to believe a
  simulation, and the circuit breaker that withholds any recommendation from a
  corpus it does not.
- `engine/prototype` — read-only attachment to a running osu!, region-clipped
  reads, pattern scanning, and a `.NET` string reader whose type check is
  bootstrapped from a string the configuration file already names.
- `engine/CLEANROOM.md` — what has been observed about osu!'s memory, how, and
  when. Nothing in it comes from another project.
- `scripts/dev-setup.ps1` — the whole contributor setup in one command.
- `osuforge.collect` and `forge collect` — an append-only record of which
  replays exist and what the timing-relevant settings were, so that sessions and
  a settings history accumulate going forward. An offset estimate is limited by
  how many separate sessions it has, and sessions only accumulate forward.
- `osuforge.live` and `forge live` — a self-contained page rewritten as plays
  finish, so a browser left open on a second monitor shows what the last play
  did a second or two after it ends. No server, no port, nothing listening.
- `osuforge.analysis.patterns` — where a play's accuracy went, split across the
  kinds of object that cost it. A partition rather than a model: the parts add
  to the whole by construction, and groups are cut within one map at a time so
  that approach rate, circle size and settings control for themselves.
- `osuforge.analysis.clustering` — a mean and an interval that account for hits
  being nested in replays and replays in sessions, reporting the effective
  sample size rather than the raw one. On the local corpus that is 893
  independent hits out of 23,723.
- An inclusion policy with its exclusions counted: replays with too few hits, or
  with a miss rate high enough that the surviving errors are a truncated sample,
  do not contribute a mean.
- `osuforge.replay.oracle` — object-by-object comparison against circleguard,
  run as a separate process in an isolated environment. This is what authorises
  an offset recommendation; the header screen on its own never does.
- `web/` — an Astro + React page that serves the replays back, with the
  playfield drawn in WebGL2 from shaders compiled at build time rather than at
  load. Spinners, repeat sliders, combo numbers and follow points are drawn
  because the things a viewer is looking for are not visible without them, and
  the cursor is interpolated between the two samples bracketing the clock while
  the samples themselves stay drawn underneath.
- `osuforge.server` — the local server the page talks to, and
  `forge serve`. New plays are pushed to an open page as they finish.
- `web/src/lib/cause.ts` — why each break happened, read off what was recorded
  against that map's own windows, with the number every verdict came from. A
  dropped slider deliberately stops at how much was collected: telling "the key
  came up" from "the cursor left" needs per-frame tracking that does not exist
  yet, and guessing would be the most useful-sounding wrong answer available.
- `osuforge.analysis.corpus` — timing bias, timing spread and arrival as three
  separate axes, each carrying whether a setting change follows from it. Refuses
  below ten replays or three sessions and says which of the two is missing, and
  `by_beatmap` takes the same estimate per map so a weakness on one map can be
  told from a habit that follows the player everywhere.
- `osuforge.analysis.gather` and `forge diagnose` — the corpus reached from a
  replay folder. Every replay is simulated against its beatmap, screened, and
  reduced to hit errors in real milliseconds; sessions are derived from the gaps
  between plays, local offsets are read from `osu!.db` rather than assumed, and
  only replays played under the settings now in force are pooled. Everything
  left out is named with why.
- `osuforge.replay.source` — locating a replay's beatmap and judging the two
  together, shared by the live page and the corpus so that the two cannot
  disagree about which objects a play hit.
- `osuforge.server.corpus` — the corpus diagnosis, served. `forge serve` feeds
  every analysed play into one `CorpusState`, prints the corpus summary at
  startup, answers `GET /api/corpus` from a cached result so no request waits
  on statistics, and pushes a fresh answer to open pages when a new play
  changes it. Plays whose simulation failed the header screen stay out of the
  estimate, are named with the screen's reason, and still count against the
  corpus's health — which reports `may_recommend: false` on every answer,
  because nothing in a serve run has been judged by the oracle.
- A corpus panel on the served page: the three axes with their actionable
  flags, the refusal sentence where the verdict would be when the corpus is
  too thin, the per-beatmap comparison with a one-sentence reading of pooled
  against per-map intervals (`osuforge.analysis.corpus.beatmap_reading`), and
  the excluded plays with their reasons.
- `osuforge.analysis.progress` — whether it changed, and by how much. The
  corpus split at the most recent settings change the collect journal
  recorded (or at the middle of the sessions, labelled as only that), each
  side estimated on its own, and the difference reported with an interval of
  its own — the wider of a Welch-corrected cluster route and a hierarchical
  bootstrap on the differenced draws. Sides too thin to compare are refused
  with the side and the missing ingredient named.
- The serve corpus is now epoch-aware: the verdict is computed from the plays
  under the settings in force — the same refusal to pool across a change that
  `forge diagnose` makes — while the older plays become the before side of a
  progress view on the panel: one dot per session, the two eras' intervals,
  the boundary marked for what it is, and the shift verdict beneath. Plays
  the journal has not seen inherit the newest fingerprint rather than
  vanishing from the page they just appeared on.
- `forge diagnose --all-epochs` prints the same comparison, and the default
  run points at it when the settings filter left an era out.
- `osuforge.server.cache` — the analysis cache. What each play reduces to for
  the corpus persists in a SQLite file beside the journal, keyed by the
  replay's name, size, mtime and the tool version that produced it — numbers
  cached by an older simulator are measurements under different rules and are
  dropped rather than mixed in. At startup `forge serve` restores every
  cached play into the corpus, analyses a bounded number of never-seen older
  plays into it (`--backfill`, default 15), and records each new play as it
  arrives, so the corpus outgrows the playback cap and covers everything ever
  played, run over run. A damaged cache costs recomputation, never the
  server; deleting the file loses minutes, not facts.
- The served page, reworked. The replay list carries accuracy and date and
  gains a filter box; the analysis panel gains a hit-error histogram drawn
  over the judgement windows in the judgement colours, with the windows
  divided by the replay's rate so a Double Time play shows its genuinely
  narrower real-time windows; `#corpus` and `#r=<replay>` deep links survive
  a refresh and let an OBS scene pin one view; space plays and pauses; the
  layout stacks on narrow windows instead of breaking; and the corpus panel
  says when its answer arrived.
- The replay player grows a transport. Playback speed from 0.25× to 2×
  scaling the same clock the timeline is drawn against; an elapsed/total
  readout; previous/next break buttons that land shortly before each break
  so the approach that caused it is what plays; a key overlay lit from the
  recorded mask with a running press count per input, mouse chips hidden for
  replays that never used them; the timeline is a scrubber that keeps
  seeking while dragged; `,` and `.` step one recorded sample; and playing
  from the end rewinds first instead of doing nothing.
- Rendering fidelity, audited and fixed. The wire already carried the modded
  beatmap — Hard Rock's reflected positions with stacking recomputed after
  the flip, the smaller circles, the shorter preempt — but four things were
  wrong and now are not: playback advances the map-time clock by the replay's
  rate, so 1× on a Double Time play means the speed it was played at rather
  than slow motion (the readout, seeks and break landings are in experienced
  seconds); the header carries the mod bitmask and the page names it, in the
  list and beside the title; Hidden renders as the player saw it — no
  approach circles, objects fading in over 40% of the preempt and out over
  the next 30%, slider bodies thinning across their own duration — with a
  `reveal HD` toggle for the analysis view; and the approach ring starts at
  four times the radius as the game draws it, not 3.4.
- Slider parts on the wire, and the body finished properly. Each slider now
  carries its ticks, repeats and tail with positions, aligned by index with a
  per-part outcome the simulator was computing and discarding — so upcoming
  ticks draw as the game's own dots and vanish as the ball passes, while a
  part that dropped tracking stays marked in the miss colour where it
  happened, including the dropped tail that is where most sliderbreaks live
  and the one place the game shows nothing. Body ends are rounded with caps
  drawn inside the same depth-tested pass as the body, so they resolve into
  one flat coat instead of compositing a seam over the ribbon.
- Slider motion, verified against the real pipeline and then made right. A
  genuine `.osu` and `.osr` were driven through `judge` and the real wire
  encoders and played back under frame capture, so ball travel, the
  declared-length trim, the repeat arrow and snaking are measured facts
  rather than impressions from fixtures. Fixed on the way: the ball now
  interpolates between path samples instead of snapping to the five-pixel
  grid, the body snakes out of the head across the first half of the preempt
  with ticks appearing as it reaches them, and the HUD counts a judgement
  when it is decided — a slider at its end, not while the ball is still
  travelling.
- The player shows the play as it stands at the playhead. Exact running
  accuracy, unstable rate and judgement counts — every judgement at or
  before the clock, nothing projected, and deliberately no running combo
  because reconstructing one exactly needs slider parts the wire does not
  carry. The game's accuracy meter, rebuilt from recorded data: the last
  dozen hits as ticks on the judgement-window scale with a marker at their
  mean. `[` and `]` mark an A–B loop the clock snaps back through, drawn on
  the timeline and dismissible from the transport. And the map's own
  background sits dimmed under the playfield, served read-only from a path
  resolved at startup (`/api/replays/{name}/background`) — the one response
  read from disk at request time, for a file the request cannot choose.
- The player page gains a keyboard panel, computed exactly from the recorded
  frames: every wait between consecutive taps plotted across the recording in
  real milliseconds with its 1/4-snap BPM equivalent, per-key press counts and
  median holds, the fastest eight-tap stretch, and how often the play changed
  hands. Nothing is estimated — each number is a count or the difference of two
  recorded timestamps — and the panel states the one limit that matters: the
  recording samples at the render framerate, so an edge lands up to one sample
  gap late and a wait carries one at each end.
- Playback is regression-tested end to end. A map and a replay are generated at
  test time, served by the real server on a real socket, played in a real
  browser, and asserted on by counting the pixels that moved between two seeks
  and the pixels that moved between two captures of the same paused frame — the
  only kind of test that could have caught a clock at the wrong speed or a ball
  on a five-pixel grid. It runs opt-in (`pytest -m e2e`) and in a new
  `e2e-playback` CI job, which also type-checks and builds the web app in CI
  for the first time.
- The collect journal records the settings behind each fingerprint. A digest
  says two configurations differ but not in what, and `(old, new)` is the whole
  content of a prediction — so `forge collect` writes a snapshot line whenever
  it records replays under values not already on file. Replay lines are
  unchanged, and a record of a kind a reader does not know is skipped rather
  than fatal, so old journals, mixed journals and journals from a later version
  all still parse. Fix-forward only: what changed across a boundary recorded
  before snapshots existed is unrecoverable, and `forge diagnose` says exactly
  that instead of reporting an empty diff as "nothing but the offset changed".
- `osuforge.analysis.verify` — did the change actually help. An offset
  recommendation is a falsifiable prediction (move the offset by +5 ms and the
  mean hit error moves by -5 ms), and this scores a settings boundary against
  it: confirmed, partial when the direction is right and the size is not,
  contradicted when it moved the wrong way, unchanged when the interval on the
  difference includes zero, and no verdict at all when either side is thinner
  than five replays in two sessions. Contradicted is the reason it exists — a
  tool that reports success whichever way the number moved launders a wrong
  recommendation into evidence for itself. `forge diagnose --all-epochs` builds
  the boundary from the journal's snapshots, prints the verdict under the
  progress block, and carries it in `--json`.
- The served corpus panel shows that verdict too — the same confirmed, partial,
  contradicted, unchanged or insufficient the command prints, reached by the
  command's own code rather than a second copy of it. The two screens read one
  journal and one corpus, and only one of them could say a change went the wrong
  way; a progress split shown without the verdict is a screen where a
  recommendation cannot fail. `forge serve` reads the journal's settings
  snapshots at startup beside the epochs it already read, the payload carries
  the whole record under `progress.verification`, and the panel prints the
  verdict, the predicted-against-observed move and the reason — deliberately
  with no interval of its own. Progress and verification each draw their own
  bootstrap over the same difference and disagree in the third decimal when that
  route wins, so the interval stays where it already was, once, on the shift.
- `osuforge.probes.audio` — the audio endpoint, read without touching the
  stream. Which device Windows defaults to and what it is plugged into, the
  shared mixer's format, how often the engine hands the driver a buffer, and
  whether the driver offers any shared period below its default one. Nothing
  here initialises an audio client: the two calls that would report a buffer
  size need `Initialize`, which puts a stream in the session, and a probe that
  claims to be read-only has no business doing that while osu! is playing.
  What is left is one term of the audio path, and the report says so rather
  than calling it latency — osu!'s own buffering is inside BASS inside the
  game, and the driver, the DAC and any effects chain add their own.
- Three audio rules, the first users of the audio category. Compatibility mode
  being on is reported as a fact about the config and as the reason an offset
  measured before turning it off would not survive turning it off; what the
  setting does to the output path is carried as the community's account,
  labelled as one. A configured device name that matches nothing about the
  default endpoint is shown beside it without a verdict, because the
  comparison is text against a localized, synthesized name and osu-forge reads
  only the default endpoint rather than the whole list. And a driver whose
  shortest shared engine period is its default one is reported as the offer it
  is, with nothing to do about it. None of the three recommends a change.
- The map's own audio travels the chain the background already did, because the
  viewer had no way to reach it: the parser never read `AudioFilename`, so no
  layer below the page had ever seen the name. It is read in `[General]` beside
  the other keys there rather than by a second pass over the raw sections — the
  argument that keeps the background out of a second reader is the same one, and
  two parsers that disagree about which file a map plays is a worse bug than the
  one being fixed. The name reaches the page in the header, the payload carries
  the resolved path with its existence checked at prepare time the way the
  backdrop is, and `/api/replays/{name}/audio` serves the file read from disk at
  request time with `.mp3`, `.ogg` and `.wav` mapped to their kinds. The whole
  track in one response and no range requests: the page fetches it into a blob
  before it plays anything. The policy gains `media-src 'self' blob:`, without
  which the track downloads and is then refused — silence that reads as a play
  with no song rather than as anything failing.
- The viewer plays that track, against the clock it already keeps. Map time and
  audio time are one timeline in osu! — a hit object's time is a timestamp into
  the song — so the element's position is the clock the canvases are drawn from
  and nothing else. Its rate is the mod rate times the chosen speed, because the
  clock advances by both, and pitch is deliberately not preserved: Double Time
  raises it in the game, and a track shifted back down to concert pitch is not
  the sound the play was made against. The position is written on a seek, where
  the size of the jump is known, and otherwise only once the two clocks have
  walked 50 ms apart — never per frame, because assigning `currentTime` restarts
  decoding and would buy a permanent stutter to fix a mismatch under the
  threshold at which anyone notices one. The download is no longer part of
  loading a replay: it happens after the field is on screen, so the picture
  stops waiting on several megabytes it cannot draw anything from, and the
  object URL is now owned by something that revokes it rather than pinning one
  decoded track per replay browsed. A map that names no audio, a fetch that
  fails, a download still in flight and an autoplay the browser refuses are all
  the same silent replay — the fallback is silence, never a viewer that broke
  because a song did not turn up. There is a mute, and it remembers nothing.
- Dependabot watches `web/`'s npm dependencies, on the same weekly schedule the
  cargo and pip blocks use. `playwright-core` is held out of automatic bumps
  entirely, patch releases included: its version has to equal the browser build
  the end-to-end job installs, Dependabot cannot edit a workflow from an npm PR,
  and a bump on one side alone is a red job on a PR that looks like a dependency
  chore. It moves by hand, in the commit that moves `ci.yml`. The license gate
  has no npm job, so the file now says which ecosystems it actually covers
  rather than implying all of them.

### Changed
- The served page is a dashboard of four, and the front door is no longer the
  replay viewer wearing `/`. There was no main page: opening the tool put a
  player inside one replay with a corpus hidden behind a hash fragment, so the
  two questions someone actually arrives with — is anything wrong, and what
  happened last — could only be answered by loading a replay first. `/` is now
  an overview built from the corpus answer and the newest play; `/replays/` is
  the viewer, unchanged in behaviour and still one island, because the clock,
  the track, the selection and the reviewer's keyboard all die on a navigation
  and everything a review needs has to be reachable without one; `/corpus/` is
  the corpus panel given a page instead of a view toggle; `/live/` is the plays
  that have finished since that document was opened, which is the only genuinely
  live thing this server has — the socket — and the page says so rather than
  filling itself from the replay listing under a heading claiming those plays
  just happened. The two addresses the single-page version answered are
  honoured: `#corpus` and `#r=<replay>` on `/` are sent to the pages that took
  them over, once, because an OBS scene pointed at one of them was told it could
  stay there. `/doctor/` is deliberately not among them — the findings exist
  only behind `forge doctor` on the command line and there is no endpoint to
  read them from, and a route that renders nothing is worse than a route that
  does not exist yet.
- The renderer, the wire protocol and every analysis panel are untouched by that
  split. What moved is which document mounts them, which is the whole reason
  this shape was chosen over a rewrite. What the pages could not share, they now
  share by name rather than by copy: the metric tile and the banner that were
  byte-identical in two panels, the verification block that was private to the
  corpus panel and is wanted by three pages, the fetch-and-watch that every page
  does the same way, and the failure sentence, which now replaces the panel
  instead of the whole page — a rejected token is fixed by restarting
  `forge serve`, and a page that also took the navigation away would leave
  nowhere to go.
- The charts draw against one set of shared constants instead of a copy each.
  Three files declared a chart width of 720, three declared a grid colour, three
  declared a muted ink, and the one accent went by three different names; the
  axis label was written out six times across them, and the tooltip's class list
  was byte-identical in three. None of them disagreed, which is the only interesting
  thing about them: three copies of a decision agree until one of them is
  edited. The linear scale, the two nice-number helpers and the four judgement
  colours move with them. The translator's function type was written out in
  seven props interfaces and is now named once. Nothing about any picture
  changes; this is the same geometry, said in one place.
- `web/src/pages/ping.astro` is gone, as its own docstring said it would be. It
  existed so a test could check "the server can serve more than one page"
  against a real build rather than a fixture, and the real pages do that better:
  the built-site tests point at `/corpus/` now. One of them had to be rewritten
  rather than repointed. It asserted the second page carried no inline script
  hash at all, which was only true because a page with no island ships no
  hydration script; every real page ships one, and on this build every page's
  scripts hash identically because Astro puts an island's identity in an element
  attribute rather than in the script. So it asserts the half a real build can
  show — that a page's policy names the scripts that page ships, all of them and
  nothing besides — and the hand-written pages next door go on covering the half
  it cannot, including the one a real build cannot show at all: a page that ships
  no inline script is sent no hash.
- The content-hashed bundles under `/_astro/` may be cached; everything else is
  still `no-store`. The single-page version paid for 180 kB of JavaScript once,
  and a dashboard was paying for it again on every navigation between its four
  pages. Their URLs carry a hash of their contents, so a browser holding one is
  holding the version it asked for and a rebuild is a new URL rather than a stale
  answer. Nothing else is relaxed: a page has this run's token substituted into
  its HTML, an API answer describes what the server knows at the moment it is
  asked, and a 404 under the prefix is not cached either, since a missing bundle
  kept for a year stays missing after it has arrived.
- The server serves pages, plural. `Site` held one `index` string and
  `load_site` read one `index.html`; every other file in the build went into the
  asset table, so a second page would have been served from there as the bytes on
  disk with `__TOKEN__` still in it. That page loads, looks finished, and is
  answered 401 on every call it makes, with nothing on either side saying why —
  the substitution is what the placeholder is for and the asset table has no
  reason to know about it. Pages are keyed now by the URL a browser asks for,
  which for a directory-format build is both `/ping/` and `/ping`, since a link
  written one way is followed the other by some browsers and a redirect to
  correct a slash is a round trip that buys nothing. Every `.html` is routed into
  that table and out of the assets one on its extension rather than by name,
  because a list of known page names is a thing the next page can be missing
  from. The token exemption is membership of that table, and a page or asset
  claiming a URL under `/api` or `/ws` is refused when the app is assembled
  rather than filtered per request, so a page cannot be added that moves data out
  from behind the token. `web/astro.config.mjs` states `build.format` and
  `trailingSlash` instead of inheriting them: the file shape the build emits and
  the URL shape the server keys on are two halves of one decision, and a default
  changing under either side would move every page's URL with nothing in the
  repository saying so. `web/src/pages/ping.astro` is the second page the tests
  check a real build against, since a hand-written fixture cannot notice that
  shape moving; it goes when the dashboard has real pages to put there.
- The Content-Security-Policy is computed per page rather than once for the site.
  Astro emits its own inline hydration script per page and the policy names the
  exact hashes, so one policy for a multipage site would have to name the union
  of them — which is also permission for page A's inline script to run inside
  page B, where it was never shipped. An injected body that is already allowed is
  most of what naming hashes was there to prevent, so the union is the one shape
  the hashes must not take. A path holding no page is sent a policy naming no
  inline script at all: an asset response and a 404 have no document for one to
  run in.
- The playback end-to-end test drives `forge serve` itself, and the test-only
  launcher that used to stand in for it is gone. The launcher existed only
  because the command line could not be imported off Windows; kept past that it
  would be a second server bootstrap that nothing runs and that has already
  drifted on fifteen behaviours, so a green run could mean the real thing is
  broken. A green run now says the token-placeholder guard, the osu!.db read,
  the analysis cache, the journal, the corpus recompute and the replay watcher
  all still work, which is most of what starting up is.
- Verification puts the same interval on the difference that the progress panel
  does — the wider of a Welch-corrected cluster route and the hierarchical
  bootstrap — where it used to report the bootstrap alone. On one corpus the two
  blocks printed "no detectable change, the interval on the difference includes
  zero (-11.1 to +1.1 ms)" and "confirmed (95% CI -7.03 to -2.96)" three lines
  apart, each internally correct and disagreeing about the same difference. A
  verdict that survives the wider interval is worth acting on and one that only
  survives the narrower is not, so expect a boundary with two sessions a side to
  read UNCHANGED where it used to read CONFIRMED, until there are more evenings
  behind it. The Welch construction now lives in one place,
  `analysis.clustering.welch_difference_ci`, because two constructions of one
  quantity are two numbers free to disagree about it.
- `forge serve` reads `osu!.db` for per-beatmap local offsets, with a `--db`
  flag mirroring `forge diagnose`'s. Until now the served corpus assumed every
  map sat at zero, so replays of a map the player had nudged in game were
  pooled into the bias on one road and excluded on the other — the same folder
  answered twice, differently. The served page now excludes them by the same
  policy, names them in the excluded list with the same reason, and carries
  `local_offsets_known` so a corpus that assumed zero says so rather than
  looking like one that checked. A caveat sits on the panel when it did.
- The served corpus gains the arrival axis: how much of the mean hit error is
  where the cursor was when the object came due, and how much is the wait after
  it had already arrived — the part no offset changes. It reaches the page by
  the code `forge diagnose` uses, not a second implementation of it.
- The arrival split now answers to the same inclusion policy the bias does.
  It was pooled over every gathered replay, including ones excluded for a local
  offset, for missing more than a tenth of the map, or for being too short to
  say anything — so its `approach` term carried shifts the bias beside it had
  refused. Two numbers in one report described different corpora. Expect
  `approach` and the arrival verdict to move on a corpus with exclusions.
- The analysis cache schema is at 2, because a play's reduction now carries its
  arrival pairs and they cannot be recovered from an older row — the cursor
  frames they need were discarded when the play was reduced. Every cached row
  is dropped once, and history re-analyses at `--backfill` speed over the next
  few starts. Nothing is lost: the replay files are still on disk.
- `forge serve` and `forge diagnose` now read a play with no reported hits the
  same way — as fully missed, which is the reading that excludes it rather than
  the one that pools a play whose judgement counts say nothing.
- Integration tests are deselected by default and require an environment
  variable pointing at real data. A plain `pytest` run can no longer read
  anyone's osu! install.
- `panic = "abort"` moved off the shared release profile onto a dedicated
  `engine-release` profile. PyO3 turns a Rust panic into a Python exception by
  catching the unwind, so under `abort` an unexpected panic would take the whole
  interpreter down with no traceback. The engine still gets the smaller binary.

### Fixed
- A link to a replay opened a different replay. `/replays/` mirrors the open
  play into the address, and that effect ran once on mount with nothing selected
  yet — `null` there means "not decided", but it was written out as an address
  with no fragment, which stripped the `#r=` naming the play to open. The listing
  that reads the fragment resolves later, by which time it was gone, so every
  deep link fell back to whichever play was newest when the server started. The
  page now reads the address on the way into that fetch rather than out of
  `location` when it answers, and writes nothing until there is a selection to
  write. This is the whole point of the front page: every card on `/` and
  `/live/` is such a link.
- The overview named the wrong play as the last one read. It took the first
  entry of `/api/replays`, which is the newest only for the startup scan — every
  play that finishes while the server runs is appended to the end of the table
  that endpoint walks, so after the second play of an evening the front page was
  naming a play from before the session began. The order is not the endpoint's
  to change, since its other callers read it for its contents; the page decides
  from `analysis.played_at` instead, which is the replay's own timestamp. A play
  the simulation could not reproduce carries no timestamp and so is never picked
  as the newest — nothing in its header says when it happened, and choosing it
  would be a guess dressed as an answer.
- The reasons a corpus may not recommend anything are shown one per line instead
  of joined with a space. Each is a full sentence written by a different check,
  with its own full stops inside it and no capital at the front, so run together
  they read as one broken sentence rather than as two findings.
- The check deciding which requests need no token asked a different question
  than the router did. It read `request.url.path`, which Starlette rebuilds from
  the scope as a string and splits again — so a percent-encoded `?` or `#` in
  the path decodes into a delimiter and everything after it is dropped.
  `/%3F../api/replays` therefore read as `/` to the exemption, which found it in
  the page table and waved it through, while the router went on matching the
  whole path. Nothing reachable came of it, because no handler answers that
  spelling, but a request was being admitted on another URL's exemption. Both
  read the routed path now, so there is one answer to which path a request is
  for. The namespace check that refuses a page under `/api` or `/ws` also folds
  case, which the router does not: it is what decides who is served without a
  token, and it should not rest on a second component's case sensitivity to
  stay true.
- A beatmap could name a file outside its own folder and have the server read it.
  The background and the audio track are both named by a line in the `.osu`, which
  is a document downloaded from a stranger, and the name was joined to the map's
  folder and then only checked for being a file. Joining discards the folder when
  the name is absolute, so `AudioFilename: C:\Windows\win.ini` names that file and
  passes the check; enough `..` gets to the same place. The endpoint would then
  read it and hand it to the page. Both assets go through one resolution now that
  requires the answer to still be inside the beatmap's folder and names nothing
  when it is not — the way the module says it wants path traversal handled, as
  something with no mechanism rather than something a filter has to catch. The
  page could not have sent what it received anywhere, since `connect-src` is
  `'self'`, but the read was real.
- The corpus panel could name one era while the boundary under it described
  another. Which epoch is current is read off the newest entry of a list, and the
  same list decides the boundary — but the list was sorted by when a replay was
  played and the boundary detection had since been taught to break ties on the
  file name. Two replays sharing a timestamp then left "newest" to whichever the
  caller happened to hand over last. Both sort alike now, which is what
  `fill_epochs` says in its own docstring has to be true.
- Nothing in the playfield faded in unless Hidden was on. The fade schedule was
  written for the mod and returned a flat opacity for every play without it, so
  every object inside the preempt window appeared at full strength at once, all
  equally loud, with nothing saying which was due next. The game fades every
  object in over `min(TimeFadeIn, TimePreempt)` whatever the mods are, and the
  approach circle over twice that window — a ring far out faint and one about to
  land solid, which is the whole timing signal. Objects fade out again over the
  stay they already had, because fading in but not out left them vanishing at
  full strength one frame after their end beside neighbours still arriving.
- The spinner was drawn as a hairline. Its ring's width was a fixed share of a
  radius that shrinks to a fifth of where it started, so it thinned to about a
  pixel exactly when how much is left is worth reading. The width is fixed in
  osu! pixels now, and does not follow Circle Size, which does not touch a
  spinner in the game either.
- A combo numeral sat on its object's head for the object's whole life, which on
  a long slider is a number parked on top of the thing worth watching. It leaves
  when the head is struck now, timed off the offset the replay recorded rather
  than off when the object was due — and a miss, which records no offset because
  there was no press to measure, keeps its numeral until the object goes.
- The progress panel and the verification verdict still put two intervals on one
  difference. Moving the Welch route into `clustering.welch_difference_ci` made
  the two agree wherever that route was the wider of the pair, which read as the
  problem being solved; the bootstrap route underneath was never shared. It was
  written twice, over 10,000 resamples on one road and 4,000 on the other, and
  out of two generator topologies — two independently seeded streams differenced
  elementwise against both sides alternating down one stream. Over 24 corpora
  carrying a real 7 ms move, three to six sessions a side, with the same seed and
  resample count handed to both, the two blocks disagreed about the same boundary
  in 21 of them; the three that agreed were the three the cluster route won on
  both roads. In the sharpest case the two roads selected different routes
  outright, each having weighed its own bootstrap width against the shared
  cluster one. There is one construction now, `clustering.difference_interval`,
  and one resample count behind both blocks.
- `progress` could answer one folder two ways. It ordered on when a replay was
  played and nothing else, while the verification beside it had already been
  taught to break ties on the file name. `sorted` is stable, so replays sharing a
  timestamp kept whatever order the caller held them in — alphabetical on the
  command line, chronological on the panel — and the bootstrap resamples out of
  those lists with one generator. One corpus whose replays share a stamp within
  each session, shuffled twelve times, produced twelve different intervals. It
  imposes the same tie-break the verification does now.
- `forge diagnose` and the served panel could reach different verdicts about one
  journal. Verification split the corpus in whatever order its caller happened to
  hold it — alphabetical by replay file name on the command line, chronological
  on the panel — and the bootstrap route inside it resamples sessions and replays
  out of those lists with one generator, so two orders of six evenings walked the
  generator differently and landed on different intervals: `confirmed` on one
  road and `unchanged` on the other, for a corpus that had not changed. The
  entries are ordered inside the verification now, by when they were played and
  then by file name, so two replays sharing a timestamp cannot hand the decision
  back to the caller either.
- Every beatmap background was blocked after a successful download. The endpoint
  wants the session token, which cannot ride on an `<img src>`, so the page
  fetches the image with the header and hands the response to the element as an
  object URL — and the policy allowed `img-src 'self' data:` only, so the browser
  refused to paint what it had just fetched. On the page that reads as a map with
  no background rather than as anything failing. The policy admits `blob:` now,
  which grants nothing further: only same-origin script can mint one, and
  `script-src` allows no script this page did not ship with.
- The corpus panel threw away the verification whenever the current-epoch corpus
  was too thin to diagnose. The diagnosis wants ten replays across three sessions
  and the comparison wants five across two a side, so the window where the panel
  refused was exactly the window just after a settings change — where a
  CONTRADICTED verdict telling the player to put the change back is the most
  useful sentence on the page, computed, sent over the wire and dropped by the
  renderer. The refusal now stands in for the diagnosis and not for the verdict,
  which is what `forge diagnose --json` already did.
- The compatibility-mode finding said "Your offset was measured on this audio
  path and belongs to it", which the rule cannot know: it reads one config key
  and has seen no replay, no journal and no offset. For the player who turned the
  setting on ten minutes ago because of crackling it was backwards — that offset
  was measured on the other path, and the setting they just changed is why it no
  longer applies. It now says any offset tuned with the setting on belongs to
  this audio path, and that turning it off splits the replay history there, which
  is true whenever the setting was turned on.
- The release checksum step would have thrown on the first tag ever pushed. It
  piped `Get-ChildItem dist` into `Out-File dist\SHA256SUMS.txt`, and PowerShell
  starts every cmdlet in a pipeline before the first one emits — so the output
  file already existed, empty and held open, when the enumeration reached it, and
  `Get-FileHash` cannot read a file another handle has open. Under the `stop`
  preference GitHub sets on every `pwsh` step the run exits non-zero there, and
  `Publish release` never runs: no release, no assets, on a workflow whose first
  execution is the first `v*` tag. The lines are collected before anything is
  written now, and `SHA256SUMS.txt` is skipped by name so a second run says what
  the first one did.
- Release signing asked for the uploaded artifact by an id the artifact did not
  have yet. The signing step read `steps.upload.outputs.artifact-id` from a step
  that ran two steps later, and a step output read before its step runs is the
  empty string — on a required input, so the first release built with signing
  turned on would have failed instead of signing. Nothing has run into it,
  because signing is gated off until the certificate exists. The upload now sits
  directly after staging, and the workflow grants itself `actions: read` so the
  signing action can download what the run just uploaded. Checksums still come
  after signing: signing rewrites the executable, so a hash taken before it
  would describe a file nobody ships.
- `osuforge.cli` could not be imported anywhere but Windows. Four probe modules
  built their `ctypes` structures from `ctypes.wintypes` while the module was
  still loading, and that module does not exist off Windows — so the rule
  engine, and with it the whole command line, refused at import rather than at
  the measurement. Each module gates its Windows plumbing on the platform now.
  The refusal happens where it always did, inside each probe, as a skip that
  says "Windows-only" and can be read in the report.
- The cluster route divided by zero when both sides of a boundary had sessions
  whose means all landed on the side mean exactly. Rare, but it cost the whole
  comparison rather than the one route, and the corpus that triggers it is
  merely unusually tidy. The route now reports itself as not holding and the
  bootstrap answers alone.
- `osuforge` shipped without a `py.typed` marker, so every consumer saw it as
  untyped however strictly it checks itself.
- The Python license gate reported success when `pip-licenses` had failed and
  returned nothing. It now fails on a non-zero exit, unparseable output, an
  empty package list, or a missing canary package, and splits compound license
  expressions so `MIT AND GPL-3.0` can no longer pass on the strength of the MIT.
- `forge diagnose` and `forge serve` looked for `osu!.db` under `%LOCALAPPDATA%`
  even when they had just found the install somewhere else. osu!stable is
  portable and keeps its index beside `osu!.exe`, so on any install off that
  path the offsets went unread, every beatmap was assumed to carry none, and the
  report blamed the Songs folder — the one ingredient that was right. Both now
  default the path from the install the config was found in, with `--db` still
  overriding.

[Unreleased]: https://github.com/osu-forge/osu-forge/commits/main
