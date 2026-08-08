//! osu-forge diffcalc — difficulty and performance-point calculation for osu!std.
//!
//! This is an independent implementation ported from the authoritative sources,
//! [ppy/osu] and [ppy/osu-tools], both MIT-licensed. See `PORTED_FROM.md` for the
//! exact upstream commits this tracks, and `NOTICE` for the required attribution.
//!
//! We port from ppy's C# directly rather than from a third-party port. A port is
//! one more layer between us and ground truth, and ports lag behind reworks.
//!
//! # Correctness
//!
//! An independently implemented difficulty calculator that is quietly wrong is
//! worse than no calculator, because star rating feeds pp and pp gets believed.
//! The defence is an official oracle: `POST /api/v2/beatmaps/{id}/attributes`
//! returns osu!'s own `star_rating`, `aim_difficulty`, `speed_difficulty`,
//! `slider_factor` and friends for any map and mod combination. CI compares our
//! output against it to 1e-6 relative error over a fixed map set (see the
//! `official-oracle` feature).
//!
//! If the tolerance is exceeded, the correct behaviour is to **refuse to display
//! pp** and surface a "calculator disagrees with official values" state. Silently
//! wrong pp is the outcome this whole arrangement exists to prevent.
//!
//! [ppy/osu]: https://github.com/ppy/osu
//! [ppy/osu-tools]: https://github.com/ppy/osu-tools

#![doc(html_root_url = "https://github.com/osu-forge/osu-forge")]

pub mod beatmap;
pub mod mods;

/// Version of this crate, surfaced in reports alongside the upstream commit
/// recorded in `PORTED_FROM.md` so a pp figure can always be traced to the
/// algorithm revision that produced it.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_is_populated() {
        assert!(!VERSION.is_empty());
    }
}
