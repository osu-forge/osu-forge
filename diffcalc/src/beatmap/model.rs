//! Beatmap data model.
//!
//! Field names follow the osu! file format's own names rather than being
//! prettified, so a reader can check any of them against the format
//! documentation without a translation step. Where a name is genuinely
//! misleading the doc comment says so instead of the field being renamed.

use std::collections::HashMap;

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
    pub pos: Pos,
    /// Milliseconds. Integers in the file format; stable parses them as such.
    pub time: i32,
    pub kind: HitObjectKind,
    pub new_combo: bool,
    /// How many combo colours to skip past on a new combo.
    pub combo_skip: i32,
    pub hit_sound: i32,
}

impl HitObject {
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
}

#[derive(Debug, Clone)]
pub struct Beatmap {
    /// The `osu file format vN` header. Several defaults depend on it, so it is
    /// kept rather than discarded after parsing.
    pub format_version: i32,
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
