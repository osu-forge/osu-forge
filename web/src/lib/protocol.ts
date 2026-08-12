/**
 * The wire format, decoded once.
 *
 * The server sends cursor samples as a packed buffer rather than JSON, and the
 * layout is described in `osuforge/server/protocol.py`. This is the other half
 * of that contract, and the version is checked rather than assumed — a client
 * reading a newer schema would draw sliders as circles and look merely wrong.
 *
 * ## Why the samples are de-interleaved here
 *
 * A sample is thirteen bytes, which is not a multiple of four, so a typed-array
 * view over the buffer is impossible: `Int32Array` requires four-byte alignment
 * and every second sample would be misaligned. The choice is between reading
 * through a `DataView` on every draw or paying once to split the buffer into
 * four aligned arrays. Drawing happens sixty times a second and loading happens
 * once, so it is paid once.
 *
 * The thirteen-byte stride is not a mistake to be fixed on the server. Padding
 * it to sixteen would add three wasted bytes per sample — about 120 KB on a
 * long replay — to save a loop that runs once.
 */

export const SUPPORTED_SCHEMA = 2;

export type ObjectKind = "circle" | "slider" | "spinner";

export interface HitObject {
  /** Map time in milliseconds. */
  t: number;
  /** When it stops accepting input; equal to `t` for a circle. */
  end: number;
  x: number;
  y: number;
  kind: ObjectKind;
  combo: boolean;
  /** `[offset, count]` into the slider path buffer, in points. Sliders only. */
  p?: [number, number];
  /** Traversals of the path. 1 means no repeat. Sliders only. */
  slides?: number;
  /** Scoring parts as `[time, x, y, kind]` — kind 0 tick, 1 repeat, 2 tail —
   *  in time order, aligned by index with the judgement's `parts`. Sliders
   *  only. */
  parts?: [number, number, number, number][];
}

export interface Judgement {
  /** 0 miss, 1 fifty, 2 hundred, 3 three hundred. */
  grade: number;
  /** Press minus object time, map milliseconds. `null` for a miss. */
  error: number | null;
  /** Distance from the centre at the press, in radii. `null` for a miss. */
  aim: number | null;
  /** Whether each scoring part held tracking, 1/0, aligned with the object's
   *  `parts`. `null` for anything not a slider. */
  parts: number[] | null;
}

export interface Finding {
  feature: string;
  label: string;
  objects: number;
  /** Share of the play's objects this group holds, 0 to 1. */
  share_of_objects: number;
  /** Share of the play's total accuracy shortfall that fell here.
   *  Compared against `share_of_objects`: a group holding 8% of the objects
   *  and 42% of the loss is doing five times its share of the damage; one
   *  holding 8% of both is doing exactly its share and says nothing. */
  share_of_loss: number;
  accuracy: number;
}

export interface Break {
  /** Map time, milliseconds. */
  time: number;
  kind: "circle" | "slider" | "spinner";
  /** 0 miss, 1 fifty, 2 hundred, 3 three hundred. */
  grade: number;
  /** The combo that ended here. */
  combo_lost: number;
  /** Timing error in map milliseconds, negative early. `null` when there was
   *  no press at all — which is itself the answer to why it broke. */
  error: number | null;
  /** Distance from the centre at the press, in radii. */
  aim_error: number | null;
  parts_collected: number | null;
  parts_total: number | null;
}

export interface Analysis {
  accuracy: number;
  /** Real milliseconds, early negative. `null` when there were no usable hits. */
  mean_error: number | null;
  /** Ten times the standard deviation, as the score screen reports it. */
  unstable_rate: number | null;
  hits: number;
  errors: number[];
  aim_errors: number[];
  /** Share of keyboard presses on the first key. 0.5 is even. */
  key_balance: number | null;
  presses: Record<string, number>;
  /** Whether the simulation reproduced the game's own judgement counts. A play
   *  it did not reproduce still has an unstable rate, and that rate describes
   *  something other than what happened. */
  agreement: string;
  agreement_reason: string;
  usable: boolean;
  max_combo: number;
  reported_max_combo: number;
  sliderbreaks: number;
  /** Why the slider-break count is being withheld, if it is. The detection is
   *  known wrong against the header's max combo, so it is hidden with its
   *  reason rather than shown as though it were sound. */
  combo_caveat: string | null;
  bpm: number;
  length_ms: number;
  object_count: number;
  played_at: string;
  findings: Finding[];
  breaks: Break[];
}

