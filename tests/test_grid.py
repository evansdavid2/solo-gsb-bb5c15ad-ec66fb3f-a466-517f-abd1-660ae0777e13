"""Pure-logic tests: runnable with stdlib unittest or pytest."""

import unittest
from fractions import Fraction

from app.grid import (
    KIND_REST,
    KIND_SOUND,
    LoopAlignmentTooLarge,
    align_loops,
    build_voice_events,
    compute_grid,
    find_rhythm_match,
    format_rational,
    parse_nonnegative_rational,
    parse_rational,
    quantize_boundaries,
    rational_lcm,
    voice_total,
)


def make_voices(specs):
    """specs: list of (name, [(kind, duration_str), ...])."""
    voices = []
    for index, (name, events) in enumerate(specs):
        raw = [
            {"kind": kind, "duration": parse_rational(duration)}
            for kind, duration in events
        ]
        voices.append({"name": name, "events": build_voice_events(index, raw)})
    return voices


class ParseRationalTest(unittest.TestCase):
    def test_integer_and_fraction(self):
        self.assertEqual(parse_rational("2"), Fraction(2, 1))
        self.assertEqual(parse_rational("3/4"), Fraction(3, 4))
        self.assertEqual(parse_rational("6/8"), Fraction(3, 4))  # reduced

    def test_rejects_bad_input(self):
        for bad in ["", "abc", "1/0", "0", "0/4", "-1/2", "1.5", "1/2/3", "/2"]:
            with self.assertRaises(ValueError, msg=bad):
                parse_rational(bad)

    def test_format_roundtrip(self):
        self.assertEqual(format_rational(Fraction(3, 4)), "3/4")
        self.assertEqual(format_rational(Fraction(2, 1)), "2")
        self.assertEqual(format_rational(Fraction(0, 1)), "0")


class ParseNonnegativeRationalTest(unittest.TestCase):
    def test_accepts_zero_and_positive(self):
        self.assertEqual(parse_nonnegative_rational("0"), Fraction(0))
        self.assertEqual(parse_nonnegative_rational("0/4"), Fraction(0))
        self.assertEqual(parse_nonnegative_rational("3/4"), Fraction(3, 4))
        self.assertEqual(parse_nonnegative_rational("2"), Fraction(2))

    def test_rejects_bad_input(self):
        for bad in ["", "abc", "1/0", "-1/2", "1.5", "1/2/3"]:
            with self.assertRaises(ValueError, msg=bad):
                parse_nonnegative_rational(bad)


class AccumulationTest(unittest.TestCase):
    def test_exact_fraction_sum(self):
        # 1/3 + 1/3 + 1/3 == 1 exactly (floats would not guarantee this).
        raw = [{"kind": KIND_SOUND, "duration": parse_rational("1/3")}] * 3
        self.assertEqual(voice_total(raw), Fraction(1))

    def test_event_offsets_are_half_open(self):
        (voice,) = make_voices([("a", [(KIND_SOUND, "1/2"), (KIND_REST, "1/4")])])
        first, second = voice["events"]
        self.assertEqual((first["start"], first["end"]), (Fraction(0), Fraction(1, 2)))
        self.assertEqual(
            (second["start"], second["end"]), (Fraction(1, 2), Fraction(3, 4))
        )
        self.assertEqual(first["id"], "v1e1")
        self.assertEqual(second["id"], "v1e2")


