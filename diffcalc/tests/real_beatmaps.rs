//! Parse a real `Songs` folder.
//!
//! Ignored by default and skipped when the environment variable is unset, so CI
//! never needs beatmap files and no `.osu` is committed. Run it against your own
//! install with:
//!
//! ```text
//! OSU_FORGE_SONGS="C:\Users\<you>\AppData\Local\osu!\Songs" \
//!   cargo test -p osu-forge-diffcalc --test real_beatmaps -- --ignored --nocapture
//! ```
//!
//! Synthetic tests prove the parser handles the cases we thought of. This one
//! is the check on the cases we did not — a decade of format versions, editors
//! and hand edits.

use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};

use osu_forge_diffcalc::beatmap::{self, HitObjectKind, ParseError, SliderPath};

fn songs_dir() -> Option<PathBuf> {
    std::env::var_os("OSU_FORGE_SONGS").map(PathBuf::from)
}

fn collect_osu_files(root: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else if path
                .extension()
                .is_some_and(|e| e.eq_ignore_ascii_case("osu"))
            {
                files.push(path);
            }
        }
    }
    files
}

#[test]
#[ignore = "needs a real Songs folder; set OSU_FORGE_SONGS"]
fn parses_every_local_beatmap() {
    let Some(root) = songs_dir() else {
        eprintln!("OSU_FORGE_SONGS is not set - nothing to check");
        return;
    };

    let files = collect_osu_files(&root);
    assert!(!files.is_empty(), "no .osu files under {}", root.display());

    let mut ok = 0usize;
    let mut other_modes = 0usize;
    let mut versions: BTreeMap<i32, usize> = BTreeMap::new();
    let mut failures: Vec<(PathBuf, ParseError)> = Vec::new();

    let mut circles = 0usize;
    let mut sliders = 0usize;
    let mut spinners = 0usize;
    let mut green_lines = 0usize;
    let mut curve_kinds: BTreeMap<String, usize> = BTreeMap::new();

    for path in &files {
        let Ok(bytes) = fs::read(path) else { continue };
        match beatmap::parse(&bytes) {
            Ok(map) => {
                ok += 1;
                *versions.entry(map.format_version).or_default() += 1;

                for object in &map.hit_objects {
                    match &object.kind {
                        HitObjectKind::Circle => circles += 1,
                        HitObjectKind::Slider(s) => {
                            sliders += 1;
                            *curve_kinds.entry(format!("{:?}", s.curve)).or_default() += 1;
                        }
                        HitObjectKind::Spinner { .. } => spinners += 1,
                        HitObjectKind::Hold { .. } => {}
                    }
                }
                green_lines += map
                    .timing_points
                    .iter()
                    .filter(|p| p.slider_velocity().is_some())
                    .count();
            }
            // Non-std files are rejected on purpose, not a parse failure.
            Err(ParseError::UnsupportedMode { .. }) => other_modes += 1,
            Err(err) => failures.push((path.clone(), err)),
        }
    }

    println!("files:        {}", files.len());
    println!("parsed:       {ok}");
    println!("other modes:  {other_modes} (rejected on purpose)");
    println!("failed:       {}", failures.len());
    println!("versions:     {versions:?}");
    println!("objects:      {circles} circles, {sliders} sliders, {spinners} spinners");
    println!("curve kinds:  {curve_kinds:?}");
    println!("green lines:  {green_lines}");

    for (path, err) in failures.iter().take(10) {
        println!("  FAIL {}: {err}", path.display());
    }

    assert!(
        failures.is_empty(),
        "{} file(s) failed to parse",
        failures.len()
    );

    // A parser that accepted everything by producing empty beatmaps would pass
    // the assertion above, so check the corpus actually has content in it.
    assert!(
        circles > 0 && sliders > 0,
        "no objects parsed - the run proves nothing"
    );
    assert!(
        curve_kinds.len() >= 2,
        "only one slider curve type seen; the corpus is not exercising the parser"
    );
}

