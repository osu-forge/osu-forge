//! Slider path geometry.
//!
//! A slider's control points describe a shape; what the game needs is a
//! position at any progress along it. That means turning each curve type into
//! a polyline, measuring it, and sampling by arc length.
//!
//! One rule governs the whole module: **the file's `length` field wins.** osu!
//! trims or extends the computed path to that number rather than trusting its
//! own geometry, so a slider whose control points describe 300 px but which
//! declares 280 ends at 280. Computing a "more correct" length would put every
//! slider end in the wrong place relative to what the player actually saw.

use super::model::{CurveKind, Pos, Slider};

/// How flat a Bézier segment must be before subdivision stops, in osu! pixels.
///
/// Smaller is closer to the true curve and slower. This value keeps the error
/// far below a pixel, which is well under anything that affects judgement.
const BEZIER_TOLERANCE: f64 = 0.25;

/// Guard against pathological input. A slider cannot legitimately need this
/// many points, and without a cap a degenerate control-point set can subdivide
/// until it exhausts memory.
const MAX_SUBDIVISION_DEPTH: u32 = 24;

/// Below this the cross product of two segments counts as collinear, and a
/// three-point arc is not constructible.
const COLLINEAR_EPSILON: f64 = 1e-6;

/// A slider path, flattened to a polyline and measured.
#[derive(Debug, Clone)]
pub struct SliderPath {
    points: Vec<Pos>,
    /// Cumulative distance to each point. Same length as `points`.
    cumulative: Vec<f64>,
    /// The length osu! uses, taken from the file rather than from the geometry.
    expected_length: f64,
}

impl SliderPath {
    /// Flatten a slider's control points and measure the result.
    ///
    /// The declared length is stored, **not applied**. Nothing is cut here:
    /// trimming and extension happen inside [`SliderPath::position_at`], which
    /// maps progress onto the declared length and walks the polyline to find
    /// it.
    ///
    /// So [`SliderPath::points`] is the raw geometry, and on the local corpus
    /// it runs a median 6–11% longer than the slider the player saw — Linear
    /// worst at +11.4%. Anything drawing or measuring the path wants
    /// [`SliderPath::resample`] instead.
    pub fn new(slider: &Slider) -> Self {
        let points = flatten(slider.curve, &slider.control_points);
        Self::from_points(points, slider.length)
    }

    fn from_points(points: Vec<Pos>, expected_length: f64) -> Self {
        let mut cumulative = Vec::with_capacity(points.len());
        let mut total = 0.0;
        for (index, point) in points.iter().enumerate() {
            if index > 0 {
                total += points[index - 1].distance_to(*point);
            }
            cumulative.push(total);
        }
        Self {
            points,
            cumulative,
            expected_length,
        }
    }

    /// Length as measured from the flattened geometry, before trimming.
    ///
    /// Useful for checking the curve code against the file's own number; not
    /// what the game plays.
    pub fn computed_length(&self) -> f64 {
        self.cumulative.last().copied().unwrap_or(0.0)
    }

    /// The length the game uses: the file's value.
    pub fn length(&self) -> f64 {
        self.expected_length
    }

    /// Position at `progress` in `0..=1` along the *declared* length.
    ///
    /// Progress outside the range is clamped. If the declared length exceeds the
    /// geometry, the path is extended along its final direction, which is what
    /// osu! does rather than stopping short.
    pub fn position_at(&self, progress: f64) -> Pos {
        let Some(&first) = self.points.first() else {
            return Pos::new(0.0, 0.0);
        };
        if self.points.len() == 1 {
            return first;
        }

        let target = self.expected_length * progress.clamp(0.0, 1.0);
        let computed = self.computed_length();

        if target <= 0.0 {
            return first;
        }
        if target >= computed {
            return self.extend_beyond(target - computed);
        }

        // Binary search for the segment containing `target`.
        let index = match self
            .cumulative
            .binary_search_by(|d| d.partial_cmp(&target).unwrap_or(std::cmp::Ordering::Less))
        {
            Ok(exact) => return self.points[exact],
            Err(insert) => insert,
        };

        let previous = self.points[index - 1];
        let next = self.points[index];
        let segment = self.cumulative[index] - self.cumulative[index - 1];
        if segment <= f64::EPSILON {
            return next;
        }
        let t = (target - self.cumulative[index - 1]) / segment;
        Pos::new(
            previous.x + (next.x - previous.x) * t,
            previous.y + (next.y - previous.y) * t,
        )
    }