class GridTest(unittest.TestCase):
    def setUp(self):
        # melody: sound 1/2, rest 1/4, sound 1/4  (total 1)
        # bass:   rest 1/4, sound 3/4             (total 1)
        self.voices = make_voices(
            [
                (
                    "melody",
                    [(KIND_SOUND, "1/2"), (KIND_REST, "1/4"), (KIND_SOUND, "1/4")],
                ),
                ("bass", [(KIND_REST, "1/4"), (KIND_SOUND, "3/4")]),
            ]
        )
        self.segments = compute_grid(self.voices)

    def test_segment_boundaries_are_union_of_event_edges(self):
        edges = [(s["start"], s["end"]) for s in self.segments]
        self.assertEqual(
            edges,
            [
                (Fraction(0), Fraction(1, 4)),
                (Fraction(1, 4), Fraction(1, 2)),
                (Fraction(1, 2), Fraction(3, 4)),
                (Fraction(3, 4), Fraction(1)),
            ],
        )

    def test_active_events_per_segment(self):
        active = [s["events"] for s in self.segments]
        self.assertEqual(
            active,
            [
                {"melody": "v1e1", "bass": None},
                {"melody": "v1e1", "bass": "v2e2"},
                {"melody": None, "bass": "v2e2"},
                {"melody": "v1e3", "bass": "v2e2"},
            ],
        )

    def test_segments_tile_target_without_gaps_or_overlap(self):
        # No lost or duplicated time: segments chain exactly onto [0, 1).
        self.assertEqual(self.segments[0]["start"], Fraction(0))
        self.assertEqual(self.segments[-1]["end"], Fraction(1))
        for left, right in zip(self.segments, self.segments[1:]):
            self.assertEqual(left["end"], right["start"])

    def test_boundary_instant_belongs_to_exactly_one_event_per_voice(self):
        # At t = 1/2 the melody switches sound -> rest: the segment
        # starting at 1/2 must show the rest (null), the one ending at
        # 1/2 must show the sound. Half-open intervals => no double
        # counting, no gap.
        before = next(s for s in self.segments if s["end"] == Fraction(1, 2))
        after = next(s for s in self.segments if s["start"] == Fraction(1, 2))
        self.assertEqual(before["events"]["melody"], "v1e1")
        self.assertIsNone(after["events"]["melody"])

    def test_unaligned_voices_produce_finer_grid(self):
        voices = make_voices(
            [
                ("x", [(KIND_SOUND, "1/3"), (KIND_REST, "2/3")]),
                ("y", [(KIND_SOUND, "1/2"), (KIND_SOUND, "1/2")]),
            ]
        )
        segments = compute_grid(voices)
        edges = [(s["start"], s["end"]) for s in segments]
        # x has edges at 0, 1/3, 1; y at 0, 1/2, 1.
        self.assertEqual(
            edges,
            [
                (Fraction(0), Fraction(1, 3)),
                (Fraction(1, 3), Fraction(1, 2)),
                (Fraction(1, 2), Fraction(1)),
            ],
        )
        self.assertEqual(segments[0]["events"], {"x": "v1e1", "y": "v2e1"})
        self.assertEqual(segments[1]["events"], {"x": None, "y": "v2e1"})
        self.assertEqual(segments[2]["events"], {"x": None, "y": "v2e2"})


class RationalLcmTest(unittest.TestCase):
    def test_integer_lengths(self):
        # Loops of length 3 and 4 first restart together at t = 12.
        self.assertEqual(rational_lcm(Fraction(3), Fraction(4)), Fraction(12))

    def test_equal_lengths(self):
        self.assertEqual(rational_lcm(Fraction(1, 2), Fraction(1, 2)), Fraction(1, 2))

    def test_fractional_lengths(self):
        # lcm(3/2, 2/3): lcm(3,2)/gcd(2,3) = 6.
        self.assertEqual(rational_lcm(Fraction(3, 2), Fraction(2, 3)), Fraction(6))
        # lcm(1/3, 1/2) = 1.
        self.assertEqual(rational_lcm(Fraction(1, 3), Fraction(1, 2)), Fraction(1))


