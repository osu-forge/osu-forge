//! Stack leniency — where objects are actually drawn.
//!
//! When objects sit on nearly the same spot within a short window, osu! offsets
//! each successive one up and to the left so the pattern reads as a stack rather
//! than as one circle. The offset is small (a few pixels per level) but it is
//! where the object is drawn, so it is also where the player aims and where the
//! game tests the cursor. Anything that compares a cursor position against an
//! object position has to compare against the stacked position or it is
//! measuring a different game.
//!
//! # What verifies this
//!
//! Not much, yet, and it is worth being plain about that. The replay hit
//! simulator's judgement-count gate is only a weak check here: a stack level is
//! a ~3 px shift against a ~36 px radius, so a wrong stack height rarely flips a
//! judgement. The output invariants tested below (heights consecutive within a
//! stack, no stacking across a gap wider than the window, first object of a
//! stack at zero) catch a broken implementation but do not prove agreement with
//! osu!.
//!
//! The real oracle arrives with difficulty calculation: stacking changes the
//! distances between objects, which changes aim strain, which changes star
//! rating — and star rating is checkable against the official API to 1e-6. Until
//! then, treat stack heights as implemented but unverified.

use crate::beatmap::curve::SliderPath;
use crate::beatmap::model::{Beatmap, HitObject, HitObjectKind, Pos, Stacking};

/// How close two objects must be to count as stacked, in osu! pixels.
pub const STACK_DISTANCE: f64 = 3.0;

/// The format version at which stable switched stacking algorithms.
const MODERN_STACKING_FORMAT: i32 = 6;

/// Everything the stacking loops need about an object, resolved once.
///
/// A slider's end time needs the timing points and its end position needs the
/// curve, and the loops below revisit objects repeatedly — resolving these
/// inside the loops would recompute slider paths thousands of times over.
struct Resolved {
    start_time: f64,
    end_time: f64,
    pos: Pos,
    end_pos: Pos,
    is_spinner: bool,
    is_slider: bool,
}

fn resolve(beatmap: &Beatmap, object: &HitObject) -> Resolved {
    let start_time = f64::from(object.time);
    let (end_time, end_pos) = match &object.kind {
        HitObjectKind::Slider(slider) => {
            let end_time = beatmap.slider_end_time(object).unwrap_or(start_time);
            let end_pos = SliderPath::new(slider).end_position(slider.slides);
            (end_time, end_pos)
        }
        _ => (
            object.end_time_simple().map_or(start_time, f64::from),
            object.pos,
        ),
    };
    Resolved {
        start_time,
        end_time,
        pos: object.pos,
        end_pos,
        is_spinner: object.is_spinner(),
        is_slider: object.is_slider(),
    }
}

/// Fill in `stack_height` for every object on the beatmap.
///
/// Called by the parser, and again by anything that changes the approach rate,
/// since the stacking window is a multiple of the preempt time.
pub(crate) fn apply(beatmap: &mut Beatmap) {
    for object in &mut beatmap.hit_objects {
        object.stack_height = 0;
    }

    if beatmap.format_version < MODERN_STACKING_FORMAT {
        beatmap.stacking = Stacking::UnsupportedLegacyFormat;
        return;
    }
    beatmap.stacking = Stacking::Applied;

    let objects: Vec<Resolved> = beatmap
        .hit_objects
        .iter()
        .map(|o| resolve(beatmap, o))
        .collect();
    if objects.len() < 2 {
        return;
    }

    // osu! reads the window off each object's own preempt time. On stable every
    // object on a map shares one, since preempt comes from the map's approach
    // rate, so resolving it once here is the same computation and not a
    // simplification.
    let threshold = beatmap.difficulty.preempt() * beatmap.stack_leniency;

    // Heights are tracked separately from the objects because the loops read
    // positions and times while writing heights, sometimes for the same index.
    let mut heights = vec![0_i32; objects.len()];

    // osu!'s version of this takes a start and end index so the editor can
    // restack part of a map. We always restack all of it, which makes the whole
    // first half of the upstream routine — extending the end index to cover
    // objects a partial range is stacked onto — unreachable. It is left out
    // rather than transcribed and never entered.
    for i in (1..objects.len()).rev() {
        if heights[i] != 0 || objects[i].is_spinner {
            continue;
        }

        // The object the search compares against. It walks backwards through
        // the stack as matches are found, so it is not always object `i`.
        let mut current = i;
        let mut n = i;

        if objects[i].is_slider {
            // The first slider of a stack. From here the whole run stacks
            // upward regardless of what else is in it.
            while n > 0 {
                n -= 1;
                if objects[n].is_spinner {
                    continue;
                }
                // Start time, not end time — unlike the circle branch below.
                // The asymmetry is osu!'s and it is load-bearing: a long slider
                // ending near the next object would otherwise keep a stack
                // alive across an arbitrarily long gap.
                if objects[current].start_time - objects[n].start_time > threshold {
                    break;
                }
                if objects[n].end_pos.distance_to(objects[current].pos) < STACK_DISTANCE {
                    heights[n] = heights[current] + 1;
                    current = n;
                }
            }
        } else {
            while n > 0 {
                n -= 1;
                if objects[n].is_spinner {
                    continue;
                }
                if objects[current].start_time - objects[n].end_time > threshold {
                    break;
                }

                // A circle sitting on the *end* of a slider stacks downward and
                // to the right instead, so it appears below the slider tail
                // rather than on top of it. Everything already assigned above
                // this slider gets pushed the other way by the same amount.
                if objects[n].is_slider
                    && objects[n].end_pos.distance_to(objects[current].pos) < STACK_DISTANCE
                {
                    let offset = heights[current] - heights[n] + 1;
                    for (j, resolved) in objects.iter().enumerate().take(i + 1).skip(n + 1) {
                        if objects[n].end_pos.distance_to(resolved.pos) < STACK_DISTANCE {
                            heights[j] -= offset;
                        }
                    }
                    // The slider keeps height 0 and gets its own turn as `i`.
                    break;
                }

                if objects[n].pos.distance_to(objects[current].pos) < STACK_DISTANCE {
                    // Note this can walk onto a slider, when the slider's
                    // *start* is what lines up rather than its end. The branch
                    // was chosen on object `i`, not on `current`, and staying
                    // here is deliberate.
                    heights[n] = heights[current] + 1;
                    current = n;
                }
            }
        }
    }

    for (object, height) in beatmap.hit_objects.iter_mut().zip(heights) {
        object.stack_height = height;
    }
}