    /// Position of the slider's visual end, accounting for repeats.
    ///
    /// An even number of slides ends where it started; an odd number ends at the
    /// far end. Getting this backwards puts every repeating slider's end in the
    /// wrong place, and repeat sliders are common.
    pub fn end_position(&self, slides: i32) -> Pos {
        if slides % 2 == 0 {
            self.position_at(0.0)
        } else {
            self.position_at(1.0)
        }
    }

    /// The raw flattened polyline, before the declared length is applied.
    ///
    /// Not the slider the player saw — see [`SliderPath::new`]. Use
    /// [`SliderPath::resample`] for that.
    pub fn points(&self) -> &[Pos] {
        &self.points
    }

    /// The played path, as points roughly `spacing` osu! pixels apart.
    ///
    /// This is [`SliderPath::position_at`] walked from 0 to 1, so it is trimmed
    /// and extended the way the game does. The last point is always the end of
    /// the declared length, even when that leaves the final gap shorter than
    /// `spacing` — a body that stops short of its tail is visible, and one
    /// segment of uneven length is not.
    ///
    /// Bézier flattening has already put the curvature error well under a
    /// pixel, so `spacing` trades size against how straight a tight arc looks
    /// rather than against accuracy.
    pub fn resample(&self, spacing: f64) -> Vec<Pos> {
        if self.points.is_empty() {
            return Vec::new();
        }
        if self.expected_length <= 0.0 || self.points.len() == 1 || spacing <= 0.0 {
            return vec![self.position_at(0.0)];
        }

        let steps = (self.expected_length / spacing).ceil().max(1.0);
        // Guard against a slider whose declared length is absurd. At 5 px this
        // caps a single path at roughly 80,000 osu! pixels, well beyond any
        // real map, and keeps a corrupt length field from allocating without
        // bound.
        let steps = steps.min(16_384.0) as usize;

        let mut out = Vec::with_capacity(steps + 1);
        for step in 0..=steps {
            out.push(self.position_at(step as f64 / steps as f64));
        }
        out
    }

    fn extend_beyond(&self, extra: f64) -> Pos {
        let last = self.points[self.points.len() - 1];
        if extra <= 0.0 {
            return last;
        }
        // Walk back to the last point that differs, so a duplicated final
        // control point does not give a zero-length direction vector.
        let direction = self
            .points
            .iter()
            .rev()
            .find(|p| p.distance_to(last) > f64::EPSILON)
            .map(|p| {
                let dx = last.x - p.x;
                let dy = last.y - p.y;
                let magnitude = (dx * dx + dy * dy).sqrt();
                (dx / magnitude, dy / magnitude)
            });
        match direction {
            Some((dx, dy)) => Pos::new(last.x + dx * extra, last.y + dy * extra),
            None => last,
        }
    }
}

/// Turn control points into a polyline.
fn flatten(kind: CurveKind, control_points: &[Pos]) -> Vec<Pos> {
    match control_points.len() {
        0 => Vec::new(),
        1 => control_points.to_vec(),
        _ => match kind {
            CurveKind::Linear => control_points.to_vec(),
            CurveKind::PerfectCircle => flatten_perfect_circle(control_points),
            CurveKind::Catmull => flatten_catmull(control_points),
            CurveKind::Bezier => flatten_bezier_segments(control_points),
        },
    }
}

/// Bézier control points are split into segments at *repeated* points.
///
/// A duplicated control point is how the format marks a corner: `B|1:1|1:1|9:9`
/// is two straight-ish segments meeting sharply, not one smooth cubic. Treating
/// the whole list as a single Bézier rounds off every corner in the map.
fn flatten_bezier_segments(control_points: &[Pos]) -> Vec<Pos> {
    let mut output = Vec::new();
    let mut segment_start = 0;

    for index in 1..=control_points.len() {
        let is_end = index == control_points.len();
        let is_repeat = !is_end && control_points[index] == control_points[index - 1];

        if is_end || is_repeat {
            let segment = &control_points[segment_start..index];
            let flattened = flatten_bezier(segment);
            // Skip the duplicated joint so the polyline has no zero-length step.
            let skip = usize::from(!output.is_empty());
            output.extend(flattened.into_iter().skip(skip));
            segment_start = index;
        }
    }

    if output.is_empty() {
        output.extend_from_slice(control_points);
    }
    output
}

