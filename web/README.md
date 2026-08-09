# web

The page `forge serve` hands a browser. Astro with React islands and Tailwind v4.

```bash
npm install
npm run build     # produces dist/, which the Python server reads
npm run dev       # Astro's dev server; the API calls will 401, see below
```

`dist/` is **not committed**. It is reproducible from this checkout, and a
committed build is a second copy of the source that drifts from it silently.

## Why there is a package.json here at all

There has to be one. `D:\node_modules` exists on the development machine, and
Node resolution walks up the tree — without a manifest and a `node_modules` of
its own, this directory would resolve imports against whatever happens to be
installed several levels above it, and the build would depend on a directory
outside the repository.

## Design tokens

From `vendor/xgis-design/DESIGN.md`, vendored with its provenance and a hash so
it cannot change underneath the project. `src/styles/tokens.css` is the
machine-readable form: Tailwind v4 reads a `@theme` block directly, so the
tokens *are* the configuration rather than being transcribed into a second file
that then has to be kept in step.

Three rules from that document are easy to undo by accident:

1. **Hairlines, never shadows.** A `box-shadow` anywhere here is a mistake.
2. **No bold.** Weight 400 throughout; emphasis is size and negative tracking.
3. **Two radii.** `8px` cards, `9999px` pills. There is no third.

## No external requests

The served page must reach nothing but the local server. Fonts are npm packages
rather than CDN links, there are no analytics, and the layout sets a
`Content-Security-Policy` with `default-src 'self'` so that a component added
later cannot quietly introduce one. The Python side asserts this.

## The token

`index.html` contains the literal `__TOKEN__`, replaced by the server as it
serves the file. It is in the HTML rather than fetched because a browser
navigating to a URL cannot send an `Authorization` header — and a cross-site
page can cause that navigation but cannot read the response, so it cannot take
the token out.

Under `npm run dev` nothing performs the substitution, so the placeholder is
what the page gets and every API call fails as unauthorised. That is correct
behaviour for a page opened without a server, not a bug to work around.

## Layout

| | |
|---|---|
| `src/lib/protocol.ts` | the wire format, and the schema version check |
| `src/components/Playfield.tsx` | the playfield: objects, slider bodies, cursor |
| `src/components/ErrorTimeline.tsx` | every judgement against the windows it was judged by |
| `src/components/App.tsx` | the one island; owns the clock and the selection |
| `src/i18n/` | UI chrome in en and ko; analysis prose stays English |

`Playfield` is Canvas 2D today. The WebGL2 renderer replaces it once the shader
pipeline is vendored, and takes the same inputs — decoded samples, a path
buffer, a clock.

## Two things the drawing must keep doing

**Cursor samples are points, not a smoothed curve.** A replay records about
sixty positions a second and nothing between them. A spline invents motion that
was never measured, and the invented parts are exactly where someone looks when
asking what their hand did. The faint line joins the samples; the samples are
drawn on top so it stays visible which is which.

**Judgement windows are drawn to scale from the map's own values.** "40 ms late"
means one thing when the 300 window runs to 56 ms and something else when it
ends at 26.
