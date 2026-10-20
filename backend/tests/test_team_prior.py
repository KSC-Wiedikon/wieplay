"""A team's usual rotations and numbers as a prior (PLAN §10.5b, wieplay/ml/team_prior.py).
Made-up numbers only."""

from __future__ import annotations

import pytest

from wieplay.ml import team_prior as tp

# Setter 7, outside 12, middle 2, opposite 1, outside 22, middle 8 (setter first).
USUAL = (7, 12, 2, 1, 22, 8)


def lineup(order: tuple[int, ...], setter_zone: int) -> tuple[int, ...]:
    """Zones I-VI with the order's setter (place 0) in `setter_zone`."""
    offset = tp.offset_of_zone(setter_zone)
    return tuple(order[(offset + z) % 6] for z in range(6))


def test_learn_turns_every_lineup_to_its_setter() -> None:
    starts = [
        tp.SetStart(lineup(USUAL, 1), setter=7),
        tp.SetStart(lineup(USUAL, 4), setter=7),
        tp.SetStart(lineup((7, 12, 2, 1, 22, 18), 2), setter=7),
    ]
    prior = tp.learn(starts)
    assert prior.consensus() == USUAL
    assert prior.orders[0][0] == USUAL
    assert prior.start_zones == {1: 1.0, 4: 1.0, 2: 1.0}
    assert prior.setters == {7: 3.0}


def test_a_lineup_without_a_known_setter_is_turned_to_fit() -> None:
    starts = [tp.SetStart(lineup(USUAL, 1), setter=7), tp.SetStart(lineup(USUAL, 3))]
    prior = tp.learn(starts)
    assert prior.orders == [(USUAL, 2.0)]
    assert prior.start_zones == {1: 1.0, 3: 1.0}


def test_round_trip_through_json() -> None:
    prior = tp.learn([tp.SetStart(lineup(USUAL, z), setter=7) for z in (1, 2, 2, 5)])
    again = tp.OrderPrior.from_json(prior.as_json())
    assert again.consensus() == prior.consensus()
    assert again.start_zones == prior.start_zones
    assert tp.OrderPrior.from_json(None).empty


@pytest.mark.parametrize("zone", [1, 2, 3, 4, 5, 6])
def test_server_walks_the_order(zone: int) -> None:
    six = lineup(USUAL, zone)
    offset = tp.offset_of_zone(zone)
    for r in range(12):
        assert tp.server(USUAL, offset, r) == six[r % 6]


def test_setter_zone_and_one_server_pin_the_rotation() -> None:
    prior = tp.learn([tp.SetStart(lineup(USUAL, z), setter=7) for z in (1, 2, 3, 4)])
    # Nothing seen: the rotation is a guess.
    best = tp.posterior(prior).best(0)
    assert best is not None and best[1] < 0.6
    # The coach's setter zone: our server at every rotation follows.
    post = tp.posterior(prior, setter_zone=3)
    six = lineup(USUAL, 3)
    for r in range(6):
        found = post.best(r)
        assert found is not None and found[0] == six[r]
    # Two servers read: the same.
    post = tp.posterior(prior, [tp.Seen(0, six[0]), tp.Seen(2, six[2])])
    for r in range(6):
        found = post.best(r)
        assert found is not None and found[0] == six[r]
        assert found[1] > 0.7


def test_a_reading_names_a_newcomer_at_its_place() -> None:
    prior = tp.learn([tp.SetStart(lineup(USUAL, 1), setter=7) for _ in range(3)])
    # #6 plays where #22 usually does, read twice at that place (rotation 4 and 10).
    post = tp.posterior(prior, [tp.Seen(0, 7), tp.Seen(4, 6), tp.Seen(10, 6)])
    best = post.best(4)
    assert best is not None and best[0] == 6
    other = post.best(1)
    assert other is not None and other[0] == 12


def test_a_coach_lineup_needs_no_memory() -> None:
    six = (18, 7, 12, 2, 1, 22)
    post = tp.posterior(tp.OrderPrior(), lineup=six)
    for r in range(6):
        found = post.best(r)
        assert found is not None and found[0] == six[r]
    start = post.lineup()
    assert start is not None and start[0] == six


def test_empty_prior_without_anything_seen_says_nothing() -> None:
    assert tp.posterior(tp.OrderPrior()).best(0) is None


def test_reading_confidence_follows_the_prior() -> None:
    regular = tp.reading_confidence(0.8, 0.9)
    rare = tp.reading_confidence(0.8, 0.05)
    assert regular > 0.95
    assert rare < regular
    assert tp.reading_confidence(0.0, 0.9) == 0.0
    prior = tp.number_prior([tp.NumberFacts(7, games=3), tp.NumberFacts(99, games=0)], 3)
    assert prior[7] > 0.8 and prior[99] < 0.2
