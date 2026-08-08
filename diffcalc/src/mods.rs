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

use crate::beatmap::{Difficulty, HitObjectKind, Pos, PLAYFIELD_HEIGHT};

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

fn mirror(pos: Pos) -> Pos {
    Pos::new(pos.x, PLAYFIELD_HEIGHT - pos.y)
}

impl crate::beatmap::Beatmap {
    /// Apply a mod combination in place.
    ///
    /// Three steps, in osu!'s order: the difficulty settings move, then Hard
    /// Rock reflects the playfield, then stacking runs again.
    ///
    /// Restacking is not optional. The stacking window is a multiple of the
    /// preempt time and both Hard Rock and Easy move the approach rate, so
    /// heights computed before the change belong to a different map. Forgetting
    /// it shifts a few objects a few pixels and never announces itself.
    ///
    /// Not idempotent: two applications of Hard Rock multiply the difficulty
    /// twice and reflect back to where they started. Apply a combination once,
    /// to a beatmap parsed fresh.
    pub fn apply_mods(&mut self, mods: Mods) {
        self.difficulty = self.difficulty.with_mods(mods);
        if mods.contains(Mods::HARD_ROCK) {
            self.mirror_vertically();
        }
        self.apply_stacking();
    }

    /// Reflect every object about the horizontal midline, as Hard Rock does.
    ///
    /// The reflection is the half of Hard Rock that changes what the player's
    /// hand has to do. Without it every position on a Hard Rock map is wrong by
    /// however far it sits from the midline, and the aim analysis is measuring
    /// a map nobody played — which is worse than measuring nothing, because the
    /// distances between consecutive objects still look entirely reasonable.
    ///
    /// A slider's control points are absolute here, so they reflect the same
    /// way its head does. lazer negates them instead, because lazer stores them
    /// relative to the head; the two are the same operation written against
    /// different origins, and copying its `-y` onto absolute points would put
    /// every slider body off the top of the screen.
    fn mirror_vertically(&mut self) {
        for object in &mut self.hit_objects {
            object.pos = mirror(object.pos);
            if let HitObjectKind::Slider(slider) = &mut object.kind {
                for point in &mut slider.control_points {
                    *point = mirror(*point);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::beatmap::SliderPath;

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

    fn parse(text: &str) -> crate::beatmap::Beatmap {
        crate::beatmap::parse(text.as_bytes()).expect("should parse")
    }

    const CIRCLES: &str = "osu file format v14\n\
                           [General]\nMode: 0\nStackLeniency: 0.7\n\
                           [Difficulty]\nCircleSize:4\nApproachRate:9\n\
                           [HitObjects]\n100,100,1000,1,0\n300,284,2000,1,0\n256,192,3000,1,0\n";

    #[test]
    fn hard_rock_reflects_about_the_midline() {
        let mut b = parse(CIRCLES);
        b.apply_mods(Mods(Mods::HARD_ROCK));
        let positions: Vec<_> = b.hit_objects.iter().map(|o| (o.pos.x, o.pos.y)).collect();
        assert_eq!(
            positions,
            vec![(100.0, 284.0), (300.0, 100.0), (256.0, 192.0)]
        );
    }

    #[test]
    fn nothing_else_reflects() {
        // Easy shares every difficulty change with Hard Rock and reflects
        // nothing, so a mirror applied on the difficulty branch rather than on
        // the mod would show up here.
        for flags in [Mods::NONE, Mods::EASY, Mods::DOUBLE_TIME, Mods::HIDDEN] {
            let mut b = parse(CIRCLES);
            b.apply_mods(Mods(flags));
            assert_eq!(b.hit_objects[0].pos.y, 100.0, "mods {flags:#x}");
        }
    }

    #[test]
    fn a_reflected_slider_keeps_its_shape_and_its_length() {
        // Control points are absolute in this crate. lazer negates them because
        // it stores them relative to the head; using its -y here would put the
        // body 384 pixels off the top while the head stayed put.
        let mut b = parse(
            "osu file format v14\n\
             [General]\nMode: 0\n\
             [Difficulty]\nCircleSize:4\nApproachRate:9\nSliderMultiplier:1.4\n\
             [TimingPoints]\n0,500,4,2,0,100,1,0\n\
             [HitObjects]\n100,100,1000,2,0,B|200:150|300:100,1,200\n",
        );
        let before = SliderPath::new(match &b.hit_objects[0].kind {
            HitObjectKind::Slider(s) => s,
            _ => unreachable!(),
        })
        .computed_length();

        b.apply_mods(Mods(Mods::HARD_ROCK));
        let HitObjectKind::Slider(slider) = &b.hit_objects[0].kind else {
            unreachable!()
        };
        let points: Vec<_> = slider.control_points.iter().map(|p| (p.x, p.y)).collect();
        assert_eq!(points, vec![(100.0, 284.0), (200.0, 234.0), (300.0, 284.0)]);
        assert_eq!(slider.control_points[0].y, b.hit_objects[0].pos.y);
        assert!((SliderPath::new(slider).computed_length() - before).abs() < 1e-9);
    }

    #[test]
    fn reflecting_twice_returns_the_map_to_where_it_started() {
        // Not a property anything relies on — apply_mods is documented as
        // single-use — but it pins the reflection to the midline. An axis at
        // 383 or 385 would survive every other assertion here.
        let original = parse(CIRCLES);
        let mut b = parse(CIRCLES);
        b.mirror_vertically();
        b.mirror_vertically();
        for (after, before) in b.hit_objects.iter().zip(&original.hit_objects) {
            assert_eq!(after.pos, before.pos);
        }
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
