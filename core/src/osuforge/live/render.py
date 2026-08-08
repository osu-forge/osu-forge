"""The page itself: one self-contained HTML file, rewritten as plays appear.

No server, no socket, no port, nothing listening. The file is rewritten and the
browser reloads it, which works as a second-monitor page and as an OBS browser
source alike. See the package docstring for why the local server that the
architecture specifies is not being built for this.

Everything is inline. No stylesheet, no script, no font, no image fetched from
anywhere — partly because a report has to work with no network, and partly
because a page that fetches nothing cannot leak what it is about by fetching it.

Charts are hand-written SVG rather than a library. A rendered image would need
drawing twice for light and dark, and would stop being selectable text; inline
SVG uses `currentColor` and CSS variables and gets both themes from one pass.

Beatmap titles are user-supplied content from files on disk, so the template
escapes. Autoescaping is on and the one place raw markup is inserted is the
chart, which is generated here and never contains anything from a file.
"""

from __future__ import annotations

import math
from datetime import datetime

from jinja2 import Environment
from markupsafe import Markup

from osuforge.analysis.failures import Clip, Failure
from osuforge.live.watch import Play, Session
from osuforge.replay.simulate import Grade
from osuforge.replay.validate import Agreement

__all__ = ["CLIPS_PER_PLAY", "REFRESH_SECONDS", "render"]

CLIPS_PER_PLAY = 8
"""Drawings shown per play, biggest combo loss first.

A play with forty misses would otherwise produce forty of them and a page nobody
scrolls to the end of.
"""

REFRESH_SECONDS = 3
"""How often the browser reloads.

Short enough that a finished play appears while you are still looking at the
results screen, long enough that it is not doing anything noticeable in between.
"""

_environment = Environment(autoescape=True)


def _histogram(errors: list[float], *, width: int = 560, height: int = 120) -> str:
    """A hit-error histogram as inline SVG.

    Zero is drawn as a line so early and late are visible at a glance, which is
    the entire point of the chart: a distribution centred left of the line is a
    player hitting early, whatever its width.
    """
    if len(errors) < 5:
        return ""
    limit = max(20.0, min(80.0, max(abs(error) for error in errors)))
    buckets = 41
    counts = [0] * buckets
    for error in errors:
        position = int((error + limit) / (2 * limit) * buckets)
        counts[min(buckets - 1, max(0, position))] += 1
    tallest = max(counts) or 1

    bar_width = width / buckets
    bars = []
    for index, count in enumerate(counts):
        if not count:
            continue
        bar_height = count / tallest * (height - 18)
        x = index * bar_width
        y = height - 18 - bar_height
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width - 1:.1f}" '
            f'height="{bar_height:.1f}" fill="currentColor" opacity="0.55"/>'
        )

    centre = width / 2
    mean = sum(errors) / len(errors)
    mean_x = centre + mean / limit * centre
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="hit error distribution">'
        + "".join(bars)
        + f'<line x1="{centre}" y1="0" x2="{centre}" y2="{height - 18}" '
        'stroke="currentColor" stroke-width="1" opacity="0.45"/>'
        + f'<line x1="{mean_x:.1f}" y1="0" x2="{mean_x:.1f}" y2="{height - 18}" '
        'stroke="currentColor" stroke-width="2" stroke-dasharray="4 3"/>'
        + f'<text x="2" y="{height - 4}" font-size="11" fill="currentColor" '
        f'opacity="0.7">early {-limit:.0f} ms</text>'
        + f'<text x="{width - 2}" y="{height - 4}" font-size="11" text-anchor="end" '
        f'fill="currentColor" opacity="0.7">late +{limit:.0f} ms</text>'
        + f'<text x="{centre + 4}" y="12" font-size="11" fill="currentColor" '
        f'opacity="0.7">mean {mean:+.1f}</text>'
        "</svg>"
    )


