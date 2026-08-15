import pytest

from ultrascope.units import eng, scpi_number


def test_zero_has_no_prefix():
    assert eng(0, "V") == "0 V"


def test_picks_the_prefix_the_front_panel_would_show():
    assert eng(20e-3, "V") == "20 mV"
    assert eng(1e-6, "s") == "1 us"
    assert eng(500e-9, "s") == "500 ns"
    assert eng(2.0, "V") == "2 V"
    assert eng(1e3, "Hz") == "1 kHz"


def test_negative_values_keep_their_sign():
    assert eng(-1.5, "V") == "-1.5 V"


def test_below_the_smallest_prefix_falls_back():
    assert eng(1e-15, "s").endswith(" s")


# --- SCPI number formatting -------------------------------------------------
# The DS1000E silently ignores a command whose number carries an exponent,
# so this formatting is a correctness concern, not cosmetics.

@pytest.mark.parametrize("value", [
    2e-9, 5e-9, 1e-8, 5e-7, 1e-6, 5e-6, 1e-12, 2.5e-7, -5e-7, 1e-5, 5e-5,
])
def test_small_values_never_use_exponent_notation(value):
    # The scope silently ignores commands whose number carries an exponent.
    text = scpi_number(value)
    assert "e" not in text.lower()
    assert float(text) == pytest.approx(value)


@pytest.mark.parametrize("value, expected", [
    (2e-9, "0.000000002"), (5e-7, "0.0000005"), (1e-6, "0.000001"),
    (5e-5, "0.00005"), (1e-4, "0.0001"), (0.002, "0.002"), (1.86, "1.86"),
    (0, "0"), (-0.5, "-0.5"), (1000.0, "1000"), (1.5, "1.5"),
])
def test_scpi_number_renders_as_the_scope_expects(value, expected):
    assert scpi_number(value) == expected


def test_every_supported_timebase_survives_formatting():
    # All 32 front-panel timebase steps must round-trip; the fast ones are
    # exactly the values Python would otherwise render as 2e-09.
    from ultrascope.profile import DS1000E

    for step in DS1000E.time_scales:
        assert float(scpi_number(step)) == pytest.approx(step)
        assert "e" not in scpi_number(step).lower()
