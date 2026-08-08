//! Python bindings for the beatmap layer.
//!
//! The replay hit simulator lives on the Python side and needs to know where
//! every object is and when. It reaches that through here rather than through a
//! second `.osu` parser, because two parsers eventually disagree — and a
//! disagreement between the parser feeding difficulty calculation and the one
//! feeding hit simulation would surface as hit errors that look like a player
//! problem rather than like a bug.
//!
//! The surface is deliberately narrow: object geometry, the difficulty
//! derivations that depend on it, and the mod flags. Anything a caller could
//! compute for itself from those is left to the caller.

// Linking a cdylib with MSVC makes the linker announce that it is creating the
// import library, and rustc surfaces that as a warning. It is not actionable and
// it appears on every build on Windows, which is the only platform this crate
// targets. Silenced here rather than by loosening `-D warnings` in CI.
#![allow(linker_messages)]

use osu_forge_diffcalc::beatmap::{self, HitObjectKind, NestedKind, Stacking};
use osu_forge_diffcalc::mods::Mods;
use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

create_exception!(
    osu_forge_diffcalc,
    BeatmapError,
    PyException,
    "A `.osu` file could not be read."
);

/// What kind of object this is, as far as judgement is concerned.
///
/// osu!mania hold notes are absent: the parser rejects files for other rulesets
/// outright, so one cannot reach this far.
// These types only ever travel Rust -> Python. `skip_from_py_object` says so
// rather than deriving a conversion nothing calls.
#[pyclass(eq, eq_int, skip_from_py_object, module = "osu_forge_diffcalc")]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObjectKind {
    Circle,
    Slider,
    Spinner,
}

/// Which kind of scoreable point along a slider this is.
#[pyclass(eq, eq_int, skip_from_py_object, module = "osu_forge_diffcalc")]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PartKind {
    Tick,
    Repeat,
    /// The slider's end, judged 36 ms early — which is what lets a slider be
    /// released slightly early without being dropped.
    Tail,
}

/// A point along a slider that is scored on its own.
///
/// A slider's grade comes from how many of these the player collected, so
/// reproducing judgement counts needs them. The head is not among them: it is
/// the hit object itself.
#[pyclass(frozen, get_all, skip_from_py_object, module = "osu_forge_diffcalc")]
#[derive(Clone)]
pub struct SliderPart {
    pub time: f64,
    /// Where the ball is at `time`, with the slider's stack offset applied.
    pub x: f64,
    pub y: f64,
    pub kind: PartKind,
}

#[pymethods]
impl SliderPart {
    fn __repr__(&self) -> String {
        format!(
            "SliderPart({:?} at {:.0}ms, ({:.1}, {:.1}))",
            self.kind, self.time, self.x, self.y
        )
    }
}

#[pyclass(frozen, get_all, skip_from_py_object, module = "osu_forge_diffcalc")]
#[derive(Clone)]
pub struct HitObject {
    /// Milliseconds of map time. Under Double Time the clock runs faster; these
    /// numbers do not change.
    pub time: i32,
    /// When the object stops accepting input. Equal to `time` for a circle,
    /// fractional for a slider because its duration comes from the beat length.
    pub end_time: f64,
    /// Where the object is drawn, and where osu! tests the cursor against.
    /// Stacking is already applied.
    pub x: f64,
    pub y: f64,
    /// Position as written in the file, before stacking. Exposed so a caller can
    /// measure how much stacking moved things — not for judging against.
    pub raw_x: f64,
    pub raw_y: f64,
    pub kind: ObjectKind,
    pub new_combo: bool,
    pub stack_height: i32,
    /// Ticks, repeat points and the tail, in time order. Empty for anything
    /// that is not a slider.
    pub parts: Vec<SliderPart>,
}

#[pymethods]
impl HitObject {
    fn __repr__(&self) -> String {
        format!(
            "HitObject({:?} at {}ms, ({:.1}, {:.1}), stack {})",
            self.kind, self.time, self.x, self.y, self.stack_height
        )
    }
}

#[pyclass(frozen, module = "osu_forge_diffcalc")]
pub struct Beatmap {
    inner: beatmap::Beatmap,
    objects: Py<PyList>,
}

