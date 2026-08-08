//! `.osu` file parser.
//!
//! Written against the osu! file-format documentation and checked against real
//! files, not ported from another parser. The format is a fact about the game;
//! see `docs/licensing.md`.
//!
//! Three things here are easy to get wrong and quiet when you do:
//!
//! 1. **`ApproachRate` did not exist before format v8.** In an older file it is
//!    absent and osu! uses `OverallDifficulty` in its place. Defaulting it to 5
//!    instead silently changes the difficulty of every old map.
//! 2. **Green timing lines store a negative percentage, not a duration.**
//!    Reading the field as a beat length yields a negative tempo and wrong
//!    slider durations everywhere. The model exposes it only through
//!    [`TimingPoint::slider_velocity`].
//! 3. **A hit object's type field is a bitfield, not an enum.** Bits 2 and 4-6
//!    carry new-combo and colour-skip information, so `type == 1` is not how you
//!    test for a circle.

use std::collections::HashMap;

use super::model::{
    Beatmap, CurveKind, Difficulty, HitObject, HitObjectKind, Metadata, Mode, Pos, Slider,
    Stacking, TimingPoint,
};

/// Why a file could not be parsed.
///
/// Every variant names the line, because "malformed beatmap" is not something
/// a user can act on.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ParseError {
    /// No `osu file format vN` header.
    MissingHeader,
    UnsupportedMode {
        mode_id: i32,
    },
    /// A field that must parse did not.
    BadValue {
        section: String,
        line_number: usize,
        line: String,
        reason: String,
    },
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ParseError::MissingHeader => {
                write!(f, "no 'osu file format vN' header; this is not a .osu file")
            }
            ParseError::UnsupportedMode { mode_id } => {
                write!(
                    f,
                    "beatmap is for game mode {mode_id}, and only osu!standard (0) is supported"
                )
            }
            ParseError::BadValue {
                section,
                line_number,
                line,
                reason,
            } => write!(f, "[{section}] line {line_number}: {reason} in {line:?}"),
        }
    }
}

impl std::error::Error for ParseError {}

struct Parser<'a> {
    section: String,
    line_number: usize,
    line: &'a str,
}

impl Parser<'_> {
    fn err(&self, reason: impl Into<String>) -> ParseError {
        ParseError::BadValue {
            section: self.section.clone(),
            line_number: self.line_number,
            line: self.line.to_string(),
            reason: reason.into(),
        }
    }

    fn f64(&self, raw: &str, what: &str) -> Result<f64, ParseError> {
        raw.trim()
            .parse::<f64>()
            .map_err(|_| self.err(format!("{what} is not a number")))
    }

    fn i32(&self, raw: &str, what: &str) -> Result<i32, ParseError> {
        let trimmed = raw.trim();
        trimmed.parse::<i32>().or_else(|_| {
            // Some editors have written "128.0" where an integer belongs.
            // osu! tolerates it, so we do too rather than rejecting the file.
            trimmed
                .parse::<f64>()
                .map(|v| v as i32)
                .map_err(|_| self.err(format!("{what} is not a number")))
        })
    }

    fn field<'f>(
        &self,
        fields: &[&'f str],
        index: usize,
        what: &str,
    ) -> Result<&'f str, ParseError> {
        fields
            .get(index)
            .copied()
            .ok_or_else(|| self.err(format!("missing {what}")))
    }
}

