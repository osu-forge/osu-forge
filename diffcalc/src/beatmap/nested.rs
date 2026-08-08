//! The parts of a slider that are scored individually.
//!
//! A slider is not one judgement. osu! scores its head, each tick along the
//! path, each repeat point, and its tail, and the slider's grade comes from how
//! many of those the player collected. Anything that reproduces judgement counts
//! has to know where and when those parts are, because a dropped slider is the
//! difference between a 300 and a 100 — and on a real replay corpus that
//! difference accounts for essentially the entire gap between a hit simulator's
//! output and the game's.
//!
//! # Reconstructed, and checked against a corpus rather than a source
//!
//! The rules below are reconstructed from the format documentation and from how
//! osu! is described to behave, not transcribed from its code. Several are
//! places to be wrong:
//!
//! - Tick spacing is the scoring distance divided by the tick rate, and a tick
//!   within ten milliseconds' travel of a span's end is dropped.
//! - The tail is judged early. stable's "legacy last tick" sits 36 ms before the
//!   slider ends, or at the midpoint of the final span if that is later, which
//!   is why a slider can be released slightly early without penalty.
//! - Alternate spans run backwards, so a tick's time and its position on the
//!   path are mirrored relative to one another.
//!
//! What settles these is the corpus: 57 replays covering some 15,000 sliders,
//! each with the game's own judgement counts in its header. A tick rule that is
//! wrong shows up immediately as a systematic count error, so the evidence is
//! available even though the source is not.

use crate::beatmap::curve::SliderPath;
use crate::beatmap::model::{Beatmap, HitObject, HitObjectKind, Pos, STACK_OFFSET_PER_LEVEL};

/// How far before its end stable judges a slider's tail.
///
/// This is what lets a slider be released a little early without dropping it.
const LEGACY_TAIL_LENIENCY_MS: f64 = 36.0;

/// A tick closer than this many milliseconds of travel to the end of a span is
/// not generated.
const TICK_MIN_MS_FROM_END: f64 = 10.0;

/// The base scoring distance, in osu! pixels per beat at 1.0x.
const BASE_SCORING_DISTANCE: f64 = 100.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NestedKind {
    Tick,
    Repeat,
    /// The end of the slider, judged early — see [`LEGACY_TAIL_LENIENCY_MS`].
    Tail,
}

/// One scoreable point along a slider, other than its head.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct NestedObject {
    pub time: f64,
    /// Where the ball is at [`NestedObject::time`], with the slider's stack
    /// offset already applied.
    pub pos: Pos,
    pub kind: NestedKind,
}