fn build_objects(py: Python<'_>, map: &beatmap::Beatmap) -> PyResult<Py<PyList>> {
    let scale = map.difficulty.scale();
    let mut items: Vec<Py<HitObject>> = Vec::with_capacity(map.hit_objects.len());
    for object in &map.hit_objects {
        let stacked = object.stacked_pos(scale);
        let start = f64::from(object.time);
        let (end_time, kind) = match &object.kind {
            HitObjectKind::Circle => (start, ObjectKind::Circle),
            HitObjectKind::Slider(_) => (
                map.slider_end_time(object).unwrap_or(start),
                ObjectKind::Slider,
            ),
            HitObjectKind::Spinner { end_time } => (f64::from(*end_time), ObjectKind::Spinner),
            // Unreachable: the parser rejects non-osu!std files. Mapped rather
            // than panicked on, because a panic here would be a crash in someone
            // else's interpreter.
            HitObjectKind::Hold { end_time } => (f64::from(*end_time), ObjectKind::Circle),
        };
        let parts = map
            .slider_parts(object)
            .into_iter()
            .map(|part| SliderPart {
                time: part.time,
                x: part.pos.x,
                y: part.pos.y,
                kind: match part.kind {
                    NestedKind::Tick => PartKind::Tick,
                    NestedKind::Repeat => PartKind::Repeat,
                    NestedKind::Tail => PartKind::Tail,
                },
            })
            .collect();
        items.push(Py::new(
            py,
            HitObject {
                time: object.time,
                end_time,
                x: stacked.x,
                y: stacked.y,
                raw_x: object.pos.x,
                raw_y: object.pos.y,
                kind,
                new_combo: object.new_combo,
                stack_height: object.stack_height,
                parts,
            },
        )?);
    }
    Ok(PyList::new(py, items)?.unbind())
}

impl Beatmap {
    fn wrap(py: Python<'_>, inner: beatmap::Beatmap) -> PyResult<Self> {
        let objects = build_objects(py, &inner)?;
        Ok(Self { inner, objects })
    }
}

#[pymethods]
impl Beatmap {
    /// Parse a `.osu` file from bytes.
    #[staticmethod]
    fn from_bytes(py: Python<'_>, data: &[u8]) -> PyResult<Self> {
        let inner = beatmap::parse(data).map_err(|e| BeatmapError::new_err(format!("{e}")))?;
        Self::wrap(py, inner)
    }

    /// Parse a `.osu` file from disk.
    #[staticmethod]
    fn from_file(py: Python<'_>, path: std::path::PathBuf) -> PyResult<Self> {
        let data = std::fs::read(&path)
            .map_err(|e| BeatmapError::new_err(format!("{}: {e}", path.display())))?;
        let inner = beatmap::parse(&data)
            .map_err(|e| BeatmapError::new_err(format!("{}: {e}", path.display())))?;
        Self::wrap(py, inner)
    }

    /// The same beatmap as the player experienced it under `mods`.
    ///
    /// Only Hard Rock and Easy change anything; both move the approach rate,
    /// which moves the stacking window, so the result is restacked. Double Time
    /// and Half Time are not applied here at all — they change the clock, not
    /// the beatmap, and a caller reporting in real time divides by the rate.
    fn with_mods(&self, py: Python<'_>, mods: u32) -> PyResult<Self> {
        let mut inner = self.inner.clone();
        inner.apply_mods(Mods(mods));
        Self::wrap(py, inner)
    }

    /// Hit objects in time order, with stacking applied.
    #[getter]
    fn objects(&self, py: Python<'_>) -> Py<PyList> {
        self.objects.clone_ref(py)
    }

    #[getter]
    fn format_version(&self) -> i32 {
        self.inner.format_version
    }

    /// `True` when stack heights were computed. `False` means the file predates
    /// format v6, whose different stacking algorithm is not implemented, so
    /// every position is the raw file position.
    #[getter]
    fn stacked(&self) -> bool {
        self.inner.stacking == Stacking::Applied
    }

    #[getter]
    fn stack_leniency(&self) -> f64 {
        self.inner.stack_leniency
    }

    #[getter]
    fn circle_size(&self) -> f64 {
        self.inner.difficulty.circle_size
    }

    #[getter]
    fn approach_rate(&self) -> f64 {
        self.inner.difficulty.approach_rate
    }

    #[getter]
    fn overall_difficulty(&self) -> f64 {
        self.inner.difficulty.overall_difficulty
    }