/// Parse a `.osu` file.
///
/// Input is bytes rather than a string: `.osu` files are UTF-8 in practice but
/// old ones can carry stray bytes from a legacy encoding, and refusing to open
/// a map over one bad character in a tag would be the wrong trade.
pub fn parse(data: &[u8]) -> Result<Beatmap, ParseError> {
    let text = decode(data);

    let mut format_version = None;
    let mut section = String::new();
    let mut raw_sections: HashMap<String, Vec<String>> = HashMap::new();

    let mut mode = Mode::Osu;
    let mut stack_leniency = 0.7;
    let mut audio_lead_in = 0;
    let mut preview_time = -1;
    let mut difficulty = Difficulty::default();
    let mut approach_rate_seen = false;
    let mut metadata = Metadata::default();
    let mut timing_points = Vec::new();
    let mut hit_objects = Vec::new();

    for (index, raw_line) in text.lines().enumerate() {
        let line_number = index + 1;
        let line = raw_line.trim();

        if format_version.is_none() {
            if let Some(version) = parse_header(line) {
                format_version = Some(version);
            }
            // Everything before the header — a BOM line, blank lines, or junk
            // some editors prepend — is skipped rather than treated as content.
            continue;
        }

        if line.is_empty() || line.starts_with("//") {
            continue;
        }

        if let Some(name) = line.strip_prefix('[').and_then(|s| s.strip_suffix(']')) {
            section = name.to_string();
            raw_sections.entry(section.clone()).or_default();
            continue;
        }

        raw_sections
            .entry(section.clone())
            .or_default()
            .push(line.to_string());

        let parser = Parser {
            section: section.clone(),
            line_number,
            line,
        };

        match section.as_str() {
            "General" => {
                let Some((key, value)) = split_key_value(line) else {
                    continue;
                };
                match key {
                    "Mode" => {
                        // Everything downstream — difficulty, pp, the replay hit
                        // simulator — is osu!standard only, so a file for another
                        // ruleset is rejected here rather than handed on to code
                        // that would produce confident nonsense from it.
                        //
                        // Converts are unaffected: an osu!std map played in
                        // another mode still records Mode: 0.
                        let id = parser.i32(value, "Mode")?;
                        match Mode::from_id(id) {
                            Some(Mode::Osu) => mode = Mode::Osu,
                            _ => return Err(ParseError::UnsupportedMode { mode_id: id }),
                        }
                    }
                    "StackLeniency" => stack_leniency = parser.f64(value, "StackLeniency")?,
                    "AudioLeadIn" => audio_lead_in = parser.i32(value, "AudioLeadIn")?,
                    "PreviewTime" => preview_time = parser.i32(value, "PreviewTime")?,
                    _ => {}
                }
            }
            "Difficulty" => {
                let Some((key, value)) = split_key_value(line) else {
                    continue;
                };
                match key {
                    "HPDrainRate" => difficulty.hp_drain_rate = parser.f64(value, key)?,
                    "CircleSize" => difficulty.circle_size = parser.f64(value, key)?,
                    "OverallDifficulty" => {
                        difficulty.overall_difficulty = parser.f64(value, key)?;
                    }
                    "ApproachRate" => {
                        difficulty.approach_rate = parser.f64(value, key)?;
                        approach_rate_seen = true;
                    }
                    "SliderMultiplier" => difficulty.slider_multiplier = parser.f64(value, key)?,
                    "SliderTickRate" => difficulty.slider_tick_rate = parser.f64(value, key)?,
                    _ => {}
                }
            }
            "Metadata" => {
                let Some((key, value)) = split_key_value(line) else {
                    continue;
                };
                let value = value.trim();
                match key {
                    "Title" => metadata.title = value.to_string(),
                    "TitleUnicode" => metadata.title_unicode = value.to_string(),
                    "Artist" => metadata.artist = value.to_string(),
                    "ArtistUnicode" => metadata.artist_unicode = value.to_string(),
                    "Creator" => metadata.creator = value.to_string(),
                    "Version" => metadata.version = value.to_string(),
                    "Source" => metadata.source = value.to_string(),
                    "Tags" => {
                        metadata.tags = value.split_whitespace().map(str::to_string).collect();
                    }
                    "BeatmapID" => metadata.beatmap_id = value.parse().ok(),
                    "BeatmapSetID" => metadata.beatmap_set_id = value.parse().ok(),
                    _ => {}
                }
            }
            "TimingPoints" => timing_points.push(parse_timing_point(&parser)?),
            "HitObjects" => hit_objects.push(parse_hit_object(&parser)?),
            _ => {}
        }
    }

    let format_version = format_version.ok_or(ParseError::MissingHeader)?;

    // ApproachRate did not exist before v8, so in an older file it is always
    // absent and means "same as OverallDifficulty". In a newer file it is
    // occasionally missing anyway, and osu! falls back the same way — so the
    // version does not actually change the behaviour, only how often the case
    // comes up. One branch, not two that look like they differ.
    //
    // Defaulting to 5 instead would silently change the difficulty of every
    // pre-v8 map, which is why this is not left to `Difficulty::default`.
    if !approach_rate_seen {
        difficulty.approach_rate = difficulty.overall_difficulty;
    }

    // Timing points are usually sorted already, but nothing enforces it in the
    // format, and `timing_point_at` relies on the order.
    timing_points.sort_by(|a, b| a.time.total_cmp(&b.time));
    hit_objects.sort_by_key(|o| o.time);

    let mut beatmap = Beatmap {
        format_version,
        // Overwritten by `apply_stacking` on the next line. There is deliberately
        // no "not stacked yet" variant: a beatmap that escaped this function
        // unstacked would be indistinguishable from one that legitimately has no
        // stacks, and every position read off it would be quietly wrong.
        stacking: Stacking::Applied,
        mode,
        stack_leniency,
        audio_lead_in,
        preview_time,
        difficulty,
        metadata,
        timing_points,
        hit_objects,
        raw_sections,
    };
    beatmap.apply_stacking();
    Ok(beatmap)
}

