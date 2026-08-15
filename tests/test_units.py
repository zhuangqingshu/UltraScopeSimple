from ultrascope.units import eng


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