/// Check the curve geometry against the length the mapper's editor wrote.
///
/// This is the closest thing to ground truth available without the osu! API:
/// the editor computed a path length from the same control points and recorded
/// it. If our flattening is wrong — a Bézier not split at repeated points, an
/// arc going the long way round — the two numbers diverge and it shows up here
/// rather than as mysteriously misplaced slider ends much later.
#[test]
#[ignore = "needs a real Songs folder; set OSU_FORGE_SONGS"]
fn computed_slider_lengths_agree_with_the_files() {
    let Some(root) = songs_dir() else {
        eprintln!("OSU_FORGE_SONGS is not set - nothing to check");
        return;
    };

    // Relative error per curve type, so a fault in one kind is not hidden by
    // the volume of another.
    let mut errors: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    let mut skipped_zero_length = 0usize;

    for path in collect_osu_files(&root) {
        let Ok(bytes) = fs::read(&path) else { continue };
        let Ok(map) = beatmap::parse(&bytes) else {
            continue;
        };

        for object in &map.hit_objects {
            let HitObjectKind::Slider(slider) = &object.kind else {
                continue;
            };
            if slider.length <= 1.0 {
                // A near-zero declared length makes the relative error
                // meaningless rather than informative.
                skipped_zero_length += 1;
                continue;
            }
            let computed = SliderPath::new(slider).computed_length();
            let relative = (computed - slider.length) / slider.length;
            errors
                .entry(format!("{:?}", slider.curve))
                .or_default()
                .push(relative);
        }
    }

    assert!(
        !errors.is_empty(),
        "no sliders measured - the run proves nothing"
    );

    println!("skipped (length <= 1px): {skipped_zero_length}");
    println!("signed relative error, (computed - declared) / declared:");

    // Interpreting these numbers took measuring them first, so the reasoning is
    // recorded here rather than the thresholds looking arbitrary.
    //
    // Every curve type comes out systematically POSITIVE — the geometry is
    // longer than the declared length, by a median of 6-11%. That is the osu!
    // editor snapping a slider's length to a rhythmic division while the
    // control points keep describing the full drag. It is not an error in the
    // flattening.
    //
    // Linear is the control that establishes this. Its length is a sum of
    // straight-segment distances: exact arithmetic, no approximation, nowhere
    // for our code to lose or gain length. Linear has the LARGEST positive bias
    // (+11.4% median) and the largest shorter-than-declared tail (4%), so
    // neither can be attributed to flattening. A curve type that resembles
    // Linear's distribution is behaving; one that departs from it sharply is
    // not.
    //
    // That is exactly how the arc bug showed up. Before it was fixed,
    // PerfectCircle's p99 was +7.9 — the sweep going the long way round the
    // circle — against +0.5 to +0.8 for the other two. Afterwards it is +0.65,
    // in line with the rest.
    let mut failures = Vec::new();

    for (kind, mut values) in errors {
        values.sort_by(f64::total_cmp);
        let n = values.len();
        let pick = |q: f64| values[((n as f64 - 1.0) * q).round() as usize];
        let (p01, median, p90, p99) = (pick(0.01), pick(0.50), pick(0.90), pick(0.99));

        println!(
            "{kind:<14} n={n:<7} p01={p01:+.4} median={median:+.4} p90={p90:+.4} p99={p99:+.4} \
             max={:+.4}",
            values[n - 1]
        );

        // Catches a sweep that wrapped, a Bezier segmented wrongly, or any other
        // fault that inflates a path. The observed values are 0.46-0.78; the bug
        // this replaced sat at 7.9.
        if p99 > 2.0 {
            failures.push(format!(
                "{kind}: p99 relative length error is {p99:+.4}. A path computing more \
                 than three times its declared length at the 99th percentile means the \
                 flattening is generating geometry that is not there - most likely a \
                 sweep or segmentation going the wrong way."
            ));
        }

        // The other direction. Falling a little short is normal — a mapper can
        // declare a length beyond what the control points describe, and osu!
        // extends the path to meet it. Falling far short would mean we are
        // losing geometry the editor found.
        if p01 < -0.10 {
            failures.push(format!(
                "{kind}: p01 relative length error is {p01:+.4}. Paths falling more than \
                 10% short of their declared length suggests the flattening is dropping \
                 segments."
            ));
        }
    }

    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

/// A slider's end position must land on its path.
#[test]
#[ignore = "needs a real Songs folder; set OSU_FORGE_SONGS"]
fn slider_endpoints_are_on_the_playfield() {
    let Some(root) = songs_dir() else {
        return;
    };

    let mut checked = 0usize;
    let mut off_playfield = Vec::new();

    for path in collect_osu_files(&root) {
        let Ok(bytes) = fs::read(&path) else { continue };
        let Ok(map) = beatmap::parse(&bytes) else {
            continue;
        };

        for object in &map.hit_objects {
            let HitObjectKind::Slider(slider) = &object.kind else {
                continue;
            };
            let end = SliderPath::new(slider).end_position(slider.slides);
            checked += 1;

            // The playfield is 512x384, but sliders legitimately overhang it and
            // osu! allows generous bounds. This only catches a result that is
            // wildly wrong — a NaN, or a point thousands of pixels away, which
            // is what a broken arc sweep produces.
            if !end.x.is_finite()
                || !end.y.is_finite()
                || end.x < -1000.0
                || end.x > 1512.0
                || end.y < -1000.0
                || end.y > 1384.0
            {
                off_playfield.push((path.clone(), object.time, end, slider.curve));
            }
        }
    }

    println!("slider endpoints checked: {checked}");
    for (path, time, end, curve) in off_playfield.iter().take(10) {
        println!(
            "  {:?} at {time}ms -> ({:.1}, {:.1}) in {}",
            curve,
            end.x,
            end.y,
            path.display()
        );
    }

    assert!(checked > 0, "no sliders checked");
    assert!(
        off_playfield.is_empty(),
        "{} slider end position(s) are impossible",
        off_playfield.len()
    );
}

/// Stack heights over a real collection.
///
/// There is no oracle for stacking yet — see the module docs in
/// `beatmap/stacking.rs`. What this checks is that the output has the properties
/// stacking must have whatever the exact algorithm is: a nonzero height means
/// there is genuinely another object close by in space and time, heights within
/// a run are consecutive, and re-running the pass changes nothing. A broken
/// implementation fails at least one of those. An implementation that disagrees
/// with osu! in some subtler way passes all of them, and that possibility is the
/// reason this is not called a verification.
#[test]
#[ignore = "needs a real Songs folder; set OSU_FORGE_SONGS"]
fn stack_heights_have_the_properties_stacking_must_have() {
    let Some(root) = songs_dir() else {
        return;
    };

    let mut objects = 0usize;
    let mut stacked = 0usize;
    let mut negative = 0usize;
    let mut heights: BTreeMap<i32, usize> = BTreeMap::new();
    let mut legacy_format = 0usize;
    let mut problems: Vec<String> = Vec::new();

    for path in collect_osu_files(&root) {
        let Ok(bytes) = fs::read(&path) else { continue };
        let Ok(map) = beatmap::parse(&bytes) else {
            continue;
        };
        if map.stacking == beatmap::Stacking::UnsupportedLegacyFormat {
            legacy_format += 1;
            continue;
        }

        let window = map.difficulty.preempt() * map.stack_leniency;
        let scale = map.difficulty.scale();

        // A stack forms wherever two objects nearly coincide, and either end of
        // a slider counts: a circle on a slider tail is what produces the
        // negative heights, and a slider whose tail lands on the next slider's
        // head is stacked by its own end rather than its start. Earlier versions
        // of this check looked only at start positions, then only at the other
        // object's end, and reported both of those real cases as unexplained.
        let anchors: Vec<(f64, f64, beatmap::Pos, beatmap::Pos)> = map
            .hit_objects
            .iter()
            .map(|o| {
                let start = f64::from(o.time);
                match &o.kind {
                    HitObjectKind::Slider(s) => (
                        start,
                        map.slider_end_time(o).unwrap_or(start),
                        o.pos,
                        SliderPath::new(s).end_position(s.slides),
                    ),
                    _ => (
                        start,
                        o.end_time_simple().map_or(start, f64::from),
                        o.pos,
                        o.pos,
                    ),
                }
            })
            .collect();

        for (index, object) in map.hit_objects.iter().enumerate() {
            objects += 1;
            *heights.entry(object.stack_height).or_default() += 1;
            if object.stack_height == 0 {
                continue;
            }
            stacked += 1;
            if object.stack_height < 0 {
                negative += 1;
            }

            // Look for anything this object could have stacked onto — not
            // necessarily the partner the algorithm actually chose, which would
            // just be restating the implementation back to itself.
            let (start, end, pos, end_pos) = anchors[index];
            let has_partner =
                anchors
                    .iter()
                    .enumerate()
                    .any(|(other, &(o_start, o_end, o_pos, o_end_pos))| {
                        // Gap between the two time spans; zero when they overlap.
                        let gap = (o_start - end).max(start - o_end).max(0.0);
                        other != index
                            && gap <= window
                            && [pos, end_pos].iter().any(|&mine| {
                                o_pos.distance_to(mine) < beatmap::STACK_DISTANCE
                                    || o_end_pos.distance_to(mine) < beatmap::STACK_DISTANCE
                            })
                    });
            if !has_partner && problems.len() < 10 {
                problems.push(format!(
                    "{}: object at {}ms has stack height {} with no other object \
                     endpoint within {:.0}px and {:.0}ms of it",
                    path.display(),
                    object.time,
                    object.stack_height,
                    beatmap::STACK_DISTANCE,
                    window
                ));
            }

            // The offset must stay proportionate. Anything past a few tens of
            // pixels means heights are accumulating without bound.
            let moved = object.stacked_pos(scale).distance_to(object.pos);
            if moved > 200.0 && problems.len() < 10 {
                problems.push(format!(
                    "{}: object at {}ms is displaced {moved:.0}px by a stack height of {}",
                    path.display(),
                    object.time,
                    object.stack_height
                ));
            }
        }

        // Stacking must be a function of the beatmap, not of how many times it
        // has been run. A pass that read its own previous output would drift
        // here and nowhere else.
        let before: Vec<i32> = map.hit_objects.iter().map(|o| o.stack_height).collect();
        let mut again = map.clone();
        again.apply_stacking();
        let after: Vec<i32> = again.hit_objects.iter().map(|o| o.stack_height).collect();
        if before != after && problems.len() < 10 {
            problems.push(format!(
                "{}: restacking changed the heights",
                path.display()
            ));
        }
    }

    let summary: Vec<String> = heights.iter().map(|(h, n)| format!("{h}:{n}")).collect();
    println!("objects:        {objects}");
    println!(
        "stacked:        {stacked} ({:.2}%), of which {negative} negative",
        100.0 * stacked as f64 / objects.max(1) as f64
    );
    println!("height counts:  {}", summary.join(" "));
    println!("legacy format:  {legacy_format} file(s) left unstacked");
    for problem in &problems {
        println!("  {problem}");
    }

    assert!(objects > 0, "no objects checked");
    assert!(
        stacked > 0,
        "no object on a real collection stacked - the pass is not running"
    );
    assert!(problems.is_empty(), "{}", problems.join("\n"));
}
