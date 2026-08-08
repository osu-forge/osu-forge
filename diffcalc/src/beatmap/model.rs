//! Beatmap data model.
//!
//! Field names follow the osu! file format's own names rather than being
//! prettified, so a reader can check any of them against the format
//! documentation without a translation step. Where a name is genuinely
//! misleading the doc comment says so instead of the field being renamed.

use std::collections::HashMap;

/// Radius of a hit object at scale 1, in osu! pixels.
pub const OBJECT_RADIUS: f64 = 64.0;

/// Height of the playfield in osu! pixels. The axis Hard Rock reflects about.
pub const PLAYFIELD_HEIGHT: f64 = 384.0;

/// How far one stack level shifts an object, before the object scale is
/// applied. Up and to the left, hence the negation at the use site.
pub const STACK_OFFSET_PER_LEVEL: f64 = 6.4;

/// stable's hit radius is very slightly larger than `64 * scale`.
///
/// This factor is documented by the community rather than by ppy, and comes out
/// of stable's playfield-to-screen mapping rather than from any deliberate
/// rule. It is kept for fidelity, but it is worth being clear that it is
/// negligible: on a CS4 map it moves the radius by 0.015 osu! pixels, far below
/// anything a judgement could turn on. Nothing here should be believed *because*
/// this factor is present.
pub const STABLE_RADIUS_FUDGE: f64 = 1.000_41;

/// osu! ruleset. Only [`Mode::Osu`] is implemented; the rest are parsed so a
/// file for another mode is rejected with a clear reason rather than silently
/// producing nonsense.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Mode {
    Osu,
    Taiko,
    Catch,
    Mania,
}

impl Mode {
    pub fn from_id(id: i32) -> Option<Self> {
        match id {
            0 => Some(Mode::Osu),
            1 => Some(Mode::Taiko),
            2 => Some(Mode::Catch),
            3 => Some(Mode::Mania),
            _ => None,
        }
    }
}

/// How a slider's control points are interpolated.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CurveKind {
    /// `B` — piecewise Bézier, split at repeated control points.
    Bezier,
    /// `C` — centripetal Catmull-Rom. Legacy; osu! kept it for old maps.
    Catmull,
    /// `L` — straight segments.
    Linear,
    /// `P` — circular arc through three points. Falls back to Bézier when the
    /// points are collinear or there are not exactly three.
    PerfectCircle,
}

