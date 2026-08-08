//! Mod flags, and what they do to a beatmap's difficulty settings.
//!
//! The bit values are osu!'s, and the same table exists on the Python side in
//! `osuforge.replay.model.Mods` — the replay parser needs it to read a `.osr`
//! header, and this crate needs it to adjust difficulty. Two copies of a
//! constant table is a real risk, so the PyO3 bindings export these values and a
//! Python test asserts the two agree. A drift becomes a test failure rather than
//! a difficulty setting that is wrong only under Hard Rock.
//!
//! Only the difficulty-affecting mods are interpreted here. Hidden and Flashlight
//! change what the player can see, not what the beatmap is, and belong to the
//! skills that model visibility.

use crate::beatmap::Difficulty;

/// A mod combination, as the 32-bit field a `.osr` header carries.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Mods(pub u32);

impl Mods {
    pub const NONE: u32 = 0;
    pub const NO_FAIL: u32 = 1 << 0;
    pub const EASY: u32 = 1 << 1;
    pub const TOUCH_DEVICE: u32 = 1 << 2;
    pub const HIDDEN: u32 = 1 << 3;
    pub const HARD_ROCK: u32 = 1 << 4;
    pub const SUDDEN_DEATH: u32 = 1 << 5;
    pub const DOUBLE_TIME: u32 = 1 << 6;
    pub const RELAX: u32 = 1 << 7;
    pub const HALF_TIME: u32 = 1 << 8;
    /// Implies [`Mods::DOUBLE_TIME`] in every file osu! writes, and carries the
    /// same rate. It is a different soundtrack, not a different speed.
    pub const NIGHTCORE: u32 = 1 << 9;
    pub const FLASHLIGHT: u32 = 1 << 10;
    pub const AUTOPLAY: u32 = 1 << 11;
    pub const SPUN_OUT: u32 = 1 << 12;
    pub const AUTOPILOT: u32 = 1 << 13;
    pub const PERFECT: u32 = 1 << 14;
    pub const TARGET_PRACTICE: u32 = 1 << 23;
    pub const SCORE_V2: u32 = 1 << 29;

    pub fn contains(self, flag: u32) -> bool {
        self.0 & flag != 0
    }

    /// Playback rate. Map time multiplied by this gives real time.
    ///
    /// Nothing else in this module changes with the rate: under Double Time the
    /// hit windows and object times stay in map time and the clock runs faster,
    /// which is the same statement from the other side. Only a consumer
    /// reporting in real seconds needs this.
    pub fn rate(self) -> f64 {
        if self.contains(Self::DOUBLE_TIME) || self.contains(Self::NIGHTCORE) {
            1.5
        } else if self.contains(Self::HALF_TIME) {
            0.75
        } else {
            1.0
        }
    }

    /// Whether the score was produced by something other than a player's hands.
    ///
    /// Replays under these mods say nothing about a human's timing, so the
    /// analysis side drops them rather than averaging them in.
    pub fn not_human(self) -> bool {
        self.contains(Self::AUTOPLAY)
            || self.contains(Self::RELAX)
            || self.contains(Self::AUTOPILOT)
            || self.contains(Self::SPUN_OUT)
    }
}

/// Hard Rock's multiplier for approach rate, overall difficulty and drain.
const HARD_ROCK_FACTOR: f64 = 1.4;
/// Circle size gets a smaller one. This is not a typo carried over from the
/// other three.
const HARD_ROCK_CIRCLE_SIZE_FACTOR: f64 = 1.3;
const EASY_FACTOR: f64 = 0.5;
const MAX_SETTING: f64 = 10.0;

impl Difficulty {
    /// Difficulty settings as the player experienced them.
    ///
    /// Hard Rock and Easy are the only mods that move these numbers. Double Time
    /// and Half Time leave overall difficulty alone and change the clock; the
    /// effective tightening of the windows falls out of that and must not be
    /// applied here as well, or it is counted twice.
    pub fn with_mods(&self, mods: Mods) -> Difficulty {
        let mut adjusted = *self;
        if mods.contains(Mods::HARD_ROCK) {
            adjusted.circle_size =
                (self.circle_size * HARD_ROCK_CIRCLE_SIZE_FACTOR).min(MAX_SETTING);
            adjusted.approach_rate = (self.approach_rate * HARD_ROCK_FACTOR).min(MAX_SETTING);
            adjusted.overall_difficulty =
                (self.overall_difficulty * HARD_ROCK_FACTOR).min(MAX_SETTING);
            adjusted.hp_drain_rate = (self.hp_drain_rate * HARD_ROCK_FACTOR).min(MAX_SETTING);
        } else if mods.contains(Mods::EASY) {
            adjusted.circle_size = self.circle_size * EASY_FACTOR;
            adjusted.approach_rate = self.approach_rate * EASY_FACTOR;
            adjusted.overall_difficulty = self.overall_difficulty * EASY_FACTOR;
            adjusted.hp_drain_rate = self.hp_drain_rate * EASY_FACTOR;
        }
        adjusted
    }
}