class AlignLoopsTest(unittest.TestCase):
    def test_loops_of_different_length_realign_at_lcm(self):
        # A: length 1, one voice "a": sound 1/2, rest 1/2.
        # B: length 2, one voice "b": sound 2.
        (voice_a,) = make_voices(
            [("a", [(KIND_SOUND, "1/2"), (KIND_REST, "1/2")])]
        )
        (voice_b,) = make_voices([("b", [(KIND_SOUND, "2")])])
        result = align_loops(Fraction(1), [voice_a], Fraction(2), [voice_b])

        self.assertEqual(result["horizon"], Fraction(2))
        self.assertEqual(result["repetitions"], {"a": 2, "b": 1})
        self.assertEqual(result["expanded_events"], 2 * 2 + 1)

        edges = [(s["start"], s["end"]) for s in result["segments"]]
        self.assertEqual(
            edges,
            [
                (Fraction(0), Fraction(1, 2)),
                (Fraction(1, 2), Fraction(1)),
                (Fraction(1), Fraction(3, 2)),
                (Fraction(3, 2), Fraction(2)),
            ],
        )
        active = [(s["a"]["a"], s["b"]["b"]) for s in result["segments"]]
        self.assertEqual(
            active,
            [
                ({"event_id": "v1e1", "loop": 1}, {"event_id": "v1e1", "loop": 1}),
                (None, {"event_id": "v1e1", "loop": 1}),
                ({"event_id": "v1e1", "loop": 2}, {"event_id": "v1e1", "loop": 1}),
                (None, {"event_id": "v1e1", "loop": 1}),
            ],
        )

    def test_same_measure_as_both_loops_restarts_after_one_cycle(self):
        (voice_a,) = make_voices([("a", [(KIND_SOUND, "1/3"), (KIND_REST, "2/3")])])
        (voice_b,) = make_voices([("a", [(KIND_SOUND, "1/3"), (KIND_REST, "2/3")])])
        result = align_loops(Fraction(1), [voice_a], Fraction(1), [voice_b])
        self.assertEqual(result["horizon"], Fraction(1))
        self.assertEqual(result["repetitions"], {"a": 1, "b": 1})
        edges = [(s["start"], s["end"]) for s in result["segments"]]
        self.assertEqual(edges, [(Fraction(0), Fraction(1, 3)), (Fraction(1, 3), Fraction(1))])
        self.assertEqual(
            result["segments"][0]["a"]["a"],
            {"event_id": "v1e1", "loop": 1},
        )
        self.assertIsNone(result["segments"][1]["b"]["a"])

    def test_loop_numbers_increase_with_each_repetition(self):
        # Three length-1 loops against one length-3 loop; event per loop
        # at its start must be tagged loop 1, 2 then 3.
        (voice_a,) = make_voices([("a", [(KIND_SOUND, "1/4"), (KIND_REST, "3/4")])])
        (voice_b,) = make_voices([("b", [(KIND_SOUND, "1/4"), (KIND_REST, "11/4")])])
        result = align_loops(Fraction(1), [voice_a], Fraction(3), [voice_b])
        starts = [
            segment["a"]["a"]["loop"]
            for segment in result["segments"]
            if segment["a"]["a"] is not None
        ]
        self.assertEqual(starts, [1, 2, 3])

    def test_expansion_over_limit_is_rejected_without_segments(self):
        (voice_a,) = make_voices([("a", [(KIND_SOUND, "1")])])
        (voice_b,) = make_voices([("b", [(KIND_SOUND, "1")])])
        with self.assertRaises(LoopAlignmentTooLarge) as ctx:
            align_loops(
                Fraction(3), [voice_a], Fraction(5), [voice_b], max_events=7
            )
        error = ctx.exception
        self.assertEqual(error.horizon, Fraction(15))
        self.assertEqual((error.repetitions_a, error.repetitions_b), (5, 3))
        self.assertEqual(error.expanded_events, 8)
        self.assertEqual(error.max_events, 7)