impl CurveKind {
    pub fn from_char(c: char) -> Option<Self> {
        match c {
            'B' => Some(CurveKind::Bezier),
            'C' => Some(CurveKind::Catmull),
            'L' => Some(CurveKind::Linear),
            'P' => Some(CurveKind::PerfectCircle),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Pos {
    pub x: f64,
    pub y: f64,
}

impl Pos {
    pub fn new(x: f64, y: f64) -> Self {
        Self { x, y }
    }

    pub fn distance_to(self, other: Pos) -> f64 {
        ((self.x - other.x).powi(2) + (self.y - other.y).powi(2)).sqrt()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Slider {
    pub curve: CurveKind,
    /// Control points **including** the slider head, which the file stores
    /// separately as the object's own x/y. Keeping it here means the curve code
    /// never has to remember to prepend it.
    pub control_points: Vec<Pos>,
    /// Number of times the ball traverses the path. 1 means no repeat, so this
    /// is not a repeat count despite often being called one.
    pub slides: i32,
    /// Path length in osu! pixels, as recorded in the file. osu! trusts this
    /// over the geometry it computes, so we keep it verbatim.
    pub length: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum HitObjectKind {
    Circle,
    Slider(Slider),
    Spinner {
        end_time: i32,
    },
    /// osu!mania only. Parsed so mania files fail loudly rather than being
    /// mistaken for a std map with strange objects.
    Hold {
        end_time: i32,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub struct HitObject {
    /// Position as written in the file, before stacking is applied. Use
    /// [`HitObject::stacked_pos`] for where the object is actually drawn and
    /// judged.
    pub pos: Pos,
    /// Milliseconds. Integers in the file format; stable parses them as such.
    pub time: i32,
    pub kind: HitObjectKind,
    pub new_combo: bool,
    /// How many combo colours to skip past on a new combo.
    pub combo_skip: i32,
    pub hit_sound: i32,
    /// How many objects this one is stacked on top of. Filled in by
    /// [`crate::beatmap::Beatmap::apply_stacking`]; zero until then, and zero
    /// for the great majority of objects on a normal map.
    ///
    /// Negative values are legitimate: circles that fall under the end of a
    /// slider stack downward instead of upward.
    pub stack_height: i32,
}

impl HitObject {
    /// Where the object is drawn and where osu! tests the cursor against.
    ///
    /// `scale` comes from [`Difficulty::scale`]. Passing it in rather than
    /// storing it keeps this type free of the difficulty settings, which change
    /// under mods while the object does not.
    pub fn stacked_pos(&self, scale: f64) -> Pos {
        let offset = f64::from(self.stack_height) * scale * -STACK_OFFSET_PER_LEVEL;
        Pos::new(self.pos.x + offset, self.pos.y + offset)
    }

    pub fn is_circle(&self) -> bool {
        matches!(self.kind, HitObjectKind::Circle)
    }

    pub fn is_slider(&self) -> bool {
        matches!(self.kind, HitObjectKind::Slider(_))
    }

    pub fn is_spinner(&self) -> bool {
        matches!(self.kind, HitObjectKind::Spinner { .. })
    }

    /// End time for objects that have one; the start time for circles.
    ///
    /// Sliders are **not** resolved here: their duration depends on the timing
    /// point in effect, which this type does not know about. Use
    /// [`crate::beatmap::Beatmap::slider_end_time`].
    pub fn end_time_simple(&self) -> Option<i32> {
        match &self.kind {
            HitObjectKind::Circle => Some(self.time),
            HitObjectKind::Spinner { end_time } | HitObjectKind::Hold { end_time } => {
                Some(*end_time)
            }
            HitObjectKind::Slider(_) => None,
        }
    }
}

/// A red or green line.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TimingPoint {
    /// Milliseconds. Kept as `f64` because lazer writes fractional offsets;
    /// stable truncates when it reads them.
    pub time: f64,
    /// The raw value from the file. Its meaning depends on `uninherited`, which
    /// is the single most error-prone thing in this format — see
    /// [`TimingPoint::beat_length`] and [`TimingPoint::slider_velocity`].
    pub raw_beat_length: f64,
    pub meter: i32,
    pub uninherited: bool,
    pub effects: i32,
}

impl TimingPoint {
    /// Milliseconds per beat, for an uninherited (red) line. `None` for green
    /// lines, which carry no tempo of their own.
    pub fn beat_length(&self) -> Option<f64> {
        if self.uninherited && self.raw_beat_length > 0.0 {
            Some(self.raw_beat_length)
        } else {
            None
        }
    }

    /// Slider velocity multiplier for an inherited (green) line.
    ///
    /// Green lines store this as a negative percentage: `-100` means 1.0x,
    /// `-50` means 2.0x, `-200` means 0.5x. Reading the field as a duration
    /// gives a negative beat length and every downstream number goes wrong
    /// quietly, which is why the raw field is not exposed under a friendly name.
    pub fn slider_velocity(&self) -> Option<f64> {
        if !self.uninherited && self.raw_beat_length < 0.0 {
            Some(-100.0 / self.raw_beat_length)
        } else {
            None
        }
    }

    pub fn kiai(&self) -> bool {
        self.effects & 1 != 0
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Difficulty {
    pub hp_drain_rate: f64,
    pub circle_size: f64,
    pub overall_difficulty: f64,
    pub approach_rate: f64,
    pub slider_multiplier: f64,
    pub slider_tick_rate: f64,
}

impl Difficulty {
    /// osu!'s piecewise-linear reading of a 0–10 setting: 0 gives `min`, 5
    /// gives `mid`, 10 gives `max`, linear on each side.
    ///
    /// The two halves have different slopes whenever `mid` is not the midpoint
    /// of `min` and `max`, which is the case for every range osu! uses. A single
    /// linear interpolation from `min` to `max` is a plausible-looking mistake
    /// that is correct at exactly three points.
    fn range(value: f64, min: f64, mid: f64, max: f64) -> f64 {
        if value > 5.0 {
            mid + (max - mid) * (value - 5.0) / 5.0
        } else if value < 5.0 {
            mid - (mid - min) * (5.0 - value) / 5.0
        } else {
            mid
        }
    }

    /// How long an object is visible before its hit time, in milliseconds.
    ///
    /// Also the input to the stacking window, which is why it lives here rather
    /// than in a drawing layer this crate does not have.
    pub fn preempt(&self) -> f64 {
        Self::range(self.approach_rate, 1800.0, 1200.0, 450.0)
    }

    /// Object scale factor from Circle Size.
    ///
    /// Computed in `f32` because stable does. The difference from `f64` is far
    /// below a pixel, but matching the arithmetic costs nothing and removes a
    /// question that would otherwise have to be re-answered every time a
    /// position looks marginally off.
    pub fn scale(&self) -> f64 {
        let cs = self.circle_size as f32;
        f64::from((1.0_f32 - 0.7_f32 * (cs - 5.0) / 5.0) / 2.0)
    }

    /// Hit-object radius in osu! pixels.
    pub fn radius(&self) -> f64 {
        OBJECT_RADIUS * self.scale() * STABLE_RADIUS_FUDGE
    }

    /// Half-width of the 300 window, in milliseconds of map time.
    ///
    /// Map time, not real time: under DT the window is unchanged here and the
    /// clock runs faster, which is the same thing seen from the other side. A
    /// consumer converting errors to real time divides by the rate.
    ///
    /// Returned unrounded. stable compares against an integer window, but which
    /// rounding it uses is a question for whatever does the comparing — this
    /// crate should not bake one in and then be quoted as the authority for it.
    pub fn hit_window_300(&self) -> f64 {
        80.0 - 6.0 * self.overall_difficulty
    }

    /// Half-width of the 100 window, in milliseconds of map time.
    pub fn hit_window_100(&self) -> f64 {
        140.0 - 8.0 * self.overall_difficulty
    }

    /// Half-width of the 50 window, in milliseconds of map time. Beyond this a
    /// press on an object is a miss.
    pub fn hit_window_50(&self) -> f64 {
        200.0 - 10.0 * self.overall_difficulty
    }
}

impl Default for Difficulty {
    /// osu!'s own defaults for a field that is absent.
    fn default() -> Self {
        Self {
            hp_drain_rate: 5.0,
            circle_size: 5.0,
            overall_difficulty: 5.0,
            approach_rate: 5.0,
            slider_multiplier: 1.4,
            slider_tick_rate: 1.0,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Metadata {
    pub title: String,
    pub title_unicode: String,
    pub artist: String,
    pub artist_unicode: String,
    pub creator: String,
    pub version: String,
    pub source: String,
    pub tags: Vec<String>,
    pub beatmap_id: Option<i32>,
    pub beatmap_set_id: Option<i32>,
    /// File name of the background image, relative to the beatmap's folder.
    ///
    /// From the `[Events]` section rather than a second reader: a consumer that
    /// wants to show the map needs it, and opening the file again to fish out
    /// one line is how a project ends up with two parsers that disagree.
    pub background: Option<String>,
}

/// Whether stack heights on a beatmap mean anything.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stacking {
    /// Computed with the algorithm stable uses for format v6 and later.
    Applied,
    /// The file predates format v6, which stacked by a different algorithm.
    ///
    /// That algorithm is not implemented, so every stack height is zero and
    /// positions are the raw file positions. This is recorded rather than
    /// silently assumed, because "no stacking" and "stacking that happened to
    /// produce no offsets" are indistinguishable from the heights alone.
    UnsupportedLegacyFormat,
}

#[derive(Debug, Clone)]
pub struct Beatmap {
    /// The `osu file format vN` header. Several defaults depend on it, so it is
    /// kept rather than discarded after parsing.
    pub format_version: i32,
    /// Whether [`Beatmap::apply_stacking`] was able to run on this file.
    pub stacking: Stacking,
    pub mode: Mode,
    pub stack_leniency: f64,
    pub audio_lead_in: i32,
    pub preview_time: i32,
    pub difficulty: Difficulty,
    pub metadata: Metadata,
    pub timing_points: Vec<TimingPoint>,
    pub hit_objects: Vec<HitObject>,
    /// Sections and keys we parsed but do not model, kept so nothing is lost
    /// silently. Useful when a rule needs a field this type has not grown yet.
    pub raw_sections: HashMap<String, Vec<String>>,
}

impl Beatmap {
    pub fn circles(&self) -> impl Iterator<Item = &HitObject> {
        self.hit_objects.iter().filter(|o| o.is_circle())
    }

    pub fn sliders(&self) -> impl Iterator<Item = &HitObject> {
        self.hit_objects.iter().filter(|o| o.is_slider())
    }

    /// The uninherited (red) line in effect at `time`.
    ///
    /// Falls back to the first uninherited line for objects that precede every
    /// timing point, which happens in practice and which osu! also does.
    pub fn timing_point_at(&self, time: f64) -> Option<&TimingPoint> {
        self.timing_points
            .iter()
            .rfind(|p| p.uninherited && p.time <= time)
            .or_else(|| self.timing_points.iter().find(|p| p.uninherited))
    }

    /// The effective slider velocity multiplier at `time`.
    ///
    /// Green lines apply until the next line of *either* kind: a red line
    /// resets the multiplier to 1.0. Treating green lines as applying until the
    /// next green line is a classic way to get slider durations wrong on maps
    /// with tempo changes.
    pub fn slider_velocity_at(&self, time: f64) -> f64 {
        self.timing_points
            .iter()
            .rfind(|p| p.time <= time)
            .and_then(|p| p.slider_velocity())
            .unwrap_or(1.0)
    }

    /// Duration of one slide, in milliseconds.
    ///
    /// `length / (SliderMultiplier * 100 * SV) * beatLength`
    pub fn slider_slide_duration(&self, object: &HitObject) -> Option<f64> {
        let HitObjectKind::Slider(slider) = &object.kind else {
            return None;
        };
        let time = f64::from(object.time);
        let beat_length = self.timing_point_at(time)?.beat_length()?;
        let velocity = self.difficulty.slider_multiplier * 100.0 * self.slider_velocity_at(time);
        if velocity <= 0.0 {
            return None;
        }
        Some(slider.length / velocity * beat_length)
    }

    /// End time of a slider, accounting for repeats.
    pub fn slider_end_time(&self, object: &HitObject) -> Option<f64> {
        let HitObjectKind::Slider(slider) = &object.kind else {
            return None;
        };
        let slide = self.slider_slide_duration(object)?;
        Some(f64::from(object.time) + slide * f64::from(slider.slides))
    }
}