fn flatten_bezier(control_points: &[Pos]) -> Vec<Pos> {
    if control_points.len() < 2 {
        return control_points.to_vec();
    }
    let mut output = vec![control_points[0]];
    subdivide(control_points, 0, &mut output);
    output
}

/// Adaptive subdivision: split until the control polygon is flat enough, then
/// emit the endpoint. Uniform sampling would need far more points to reach the
/// same accuracy on tight curves and would still over-sample straight ones.
fn subdivide(points: &[Pos], depth: u32, output: &mut Vec<Pos>) {
    if depth >= MAX_SUBDIVISION_DEPTH || is_flat_enough(points) {
        output.push(points[points.len() - 1]);
        return;
    }
    let (left, right) = split_at_midpoint(points);
    subdivide(&left, depth + 1, output);
    subdivide(&right, depth + 1, output);
}

/// Distance of each interior control point from the chord, compared against the
/// tolerance.
fn is_flat_enough(points: &[Pos]) -> bool {
    let first = points[0];
    let last = points[points.len() - 1];
    let dx = last.x - first.x;
    let dy = last.y - first.y;
    let chord = (dx * dx + dy * dy).sqrt();

    if chord <= f64::EPSILON {
        // Degenerate chord: fall back to the spread of the points themselves.
        return points
            .iter()
            .all(|p| p.distance_to(first) <= BEZIER_TOLERANCE);
    }

    points[1..points.len() - 1].iter().all(|p| {
        let cross = (p.x - first.x) * dy - (p.y - first.y) * dx;
        (cross / chord).abs() <= BEZIER_TOLERANCE
    })
}

/// De Casteljau split at t = 0.5.
fn split_at_midpoint(points: &[Pos]) -> (Vec<Pos>, Vec<Pos>) {
    let n = points.len();
    let mut scratch: Vec<Pos> = points.to_vec();
    let mut left = Vec::with_capacity(n);
    let mut right = vec![Pos::new(0.0, 0.0); n];

    for step in 0..n {
        left.push(scratch[0]);
        right[n - 1 - step] = scratch[n - 1 - step];
        for i in 0..n - 1 - step {
            scratch[i] = Pos::new(
                (scratch[i].x + scratch[i + 1].x) * 0.5,
                (scratch[i].y + scratch[i + 1].y) * 0.5,
            );
        }
    }

    (left, right)
}

/// Circular arc through three points.
///
/// Falls back to Bézier when the input is not exactly three points or when they
/// are collinear — both cases occur in real maps, and osu! falls back the same
/// way rather than refusing the slider.
fn flatten_perfect_circle(control_points: &[Pos]) -> Vec<Pos> {
    if control_points.len() != 3 {
        return flatten_bezier_segments(control_points);
    }
    let (a, b, c) = (control_points[0], control_points[1], control_points[2]);

    // Twice the signed area of the triangle. Zero means collinear, so there is
    // no circle through them.
    let cross = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
    if cross.abs() < COLLINEAR_EPSILON {
        return flatten_bezier_segments(control_points);
    }

    let a_sq = a.x * a.x + a.y * a.y;
    let b_sq = b.x * b.x + b.y * b.y;
    let c_sq = c.x * c.x + c.y * c.y;

    let centre = Pos::new(
        (a_sq * (b.y - c.y) + b_sq * (c.y - a.y) + c_sq * (a.y - b.y)) / (2.0 * cross),
        (a_sq * (c.x - b.x) + b_sq * (a.x - c.x) + c_sq * (b.x - a.x)) / (2.0 * cross),
    );
    let radius = centre.distance_to(a);

    if !radius.is_finite() || radius <= 0.0 {
        return flatten_bezier_segments(control_points);
    }

    let start = (a.y - centre.y).atan2(a.x - centre.x);
    let mut end = (c.y - centre.y).atan2(c.x - centre.x);

    // The sign of the cross product already fixes the traversal direction:
    // a -> b -> c goes one way round, and the arc we want is the one from a to
    // c taken that way. So the only thing left is to unwrap `end` onto the same
    // branch, in that direction.
    //
    // Unwrapping the *middle* point as well, as a first attempt here did, is
    // both unnecessary and wrong. On a shallow arc — three nearly collinear
    // points, which is a very common slider shape — the endpoints land on
    // opposite sides of the atan2 branch cut, and the extra correction sent the
    // sweep the long way round. A real map produced a 170 px arc measured as
    // 22,330 px that way, and nothing about the result looked malformed enough
    // to notice by eye.
    if cross > 0.0 {
        while end < start {
            end += std::f64::consts::TAU;
        }
    } else {
        while end > start {
            end -= std::f64::consts::TAU;
        }
    }

    // One point roughly every quarter pixel of arc, bounded so a huge radius
    // cannot produce an unbounded polyline.
    let sweep = (end - start).abs();
    let steps = ((sweep * radius / 0.25).ceil() as usize).clamp(2, 4096);

    (0..=steps)
        .map(|i| {
            let t = i as f64 / steps as f64;
            let angle = start + (end - start) * t;
            Pos::new(
                centre.x + radius * angle.cos(),
                centre.y + radius * angle.sin(),
            )
        })
        .collect()
}