class QuantizeBoundariesTest(unittest.TestCase):
    def test_boundaries_already_on_grid_stay_put_with_zero_shift(self):
        boundaries = [
            Fraction(0),
            Fraction(1, 4),
            Fraction(1, 2),
            Fraction(3, 4),
            Fraction(1),
        ]
        result = quantize_boundaries(boundaries, Fraction(1), 4, Fraction(0))
        self.assertEqual(result, boundaries)

    def test_snaps_to_nearest_tick_within_max_shift(self):
        # 1/3 is exactly 1/6 away from tick 1/2 of a 2-division grid.
        result = quantize_boundaries(
            [Fraction(0), Fraction(1, 3), Fraction(1)],
            Fraction(1),
            2,
            Fraction(1, 6),
        )
        self.assertEqual(result, [Fraction(0), Fraction(1, 2), Fraction(1)])

    def test_infeasible_when_required_shift_exceeds_max(self):
        # 1/3 needs a shift of 1/6 to reach tick 1/2; 1/7 is not enough.
        self.assertIsNone(
            quantize_boundaries(
                [Fraction(0), Fraction(1, 3), Fraction(1)],
                Fraction(1),
                2,
                Fraction(1, 7),
            )
        )

    def test_infeasible_when_more_boundaries_than_grid_points(self):
        boundaries = [
            Fraction(0),
            Fraction(1, 4),
            Fraction(1, 2),
            Fraction(3, 4),
            Fraction(1),
        ]
        # 5 strictly increasing boundaries cannot fit on 4 ticks.
        self.assertIsNone(quantize_boundaries(boundaries, Fraction(1), 3, Fraction(1)))

    def test_strictly_increasing_forces_non_nearest_tick(self):
        # 7/16 is nearest to tick 1/3 (5/48 away), but 1/4 already
        # occupies that tick, so 7/16 moves on to tick 2/3 (11/48).
        result = quantize_boundaries(
            [Fraction(0), Fraction(1, 4), Fraction(7, 16), Fraction(1)],
            Fraction(1),
            3,
            Fraction(1, 4),
        )
        self.assertEqual(
            result, [Fraction(0), Fraction(1, 3), Fraction(2, 3), Fraction(1)]
        )

    def test_infeasible_when_boundaries_cannot_stay_ordered(self):
        # Within 1/8 both 1/4 and 7/16 can only reach tick 1/3, and two
        # boundaries may not share a tick.
        self.assertIsNone(
            quantize_boundaries(
                [Fraction(0), Fraction(1, 4), Fraction(7, 16), Fraction(1)],
                Fraction(1),
                3,
                Fraction(1, 8),
            )
        )

    def test_tie_broken_by_lexicographically_smallest_tick_sequence(self):
        # 3/8 sits exactly between ticks 1/4 and 1/2: equal total shift
        # either way, the earlier tick wins.
        result = quantize_boundaries(
            [Fraction(0), Fraction(3, 8), Fraction(1)],
            Fraction(1),
            4,
            Fraction(1, 8),
        )
        self.assertEqual(result, [Fraction(0), Fraction(1, 4), Fraction(1)])

    def test_tie_break_compares_whole_sequence_not_single_points(self):
        # 2/5 alone is nearest to tick 1/2, but (1/4, 1/2) and (1/2, 3/4)
        # tie on total shift 1/4; the lexicographically smaller sequence
        # (0, 1, 2, 4) wins.
        result = quantize_boundaries(
            [Fraction(0), Fraction(2, 5), Fraction(3, 5), Fraction(1)],
            Fraction(1),
            4,
            Fraction(1, 4),
        )
        self.assertEqual(
            result, [Fraction(0), Fraction(1, 4), Fraction(1, 2), Fraction(1)]
        )

    def test_endpoints_are_pinned_to_grid_ends(self):
        # With a huge budget the interior boundary still cannot pull the
        # endpoints off 0 and total.
        result = quantize_boundaries(
            [Fraction(0), Fraction(1, 10), Fraction(1)],
            Fraction(1),
            4,
            Fraction(10),
        )
        self.assertEqual(result[0], Fraction(0))
        self.assertEqual(result[-1], Fraction(1))
        # 1/10's nearest tick is 0 (shift 1/10) but strict increase
        # forces it to tick 1 (shift 3/20).
        self.assertEqual(result, [Fraction(0), Fraction(1, 4), Fraction(1)])

    def test_non_unit_total_duration(self):
        # Grid of a 3/2-long measure in 3 divisions: ticks 0, 1/2, 1, 3/2.
        result = quantize_boundaries(
            [Fraction(0), Fraction(2, 5), Fraction(1), Fraction(3, 2)],
            Fraction(3, 2),
            3,
            Fraction(1, 10),
        )
        self.assertEqual(
            result, [Fraction(0), Fraction(1, 2), Fraction(1), Fraction(3, 2)]
        )

    def test_rejects_malformed_boundaries(self):
        with self.assertRaises(ValueError):
            quantize_boundaries([Fraction(0)], Fraction(1), 4, Fraction(0))
        with self.assertRaises(ValueError):
            quantize_boundaries(
                [Fraction(0), Fraction(1)], Fraction(1), 0, Fraction(0)
            )