impl crate::beatmap::Beatmap {
    /// Apply a mod combination in place.
    ///
    /// Restacks, because the stacking window is a multiple of the preempt time
    /// and Hard Rock and Easy both move the approach rate. Forgetting that step
    /// leaves stack heights computed for a different approach rate, which is the
    /// kind of error that shifts a few objects a few pixels and never announces
    /// itself.
    pub fn apply_mods(&mut self, mods: Mods) {
        self.difficulty = self.difficulty.with_mods(mods);
        self.apply_stacking();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn difficulty(cs: f64, ar: f64, od: f64) -> Difficulty {
        Difficulty {
            circle_size: cs,
            approach_rate: ar,
            overall_difficulty: od,
            ..Difficulty::default()
        }
    }

    #[test]
    fn no_mods_changes_nothing() {
        let d = difficulty(4.0, 9.0, 8.0);
        assert_eq!(d.with_mods(Mods(Mods::NONE)), d);
        assert_eq!(d.with_mods(Mods(Mods::HIDDEN | Mods::NO_FAIL)), d);
    }

    #[test]
    fn hard_rock_uses_a_smaller_factor_for_circle_size() {
        let d = difficulty(4.0, 5.0, 5.0).with_mods(Mods(Mods::HARD_ROCK));
        assert!((d.circle_size - 5.2).abs() < 1e-12);
        assert!((d.approach_rate - 7.0).abs() < 1e-12);
        assert!((d.overall_difficulty - 7.0).abs() < 1e-12);
    }

    #[test]
    fn hard_rock_caps_at_ten() {
        let d = difficulty(9.0, 9.0, 9.0).with_mods(Mods(Mods::HARD_ROCK));
        assert_eq!(d.circle_size, 10.0);
        assert_eq!(d.approach_rate, 10.0);
        assert_eq!(d.overall_difficulty, 10.0);
    }

    #[test]
    fn easy_halves() {
        let d = difficulty(4.0, 9.0, 8.0).with_mods(Mods(Mods::EASY));
        assert_eq!(
            (d.circle_size, d.approach_rate, d.overall_difficulty),
            (2.0, 4.5, 4.0)
        );
    }

    #[test]
    fn double_time_leaves_the_difficulty_settings_alone() {
        let d = difficulty(4.0, 9.0, 8.0);
        assert_eq!(d.with_mods(Mods(Mods::DOUBLE_TIME)), d);
        assert_eq!(Mods(Mods::DOUBLE_TIME).rate(), 1.5);
        assert_eq!(Mods(Mods::NIGHTCORE).rate(), 1.5);
        assert_eq!(Mods(Mods::HALF_TIME).rate(), 0.75);
        assert_eq!(Mods(Mods::NONE).rate(), 1.0);
    }

    #[test]
    fn changing_the_approach_rate_restacks() {
        // Two circles 500 ms apart on the same spot. At AR 9 the window is
        // 600 * 0.7 = 420 ms and they do not stack; Easy drops it to AR 4.5,
        // a 1260 ms preempt, an 882 ms window, and they do.
        let text = "osu file format v14\n\
                    [General]\nMode: 0\nStackLeniency: 0.7\n\
                    [Difficulty]\nCircleSize:4\nApproachRate:9\n\
                    [HitObjects]\n100,100,1000,1,0\n100,100,1500,1,0\n";
        let mut b = crate::beatmap::parse(text.as_bytes()).expect("should parse");
        assert_eq!(b.hit_objects[0].stack_height, 0);

        b.apply_mods(Mods(Mods::EASY));
        assert_eq!(b.hit_objects[0].stack_height, 1);
    }
}
