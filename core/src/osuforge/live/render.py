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

from osuforge.live.watch import Play, Session
from osuforge.replay.simulate import Grade
from osuforge.replay.validate import Agreement

__all__ = ["REFRESH_SECONDS", "render"]

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
  .play { border-top: 1px solid var(--edge); padding: .9rem 0; }
  .warn { border-left: 3px solid currentColor; padding-left: .7rem; opacity: .8;
          font-size: .85rem; margin: .5rem 0; }
  table { border-collapse: collapse; width: 100%; font-size: .85rem; }
  td, th { text-align: left; padding: .2rem .4rem .2rem 0; }
  th { font-weight: 600; opacity: .65; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
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
<div class="play">
  <div><strong>{{ play.artist }} — {{ play.title }}</strong> [{{ play.version }}]</div>
  <div class="sub">
    {{ '%.2f%%'|format(play.accuracy * 100) }} ·
    {{ play.counts_300 }}/{{ play.counts_100 }}/{{ play.counts_50 }}/{{ play.counts_miss }} ·
    {{ play.played_at.strftime('%H:%M') }}
  </div>

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
    </div>

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
  {% endif %}
</div>
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