def _clip_svg(clip: Clip, *, size: int = 300) -> str:
    """The moment around a miss, drawn.

    A line of text says a turn was 41 degrees at 2.3 px/ms. This shows the
    cursor going there and not arriving, which is a different kind of knowing.

    The path fades with age so its direction is readable without an arrowhead,
    and the object that was missed is the only one drawn solid.
    """
    if clip.empty:
        return ""

    points = [(x, y) for x, y, _ in clip.cursor]
    points += [(x, y) for x, y, _, _ in clip.objects]
    if not points:
        return ""

    pad = clip.radius * 1.6
    left = min(x for x, _ in points) - pad
    right = max(x for x, _ in points) + pad
    top = min(y for _, y in points) - pad
    bottom = max(y for _, y in points) + pad
    span = max(right - left, bottom - top, 1.0)
    # A square viewport keeps the geometry undistorted; a wide clip and a tall
    # one would otherwise imply different aim distances for the same movement.
    centre_x = (left + right) / 2
    centre_y = (top + bottom) / 2
    view = f"{centre_x - span / 2:.1f} {centre_y - span / 2:.1f} {span:.1f} {span:.1f}"
    stroke = span / size

    parts: list[str] = []
    for position, ((x1, y1, _), (x2, y2, _)) in enumerate(
        zip(clip.cursor, clip.cursor[1:], strict=False)
    ):
        age = (position + 1) / max(1, len(clip.cursor) - 1)
        parts.append(
            f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="currentColor" stroke-width="{stroke * 2:.2f}" '
            f'opacity="{0.12 + 0.68 * age:.2f}" stroke-linecap="round"/>'
        )

    for x, y, _, is_failure in clip.objects:
        if is_failure:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{clip.radius:.1f}" '
                f'fill="currentColor" opacity="0.30"/>'
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{clip.radius:.1f}" '
                f'fill="none" stroke="currentColor" stroke-width="{stroke * 2.5:.2f}"/>'
            )
        else:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{clip.radius:.1f}" '
                f'fill="none" stroke="currentColor" stroke-width="{stroke:.2f}" '
                f'opacity="0.35"/>'
            )

    for x, y, _ in clip.presses:
        radius = clip.radius * 0.22
        parts.append(
            f'<line x1="{x - radius:.1f}" y1="{y - radius:.1f}" '
            f'x2="{x + radius:.1f}" y2="{y + radius:.1f}" stroke="currentColor" '
            f'stroke-width="{stroke * 2.5:.2f}"/>'
            f'<line x1="{x - radius:.1f}" y1="{y + radius:.1f}" '
            f'x2="{x + radius:.1f}" y2="{y - radius:.1f}" stroke="currentColor" '
            f'stroke-width="{stroke * 2.5:.2f}"/>'
        )

    return (
        f'<svg viewBox="{view}" width="{size}" height="{size}" role="img" '
        f'aria-label="cursor path around the missed object">' + "".join(parts) + "</svg>"
    )


