## What and why

<!-- What changes, and what problem it solves. -->

## Checklist

- [ ] `ruff check . && ruff format --check . && mypy core/src && pytest` passes
- [ ] `cargo fmt --check && cargo clippy -- -D warnings && cargo test` passes
- [ ] `cargo deny check licenses` passes — no GPL/LGPL/AGPL or unlicensed dependency
- [ ] `CHANGELOG.md` updated under `## [Unreleased]`
- [ ] No `.osr`, `.osu`, or `.db` files committed

## If this touches the engine

- [ ] Any new byte pattern or struct offset is in the signature data table, with a
      comment stating it as a fact about the binary and naming the osu! build it was
      confirmed against
- [ ] Provenance recorded per [`docs/licensing.md`](../docs/licensing.md) — no code,
      comments, naming scheme, or output schema copied from another osu! tool
- [ ] New fields have a self-validation predicate. A field that can be silently
      wrong without any check failing is not ready to merge

## If this produces a number a user might act on

- [ ] Reports its sample size and a confidence interval
- [ ] Suppressed below the minimum sample size rather than shown with a caveat
- [ ] Tested against **known ground truth** — a synthetic fixture whose correct
      answer is derivable analytically, not just a smoke test on real data

<!--
If you used an AI assistant: models trained on existing osu! tooling reproduce that
tooling's field names and output schema by default. Please confirm you checked for
this — it is the most realistic way a licensing violation reaches the repo.
-->
