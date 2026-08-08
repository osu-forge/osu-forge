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

use osu_forge_diffcalc::beatmap::{self, HitObjectKind, ParseError};

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