export interface ReplayHeader {
  schema_version: number;
  replay: string;
  rate: number;
  /** The raw osu! mod bitmask the play was made with. The `beatmap` block
   *  already carries the modded values; this is what lets the page name them
   *  and render what the player saw. */
  mods: number;
  sample_bytes: number;
  sample_count: number;
  path_point_bytes: number;
  path_points: number;
  beatmap: {
    artist: string;
    title: string;
    version: string;
    radius: number;
    preempt: number;
    circle_size: number;
    approach_rate: number;
    overall_difficulty: number;
    background: string | null;
    /** The audio track's file name, or `null` when the map names none. A name,
     *  not a URL: it says only whether there is a track to ask the server for. */
    audio: string | null;
  };
  /** `null` when the play could not be analysed, so a page can tell that from
   *  "analysed and found nothing". */
  analysis: Analysis | null;
  /** Rounded 300/100/50 half-widths, map milliseconds. */
  windows: [number, number, number];
  counts: { "300": number; "100": number; "50": number; miss: number };
  objects: HitObject[];
  judgements: Judgement[];
}

export interface Samples {
  /** Map time, milliseconds. Ascending, so a seek is a binary search. */
  t: Int32Array;
  x: Float32Array;
  y: Float32Array;
  /** Raw four-bit key mask. Non-zero means something was held. */
  keys: Uint8Array;
}

export interface CorpusAxis {
  /** "timing bias", "timing spread", "arrival". */
  name: string;
  /** What to do, or that there is nothing to do. Both are outcomes. */
  verdict: string;
  /** Whether a setting change follows. False is the more common answer, and a
   *  page that only renders true ones would say "change this" when it should
   *  not. */
  actionable: boolean;
  detail: string;
  evidence: string[];
}

export interface CorpusBeatmap {
  name: string;
  /** Real milliseconds, early negative. `null` where the estimator refused. */
  mean: number | null;
  ci_low: number | null;
  ci_high: number | null;
  replays: number;
  /** Whether this map's own interval excludes zero — a shift it carries by
   *  itself rather than one borrowed from the pool. */
  excludes_zero: boolean;
}

export interface ProgressPoint {
  session: number;
  started_at: string;
  replays: number;
  hits: number;
  /** Real milliseconds, early negative. A description of one sitting — no
   *  interval is claimed on a single session. */
  mean_error: number | null;
  spread_ms: number | null;
}

export interface ProgressSide {
  mean: number | null;
  ci_low: number | null;
  ci_high: number | null;
  replays: number;
  sessions: number;
  hits: number;
}

export interface ProgressShift {
  before: ProgressSide;
  after: ProgressSide;
  /** `after − before`, real milliseconds. Positive means the bias moved later. */
  difference: number | null;
  ci_low: number | null;
  ci_high: number | null;
  ci_source: string;
  spread_before: number | null;
  spread_after: number | null;
  moved: boolean;
  toward_zero: boolean;
  verdict: string;
}

export interface CorpusVerification {
  /** `confirmed`, `partial`, `contradicted`, `unchanged` or `insufficient`.
   *  The server's own word, rendered as it arrives. */
  verdict: string;
  before_mean: number | null;
  after_mean: number | null;
  difference: number | null;
  /** The interval on the difference. Carried for a JSON reader; the panel
   *  deliberately does not print it — see the note in `Corpus.tsx`. */
  ci_low: number | null;
  ci_high: number | null;
  /** How far the mean was predicted to move. `null` when the boundary changed
   *  something other than the offset, or when what it changed was never
   *  written down: it made no prediction and must not be scored as if it had. */
  predicted: number | null;
  reason: string;
}

export interface CorpusProgress {
  points: ProgressPoint[];
  /** `settings` when the collect journal recorded a change; `midpoint` when
   *  the split is only the middle of the sessions. */
  boundary: { kind: string; at: string; label: string } | null;
  shift: ProgressShift | null;
  insufficient: string | null;
  /** Whether the change did what it predicted. `null` on a midpoint split,
   *  which is a description of time and predicts nothing. */
  verification: CorpusVerification | null;
}