impl Beatmap {
    /// The scoreable points of a slider, in time order, excluding its head.
    ///
    /// Empty for anything that is not a slider, and for a slider whose duration
    /// cannot be resolved — a green line with a zero multiplier, say. An empty
    /// result means "nothing to follow", which scores the slider on its head
    /// alone; that is the right failure, because inventing ticks for a slider
    /// whose timing is unknown would put them in the wrong places.
    pub fn slider_parts(&self, object: &HitObject) -> Vec<NestedObject> {
        let HitObjectKind::Slider(slider) = &object.kind else {
            return Vec::new();
        };
        let start = f64::from(object.time);
        let Some(span_duration) = self.slider_slide_duration(object) else {
            return Vec::new();
        };
        if span_duration <= 0.0 || slider.slides < 1 {
            return Vec::new();
        }

        let path = SliderPath::new(slider);
        let length = path.length();
        if length <= 0.0 {
            return Vec::new();
        }

        // Pixels per millisecond along the path.
        let velocity = length / span_duration;
        let scoring_distance = BASE_SCORING_DISTANCE
            * self.difficulty.slider_multiplier
            * self.slider_velocity_at(start);
        let tick_rate = self.difficulty.slider_tick_rate;
        let tick_distance = if tick_rate > 0.0 && scoring_distance > 0.0 {
            scoring_distance / tick_rate
        } else {
            0.0
        };
        let min_distance_from_end = velocity * TICK_MIN_MS_FROM_END;

        // Stacking moves the whole slider, path included, not just its head.
        let stack =
            f64::from(object.stack_height) * self.difficulty.scale() * -STACK_OFFSET_PER_LEVEL;
        let at = |progress: f64| {
            let p = path.position_at(progress);
            Pos::new(p.x + stack, p.y + stack)
        };

        let mut parts = Vec::new();

        for span in 0..slider.slides {
            let span_start = start + f64::from(span) * span_duration;
            let reversed = span % 2 == 1;

            if tick_distance > 0.0 {
                let mut distance = tick_distance;
                while distance < length - min_distance_from_end {
                    let path_progress = distance / length;
                    // On a reversed span the ball runs from the end back to the
                    // start, so a point that is 30% along the path is reached at
                    // 70% of the span's time.
                    let time_progress = if reversed {
                        1.0 - path_progress
                    } else {
                        path_progress
                    };
                    parts.push(NestedObject {
                        time: span_start + time_progress * span_duration,
                        pos: at(path_progress),
                        kind: NestedKind::Tick,
                    });
                    distance += tick_distance;
                }
            }

            if span < slider.slides - 1 {
                // A repeat point: the ball turns around here. The last span does
                // not get one, because its end is the tail.
                parts.push(NestedObject {
                    time: span_start + span_duration,
                    pos: at(if reversed { 0.0 } else { 1.0 }),
                    kind: NestedKind::Repeat,
                });
            }
        }

        let end_time = start + span_duration * f64::from(slider.slides);
        let final_span_start = end_time - span_duration;
        let tail_time =
            (end_time - LEGACY_TAIL_LENIENCY_MS).max(final_span_start + span_duration / 2.0);
        let final_reversed = (slider.slides - 1) % 2 == 1;
        // The tail's position is where the ball actually is when it is judged,
        // not the path's end. They differ by 36 ms of travel, which on a fast
        // slider is far enough to matter to a follow-circle test.
        let tail_progress = {
            let along = (tail_time - final_span_start) / span_duration;
            if final_reversed {
                1.0 - along
            } else {
                along
            }
        };
        parts.push(NestedObject {
            time: tail_time,
            pos: at(tail_progress.clamp(0.0, 1.0)),
            kind: NestedKind::Tail,
        });

        parts.sort_by(|a, b| a.time.total_cmp(&b.time));
        parts
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::beatmap::parse;

    /// 500 ms beats, 1.0x multiplier, tick rate 1: one tick per 100 px, and a
    /// 100 px slider lasts 500 ms.
    fn map(objects: &str, tick_rate: f64) -> Beatmap {
        let text = format!(
            "osu file format v14\n\
             [General]\nMode: 0\nStackLeniency: 0.7\n\
             [Difficulty]\nCircleSize:4\nApproachRate:9\n\
             SliderMultiplier:1\nSliderTickRate:{tick_rate}\n\
             [TimingPoints]\n0,500,4,2,0,100,1,0\n\
             [HitObjects]\n{objects}"
        );
        parse(text.as_bytes()).expect("fixture should parse")
    }

    fn parts(beatmap: &Beatmap) -> Vec<NestedObject> {
        beatmap.slider_parts(&beatmap.hit_objects[0])
    }

    #[test]
    fn a_circle_has_no_parts() {
        let b = map("100,100,1000,1,0\n", 1.0);
        assert!(b.slider_parts(&b.hit_objects[0]).is_empty());
    }

    #[test]
    fn a_short_slider_has_only_a_tail() {
        // 100 px at 100 px/beat over a 500 ms beat: one span of 500 ms. A tick
        // would fall at 100 px, which is the end, so it is dropped.
        let b = map("0,0,1000,2,0,L|100:0,1,100\n", 1.0);
        let p = parts(&b);
        assert_eq!(p.len(), 1);
        assert_eq!(p[0].kind, NestedKind::Tail);
    }

    #[test]
    fn ticks_land_at_the_tick_distance() {
        // 300 px over 1500 ms, ticks every 100 px: at 100 px and 200 px. The one
        // at 300 px is the end and is dropped.
        let b = map("0,0,1000,2,0,L|300:0,1,300\n", 1.0);
        let ticks: Vec<_> = parts(&b)
            .into_iter()
            .filter(|p| p.kind == NestedKind::Tick)
            .collect();
        assert_eq!(ticks.len(), 2, "{ticks:?}");
        assert!((ticks[0].time - 1500.0).abs() < 1e-6, "{:?}", ticks[0]);
        assert!((ticks[0].pos.x - 100.0).abs() < 0.5, "{:?}", ticks[0]);
        assert!((ticks[1].time - 2000.0).abs() < 1e-6, "{:?}", ticks[1]);
        assert!((ticks[1].pos.x - 200.0).abs() < 0.5, "{:?}", ticks[1]);
    }

    #[test]
    fn a_higher_tick_rate_puts_ticks_closer_together() {
        let b = map("0,0,1000,2,0,L|300:0,1,300\n", 2.0);
        let ticks = parts(&b)
            .into_iter()
            .filter(|p| p.kind == NestedKind::Tick)
            .count();
        // Every 50 px now: 50, 100, 150, 200, 250. 300 is the end.
        assert_eq!(ticks, 5);
    }

    #[test]
    fn a_repeat_adds_a_turning_point_and_mirrors_the_ticks() {
        let b = map("0,0,1000,2,0,L|300:0,2,300\n", 1.0);
        let p = parts(&b);
        let repeats: Vec<_> = p.iter().filter(|n| n.kind == NestedKind::Repeat).collect();
        assert_eq!(repeats.len(), 1);
        assert!((repeats[0].time - 2500.0).abs() < 1e-6);
        assert!(
            (repeats[0].pos.x - 300.0).abs() < 0.5,
            "turns at the far end"
        );

        let ticks: Vec<_> = p.iter().filter(|n| n.kind == NestedKind::Tick).collect();
        assert_eq!(ticks.len(), 4, "two per span");
        // The second span runs backwards: the 200 px point comes first.
        assert!((ticks[2].pos.x - 200.0).abs() < 0.5, "{:?}", ticks[2]);
        assert!((ticks[3].pos.x - 100.0).abs() < 0.5, "{:?}", ticks[3]);
    }

    #[test]
    fn the_tail_is_judged_before_the_slider_ends() {
        let b = map("0,0,1000,2,0,L|300:0,1,300\n", 1.0);
        let tail = parts(&b)
            .into_iter()
            .find(|p| p.kind == NestedKind::Tail)
            .expect("every slider has a tail");
        // Ends at 2500; judged 36 ms early.
        assert!((tail.time - 2464.0).abs() < 1e-6, "{tail:?}");
    }

    #[test]
    fn a_very_short_slider_judges_its_tail_at_the_midpoint_not_earlier() {
        // 10 px over 50 ms. Taking 36 ms off would land at 1014, before the
        // halfway point at 1025, and stable clamps to the midpoint. The
        // threshold is a 72 ms span; a 100 ms one is still long enough for the
        // plain 36 ms rule, which a first version of this test got wrong.
        let b = map("0,0,1000,2,0,L|10:0,1,10\n", 1.0);
        let tail = parts(&b)
            .into_iter()
            .find(|p| p.kind == NestedKind::Tail)
            .expect("every slider has a tail");
        assert!((tail.time - 1025.0).abs() < 1e-6, "{tail:?}");
    }

    #[test]
    fn parts_come_out_in_time_order() {
        let b = map("0,0,1000,2,0,L|300:0,3,300\n", 2.0);
        let p = parts(&b);
        assert!(p.len() > 5);
        assert!(p.windows(2).all(|w| w[0].time <= w[1].time));
    }
}