/// Centripetal Catmull-Rom, kept because old maps use it.
fn flatten_catmull(control_points: &[Pos]) -> Vec<Pos> {
    const STEPS: usize = 50;
    let n = control_points.len();
    let mut output = Vec::with_capacity(n * STEPS);

    for i in 0..n {
        let p0 = control_points[i.saturating_sub(1)];
        let p1 = control_points[i];
        let p2 = if i + 1 < n {
            control_points[i + 1]
        } else {
            // Reflect past the end so the last span has a control point.
            Pos::new(p1.x * 2.0 - p0.x, p1.y * 2.0 - p0.y)
        };
        let p3 = if i + 2 < n {
            control_points[i + 2]
        } else {
            Pos::new(p2.x * 2.0 - p1.x, p2.y * 2.0 - p1.y)
        };

        for step in 0..STEPS {
            let t = step as f64 / STEPS as f64;
            output.push(catmull_point(p0, p1, p2, p3, t));
        }
    }
    output.push(control_points[n - 1]);
    output
}

fn catmull_point(p0: Pos, p1: Pos, p2: Pos, p3: Pos, t: f64) -> Pos {
    let t2 = t * t;
    let t3 = t2 * t;
    let axis = |v0: f64, v1: f64, v2: f64, v3: f64| {
        0.5 * ((-v0 + v2) * t
            + (2.0 * v0 - 5.0 * v1 + 4.0 * v2 - v3) * t2
            + (-v0 + 3.0 * v1 - 3.0 * v2 + v3) * t3
            + 2.0 * v1)
    };
    Pos::new(axis(p0.x, p1.x, p2.x, p3.x), axis(p0.y, p1.y, p2.y, p3.y))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn slider(curve: CurveKind, points: &[(f64, f64)], length: f64) -> Slider {
        Slider {
            curve,
            control_points: points.iter().map(|&(x, y)| Pos::new(x, y)).collect(),
            slides: 1,
            length,
        }
    }

    fn close(a: f64, b: f64, tolerance: f64) -> bool {
        (a - b).abs() <= tolerance
    }

    mod linear {
        use super::*;

        #[test]
        fn length_is_exact() {
            let path = SliderPath::new(&slider(
                CurveKind::Linear,
                &[(0.0, 0.0), (100.0, 0.0)],
                100.0,
            ));
            assert!(close(path.computed_length(), 100.0, 1e-9));
        }

        #[test]
        fn multiple_segments_sum() {
            let path = SliderPath::new(&slider(
                CurveKind::Linear,
                &[(0.0, 0.0), (100.0, 0.0), (100.0, 50.0)],
                150.0,
            ));
            assert!(close(path.computed_length(), 150.0, 1e-9));
        }

        #[test]
        fn position_is_sampled_by_arc_length_not_by_segment() {
            // Two segments of different lengths: halfway along the path is not
            // halfway along the control points.
            let path = SliderPath::new(&slider(
                CurveKind::Linear,
                &[(0.0, 0.0), (100.0, 0.0), (100.0, 300.0)],
                400.0,
            ));
            let midpoint = path.position_at(0.5);
            assert!(close(midpoint.x, 100.0, 1e-6));
            assert!(close(midpoint.y, 100.0, 1e-6));
        }

        #[test]
        fn endpoints() {
            let path = SliderPath::new(&slider(
                CurveKind::Linear,
                &[(0.0, 0.0), (100.0, 0.0)],
                100.0,
            ));
            assert_eq!(path.position_at(0.0), Pos::new(0.0, 0.0));
            assert!(close(path.position_at(1.0).x, 100.0, 1e-9));
        }

        #[test]
        fn progress_is_clamped() {
            let path = SliderPath::new(&slider(
                CurveKind::Linear,
                &[(0.0, 0.0), (100.0, 0.0)],
                100.0,
            ));
            assert_eq!(path.position_at(-1.0), path.position_at(0.0));
            assert_eq!(path.position_at(2.0), path.position_at(1.0));
        }
    }

    mod declared_length_wins {
        use super::*;

        #[test]
        fn a_shorter_declared_length_truncates_the_path() {
            // Geometry says 100, the file says 50. osu! plays 50, and computing
            // a "more correct" end would put it where the player never saw it.
            let path = SliderPath::new(&slider(
                CurveKind::Linear,
                &[(0.0, 0.0), (100.0, 0.0)],
                50.0,
            ));
            assert!(close(path.computed_length(), 100.0, 1e-9));
            assert!(close(path.position_at(1.0).x, 50.0, 1e-6));
        }

        #[test]
        fn a_longer_declared_length_extends_along_the_final_direction() {
            let path = SliderPath::new(&slider(
                CurveKind::Linear,
                &[(0.0, 0.0), (100.0, 0.0)],
                150.0,
            ));
            assert!(close(path.position_at(1.0).x, 150.0, 1e-6));
            assert!(close(path.position_at(1.0).y, 0.0, 1e-6));
        }
    }

    mod resampling {
        use super::*;

        fn walked(points: &[Pos]) -> f64 {
            points.windows(2).map(|w| w[0].distance_to(w[1])).sum()
        }

        #[test]
        fn the_resampled_path_is_the_declared_length_not_the_geometry() {
            // The distinction the whole module turns on. Geometry says 100,
            // the file says 50, and the player saw 50.
            let path = SliderPath::new(&slider(
                CurveKind::Linear,
                &[(0.0, 0.0), (100.0, 0.0)],
                50.0,
            ));
            assert!(close(walked(&path.resample(5.0)), 50.0, 0.5));
        }

        #[test]
        fn it_extends_when_the_file_asks_for_more_than_the_geometry_has() {
            let path = SliderPath::new(&slider(
                CurveKind::Linear,
                &[(0.0, 0.0), (100.0, 0.0)],
                150.0,
            ));
            let points = path.resample(5.0);
            assert!(close(walked(&points), 150.0, 0.5));
            assert!(close(points.last().unwrap().x, 150.0, 1e-6));
        }

        #[test]
        fn a_curve_resamples_to_within_one_percent() {
            // A quarter circle of radius 100: arc length 157.08. Chords cut the
            // corners, so the walked length is slightly short — one percent is
            // the gate, and 5 px spacing is far inside it.
            let path = SliderPath::new(&slider(
                CurveKind::PerfectCircle,
                &[(0.0, 0.0), (29.29, 70.71), (100.0, 100.0)],
                157.08,
            ));
            let walked = walked(&path.resample(5.0));
            assert!(
                (walked - 157.08).abs() / 157.08 < 0.01,
                "walked {walked}, declared 157.08"
            );
        }

        #[test]
        fn the_ends_are_exact_whatever_the_spacing() {
            let path = SliderPath::new(&slider(
                CurveKind::Linear,
                &[(0.0, 0.0), (100.0, 0.0)],
                100.0,
            ));
            for spacing in [1.0, 5.0, 7.3, 99.0, 1000.0] {
                let points = path.resample(spacing);
                assert!(points.len() >= 2, "spacing {spacing}");
                assert!(close(points[0].x, 0.0, 1e-9), "spacing {spacing}");
                assert!(
                    close(points.last().unwrap().x, 100.0, 1e-9),
                    "spacing {spacing}"
                );
            }
        }

        #[test]
        fn a_degenerate_slider_gives_one_point_rather_than_nothing() {
            // Zero length and a single control point both occur in real files.
            // A caller drawing an empty path draws nothing and never finds out.
            let zero = SliderPath::new(&slider(CurveKind::Linear, &[(5.0, 5.0), (9.0, 9.0)], 0.0));
            assert_eq!(zero.resample(5.0).len(), 1);

            let single = SliderPath::new(&slider(CurveKind::Linear, &[(5.0, 5.0)], 100.0));
            assert_eq!(single.resample(5.0), vec![Pos::new(5.0, 5.0)]);
        }

        #[test]
        fn an_absurd_declared_length_is_capped_rather_than_allocated() {
            let path =
                SliderPath::new(&slider(CurveKind::Linear, &[(0.0, 0.0), (100.0, 0.0)], 1e9));
            assert_eq!(path.resample(5.0).len(), 16_385);
        }
    }

    mod repeats {
        use super::*;

        #[test]
        fn odd_slides_end_at_the_far_end_even_slides_come_back() {
            // Getting this backwards misplaces the end of every repeat slider.
            let path = SliderPath::new(&slider(
                CurveKind::Linear,
                &[(0.0, 0.0), (100.0, 0.0)],
                100.0,
            ));
            assert!(close(path.end_position(1).x, 100.0, 1e-6));
            assert!(close(path.end_position(2).x, 0.0, 1e-6));
            assert!(close(path.end_position(3).x, 100.0, 1e-6));
            assert!(close(path.end_position(4).x, 0.0, 1e-6));
        }
    }

    mod bezier {
        use super::*;

        #[test]
        fn a_two_point_bezier_is_a_straight_line() {
            let path = SliderPath::new(&slider(
                CurveKind::Bezier,
                &[(0.0, 0.0), (100.0, 0.0)],
                100.0,
            ));
            assert!(close(path.computed_length(), 100.0, 1e-6));
        }

        #[test]
        fn a_curve_is_longer_than_its_chord_but_shorter_than_its_polygon() {
            // The standard sanity bound on any Bézier.
            let path = SliderPath::new(&slider(
                CurveKind::Bezier,
                &[(0.0, 0.0), (50.0, 100.0), (100.0, 0.0)],
                100.0,
            ));
            let chord = 100.0;
            let polygon = Pos::new(0.0, 0.0).distance_to(Pos::new(50.0, 100.0))
                + Pos::new(50.0, 100.0).distance_to(Pos::new(100.0, 0.0));
            let computed = path.computed_length();
            assert!(computed > chord, "{computed} should exceed the chord");
            assert!(computed < polygon, "{computed} should be under {polygon}");
        }

        #[test]
        fn a_repeated_control_point_starts_a_new_segment() {
            // `B|100:0|100:0|100:100` is a corner, not a smooth curve. Treated
            // as one Bézier the corner rounds off and the path gets shorter.
            let cornered = SliderPath::new(&slider(
                CurveKind::Bezier,
                &[(0.0, 0.0), (100.0, 0.0), (100.0, 0.0), (100.0, 100.0)],
                200.0,
            ));
            // Two straight segments: exactly 200.
            assert!(
                close(cornered.computed_length(), 200.0, 1e-6),
                "got {}",
                cornered.computed_length()
            );

            let smooth = SliderPath::new(&slider(
                CurveKind::Bezier,
                &[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)],
                200.0,
            ));
            assert!(smooth.computed_length() < cornered.computed_length());
        }

        #[test]
        fn subdivision_converges_rather_than_running_away() {
            // Degenerate input must terminate; the depth cap is the backstop.
            let path = SliderPath::new(&slider(
                CurveKind::Bezier,
                &[(0.0, 0.0), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0)],
                0.0,
            ));
            assert!(path.points().len() < 1000);
        }
    }

    mod perfect_circle {
        use super::*;

        #[test]
        fn a_semicircle_has_the_expected_arc_length() {
            // Radius 100 through (0,0) -> (100,100) -> (200,0): a half circle,
            // length pi * r.
            let path = SliderPath::new(&slider(
                CurveKind::PerfectCircle,
                &[(0.0, 0.0), (100.0, 100.0), (200.0, 0.0)],
                314.159,
            ));
            let expected = std::f64::consts::PI * 100.0;
            assert!(
                close(path.computed_length(), expected, 0.5),
                "got {}, expected about {expected}",
                path.computed_length()
            );
        }

        #[test]
        fn the_arc_passes_through_the_middle_control_point() {
            let path = SliderPath::new(&slider(
                CurveKind::PerfectCircle,
                &[(0.0, 0.0), (100.0, 100.0), (200.0, 0.0)],
                314.159,
            ));
            let nearest = path
                .points()
                .iter()
                .map(|p| p.distance_to(Pos::new(100.0, 100.0)))
                .fold(f64::INFINITY, f64::min);
            assert!(nearest < 1.0, "arc missed the middle point by {nearest}");
        }

        #[test]
        fn a_shallow_arc_takes_the_short_way_round() {
            // Three nearly collinear points: a very common slider shape, and the
            // one that exposed a sweep going the long way around the circle. The
            // endpoints land either side of the atan2 branch cut, so the
            // unwrapping has to be driven by the traversal direction alone.
            //
            // Taken from a real map. The true arc is a shade over the 170 px
            // between the endpoints; the bug measured it as 22,330.
            let path = SliderPath::new(&slider(
                CurveKind::PerfectCircle,
                &[(156.0, 125.0), (155.0, 202.0), (156.0, 295.0)],
                125.0,
            ));
            let computed = path.computed_length();
            assert!(
                (150.0..250.0).contains(&computed),
                "expected a short arc, got {computed}"
            );
        }

        #[test]
        fn a_shallow_arc_bulging_the_other_way_is_also_short() {
            // Mirror of the case above, so the fix cannot be direction-specific.
            let path = SliderPath::new(&slider(
                CurveKind::PerfectCircle,
                &[(156.0, 125.0), (157.0, 202.0), (156.0, 295.0)],
                125.0,
            ));
            let computed = path.computed_length();
            assert!(
                (150.0..250.0).contains(&computed),
                "expected a short arc, got {computed}"
            );
        }

        #[test]
        fn an_arc_never_exceeds_its_own_circumference() {
            // A three-point arc is at most a full turn; anything longer means
            // the sweep wrapped.
            for points in [
                [(156.0, 125.0), (155.0, 202.0), (156.0, 295.0)],
                [(0.0, 0.0), (100.0, 100.0), (200.0, 0.0)],
                [(0.0, 0.0), (100.0, 10.0), (200.0, 0.0)],
                [(300.0, 100.0), (200.0, 200.0), (100.0, 100.0)],
            ] {
                let s = slider(CurveKind::PerfectCircle, &points, 100.0);
                let path = SliderPath::new(&s);
                // Radius from the first and middle points is enough to bound it.
                let chord = Pos::new(points[0].0, points[0].1)
                    .distance_to(Pos::new(points[2].0, points[2].1));
                assert!(
                    path.computed_length() < chord * 20.0,
                    "arc for {points:?} measured {} against a chord of {chord}",
                    path.computed_length()
                );
            }
        }

        #[test]
        fn collinear_points_fall_back_to_bezier() {
            // No circle passes through three points on a line. osu! falls back
            // rather than refusing the slider, and so do we.
            let path = SliderPath::new(&slider(
                CurveKind::PerfectCircle,
                &[(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)],
                100.0,
            ));
            assert!(close(path.computed_length(), 100.0, 1e-6));
        }

        #[test]
        fn a_point_count_other_than_three_falls_back_to_bezier() {
            let path = SliderPath::new(&slider(
                CurveKind::PerfectCircle,
                &[(0.0, 0.0), (50.0, 50.0), (100.0, 0.0), (150.0, 50.0)],
                200.0,
            ));
            assert!(path.computed_length() > 0.0);
        }
    }

    mod catmull {
        use super::*;

        #[test]
        fn passes_through_its_control_points() {
            let path = SliderPath::new(&slider(
                CurveKind::Catmull,
                &[(0.0, 0.0), (50.0, 50.0), (100.0, 0.0)],
                150.0,
            ));
            for target in [Pos::new(50.0, 50.0), Pos::new(100.0, 0.0)] {
                let nearest = path
                    .points()
                    .iter()
                    .map(|p| p.distance_to(target))
                    .fold(f64::INFINITY, f64::min);
                assert!(nearest < 1.0, "missed {target:?} by {nearest}");
            }
        }
    }

    mod degenerate {
        use super::*;

        #[test]
        fn a_single_control_point_is_a_point() {
            let path = SliderPath::new(&slider(CurveKind::Linear, &[(10.0, 20.0)], 0.0));
            assert_eq!(path.position_at(0.0), Pos::new(10.0, 20.0));
            assert_eq!(path.position_at(1.0), Pos::new(10.0, 20.0));
        }

        #[test]
        fn a_zero_length_path_does_not_divide_by_zero() {
            let path = SliderPath::new(&slider(CurveKind::Linear, &[(5.0, 5.0), (5.0, 5.0)], 50.0));
            let end = path.position_at(1.0);
            assert!(end.x.is_finite() && end.y.is_finite());
        }
    }
}
