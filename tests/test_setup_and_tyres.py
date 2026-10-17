"""Setup differences between two laps, and tyre life per stint."""
import pytest

from analysis.lap_tracker import CompletedLap
from analysis.setup import setup_diff
from analysis.tyres import stints

BASE = {"front_wing": 6, "rear_wing": 4, "brake_bias": 56, "front_left_tyre_pressure": 23.0, "fuel_load": 53.0}


def test_only_changed_settings_are_listed_with_units():
    rows = setup_diff({**BASE, "front_wing": 7, "front_left_tyre_pressure": 22.6}, BASE)
    assert rows == [("Front wing", "7", "6"), ("Front-left tyre pressure", "22.6 psi", "23.0 psi")]


def test_identical_setups_have_no_rows_and_missing_ones_none():
    assert setup_diff(BASE, dict(BASE)) == []
    assert setup_diff(BASE, None) is None and setup_diff(None, BASE) is None


def test_settings_only_one_game_sends_are_shown_as_not_sent():
    rows = setup_diff({**BASE, "engine_braking": 60}, BASE)
    assert rows == [("Engine braking", "60%", "not sent")]


def lap(n, ms, age, compound="Soft", valid=True, pit=False):
    return CompletedLap(n, ms, None, None, None, valid=valid, samples_complete=True, pit_lap=pit, out_lap=False,
                        tyre_age_laps=age, tyre_compound=compound)


def test_stints_split_on_new_tyres_and_fit_a_slope():
    laps = [lap(1, 90_800, 1), lap(2, 91_000, 2), lap(3, 91_200, 3), lap(4, 91_400, 4), lap(5, 91_600, 5),
            lap(6, 99_000, 6, pit=True),
            lap(7, 90_500, 1, "Hard"), lap(8, 90_600, 2, "Hard"), lap(9, 90_700, 3, "Hard")]
    s = stints(laps)
    assert [(x.compound, [lp.lap_number for lp in x.laps]) for x in s] == [("Soft", [1, 2, 3, 4, 5, 6]),
                                                                          ("Hard", [7, 8, 9])]
    # timed laps only (the pit lap and nothing else is left out of the fit)
    assert s[0].slope_s_per_lap == pytest.approx(0.2) and s[0].fit_laps == 5
    assert s[1].slope_s_per_lap == pytest.approx(0.1) and s[1].fit_laps == 3


def test_too_few_laps_give_no_slope_and_laps_without_age_are_skipped():
    s = stints([lap(1, 90_000, 1), lap(2, 90_100, 2), CompletedLap(3, 90_000, None, None, None, True, True, False,
                                                                     False)])
    assert len(s) == 1 and s[0].slope_s_per_lap is None and len(s[0].laps) == 2