fn decode(data: &[u8]) -> String {
    let data = data.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(data);
    String::from_utf8_lossy(data).into_owned()
}

fn parse_header(line: &str) -> Option<i32> {
    let rest = line.strip_prefix("osu file format v")?;
    rest.trim().parse().ok()
}

fn split_key_value(line: &str) -> Option<(&str, &str)> {
    let (key, value) = line.split_once(':')?;
    Some((key.trim(), value))
}

fn parse_timing_point(parser: &Parser<'_>) -> Result<TimingPoint, ParseError> {
    let fields: Vec<&str> = parser.line.split(',').collect();

    let time = parser.f64(parser.field(&fields, 0, "timing point offset")?, "offset")?;
    let raw_beat_length = parser.f64(parser.field(&fields, 1, "beat length")?, "beat length")?;

    // Everything past the second field is optional: very old files stop there.
    let meter = match fields.get(2) {
        Some(v) if !v.trim().is_empty() => parser.i32(v, "meter")?,
        _ => 4,
    };
    // Absent `uninherited` means an old file, which had only red lines.
    let uninherited = match fields.get(6) {
        Some(v) if !v.trim().is_empty() => parser.i32(v, "uninherited")? != 0,
        _ => true,
    };
    let effects = match fields.get(7) {
        Some(v) if !v.trim().is_empty() => parser.i32(v, "effects")?,
        _ => 0,
    };

    Ok(TimingPoint {
        time,
        raw_beat_length,
        meter,
        uninherited,
        effects,
    })
}

// Hit object type bits. 2 and 4-6 are combo information, not object kinds.
const TYPE_CIRCLE: i32 = 1;
const TYPE_SLIDER: i32 = 1 << 1;
const TYPE_NEW_COMBO: i32 = 1 << 2;
const TYPE_SPINNER: i32 = 1 << 3;
const TYPE_COMBO_SKIP_MASK: i32 = 0b0111_0000;
const TYPE_COMBO_SKIP_SHIFT: i32 = 4;
const TYPE_HOLD: i32 = 1 << 7;

