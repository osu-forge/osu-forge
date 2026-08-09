# X-GIS design tokens

`DESIGN.md` in this directory is a verbatim copy, taken so that the design this
project follows is pinned in the repository rather than fetched from a moving
branch. A token file that silently changes upstream produces a UI that drifts
without any commit showing it.

| | |
|---|---|
| Source | <https://github.com/X-GIS/X-GIS/blob/main/site/DESIGN.md> |
| Raw | <https://raw.githubusercontent.com/X-GIS/X-GIS/main/site/DESIGN.md> |
| Copied from | `D:\X-GIS\site\DESIGN.md` |
| Copied on | 2026-08-09 |
| Size | 25,080 bytes |
| SHA-256 | `D7FD532947C681748505940B549B48556878DB5286E5DA6203B25C4D861B08D4` |

The local copy was checked against `main` at the time of copying: the colour,
radius, spacing and typography values used here are identical in both.

## Permission

X-GIS is the repository owner's own project, and they authorised its use here.
It is not published to npm, so vendoring is how it travels.

## What derives from it

`web/src/styles/tokens.css` is the machine-readable form. Tailwind v4 reads a
`@theme` block directly, so the tokens are the configuration rather than being
transcribed into a second file that then has to be kept in step.

Three rules from the document shape everything and are easy to undo by accident:

1. **Hairlines, never shadows.** Separation is a 1px `--color-hairline` border.
   A `box-shadow` anywhere in this project is a mistake.
2. **No bold.** Weight 400 throughout; emphasis comes from size and from
   negative letter-spacing at display sizes.
3. **Two radii only.** `8px` for cards, `9999px` for pills. There is no third.

## Deliberate deviations

Both are departures from the source and are listed here rather than left to be
discovered in a diff:

- **Universal Sans is not used.** It is commercially licensed and this project
  ships its fonts. Inter replaces it, which is the fallback the document itself
  names, and Geist Mono is used unchanged. Both are OFL-1.1 and are bundled as
  npm packages, because the served page must make no external request.

- **Judgement colours are this project's own.** The source has no notion of a
  300 or a miss. Rather than introduce new hues, they are drawn from accents the
  palette already has — `accent-breeze` for a 300 through to `accent-sunset` for
  a miss — so the scale reads as part of the same language.
