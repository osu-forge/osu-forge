//! Reading `.osu` beatmaps.
//!
//! The parser is shared: difficulty calculation needs the hit objects, and so
//! does the replay hit simulator on the Python side, which reaches it through
//! the PyO3 bindings. One parser means the two cannot disagree about what a
//! beatmap says — and if they did, the disagreement would show up as hit errors
//! that look like a player problem.

mod model;
mod parse;

pub use model::{
    Beatmap, CurveKind, Difficulty, HitObject, HitObjectKind, Metadata, Mode, Pos, Slider,
    TimingPoint,
};
pub use parse::{parse, ParseError};