fn parse_hit_object(parser: &Parser<'_>) -> Result<HitObject, ParseError> {
    let fields: Vec<&str> = parser.line.split(',').collect();

    let x = parser.f64(parser.field(&fields, 0, "x")?, "x")?;
    let y = parser.f64(parser.field(&fields, 1, "y")?, "y")?;
    let time = parser.i32(parser.field(&fields, 2, "time")?, "time")?;
    let raw_type = parser.i32(parser.field(&fields, 3, "type")?, "type")?;
    let hit_sound = parser.i32(parser.field(&fields, 4, "hit sound")?, "hit sound")?;

    let new_combo = raw_type & TYPE_NEW_COMBO != 0;
    let combo_skip = (raw_type & TYPE_COMBO_SKIP_MASK) >> TYPE_COMBO_SKIP_SHIFT;

    let kind = if raw_type & TYPE_SLIDER != 0 {
        HitObjectKind::Slider(parse_slider(parser, &fields, Pos::new(x, y))?)
    } else if raw_type & TYPE_SPINNER != 0 {
        HitObjectKind::Spinner {
            end_time: parser.i32(parser.field(&fields, 5, "spinner end time")?, "end time")?,
        }
    } else if raw_type & TYPE_HOLD != 0 {
        // mania hold: end time is before the first ':' of field 5.
        let raw = parser.field(&fields, 5, "hold end time")?;
        let end = raw.split(':').next().unwrap_or(raw);
        HitObjectKind::Hold {
            end_time: parser.i32(end, "hold end time")?,
        }
    } else if raw_type & TYPE_CIRCLE != 0 {
        HitObjectKind::Circle
    } else {
        return Err(parser.err(format!("type {raw_type} names no object kind")));
    };

    Ok(HitObject {
        pos: Pos::new(x, y),
        time,
        kind,
        new_combo,
        combo_skip,
        hit_sound,
        // Filled in once the whole file is read: stacking depends on the
        // approach rate and on the objects on either side.
        stack_height: 0,
    })
}

