"""The combobox option table, which replaces the old string round-trip."""

from ultrascope.gui.state import OptionTable
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