#[cfg(test)]
mod tests {
    use crate::beatmap::model::Stacking;
    use crate::beatmap::parse;

    /// AR 9 gives a 600 ms preempt, so at the default stack leniency of 0.7 the
    /// window is 420 ms. Every fixture below is written against that.
    fn map(objects: &str) -> crate::beatmap::Beatmap {
        let text = format!(
            "osu file format v14\n\
             [General]\nMode: 0\nStackLeniency: 0.7\n\
             [Difficulty]\nCircleSize:4\nOverallDifficulty:8\nApproachRate:9\n\
             SliderMultiplier:1.4\n\
             [TimingPoints]\n0,500,4,2,0,100,1,0\n\
             [HitObjects]\n{objects}"
        );
        parse(text.as_bytes()).expect("fixture should parse")
    }

    fn heights(beatmap: &crate::beatmap::Beatmap) -> Vec<i32> {
        beatmap.hit_objects.iter().map(|o| o.stack_height).collect()
    }

    #[test]
    fn isolated_circles_do_not_stack() {
        let b = map("100,100,1000,1,0\n300,300,1100,1,0\n");
        assert_eq!(heights(&b), vec![0, 0]);
    }

    #[test]
    fn coincident_circles_stack_upward_from_the_last() {
        // The last object of a stack keeps height 0 and each earlier one is one
        // level higher, which is what puts the newest circle at the bottom.
        let b = map("100,100,1000,1,0\n100,100,1100,1,0\n100,100,1200,1,0\n");
        assert_eq!(heights(&b), vec![2, 1, 0]);
    }

    #[test]
    fn a_gap_wider_than_the_window_breaks_the_stack() {
        // 421 ms apart, against a 420 ms window.
        let b = map("100,100,1000,1,0\n100,100,1421,1,0\n");
        assert_eq!(heights(&b), vec![0, 0]);
    }

    #[test]
    fn separation_beyond_the_stack_distance_does_not_stack() {
        // 3.0 px apart exactly; the comparison is strict, so this is not a stack.
        let b = map("100,100,1000,1,0\n103,100,1100,1,0\n");
        assert_eq!(heights(&b), vec![0, 0]);
    }

    #[test]
    fn near_coincidence_within_the_stack_distance_does_stack() {
        let b = map("100,100,1000,1,0\n102,100,1100,1,0\n");
        assert_eq!(heights(&b), vec![1, 0]);
    }

    #[test]
    fn a_spinner_between_two_circles_does_not_break_the_stack() {
        let b = map("100,100,1000,1,0\n256,192,1050,12,0,1080\n100,100,1100,1,0\n");
        assert_eq!(heights(&b), vec![1, 0, 0]);
    }

    #[test]
    fn circles_under_a_slider_end_stack_downward() {
        // A linear slider from (100,100) to (300,100). The circles sit on its
        // tail, so they go below it and take negative heights.
        let b = map("100,100,1000,2,0,L|300:100,1,200\n\
             300,100,1300,1,0\n\
             300,100,1400,1,0\n");
        let h = heights(&b);
        assert_eq!(h[0], 0, "the slider itself is the base of the stack");
        assert!(
            h[1] < 0 && h[2] < 0,
            "circles on a slider tail stack down: {h:?}"
        );
        assert_eq!(h[1], h[2] + 1, "and by consecutive levels: {h:?}");
    }

    #[test]
    fn a_legacy_format_is_reported_rather_than_stacked_by_the_wrong_algorithm() {
        let text = "osu file format v5\n\
                    [General]\nMode: 0\n\
                    [Difficulty]\nApproachRate:9\n\
                    [HitObjects]\n100,100,1000,1,0\n100,100,1100,1,0\n";
        let b = parse(text.as_bytes()).expect("should parse");
        assert_eq!(b.stacking, Stacking::UnsupportedLegacyFormat);
        assert_eq!(heights(&b), vec![0, 0]);
    }

    #[test]
    fn stacked_position_moves_up_and_left() {
        let b = map("100,100,1000,1,0\n100,100,1100,1,0\n");
        let scale = b.difficulty.scale();
        let top = b.hit_objects[0].stacked_pos(scale);
        assert!(top.x < 100.0 && top.y < 100.0);
        // One level at CS4: 6.4 * 0.57 = 3.648 px. The tolerance is 1e-6 rather
        // than 1e-9 because the scale is computed in f32, as stable does — the
        // residual here is about 5e-8 px and is the intended behaviour, not
        // slack in the test.
        assert!((top.x - (100.0 - 3.648)).abs() < 1e-6, "{top:?}");
        assert_ne!(
            top.x,
            100.0 - 3.648,
            "the f32 scale should still be visible"
        );
        // Height 0 leaves the position untouched.
        assert_eq!(b.hit_objects[1].stacked_pos(scale), b.hit_objects[1].pos);
    }
}