_TEMPLATE = _environment.from_string(
    """<!doctype html>
<meta charset="utf-8">
<meta http-equiv="refresh" content="{{ refresh }}">
<title>osu-forge — {{ session.plays|length }} play(s)</title>
<style>
  :root { color-scheme: light dark; --edge: #8883; }
  body { font: 15px/1.5 system-ui, sans-serif; margin: 0 auto; padding: 1.5rem;
         max-width: 46rem; }
  h1 { font-size: 1.1rem; margin: 0 0 .25rem; font-weight: 600; }
  h2 { font-size: .95rem; margin: 1.75rem 0 .5rem; font-weight: 600; }
  .sub { opacity: .65; font-size: .85rem; margin: 0 0 1rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(7.5rem, 1fr));
          gap: .75rem; margin: .75rem 0; }
  .cell { border: 1px solid var(--edge); border-radius: .4rem; padding: .5rem .65rem; }
  .cell b { display: block; font-size: 1.25rem; font-weight: 600; }
  .cell span { opacity: .65; font-size: .78rem; }
  .play { border: 1px solid var(--edge); border-radius: .5rem; margin: .6rem 0;
          overflow: hidden; }
  .play > summary { display: flex; align-items: center; gap: .8rem; padding: .55rem .7rem;
                    cursor: pointer; list-style: none; }
  .play > summary::-webkit-details-marker { display: none; }
  .play[open] > summary { border-bottom: 1px solid var(--edge); }
  .play > :not(summary) { margin-left: .7rem; margin-right: .7rem; }
  .thumb { width: 6.5rem; height: 3.6rem; object-fit: cover; border-radius: .3rem;
           flex: none; background: #8882; }
  .who { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .who strong { font-size: .95rem; overflow: hidden; text-overflow: ellipsis;
                white-space: nowrap; }
  .who .sub, .score .sub { margin: 0; font-size: .76rem; }
  .score { text-align: right; flex: none; }
  .score b { font-size: 1.15rem; font-variant-numeric: tabular-nums; }
  h3 { font-size: .85rem; margin: 1.2rem 0 .4rem; font-weight: 600; opacity: .8; }
  .clips { display: flex; flex-wrap: wrap; gap: .6rem; }
  .clips figure { margin: 0; }
  .clips svg { width: 8.5rem; height: 8.5rem; border: 1px solid var(--edge);
               border-radius: .3rem; }
  .clips figcaption { font-size: .72rem; opacity: .65; text-align: center;
                      font-variant-numeric: tabular-nums; }
  .warn { border-left: 3px solid currentColor; padding-left: .7rem; opacity: .8;
          font-size: .85rem; margin: .5rem 0; }
  table { border-collapse: collapse; width: 100%; font-size: .85rem; }
  td, th { text-align: left; padding: .2rem .4rem .2rem 0; }
  th { font-weight: 600; opacity: .65; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.when { font-variant-numeric: tabular-nums; white-space: nowrap;
            padding-right: .8rem; opacity: .8; }
  footer { margin-top: 2.5rem; font-size: .78rem; opacity: .6; }
</style>

<h1>osu-forge</h1>
<p class="sub">
  {{ session.usable|length }} play(s) analysed this session, refreshed every
  {{ refresh }} seconds. Updated {{ updated }}.
</p>

{% if not session.plays %}
  <p>Nothing yet. Finish a play and it will appear here.</p>
  <p class="sub">
    A play only leaves a replay when it finishes. Quick-retrying after a mistake
    leaves nothing behind, so those attempts cannot appear here at all.
  </p>
{% else %}

<h2>This session</h2>
<div class="grid">
  <div class="cell"><b>{{ session.usable|length }}</b><span>plays</span></div>
  <div class="cell"><b>{{ session.errors|length }}</b><span>circle hits</span></div>
  <div class="cell"><b>{{ '%+.1f'|format(session.mean_error) }}</b>
    <span>mean error, ms</span></div>
  <div class="cell"><b>{{ '%.0f'|format(session.unstable_rate) }}</b>
    <span>unstable rate</span></div>
  <div class="cell"><b>{{ '%.0f%%'|format(session.key_balance * 100) }}</b>
    <span>on key 1</span></div>
</div>
<div class="chart">{{ session_chart }}</div>
<p class="sub">
  No confidence interval, and that is not an omission. Hits within one session
  are not independent of each other, so an interval from a single sitting would
  be far narrower than the truth. It takes several separate sessions before a
  range means anything — <code>forge collect</code> is what accumulates them.
</p>

<h2>Plays</h2>
{% for play in session.plays %}
<details class="play" {% if loop.first %}open{% endif %}>
  <summary>
    {% if play.background_uri %}
      <img class="thumb" src="{{ play.background_uri }}" alt="">
    {% else %}
      <span class="thumb"></span>
    {% endif %}
    <span class="who">
      <strong>{{ play.title }}</strong>
      <span class="sub">{{ play.artist }} · [{{ play.version }}]</span>
      <span class="sub">
        {{ '%.0f'|format(play.bpm) }} BPM ·
        {{ play.object_count }} objects ·
        {{ play.duration }} ·
        CS {{ '%.1f'|format(play.circle_size) }}
        AR {{ '%.1f'|format(play.approach_rate) }}
        OD {{ '%.1f'|format(play.overall_difficulty) }}
      </span>
    </span>
    <span class="score">
      <b>{{ '%.2f%%'|format(play.accuracy * 100) }}</b>
      <span class="sub">{{ play.counts_miss }} miss ·
        {{ play.played_at.strftime('%H:%M') }}</span>
    </span>
  </summary>

  {% if not play.usable %}
    <div class="warn">
      Not analysed: {{ play.agreement_reason }}
    </div>
  {% else %}
    <div class="grid">
      <div class="cell"><b>{{ '%+.1f'|format(play.mean_error) }}</b>
        <span>mean error, ms</span></div>
      <div class="cell"><b>{{ '%.0f'|format(play.unstable_rate) }}</b>
        <span>unstable rate</span></div>
      <div class="cell"><b>{{ '%.2f'|format(play.aim_median) }}</b>
        <span>median aim, radii</span></div>
      <div class="cell"><b>{{ '%.0f%%'|format(play.key_balance * 100) }}</b>
        <span>on key 1</span></div>
      <div class="cell"><b>{{ play.breaks|length }}</b><span>combo breaks</span></div>
      <div class="cell"><b>{{ play.reported_max_combo }}</b><span>longest combo</span></div>
    </div>

    {% if play.breaks %}
      <table>
        <tr>
          <th>combo broke</th>
          <th>{{ play.breaks|length }} time(s){% if play.sliderbreaks %},
            {{ play.sliderbreaks }} on sliders{% endif %}</th>
        </tr>
        {% for failure in play.breaks %}
        <tr>
          <td class="when">{{ failure.at }}</td>
          <td>{{ failure.describe() }}</td>
        </tr>
        {% endfor %}
      </table>
      {% if play.combo_caveat %}
        <div class="warn">{{ play.combo_caveat }}</div>
      {% endif %}
      <p class="sub">
        Seek to a timestamp and watch it. What is written beside it is what the
        map was asking for at that moment — a fact about the beatmap, not a
        diagnosis. A slider that scores 100 breaks combo just as a miss does,
        and the results screen does not say so.
        {% if not play.combo_caveat %}
          The combo worked through here comes to the {{ play.reported_max_combo }}
          the game recorded, which is the check that these are in the right
          places.
        {% endif %}
      </p>
    {% endif %}

    {% if play.findings() %}
      <table>
        <tr><th>where the accuracy went</th><th>objects</th><th>of loss</th></tr>
        {% for share in play.findings() %}
        <tr>
          <td>{{ share.label }}</td>
          <td class="num">{{ '%.0f%%'|format(share.share_of_objects * 100) }}</td>
          <td class="num">{{ '%.0f%%'|format(share.share_of_loss * 100) }}</td>
        </tr>
        {% endfor %}
      </table>
    {% endif %}

    {% if play.clips %}
      <h3>What it looked like</h3>
      <div class="clips">
        {% for failure in play.clips %}
        <figure>
          {{ failure.drawing }}
          <figcaption>{{ failure.at }} · lost {{ failure.combo_lost }}</figcaption>
        </figure>
        {% endfor %}
      </div>
      <p class="sub">
        The cursor's path into each missed object, fading from where it started.
        The solid circle is the one that was missed; crosses are presses. About
        seven tenths of a second before and a third after, drawn at 60 samples a
        second because that is what a replay records.
      </p>
    {% endif %}
  {% endif %}
</details>
{% endfor %}
{% endif %}

{% if session.skipped %}
<h2>Skipped</h2>
<table>
  {% for name, reason in session.skipped.items() %}
  <tr><td>{{ name }}</td><td>{{ reason }}</td></tr>
  {% endfor %}
</table>
{% endif %}

<footer>
  osu-forge never modifies osu! files and never recommends a setting from a
  single session. Numbers here describe what happened; deciding what to change
  needs several sessions and the checks that go with them.
</footer>
"""
)


