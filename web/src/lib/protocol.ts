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
}

export interface Judgement {
  /** 0 miss, 1 fifty, 2 hundred, 3 three hundred. */
  grade: number;
  /** Press minus object time, map milliseconds. `null` for a miss. */
  error: number | null;
  /** Distance from the centre at the press, in radii. `null` for a miss. */
  aim: number | null;
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

export interface CorpusProgress {
  points: ProgressPoint[];
  /** `settings` when the collect journal recorded a change; `midpoint` when
   *  the split is only the middle of the sessions. */
  boundary: { kind: string; at: string; label: string } | null;
  shift: ProgressShift | null;
  insufficient: string | null;
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
  load(name: string): Promise<{ header: ReplayHeader; samples: Samples; paths: Float32Array }>;
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
      const header = await json<ReplayHeader>(`/api/replays/${name}/header`);
      check(header);
      const [frames, paths] = await Promise.all([
        bytes(`/api/replays/${name}/frames`),
        bytes(`/api/replays/${name}/paths`),
      ]);
      return {
        header,
        samples: decodeSamples(header, frames),
        paths: decodePaths(header, paths),
      };
    },
  };
}