export interface Corpus {
  replays: number;
  sessions: number;
  hits: number;
  summary: string;
  /** Why the corpus cannot answer yet, or `null` when it can. "Not enough
   *  data" and "nothing wrong" are opposite conclusions that look identical
   *  as an empty panel, so this is a sentence rather than an absent key. */
  insufficient: string | null;
  /** How many independent hits the corpus is worth after clustering. */
  effective_hits: number | null;
  design_effect: number | null;
  bias: {
    mean: number | null;
    ci_low: number | null;
    ci_high: number | null;
    ci_source: string;
  } | null;
  unstable_rate: number | null;
  spread_ms: number | null;
  axes: CorpusAxis[];
  /** Replay name to the reason it is not in this answer — the simulation
   *  screen and the inclusion policy both land here, each with its own words. */
  excluded: Record<string, string>;
  /** Whether per-beatmap local offsets were read from osu!.db, or assumed to
   *  be zero everywhere. The two corpora look identical in every other number
   *  here, and only one of them has excluded the maps the player nudged. */
  local_offsets_known: boolean;
  health: {
    total: number;
    usable: number;
    /** False until an independent oracle has judged these simulations. What
     *  separates a measurement from advice; the panel must not blur it. */
    may_recommend: boolean;
    blockers: string[];
    summary: string;
  };
  /** The corpus over time. `null` when the server did not compute it, which
   *  is not the same statement as "nothing has changed". */
  progress: CorpusProgress | null;
  beatmaps: {
    played: number;
    /** One sentence reading the pooled interval against the per-map ones. */
    reading: string | null;
    reported: CorpusBeatmap[];
  };
}

export class ProtocolError extends Error {}

/** Bits of the osu! mod mask, in display order. */
export const MOD_HIDDEN = 8;

const MOD_NAMES: [number, string][] = [
  [2, "EZ"],
  [1, "NF"],
  [256, "HT"],
  [MOD_HIDDEN, "HD"],
  [16, "HR"],
  [64, "DT"],
  [512, "NC"],
  [1024, "FL"],
  [32, "SD"],
  [16384, "PF"],
  [4096, "SO"],
];

/** The mods as the game abbreviates them.
 *
 * Nightcore carries the Double Time bit and Perfect carries Sudden Death's,
 * so the contained mod is dropped rather than shown twice — a Nightcore play
 * reads "NC", not "DT NC".
 */
export function modNames(mods: number): string[] {
  const names: string[] = [];
  for (const [bit, name] of MOD_NAMES) {
    if (!(mods & bit)) continue;
    if (name === "DT" && mods & 512) continue;
    if (name === "SD" && mods & 16384) continue;
    names.push(name);
  }
  return names;
}

/**
 * The game's own accuracy, from the counts every header carries.
 *
 * Recomputed rather than read off `analysis.accuracy`, because a header exists
 * for plays the simulation could not reproduce and those have no analysis at
 * all. The counts come from the replay file itself, so this is the number the
 * score screen showed whatever the simulation later made of it.
 */
export function accuracyOf(counts: ReplayHeader["counts"]): number {
  const total = counts["300"] + counts["100"] + counts["50"] + counts.miss;
  if (total === 0) return 0;
  return (300 * counts["300"] + 100 * counts["100"] + 50 * counts["50"]) / (300 * total);
}

/**
 * When the play happened, as an instant that can be compared, or `null` when
 * the header does not say.
 *
 * The timestamp is written by the analysis because that is where the replay
 * file is read, so a play the simulation could not reproduce carries no time at
 * all. That is a `null` a caller has to decide about rather than a zero to sort
 * against: sorting an unknown time as the epoch would rank the play whose
 * analysis failed as the oldest thing on the server.
 */
export function playedAt(entry: Entry): number | null {
  const at = entry.header.analysis?.played_at;
  if (at === undefined) return null;
  const parsed = Date.parse(at);
  return Number.isNaN(parsed) ? null : parsed;
}

/**
 * The most recently played of these, or `null` when none of them says when.
 *
 * Not simply the first. `/api/replays` walks the dictionary the server fills,
 * and that dictionary is newest-first only for the startup scan — every play
 * that finishes while the server runs is appended to the end of it. So the
 * first entry is the newest until the second play of the evening lands, and
 * after that it is whatever was newest when `forge serve` started. Other
 * callers read that endpoint for its whole contents rather than for an order,
 * so the ordering is not the endpoint's to change; the question is answered
 * here instead, from the timestamps every header already carries.
 *
 * A play with no analysis never wins, because nothing in its header says when
 * it was played and choosing it would be a guess dressed as an answer. Ties and
 * plays without a time keep the caller's own order, so asking twice about an
 * unchanged list gives the same play twice.
 */
export function newestPlay(entries: readonly Entry[]): Entry | null {
  let newest: Entry | null = null;
  let at = -Infinity;
  for (const entry of entries) {
    const when = playedAt(entry);
    if (when === null || when <= at) continue;
    newest = entry;
    at = when;
  }
  return newest;
}