class _ClipView:
    """A failure plus its drawing, so the template does no geometry."""

    def __init__(self, failure: Failure) -> None:
        self._failure = failure

    def __getattr__(self, name: str) -> object:
        return getattr(self._failure, name)

    @property
    def drawing(self) -> Markup:
        assert self._failure.clip is not None
        # Generated a few lines away from numbers, never from a file.
        return Markup(_clip_svg(self._failure.clip))


class _PlayView:
    """Adds the few derived values the template wants, without touching `Play`.

    Keeping formatting out of the analysis types means the page can change
    without anything that computes a number changing with it.
    """

    def __init__(self, play: Play) -> None:
        self._play = play

    def __getattr__(self, name: str) -> object:
        return getattr(self._play, name)

    @property
    def counts_300(self) -> int:
        return self._play.counts.get(Grade.THREE_HUNDRED, 0)

    @property
    def counts_100(self) -> int:
        return self._play.counts.get(Grade.HUNDRED, 0)

    @property
    def counts_50(self) -> int:
        return self._play.counts.get(Grade.FIFTY, 0)

    @property
    def counts_miss(self) -> int:
        return self._play.counts.get(Grade.MISS, 0)

    @property
    def aim_median(self) -> float:
        values = sorted(self._play.aim_errors)
        return values[len(values) // 2] if values else math.nan

    @property
    def usable(self) -> bool:
        return self._play.agreement is not Agreement.MISMATCH

    @property
    def background_uri(self) -> str | None:
        """A `file:` URL for the map's background.

        The page is already a local file, so pointing at a neighbouring one
        costs nothing and embeds nothing. It is the only reference the page
        makes, it never leaves the machine, and a page copied elsewhere simply
        loses its thumbnails.
        """
        background = self._play.background
        return background.as_uri() if background else None

    @property
    def duration(self) -> str:
        total = int(self._play.length_ms / 1000)
        return f"{total // 60}:{total % 60:02d}"

    @property
    def clips(self) -> list[_ClipView]:
        """The misses worth drawing, biggest combo loss first.

        Capped: a play with forty misses would otherwise produce forty drawings
        and a page nobody scrolls to the end of.
        """
        drawable = [
            failure
            for failure in self._play.breaks
            if failure.clip is not None and not failure.clip.empty
        ]
        drawable.sort(key=lambda failure: -failure.combo_lost)
        return [_ClipView(failure) for failure in drawable[:CLIPS_PER_PLAY]]


class _SessionView:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.plays = [_PlayView(play) for play in session.plays]
        self.usable = [view for view in self.plays if view.usable]

    def __getattr__(self, name: str) -> object:
        return getattr(self._session, name)


def render(session: Session, *, now: datetime | None = None) -> str:
    """Produce the whole page."""
    view = _SessionView(session)
    return _TEMPLATE.render(
        session=view,
        refresh=REFRESH_SECONDS,
        updated=(now or datetime.now()).strftime("%H:%M:%S"),
        # The only markup inserted unescaped. It is generated a few lines above
        # from a list of floats and never contains anything read from a file,
        # which is what makes marking it safe defensible rather than habitual.
        session_chart=Markup(_histogram(session.errors)),
    )