class FindRhythmMatchTest(unittest.TestCase):
    def make_events(self, spec):
        # spec: list of (kind, duration_str); voice index is irrelevant.
        return build_voice_events(
            0,
            [
                {"kind": kind, "duration": parse_rational(duration)}
                for kind, duration in spec
            ],
        )

    def test_identical_rhythm_matches_ratio_one_shift_zero(self):
        source = self.make_events(
            [(KIND_SOUND, "1/2"), (KIND_REST, "1/4"), (KIND_SOUND, "1/4")]
        )
        match = find_rhythm_match(source, source)
        self.assertIsNotNone(match)
        self.assertEqual(match["ratio"], Fraction(1))
        self.assertEqual(match["shift"], 0)

    def test_uniform_retempo_matches_with_common_ratio(self):
        source = self.make_events(
            [(KIND_SOUND, "1/3"), (KIND_REST, "1/3"), (KIND_SOUND, "1/3")]
        )
        # Every duration doubled.
        candidate = self.make_events(
            [(KIND_SOUND, "2/3"), (KIND_REST, "2/3"), (KIND_SOUND, "2/3")]
        )
        match = find_rhythm_match(source, candidate)
        self.assertEqual(match, {"ratio": Fraction(2), "shift": 0})

    def test_non_unit_fraction_ratio_is_exact(self):
        # Ratios like 3/2 must not be reduced through floating point.
        source = self.make_events(
            [(KIND_SOUND, "1/2"), (KIND_REST, "1/6"), (KIND_SOUND, "1/3")]
        )
        candidate = self.make_events(
            [(KIND_SOUND, "3/4"), (KIND_REST, "1/4"), (KIND_SOUND, "1/2")]
        )
        match = find_rhythm_match(source, candidate)
        self.assertEqual(match, {"ratio": Fraction(3, 2), "shift": 0})

    def test_cyclic_shift_matches(self):
        source = self.make_events(
            [(KIND_SOUND, "1/4"), (KIND_REST, "1/4"), (KIND_SOUND, "1/2")]
        )
        # Candidate event j = source event (j + 2) mod 3, same durations.
        candidate = self.make_events(
            [(KIND_SOUND, "1/2"), (KIND_SOUND, "1/4"), (KIND_REST, "1/4")]
        )
        match = find_rhythm_match(source, candidate)
        self.assertEqual(match, {"ratio": Fraction(1), "shift": 2})

    def test_smallest_shift_is_chosen(self):
        # A uniform rhythm (all durations equal) with all events of the
        # same kind matches at every shift: 0 must win.
        source = self.make_events([(KIND_SOUND, "1/4")] * 4)
        candidate = self.make_events([(KIND_SOUND, "1/2")] * 4)
        match = find_rhythm_match(source, candidate)
        self.assertEqual(match, {"ratio": Fraction(2), "shift": 0})

    def test_kind_mismatch_anywhere_fails(self):
        source = self.make_events(
            [(KIND_SOUND, "1/2"), (KIND_REST, "1/2")]
        )
        candidate = self.make_events(
            [(KIND_SOUND, "1/2"), (KIND_SOUND, "1/2")]
        )
        self.assertIsNone(find_rhythm_match(source, candidate))

    def test_different_event_counts_fail(self):
        source = self.make_events(
            [(KIND_SOUND, "1/3"), (KIND_REST, "2/3")]
        )
        candidate = self.make_events(
            [(KIND_SOUND, "1/3"), (KIND_REST, "1/3"), (KIND_REST, "1/3")]
        )
        self.assertIsNone(find_rhythm_match(source, candidate))

    def test_non_uniform_ratios_fail(self):
        source = self.make_events(
            [(KIND_SOUND, "1/2"), (KIND_REST, "1/2")]
        )
        candidate = self.make_events(
            [(KIND_SOUND, "1/4"), (KIND_REST, "3/4")]
        )
        self.assertIsNone(find_rhythm_match(source, candidate))


if __name__ == "__main__":
    unittest.main()