export function decodeSamples(header: ReplayHeader, buffer: ArrayBuffer): Samples {
  const stride = header.sample_bytes;
  const expected = stride * header.sample_count;
  if (buffer.byteLength !== expected) {
    throw new ProtocolError(
      `frames buffer is ${buffer.byteLength} bytes, expected ${expected} ` +
        `(${header.sample_count} samples of ${stride})`,
    );
  }

  const view = new DataView(buffer);
  const n = header.sample_count;
  const t = new Int32Array(n);
  const x = new Float32Array(n);
  const y = new Float32Array(n);
  const keys = new Uint8Array(n);

  for (let i = 0; i < n; i++) {
    const at = i * stride;
    t[i] = view.getInt32(at, true);
    x[i] = view.getFloat32(at + 4, true);
    y[i] = view.getFloat32(at + 8, true);
    keys[i] = view.getUint8(at + 12);
  }
  return { t, x, y, keys };
}

export function decodePaths(header: ReplayHeader, buffer: ArrayBuffer): Float32Array {
  const expected = header.path_points * header.path_point_bytes;
  if (buffer.byteLength !== expected) {
    throw new ProtocolError(
      `paths buffer is ${buffer.byteLength} bytes, expected ${expected}`,
    );
  }
  // Eight-byte points are four-byte aligned, so this one is a view rather than
  // a copy — and it is the buffer the GPU wants, uploaded without touching it.
  return new Float32Array(buffer);
}