    #[getter]
    fn hp_drain_rate(&self) -> f64 {
        self.inner.difficulty.hp_drain_rate
    }

    #[getter]
    fn slider_multiplier(&self) -> f64 {
        self.inner.difficulty.slider_multiplier
    }

    #[getter]
    fn slider_tick_rate(&self) -> f64 {
        self.inner.difficulty.slider_tick_rate
    }

    /// Hit-object radius in osu! pixels.
    #[getter]
    fn radius(&self) -> f64 {
        self.inner.difficulty.radius()
    }

    /// How long an object is visible before its hit time, in milliseconds.
    #[getter]
    fn preempt(&self) -> f64 {
        self.inner.difficulty.preempt()
    }

    /// Half-widths of the judgement windows, in milliseconds of map time,
    /// unrounded. stable compares against an integer; which rounding it uses is
    /// the caller's decision to make and defend.
    #[getter]
    fn hit_window_300(&self) -> f64 {
        self.inner.difficulty.hit_window_300()
    }

    #[getter]
    fn hit_window_100(&self) -> f64 {
        self.inner.difficulty.hit_window_100()
    }

    #[getter]
    fn hit_window_50(&self) -> f64 {
        self.inner.difficulty.hit_window_50()
    }

    #[getter]
    fn title(&self) -> &str {
        &self.inner.metadata.title
    }

    #[getter]
    fn artist(&self) -> &str {
        &self.inner.metadata.artist
    }

    #[getter]
    fn creator(&self) -> &str {
        &self.inner.metadata.creator
    }

    #[getter]
    fn version(&self) -> &str {
        &self.inner.metadata.version
    }

    #[getter]
    fn beatmap_id(&self) -> Option<i32> {
        self.inner.metadata.beatmap_id
    }

    #[getter]
    fn beatmap_set_id(&self) -> Option<i32> {
        self.inner.metadata.beatmap_set_id
    }

    fn __repr__(&self) -> String {
        format!(
            "Beatmap({} - {} [{}], {} objects)",
            self.inner.metadata.artist,
            self.inner.metadata.title,
            self.inner.metadata.version,
            self.inner.hit_objects.len()
        )
    }
}

/// The mod bit values this crate interprets.
///
/// Exported so the Python side can assert its own copy of the table agrees.
/// Two hand-maintained copies of a constant table is a real risk, and a drift
/// would show up as difficulty settings that are wrong only under Hard Rock —
/// which nobody would notice for a long time.
fn mod_table(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let table = PyDict::new(py);
    for (name, value) in [
        ("NO_FAIL", Mods::NO_FAIL),
        ("EASY", Mods::EASY),
        ("TOUCH_DEVICE", Mods::TOUCH_DEVICE),
        ("HIDDEN", Mods::HIDDEN),
        ("HARD_ROCK", Mods::HARD_ROCK),
        ("SUDDEN_DEATH", Mods::SUDDEN_DEATH),
        ("DOUBLE_TIME", Mods::DOUBLE_TIME),
        ("RELAX", Mods::RELAX),
        ("HALF_TIME", Mods::HALF_TIME),
        ("NIGHTCORE", Mods::NIGHTCORE),
        ("FLASHLIGHT", Mods::FLASHLIGHT),
        ("AUTOPLAY", Mods::AUTOPLAY),
        ("SPUN_OUT", Mods::SPUN_OUT),
        ("AUTOPILOT", Mods::AUTOPILOT),
        ("PERFECT", Mods::PERFECT),
        ("TARGET_PRACTICE", Mods::TARGET_PRACTICE),
        ("SCORE_V2", Mods::SCORE_V2),
    ] {
        table.set_item(name, value)?;
    }
    Ok(table)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = m.py();
    m.add_class::<Beatmap>()?;
    m.add_class::<HitObject>()?;
    m.add_class::<ObjectKind>()?;
    m.add_class::<SliderPart>()?;
    m.add_class::<PartKind>()?;
    m.add("BeatmapError", py.get_type::<BeatmapError>())?;
    m.add("MODS", mod_table(py)?)?;
    m.add("STACK_DISTANCE", beatmap::STACK_DISTANCE)?;
    m.add("VERSION", osu_forge_diffcalc::VERSION)?;
    Ok(())
}
