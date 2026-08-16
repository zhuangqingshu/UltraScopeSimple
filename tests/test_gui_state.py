"""The combobox option table, which replaces the old string round-trip."""

import pytest

from ultrascope.gui.state import (OptionTable, TRIGGER_SOURCES,
                                  condition_label_for, condition_labels,
                                  normalise_trigger_source, timed_trigger_spec)
from ultrascope.profile import DS1000E

VOLTS = OptionTable(DS1000E.volt_scales, "V")
SECONDS = OptionTable(DS1000E.time_scales, "s")


def test_label_maps_back_to_the_exact_value():
    assert VOLTS.value_for("20 mV") == 0.02
    assert SECONDS.value_for("1 us") == 1e-6


def test_empty_selection_is_none():
    assert VOLTS.value_for("") is None


def test_an_unknown_label_is_none_rather_than_a_wrong_value():
    assert VOLTS.value_for("17 mV") is None


def test_a_value_the_instrument_reports_snaps_to_the_nearest_step():
    # The scope answers with float noise; the old code string-compared and
    # ended up selecting nothing at all.
    assert VOLTS.label_for(0.020000000000001) == "20 mV"
    assert VOLTS.label_for(0.0199999) == "20 mV"
    assert SECONDS.label_for(1.0000001e-6) == "1 us"


def test_every_label_round_trips():
    for label, value in zip(VOLTS.labels, VOLTS.values):
        assert VOLTS.value_for(label) == value
        assert VOLTS.label_for(value) == label


# --- trigger source normalisation ------------------------------------------

@pytest.mark.parametrize("reported, expected", [
    ("CH1", "CHAN1"), ("CH2", "CHAN2"),          # what the scope actually answers
    ("CHAN1", "CHAN1"), ("CHAN2", "CHAN2"),      # already canonical
    ("ch1", "CHAN1"), (" CH2 ", "CHAN2"),        # case and whitespace
    ("EXT", "EXT"), ("ACLINE", "ACLINE"),
])
def test_reported_source_maps_onto_a_combobox_option(reported, expected):
    assert normalise_trigger_source(reported) == expected


def test_every_normalised_channel_source_is_a_real_option():
    # Setting a value that is not in the list leaves the readonly combobox
    # showing something the user cannot re-select.
    for reported in ("CH1", "CH2", "CHAN1", "CHAN2", "EXT", "ACLINE"):
        assert normalise_trigger_source(reported) in TRIGGER_SOURCES


def test_an_empty_answer_does_not_crash():
    assert normalise_trigger_source("") == ""
    assert normalise_trigger_source(None) == ""


# --- timed trigger condition labels ----------------------------------------

@pytest.mark.parametrize("mode, noun", [("PULSE", "Width"), ("SLOPE", "Slope")])
def test_timed_modes_offer_six_labelled_conditions(mode, noun):
    labels = condition_labels(mode)
    assert len(labels) == 6
    assert all(noun in label for label in labels)
    assert labels[0].startswith("+") and labels[-1].startswith("-")


@pytest.mark.parametrize("mode", ["EDGE", "VIDEO", "PATTERN", "ALTERNATION", ""])
def test_modes_without_a_condition_offer_no_labels(mode):
    assert condition_labels(mode) == ()


def test_reported_keyword_maps_back_to_its_label():
    assert condition_label_for("PULSE", "+LESSthan") == "+Width <"
    assert condition_label_for("SLOPE", "-GREaterthan") == "-Slope >"


def test_label_lookup_is_case_insensitive():
    # The instrument may echo the keyword in any case.
    assert condition_label_for("PULSE", "+LESSTHAN") == "+Width <"


def test_label_lookup_copes_with_nothing_reported():
    assert condition_label_for("PULSE", "") == ""
    assert condition_label_for("EDGE", "+LESSthan") == ""


def test_every_label_is_selectable_in_its_own_mode():
    # A value not in the combobox list cannot be re-selected by the user.
    for mode in ("PULSE", "SLOPE"):
        labels = condition_labels(mode)
        for label in labels:
            keyword = timed_trigger_spec(mode).conditions[label]
            assert condition_label_for(mode, keyword) in labels