/** Index of the last sample at or before `time`. */
export function sampleAt(t: Int32Array, time: number): number {
  let low = 0;
  let high = t.length - 1;
  let found = 0;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (t[mid]! <= time) {
      found = mid;
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return found;
}

export interface Entry {
  name: string;
  header: ReplayHeader;
}

export interface Client {
  list(): Promise<Entry[]>;
  /** `backdrop` is an object URL for the map's background image, or `null` when
   *  the map names none — the caller owns revoking it when the replay is
   *  unloaded. The audio track is deliberately not here; see `song`. */
  load(name: string): Promise<{
    header: ReplayHeader;
    samples: Samples;
    paths: Float32Array;
    backdrop: string | null;
  }>;
  /** The map's audio track as an object URL, or `null` when there is nothing to
   *  play — the caller owns revoking it.
   *
   *  Its own call rather than part of `load` because it is by far the largest
   *  thing this server serves and nothing is drawn from it: a load that waited
   *  on the track would hold the field blank for the length of a download the
   *  page has no use for until playback starts. Two round trips instead of one
   *  is the price, and the field appearing is what the trip is being spent on.
   *
   *  The whole header is the argument, not a name: it carries both the replay
   *  the server knows this track by and whether the map names a track at all,
   *  so the request that would answer 404 for a map with no audio is never
   *  made, and a caller holding one loaded header cannot ask for another
   *  replay's song by accident. */
  song(header: ReplayHeader): Promise<string | null>;
  /** The corpus answer, or `null` when the server has none — a server built
   *  without a corpus, not a corpus with nothing in it. */
  corpus(): Promise<Corpus | null>;
  /** Watch for plays that finish while the page is open, and for the corpus
   *  answer changing underneath them. Returns a closer. */
  watch(onReplay: (entry: Entry) => void, onCorpus?: (corpus: Corpus) => void): () => void;
}

/** The subprotocol the token rides on. A browser cannot set a header here. */
const TOKEN_SUBPROTOCOL = "osu-forge-token.";

/**
 * Talks to the local server.
 *
 * The token comes from the page rather than from storage: it is valid for one
 * run of `forge serve` and keeping it anywhere persistent would outlive the
 * server it authenticates against.
 */
export function client(token: string, origin = ""): Client {
  const auth = { Authorization: `Bearer ${token}` };

  async function json<T>(path: string): Promise<T> {
    const response = await fetch(`${origin}${path}`, { headers: auth });
    if (response.status === 401) throw new ProtocolError("unauthorised");
    if (!response.ok) throw new ProtocolError(`${path}: ${response.status}`);
    return (await response.json()) as T;
  }

  async function bytes(path: string): Promise<ArrayBuffer> {
    const response = await fetch(`${origin}${path}`, { headers: auth });
    if (!response.ok) throw new ProtocolError(`${path}: ${response.status}`);
    return await response.arrayBuffer();
  }

  /**
   * An authenticated asset, wrapped in an object URL the caller owns.
   *
   * An object URL rather than a data URL because the token cannot ride on the
   * `src` of an element, so the page fetches with the header and hands the
   * response over as a blob. Megabytes of base64 in the DOM would also be paid
   * again on every re-render, and a song is bigger than the background.
   *
   * Never throws: a missing background or a missing track is a quieter page,
   * not a replay that failed to load.
   */
  async function objectUrl(path: string): Promise<string | null> {
    try {
      const response = await fetch(`${origin}${path}`, { headers: auth });
      return response.ok ? URL.createObjectURL(await response.blob()) : null;
    } catch {
      return null;
    }
  }

  function check(header: ReplayHeader): void {
    if (header.schema_version !== SUPPORTED_SCHEMA) {
      throw new ProtocolError(
        `server speaks schema ${header.schema_version}, this page speaks ` +
          `${SUPPORTED_SCHEMA}. Reading it anyway would draw a plausible picture ` +
          "of something other than what was played.",
      );
    }
  }

  return {
    async list() {
      const { replays } = await json<{ replays: Entry[] }>("/api/replays");
      return replays;
    },

    async corpus() {
      const response = await fetch(`${origin}/api/corpus`, { headers: auth });
      // 404 is an answer, not a failure: this server has no corpus to show,
      // and the page hides the panel rather than showing an empty one.
      if (response.status === 404) return null;
      if (response.status === 401) throw new ProtocolError("unauthorised");
      if (!response.ok) throw new ProtocolError(`/api/corpus: ${response.status}`);
      return (await response.json()) as Corpus;
    },

    watch(onReplay: (entry: Entry) => void, onCorpus?: (corpus: Corpus) => void) {
      // Reconnecting rather than giving up: the server restarting is the
      // ordinary reason this drops, and a page that silently stops updating
      // after that is the failure this whole feature exists to remove. The
      // delay backs off so a server that is gone for good is not polled hard.
      let socket: WebSocket | null = null;
      let timer: number | undefined;
      let delay = 1000;
      let closed = false;

      const connect = () => {
        if (closed) return;
        const url = new URL("/ws", location.origin);
        url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
        socket = new WebSocket(url, [`${TOKEN_SUBPROTOCOL}${token}`]);

        socket.addEventListener("open", () => {
          delay = 1000;
        });
        socket.addEventListener("message", (event) => {
          if (typeof event.data !== "string") return;
          try {
            const message = JSON.parse(event.data) as { event?: string; corpus?: Corpus } & Entry;
            if (message.event === "replay" && message.header) onReplay(message);
            else if (message.event === "corpus" && message.corpus) onCorpus?.(message.corpus);
          } catch {
            // A frame this page does not understand is not a reason to tear
            // down a connection that is otherwise working.
          }
        });
        socket.addEventListener("close", () => {
          if (closed) return;
          timer = window.setTimeout(connect, delay);
          delay = Math.min(delay * 2, 30_000);
        });
      };

      connect();
      return () => {
        closed = true;
        window.clearTimeout(timer);
        socket?.close();
      };
    },
    async load(name: string) {
      // Encoded, because a replay's name is a file name and osu! puts the map's
      // title in it. A `#` in one would cut everything after it off into a
      // fragment the server never sees, so `.../frames` would arrive as `/` and
      // answer the wrong thing; a `?` would turn the rest into a query. The
      // server reads the segment back as a dictionary key and never as a path,
      // so this is about the request arriving intact rather than about what it
      // could otherwise reach.
      const at = encodeURIComponent(name);
      const header = await json<ReplayHeader>(`/api/replays/${at}/header`);
      check(header);
      const [frames, paths, backdrop] = await Promise.all([
        bytes(`/api/replays/${at}/frames`),
        bytes(`/api/replays/${at}/paths`),
        // Asked for only when the header names one, so a map without a
        // background costs no request that would answer 404. Alongside the
        // frames rather than after them, because it is wanted the moment the
        // field appears and it is tens of kilobytes: waiting on it costs the
        // load nothing that is not already being waited for.
        header.beatmap.background ? objectUrl(`/api/replays/${at}/background`) : null,
      ]);
      return {
        header,
        samples: decodeSamples(header, frames),
        paths: decodePaths(header, paths),
        backdrop,
      };
    },

    async song(header: ReplayHeader) {
      if (!header.beatmap.audio) return null;
      return await objectUrl(`/api/replays/${encodeURIComponent(header.replay)}/audio`);
    },
  };
}
