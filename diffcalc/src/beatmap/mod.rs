//! Reading `.osu` beatmaps.
//!
//! The parser is shared: difficulty calculation needs the hit objects, and so
//! does the replay hit simulator on the Python side, which reaches it through
//! the PyO3 bindings. One parser means the two cannot disagree about what a
//! beatmap says — and if they did, the disagreement would show up as hit errors
//! that look like a player problem.

mod curve;
mod model;
mod nested;
mod parse;
mod stacking;

pub use curve::SliderPath;
pub use model::{
    Beatmap, CurveKind, Difficulty, HitObject, HitObjectKind, Metadata, Mode, Pos, Slider,
    Stacking, TimingPoint, OBJECT_RADIUS, STABLE_RADIUS_FUDGE, STACK_OFFSET_PER_LEVEL,
};
pub use nested::{NestedKind, NestedObject};
pub use parse::{parse, ParseError};
pub use stacking::STACK_DISTANCE;

impl Beatmap {
    /// Recompute every object's stack height.
    ///
    /// The parser does this already. Call it again after changing the approach
    /// rate — under HR or EZ, say — because the stacking window is a multiple of
    /// the preempt time, so the heights computed for one AR are wrong for
    /// another.
    pub fn apply_stacking(&mut self) {
        stacking::apply(self);
    }
}
