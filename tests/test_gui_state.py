"""The combobox option table, which replaces the old string round-trip."""

import pytest

from ultrascope.gui.state import (OptionTable, TRIGGER_SOURCES,
                                  normalise_trigger_source)
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