fn parse_slider(parser: &Parser<'_>, fields: &[&str], head: Pos) -> Result<Slider, ParseError> {
    let path = parser.field(fields, 5, "slider path")?;
    let mut parts = path.split('|');

    let kind_token = parts
        .next()
        .ok_or_else(|| parser.err("slider path is empty"))?;
    let kind_char = kind_token
        .trim()
        .chars()
        .next()
        .ok_or_else(|| parser.err("slider path has no curve type"))?;
    let curve = CurveKind::from_char(kind_char)
        .ok_or_else(|| parser.err(format!("unknown slider curve type {kind_char:?}")))?;

    // The head is stored in the object's own x/y, not in the path, so it is
    // prepended here once rather than remembered at every use site.
    let mut control_points = vec![head];
    for token in parts {
        let (x, y) = token
            .split_once(':')
            .ok_or_else(|| parser.err(format!("control point {token:?} is not 'x:y'")))?;
        control_points.push(Pos::new(
            parser.f64(x, "control point x")?,
            parser.f64(y, "control point y")?,
        ));
    }

    let slides = parser.i32(parser.field(fields, 6, "slide count")?, "slide count")?;
    let length = parser.f64(parser.field(fields, 7, "slider length")?, "slider length")?;

    Ok(Slider {
        curve,
        control_points,
        slides,
        length,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn map(body: &str) -> Beatmap {
        parse(body.as_bytes()).expect("should parse")
    }

    const MINIMAL: &str = "osu file format v14\n\n[General]\nMode: 0\n";

    #[test]
    fn requires_a_header() {
        assert_eq!(
            parse(b"[General]\nMode: 0\n").unwrap_err(),
            ParseError::MissingHeader
        );
    }

    #[test]
    fn reads_the_format_version() {
        assert_eq!(map(MINIMAL).format_version, 14);
        assert_eq!(map("osu file format v3\n").format_version, 3);
    }

    #[test]
    fn an_impossible_format_version_is_accepted_not_rejected() {
        // A real file in a real Songs folder declares v128, which osu! never
        // shipped. osu! reads the number and carries on, so refusing it would
        // make us stricter than the game for no benefit to the user.
        assert_eq!(
            map("osu file format v128\n[General]\nMode: 0\n").format_version,
            128
        );
    }

    #[test]
    fn skips_a_utf8_bom_before_the_header() {
        let mut data = vec![0xEF, 0xBB, 0xBF];
        data.extend_from_slice(MINIMAL.as_bytes());
        assert_eq!(parse(&data).unwrap().format_version, 14);
    }

    #[test]
    fn tolerates_junk_before_the_header() {
        let text = format!("\n\n// exported by something\n{MINIMAL}");
        assert_eq!(map(&text).format_version, 14);
    }

    #[test]
    fn rejects_other_game_modes_by_name() {
        // Rejected at the parser rather than downstream, so a taiko or mania
        // file cannot reach code that would produce confident nonsense from it.
        for mode_id in [1, 2, 3, 7] {
            let text = format!("osu file format v14\n[General]\nMode: {mode_id}\n");
            let err = parse(text.as_bytes()).unwrap_err();
            assert_eq!(err, ParseError::UnsupportedMode { mode_id });
            assert!(err.to_string().contains("osu!standard"), "{err}");
        }
    }

    #[test]
    fn accepts_osu_standard_and_files_with_no_mode_line() {
        // An absent Mode means 0, which is how most older files are written.
        assert_eq!(
            map("osu file format v14\n[General]\nMode: 0\n").mode,
            Mode::Osu
        );
        assert_eq!(map("osu file format v14\n[General]\n").mode, Mode::Osu);
    }

    #[test]
    fn invalid_utf8_does_not_reject_the_file() {
        // A stray byte in a tag must not cost the whole beatmap.
        let mut data = MINIMAL.as_bytes().to_vec();
        data.extend_from_slice(b"[Metadata]\nTags:caf\xC3\x28 fine\n");
        assert!(parse(&data).is_ok());
    }

    mod difficulty {
        use super::*;

        #[test]
        fn absent_approach_rate_falls_back_to_overall_difficulty() {
            // Pre-v8 files have no ApproachRate at all. Defaulting it to 5 would
            // silently change the difficulty of every old map.
            let text = "osu file format v7\n[Difficulty]\nOverallDifficulty:9\n";
            assert_eq!(map(text).difficulty.approach_rate, 9.0);
        }

        #[test]
        fn present_approach_rate_wins() {
            let text = "osu file format v14\n[Difficulty]\nOverallDifficulty:9\nApproachRate:5\n";
            assert_eq!(map(text).difficulty.approach_rate, 5.0);
        }

        #[test]
        fn an_approach_rate_of_zero_is_not_treated_as_absent() {
            let text = "osu file format v14\n[Difficulty]\nOverallDifficulty:9\nApproachRate:0\n";
            assert_eq!(map(text).difficulty.approach_rate, 0.0);
        }

        #[test]
        fn defaults_match_osu() {
            let d = map(MINIMAL).difficulty;
            assert_eq!(d.slider_multiplier, 1.4);
            assert_eq!(d.slider_tick_rate, 1.0);
            assert_eq!(d.circle_size, 5.0);
        }

        #[test]
        fn stack_leniency_defaults_to_the_osu_value() {
            assert_eq!(map(MINIMAL).stack_leniency, 0.7);
        }
    }

    mod timing {
        use super::*;

        fn with_points(points: &str) -> Beatmap {
            map(&format!("osu file format v14\n[TimingPoints]\n{points}"))
        }

        #[test]
        fn red_line_carries_a_beat_length() {
            let b = with_points("0,500,4,2,0,100,1,0\n");
            assert_eq!(b.timing_points[0].beat_length(), Some(500.0));
            assert_eq!(b.timing_points[0].slider_velocity(), None);
        }

        #[test]
        fn green_line_is_a_negative_percentage_not_a_duration() {
            // -100 means 1.0x, -50 means 2.0x. Read as a duration it is a
            // negative tempo and every slider length downstream goes wrong.
            let b = with_points("0,500,4,2,0,100,1,0\n1000,-50,4,2,0,100,0,0\n");
            let green = b.timing_points[1];
            assert_eq!(green.beat_length(), None);
            assert_eq!(green.slider_velocity(), Some(2.0));
        }

        #[test]
        fn slider_velocity_percentages() {
            for (raw, expected) in [(-100.0, 1.0), (-50.0, 2.0), (-200.0, 0.5), (-400.0, 0.25)] {
                let b = with_points(&format!("0,500,4,2,0,100,1,0\n10,{raw},4,2,0,100,0,0\n"));
                assert_eq!(b.timing_points[1].slider_velocity(), Some(expected));
            }
        }

        #[test]
        fn a_red_line_resets_slider_velocity() {
            // Green lines apply until the next line of EITHER kind. Treating
            // them as applying until the next green line gets durations wrong
            // on any map with a tempo change.
            let b =
                with_points("0,500,4,2,0,100,1,0\n100,-50,4,2,0,100,0,0\n200,400,4,2,0,100,1,0\n");
            assert_eq!(b.slider_velocity_at(150.0), 2.0);
            assert_eq!(b.slider_velocity_at(250.0), 1.0);
        }

        #[test]
        fn old_files_without_an_uninherited_field_are_red_lines() {
            let b = with_points("0,500\n");
            assert!(b.timing_points[0].uninherited);
            assert_eq!(b.timing_points[0].beat_length(), Some(500.0));
            assert_eq!(b.timing_points[0].meter, 4);
        }

        #[test]
        fn points_are_sorted_by_time() {
            let b = with_points("2000,500,4,2,0,100,1,0\n0,400,4,2,0,100,1,0\n");
            assert_eq!(b.timing_points[0].time, 0.0);
        }

        #[test]
        fn objects_before_every_timing_point_use_the_first_one() {
            let b = with_points("1000,500,4,2,0,100,1,0\n");
            assert_eq!(b.timing_point_at(0.0).unwrap().time, 1000.0);
        }

        #[test]
        fn kiai_flag() {
            let b = with_points("0,500,4,2,0,100,1,1\n");
            assert!(b.timing_points[0].kiai());
        }
    }

    mod hit_objects {
        use super::*;

        fn with_objects(objects: &str) -> Beatmap {
            map(&format!("osu file format v14\n[HitObjects]\n{objects}"))
        }

        #[test]
        fn circle() {
            let b = with_objects("256,192,1000,1,0,0:0:0:0:\n");
            let o = &b.hit_objects[0];
            assert!(o.is_circle());
            assert_eq!(o.time, 1000);
            assert_eq!(o.pos, Pos::new(256.0, 192.0));
        }

        #[test]
        fn type_is_a_bitfield_not_an_enum() {
            // 5 = circle (1) | new combo (4). Testing `type == 1` misses it.
            let b = with_objects("256,192,1000,5,0,0:0:0:0:\n");
            assert!(b.hit_objects[0].is_circle());
            assert!(b.hit_objects[0].new_combo);
        }

        #[test]
        fn combo_skip_is_bits_four_through_six() {
            // 4 | (2 << 4) = 36, plus the circle bit.
            let b = with_objects("256,192,1000,37,0,0:0:0:0:\n");
            assert!(b.hit_objects[0].new_combo);
            assert_eq!(b.hit_objects[0].combo_skip, 2);
        }

        #[test]
        fn slider_control_points_include_the_head() {
            // The head lives in the object's x/y, not in the path field.
            let b = with_objects("100,100,0,2,0,L|200:200|300:100,1,300\n");
            let HitObjectKind::Slider(s) = &b.hit_objects[0].kind else {
                panic!("expected a slider");
            };
            assert_eq!(s.control_points.len(), 3);
            assert_eq!(s.control_points[0], Pos::new(100.0, 100.0));
            assert_eq!(s.control_points[2], Pos::new(300.0, 100.0));
            assert_eq!(s.curve, CurveKind::Linear);
            assert_eq!(s.slides, 1);
            assert_eq!(s.length, 300.0);
        }

        #[test]
        fn every_curve_type() {
            for (token, expected) in [
                ('B', CurveKind::Bezier),
                ('C', CurveKind::Catmull),
                ('L', CurveKind::Linear),
                ('P', CurveKind::PerfectCircle),
            ] {
                let b = with_objects(&format!("0,0,0,2,0,{token}|10:10,1,50\n"));
                let HitObjectKind::Slider(s) = &b.hit_objects[0].kind else {
                    panic!("expected a slider");
                };
                assert_eq!(s.curve, expected);
            }
        }

        #[test]
        fn slides_of_two_means_one_repeat() {
            let b = with_objects("0,0,0,2,0,L|100:0,2,100\n");
            let HitObjectKind::Slider(s) = &b.hit_objects[0].kind else {
                panic!("expected a slider");
            };
            assert_eq!(s.slides, 2);
        }

        #[test]
        fn spinner_end_time() {
            let b = with_objects("256,192,1000,12,0,3000,0:0:0:0:\n");
            assert!(b.hit_objects[0].is_spinner());
            assert_eq!(b.hit_objects[0].end_time_simple(), Some(3000));
        }

        #[test]
        fn objects_are_sorted_by_time() {
            let b = with_objects("0,0,2000,1,0\n0,0,1000,1,0\n");
            assert_eq!(b.hit_objects[0].time, 1000);
        }

        #[test]
        fn a_type_naming_no_kind_is_an_error_not_a_silent_skip() {
            let text = "osu file format v14\n[HitObjects]\n0,0,0,4,0\n";
            let err = parse(text.as_bytes()).unwrap_err();
            assert!(matches!(err, ParseError::BadValue { .. }));
        }

        #[test]
        fn errors_name_the_line() {
            let text = "osu file format v14\n[HitObjects]\n0,0,notatime,1,0\n";
            let message = parse(text.as_bytes()).unwrap_err().to_string();
            assert!(message.contains("HitObjects"), "{message}");
            assert!(message.contains("line 3"), "{message}");
            assert!(message.contains("notatime"), "{message}");
        }

        #[test]
        fn a_float_where_an_integer_belongs_is_tolerated() {
            // Some editors have written "1000.0" for a time; osu! accepts it.
            let b = with_objects("256,192,1000.0,1,0\n");
            assert_eq!(b.hit_objects[0].time, 1000);
        }
    }

    mod slider_timing {
        use super::*;

        /// length / (SliderMultiplier * 100 * SV) * beatLength
        fn duration_of(sv_line: &str, length: f64, slides: i32) -> f64 {
            let text = format!(
                "osu file format v14\n\
                 [Difficulty]\nSliderMultiplier:1.4\n\
                 [TimingPoints]\n0,500,4,2,0,100,1,0\n{sv_line}\
                 [HitObjects]\n0,0,1000,2,0,L|100:0,{slides},{length}\n"
            );
            let b = map(&text);
            b.slider_slide_duration(&b.hit_objects[0]).unwrap()
        }

        #[test]
        fn base_case() {
            // 140 / (1.4 * 100 * 1.0) * 500 = 500 ms
            assert!((duration_of("", 140.0, 1) - 500.0).abs() < 1e-9);
        }

        #[test]
        fn slider_velocity_shortens_it() {
            // 2.0x velocity halves the duration.
            assert!((duration_of("500,-50,4,2,0,100,0,0\n", 140.0, 1) - 250.0).abs() < 1e-9);
        }

        #[test]
        fn repeats_multiply_the_end_time() {
            let text = "osu file format v14\n\
                        [Difficulty]\nSliderMultiplier:1.4\n\
                        [TimingPoints]\n0,500,4,2,0,100,1,0\n\
                        [HitObjects]\n0,0,1000,2,0,L|100:0,3,140\n";
            let b = map(text);
            assert!((b.slider_end_time(&b.hit_objects[0]).unwrap() - 2500.0).abs() < 1e-9);
        }

        #[test]
        fn end_time_is_none_for_non_sliders() {
            let b = map("osu file format v14\n[HitObjects]\n0,0,0,1,0\n");
            assert_eq!(b.slider_end_time(&b.hit_objects[0]), None);
        }
    }

    #[test]
    fn unmodelled_sections_are_kept_rather_than_dropped() {
        let text = "osu file format v14\n[Events]\n0,0,\"bg.jpg\",0,0\n";
        let b = map(text);
        assert_eq!(b.raw_sections["Events"], vec!["0,0,\"bg.jpg\",0,0"]);
    }

    #[test]
    fn comments_and_blank_lines_are_ignored() {
        let text = "osu file format v14\n\n// a comment\n[General]\n\nMode: 0\n";
        assert_eq!(map(text).mode, Mode::Osu);
    }
}
