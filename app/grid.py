"""Core beat-grid logic.

Pure standard-library module: durations are exact ``fractions.Fraction``
values parsed from positive rational strings, and the grid is built from
the union of every voice's event boundaries.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from fractions import Fraction
from math import gcd, lcm

# A positive rational: "3", "3/4", "12/8" ... (no sign, no decimals).
_RATIONAL_RE = re.compile(r"^(\d+)(?:/(\d+))?$")

KIND_SOUND = "sound"
KIND_REST = "rest"
KINDS = (KIND_SOUND, KIND_REST)

# Safety cap on the number of repeated event copies an alignment may
# expand to; over this the request is rejected and no timeline is built.
MAX_EXPANDED_EVENTS = 2000


def parse_rational(text: str) -> Fraction:
    """Parse a positive rational string ("2", "3/4") into a Fraction.

    Raises ValueError on malformed input, zero denominators, or
    non-positive values.
    """
    if not isinstance(text, str):
        raise ValueError(f"rational must be a string, got {type(text).__name__}")
    match = _RATIONAL_RE.match(text.strip())
    if not match:
        raise ValueError(f"invalid rational number: {text!r}")
    numerator = int(match.group(1))
    denominator = int(match.group(2)) if match.group(2) else 1
    if denominator == 0:
        raise ValueError(f"denominator must not be zero: {text!r}")
    value = Fraction(numerator, denominator)
    if value <= 0:
        raise ValueError(f"duration must be positive, got {text!r}")
    return value


def format_rational(value: Fraction) -> str:
    """Canonical string form of a Fraction ("3/4", "2", "0")."""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def event_id(voice_index: int, event_index: int) -> str:
    """Deterministic event identifier, 1-based: v1e1, v1e2, v2e1, ..."""
    return f"v{voice_index + 1}e{event_index + 1}"


def build_voice_events(voice_index: int, events: list[dict]) -> list[dict]:
    """Attach id/start/end offsets to a voice's events.

    ``events`` items must have ``kind`` and a ``Fraction`` ``duration``.
    Offsets are cumulative exact fractions; event i spans the half-open
    interval [start, end).
    """
    built = []
    cursor = Fraction(0)
    for position, event in enumerate(events):
        end = cursor + event["duration"]
        built.append(
            {
                "id": event_id(voice_index, position),
                "kind": event["kind"],
                "duration": event["duration"],
                "start": cursor,
                "end": end,
                "position": position,
            }
        )
        cursor = end
    return built


def voice_total(events: list[dict]) -> Fraction:
    """Exact accumulated duration of built (or raw) events."""
    total = Fraction(0)
    for event in events:
        total += event["duration"]
    return total


def compute_grid(voices: list[dict]) -> list[dict]:
    """Build half-open time segments from the union of all boundaries.

    ``voices`` is a list of {"name": str, "events": [built events]}.
    Every voice must span the same total duration. Returns one segment
    per adjacent boundary pair [start, end); each segment maps voice
    names to the sounding event id, or None when the voice rests.

    Because segment edges are exactly the union of event edges, each
    segment lies fully inside exactly one event per voice: boundaries
    neither drop nor duplicate sound (half-open intervals: an event is
    active for start <= t < end).
    """
    points: set[Fraction] = set()
    for voice in voices:
        for event in voice["events"]:
            points.add(event["start"])
            points.add(event["end"])
    ordered = sorted(points)

    # Per voice, event end offsets are strictly increasing (durations
    # are positive), so bisect finds the event covering any segment.
    ends_per_voice = [[event["end"] for event in voice["events"]] for voice in voices]

    segments = []
    for index in range(len(ordered) - 1):
        start, end = ordered[index], ordered[index + 1]
        active: dict[str, str | None] = {}
        for voice, ends in zip(voices, ends_per_voice):
            slot = bisect_right(ends, start)
            event = voice["events"][slot]
            # The segment starts inside this event by construction.
            assert event["start"] <= start < event["end"]
            active[voice["name"]] = (
                event["id"] if event["kind"] == KIND_SOUND else None
            )
        segments.append({"start": start, "end": end, "events": active})
    return segments


def find_rhythm_match(
    source_events: list[dict], candidate_events: list[dict]
) -> dict | None:
    """Check whether a candidate voice is a cyclic re-tempo of a source.

    Each list holds built events with exact ``Fraction`` durations. The
    rhythms are considered equivalent when both sequences contain the
    same number N of events and some cyclic shift ``s`` in [0, N) makes,
    for every j, the candidate's j-th event correspond to the source's
    (j + s) mod N-th event:

    * the sound/rest kinds agree one by one, and
    * every candidate/source duration ratio equals the same positive
      rational (a uniform re-tempo, possibly by 1).

    Ratios are compared exactly with Fractions. Returns
    ``{"ratio": Fraction, "shift": int}`` for the smallest feasible
    shift (shifts are tried from 0 upward), or ``None`` when no shift
    works (including event-count mismatches).
    """
    count = len(source_events)
    if count == 0 or count != len(candidate_events):
        return None

    source_kinds = [event["kind"] for event in source_events]
    candidate_kinds = [event["kind"] for event in candidate_events]
    source_durations = [event["duration"] for event in source_events]
    candidate_durations = [event["duration"] for event in candidate_events]

    for shift in range(count):
        # Event durations are stored positive, so this ratio is positive.
        ratio = candidate_durations[0] / source_durations[shift]
        for j in range(count):
            source_index = (j + shift) % count
            if candidate_kinds[j] != source_kinds[source_index]:
                break
            if candidate_durations[j] != ratio * source_durations[source_index]:
                break
        else:
            return {"ratio": ratio, "shift": shift}
    return None


def rational_lcm(left: Fraction, right: Fraction) -> Fraction:
    """Least positive common multiple of two positive rationals.

    This is the first instant t > 0 at which two loops of lengths
    ``left`` and ``right`` are simultaneously back at their start:
    t = k * left = m * right for positive integers k, m.
    """
    return Fraction(
        lcm(left.numerator, right.numerator),
        gcd(left.denominator, right.denominator),
    )


class LoopAlignmentTooLarge(Exception):
    """Expansion would produce more than ``max_events`` event copies."""

    def __init__(
        self,
        *,
        horizon: Fraction,
        repetitions_a: int,
        repetitions_b: int,
        event_count_a: int,
        event_count_b: int,
        expanded_events: int,
        max_events: int,
    ) -> None:
        super().__init__(
            f"loop alignment expands to {expanded_events} events, "
            f"over the limit of {max_events}"
        )
        self.horizon = horizon
        self.repetitions_a = repetitions_a
        self.repetitions_b = repetitions_b
        self.event_count_a = event_count_a
        self.event_count_b = event_count_b
        self.expanded_events = expanded_events
        self.max_events = max_events


def align_loops(
    length_a: Fraction,
    voices_a: list[dict],
    length_b: Fraction,
    voices_b: list[dict],
    *,
    max_events: int = MAX_EXPANDED_EVENTS,
) -> dict:
    """Align two looping measures at their first common restart.

    Each measure is treated as a loop repeated from t = 0. The horizon
    is the first positive instant at which both loops restart together
    (the rational LCM of their lengths); events are repeated to cover
    [0, horizon). The timeline is cut into half-open segments by the
    union of every repeated event boundary. Per segment, every voice is
    tagged with the original event id and its 1-based loop number, or
    null while that voice rests.

    ``voices`` items are {"name": str, "events": [built events]} and
    each voice must span exactly its loop length. Raises
    LoopAlignmentTooLarge when the total number of repeated event copies
    (before any timeline is built) exceeds ``max_events``.
    """
    horizon = rational_lcm(length_a, length_b)
    reps_a = horizon // length_a
    reps_b = horizon // length_b

    def event_count(voices: list[dict]) -> int:
        return sum(len(voice["events"]) for voice in voices)

    count_a = event_count(voices_a)
    count_b = event_count(voices_b)
    expanded = reps_a * count_a + reps_b * count_b
    if expanded > max_events:
        raise LoopAlignmentTooLarge(
            horizon=horizon,
            repetitions_a=reps_a,
            repetitions_b=reps_b,
            event_count_a=count_a,
            event_count_b=count_b,
            expanded_events=expanded,
            max_events=max_events,
        )

    # Union of all repeated event boundaries. For loop r (0-based) and a
    # base measure boundary p the absolute time is r * length + p.
    points: set[Fraction] = set()
    for repetition in range(reps_a):
        origin = repetition * length_a
        for voice in voices_a:
            for event in voice["events"]:
                points.add(origin + event["start"])
                points.add(origin + event["end"])
    for repetition in range(reps_b):
        origin = repetition * length_b
        for voice in voices_b:
            for event in voice["events"]:
                points.add(origin + event["start"])
                points.add(origin + event["end"])
    ordered = sorted(points)

    def active_voices(length: Fraction, voices: list[dict], time: Fraction) -> dict:
        # time is a boundary strictly below horizon, hence it falls in
        # exactly one half-open event copy per voice.
        repetition = time // length
        offset = time - repetition * length
        result: dict[str, dict] = {}
        for voice in voices:
            ends = [event["end"] for event in voice["events"]]
            event = voice["events"][bisect_right(ends, offset)]
            # The boundary lies inside this event by construction.
            assert event["start"] <= offset < event["end"]
            result[voice["name"]] = (
                {"event_id": event["id"], "loop": repetition + 1}
                if event["kind"] == KIND_SOUND
                else None
            )
        return result

    segments = []
    for index in range(len(ordered) - 1):
        start, end = ordered[index], ordered[index + 1]
        segments.append(
            {
                "start": start,
                "end": end,
                "a": active_voices(length_a, voices_a, start),
                "b": active_voices(length_b, voices_b, start),
            }
        )

    return {
        "horizon": horizon,
        "repetitions": {"a": reps_a, "b": reps_b},
        "expanded_events": expanded,
        "segments": segments,
    }
