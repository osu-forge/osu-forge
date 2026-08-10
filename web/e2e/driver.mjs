// Drives the served page in a real browser and reports what moved.
//
// Two rounds of this project shipped playback that rendered and was still
// wrong: a Double Time clock running at two thirds speed, a ball snapped to a
// five-pixel grid, sliders drawn fully formed before they were reached. None
// of it was visible to a unit test, because none of it is expressible without a
// GPU. So this measures pixels.
//
// It measures them *relatively*. There are no golden images: SwiftShader is
// deterministic within one browser build and not across them, so a committed
// screenshot would be a time bomb. What is asserted instead is motion between
// two seeks and stillness between two captures of the same paused frame.
//
// Nothing here waits on the wall clock. `Home` then N presses of `Period` makes
// map time an exact function of the key count, and two `requestAnimationFrame`
// callbacks are what "the frame has been drawn" actually means.

import { mkdir } from "node:fs/promises";
import path from "node:path";

import { chromium } from "playwright-core";

const VIEWPORT = { width: 1280, height: 800 };
const CHANNEL_TOLERANCE = 8;
const CANVAS_TIMEOUT_MS = 60_000;

function parseArgs(argv) {
  const args = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    if (!key.startsWith("--")) throw new Error(`unexpected argument: ${key}`);
    args[key.slice(2)] = argv[index + 1];
  }
  return args;
}

/** Count pixels that differ, and pixels that are not the background colour. */
async function measure(page, shots, tolerance) {
  return page.evaluate(
    async ([frames, limit]) => {
      const decode = async (encoded) => {
        const binary = atob(encoded);
        const bytes = new Uint8Array(binary.length);
        for (let index = 0; index < binary.length; index += 1) {
          bytes[index] = binary.charCodeAt(index);
        }
        const bitmap = await createImageBitmap(new Blob([bytes], { type: "image/png" }));
        const surface = new OffscreenCanvas(bitmap.width, bitmap.height);
        const context = surface.getContext("2d");
        context.drawImage(bitmap, 0, 0);
        return context.getImageData(0, 0, bitmap.width, bitmap.height);
      };

      const images = {};
      for (const [name, encoded] of Object.entries(frames)) images[name] = await decode(encoded);

      const differing = (first, second) => {
        if (first.width !== second.width || first.height !== second.height) return -1;
        const a = first.data;
        const b = second.data;
        let count = 0;
        for (let index = 0; index < a.length; index += 4) {
          const delta = Math.max(
            Math.abs(a[index] - b[index]),
            Math.abs(a[index + 1] - b[index + 1]),
            Math.abs(a[index + 2] - b[index + 2]),
          );
          if (delta > limit) count += 1;
        }
        return count;
      };

      // The top-left corner of the playfield is empty in every frame, so it is
      // the background by construction. An all-black canvas scores zero here.
      const painted = (image) => {
        const data = image.data;
        const [red, green, blue] = [data[0], data[1], data[2]];
        let count = 0;
        for (let index = 0; index < data.length; index += 4) {
          const delta = Math.max(
            Math.abs(data[index] - red),
            Math.abs(data[index + 1] - green),
            Math.abs(data[index + 2] - blue),
          );
          if (delta > limit) count += 1;
        }
        return count;
      };

      return {
        diffs: {
          start_to_mid: differing(images.s0, images.s1a),
          mid_to_end: differing(images.s1a, images.s2),
          paused_repeat: differing(images.s1a, images.s1b),
        },
        painted: Object.fromEntries(
          Object.entries(images).map(([name, image]) => [name, painted(image)]),
        ),
      };
    },
    [shots, tolerance],
  );
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.url || !args.out) throw new Error("usage: driver.mjs --url <url> --out <dir> [...]");
  const steps = Number(args.steps ?? 40);
  await mkdir(args.out, { recursive: true });

  const browser = await chromium.launch({
    executablePath: args.executable || process.env.OSU_FORGE_E2E_BROWSER || undefined,
    // Headless CI has no GPU, so WebGL2 goes through SwiftShader. Chromium
    // refuses that silently without this flag, and the page would come back
    // blank with one page error.
    args: ["--enable-unsafe-swiftshader"],
  });
  const pageerrors = [];
  try {
    const context = await browser.newContext({ viewport: VIEWPORT, deviceScaleFactor: 1 });
    const page = await context.newPage();
    page.on("pageerror", (error) => pageerrors.push(String(error)));

    // Not `networkidle`: the page opens a WebSocket and keeps it open, so the
    // network is never idle. The canvas exists only once a replay has been
    // listed, selected and decoded, which is the state worth waiting for.
    await page.goto(args.url, { waitUntil: "load" });
    const canvas = page.locator("canvas").first();
    await canvas.waitFor({ state: "visible", timeout: CANVAS_TIMEOUT_MS });
    const clip = await canvas.boundingBox();
    if (!clip) throw new Error("the playfield canvas has no box to capture");

    const settle = () =>
      page.evaluate(
        () =>
          new Promise((resolve) => {
            requestAnimationFrame(() => requestAnimationFrame(() => resolve(null)));
          }),
      );

    const shots = {};
    const capture = async (name) => {
      await settle();
      const png = await page.screenshot({ clip, path: path.join(args.out, `${name}.png`) });
      shots[name] = png.toString("base64");
    };
    const advance = async (count) => {
      for (let index = 0; index < count; index += 1) await page.keyboard.press("Period");
    };

    await page.keyboard.press("Home");
    await capture("s0");
    await advance(steps);
    await capture("s1a");
    // Same clock, captured twice. Whatever this scores is the noise floor the
    // motion numbers have to clear.
    await capture("s1b");
    await advance(steps);
    await capture("s2");

    const { diffs, painted } = await measure(page, shots, CHANNEL_TOLERANCE);
    process.stdout.write(
      `${JSON.stringify({ steps, clip, tolerance: CHANNEL_TOLERANCE, pageerrors, diffs, painted })}\n`,
    );
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(`${error && error.stack ? error.stack : String(error)}\n`);
  process.exitCode = 1;
});
