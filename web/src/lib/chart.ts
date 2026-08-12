/**
 * What every chart on the page already agreed about, said once.
 *
 * The charts were drawing against their own copies: three files each declared
 * `WIDTH = 720`, three each declared a grid colour, three each declared a muted
 * ink, and the one accent was called `BAR` in the histogram, `DOT` in the key
 * panel and `ACCENT` in the progress chart. The axis label — mono, 11 px, muted
 * — was written out six times across those same three files. None of them
 * disagreed yet, which is the only interesting thing about them: three
 * copies of a decision agree until the day one of them is edited, and nothing
 * about that day tells the other two. The tokens are read from the stylesheet
 * at paint time either way, so nothing here decides a colour — it decides which
 * token each part of a chart wears, which is the thing that has to match.
 *
 * The width is shared and the height is not, deliberately. Every chart is drawn
 * into a `viewBox` of the same width and scaled to whatever column it lands in,
 * so the widths have to agree or a 720-unit tick and a 640-unit tick come out
 * different thicknesses in the same aside. Height is the chart's own business:
 * a histogram is short and a time series is not.
 */

/**
 * The view width every chart is drawn in.
 *
 * Not pixels. Each `<svg>` carries `class="block h-auto w-full"`, so this is
 * the unit the geometry is expressed in and the browser scales it to the column
 * — which is why a stroke width of 1.6 means the same weight in the wide panel
 * and the narrow aside.
 */
export const CHART_WIDTH = 720;

/** The data. One accent, so a second hue always means a second quantity. */
export const ACCENT = "var(--color-accent-breeze)";

/** Grid lines and axes, recessive on purpose: they are the ruler, not the
 *  reading. */
export const GRID = "var(--color-hairline)";

/** Axis labels and anything annotating rather than measuring. */
export const MUTED = "var(--color-mute)";

/** The page behind the chart, for ringing a mark so overlapping ones separate. */
export const SURFACE = "var(--color-canvas)";

/**
 * How an axis label is drawn.
 *
 * Mono and tabular, because a column of numbers that shifts as its digits
 * change is a column nobody can read down. Spread onto a `<text>` rather than
 * imposed by a wrapper: SVG text has no box to inherit from.
 */
export const AXIS_TEXT = {
  fontSize: 11,
  fill: MUTED,
  fontFamily: "var(--font-mono, monospace)",
} as const;

/** Four of something, indexed by grade — 0 miss, 1 fifty, 2 hundred, 3 three
 *  hundred. The order is the wire's, so a judgement indexes straight into it. */
type ByGrade<T> = readonly [T, T, T, T];

/**
 * The custom properties the four judgement colours live in.
 *
 * Private, and it is the only list of these names in the module. Both public
 * forms below are built from it, so there is nothing here for a second copy to
 * drift from and no pair of look-alike arrays to hand to the wrong consumer:
 * one of them is a list of colours and the other is a function.
 */
const JUDGEMENT_PROPERTIES: ByGrade<`--${string}`> = [
  "--color-judge-miss",
  "--color-judge-50",
  "--color-judge-100",
  "--color-judge-300",
];

/**
 * Judgement colours by grade, for anything that takes a CSS colour.
 *
 * Three charts and the in-replay overlay paint the same four judgements, and a
 * miss that is orange in one of them and red in another would read as two
 * different things happening.
 */
export const GRADE_COLOURS: ByGrade<`var(--${string})`> = [
  `var(${JUDGEMENT_PROPERTIES[0]})`,
  `var(${JUDGEMENT_PROPERTIES[1]})`,
  `var(${JUDGEMENT_PROPERTIES[2]})`,
  `var(${JUDGEMENT_PROPERTIES[3]})`,
];

/**
 * The same four resolved against a stylesheet, for a canvas.
 *
 * A canvas will not take the `var()` form: assigning an unparseable colour to
 * `fillStyle` is defined as leaving the previous one in place, so a chart drawn
 * that way is not miscoloured, it is silently painted in whatever colour ran
 * before it.
 *
 * A value and a call rather than two arrays that look alike, so the two cannot
 * be reached for interchangeably at the point of use: `GRADE_COLOURS(style)`
 * and `gradeColours[0]` are both compile errors. What the shapes do not catch
 * is a slot typed plain `string`, which either form satisfies — there is no
 * such slot today, and giving one a resolved-colour type is what would be
 * needed before that could be claimed.
 *
 * White is the fallback for a property the stylesheet does not define, which
 * means a token renamed out from under a chart shows up as a chart drawn in
 * white rather than as a chart that vanished into the background.
 */
export function gradeColours(style: CSSStyleDeclaration): ByGrade<string> {
  const read = (name: `--${string}`) => style.getPropertyValue(name).trim() || "#ffffff";
  return [
    read(JUDGEMENT_PROPERTIES[0]),
    read(JUDGEMENT_PROPERTIES[1]),
    read(JUDGEMENT_PROPERTIES[2]),
    read(JUDGEMENT_PROPERTIES[3]),
  ];
}

/**
 * A straight mapping from a data range onto a drawn one.
 *
 * `domain` and `range` are both `[from, to]` and either may run backwards,
 * which is how a value axis is inverted: `linear([high, low], [top, bottom])`
 * puts the larger number nearer the top of the picture, where a reader expects
 * it, without a subtraction written out at every call site.
 *
 * A zero-width domain divides by zero and every caller guards it — a chart of
 * one point has no scale to be drawn on, and picks its own answer for where
 * that point sits.
 */
export function linear(
  domain: readonly [number, number],
  range: readonly [number, number],
): (value: number) => number {
  const [first, last] = domain;
  const [start, end] = range;
  const span = last - first;
  const width = end - start;
  return (value) => start + ((value - first) / span) * width;
}

/** The power of ten at or below `value`. */
function magnitude(value: number): number {
  return 10 ** Math.floor(Math.log10(value));
}

/**
 * A grid step that cuts `span` into about four, landing on a round number.
 *
 * Gridlines at 3.7 ms are a grid nobody reads a value off. The ladder is the
 * usual 1/2/5 one: every step divides into its own decade, so the labels stay
 * short whatever the data's magnitude turns out to be.
 */
export function niceStep(span: number): number {
  const raw = span / 4;
  const power = magnitude(raw);
  for (const step of [1, 2, 5, 10]) {
    if (raw <= step * power) return step * power;
  }
  return 10 * power;
}

/**
 * The smallest round number at or above `value`, for an axis top.
 *
 * A finer ladder than {@link niceStep} because this one is a single label
 * rather than a series: rounding 210 up to 500 to keep the decade clean would
 * spend more than half the picture on nothing.
 */
export function niceCeil(value: number): number {
  const power = magnitude(Math.max(value, 1));
  for (const step of [1, 1.5, 2, 3, 5, 7.5]) {
    if (value <= step * power) return step * power;
  }
  return 10 * power;
}
