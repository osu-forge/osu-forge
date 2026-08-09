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

export interface Client {
  list(): Promise<{ name: string; header: ReplayHeader }[]>;
  load(name: string): Promise<{ header: ReplayHeader; samples: Samples; paths: Float32Array }>;
}

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
      const { replays } = await json<{ replays: { name: string; header: ReplayHeader }[] }>(
        "/api/replays",
      );
      return replays;
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
