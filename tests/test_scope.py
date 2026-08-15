"""Command-layer behaviour, driven entirely off FakeTransport."""

import pytest

from ultrascope.scope import Scope, ScopeError
from ultrascope.transport import TIMEOUT_RAW_MS, FakeTransport

from test_waveform import block


def make_scope(responses=None, blocks=None):
    transport = FakeTransport(responses or {}, blocks or [])
    return Scope(transport, idn="RIGOL,DS1102E,TEST"), transport


def sent(transport, prefix):
    return [c for c in transport.written if c.startswith(prefix)]


# --- the "a setting you did not pass is never written" contract -------------

def test_set_trigger_writes_nothing_when_given_nothing():
    scope, transport = make_scope({":TRIG:MODE?": "EDGE"})
    scope.set_trigger()
    assert sent(transport, ":TRIG:EDGE:") == []


def test_set_trigger_writes_only_the_supplied_settings():
    scope, transport = make_scope({":TRIG:MODE?": "EDGE"})
    scope.set_trigger(level=1.5)
    assert sent(transport, ":TRIG:EDGE:LEV") == [":TRIG:EDGE:LEV 1.5"]
    assert sent(transport, ":TRIG:EDGE:SLOP") == []
    assert sent(transport, ":TRIG:EDGE:SOUR") == []
    assert sent(transport, ":TRIG:EDGE:COUP") == []


def test_set_acquire_writes_nothing_when_given_nothing():
    scope, transport = make_scope()
    scope.set_acquire()
    assert sent(transport, ":ACQ:") == []


# --- trigger subsystem routing ---------------------------------------------

@pytest.mark.parametrize("mode, subtree", [
    ("EDGE", "EDGE"), ("PULSE", "PULS"), ("SLOPE", "SLOP"),
    ("PATTERN", "PATT"), ("DURATION", "DUR"), ("ALTERNATION", "ALT"),
])
def test_spelled_out_mode_maps_to_the_abbreviated_subtree(mode, subtree):
    scope, transport = make_scope({":TRIG:MODE?": mode})
    scope.set_trigger(source="ch1")
    assert sent(transport, f":TRIG:{subtree}:SOUR") == [f":TRIG:{subtree}:SOUR CHAN1"]


def test_unknown_trigger_mode_is_rejected():
    scope, _ = make_scope({":TRIG:MODE?": "NONSENSE"})
    with pytest.raises(ScopeError):
        scope.set_trigger(level=1.0)


def test_alternation_has_no_sweep_setting():
    scope, _ = make_scope({":TRIG:MODE?": "ALTERNATION"})
    with pytest.raises(ScopeError):
        scope.set_trigger(sweep="auto")


def test_source_aliases_normalise_to_scpi_names():
    scope, transport = make_scope({":TRIG:MODE?": "EDGE"})
    scope.set_trigger(source="2")
    assert sent(transport, ":TRIG:EDGE:SOUR") == [":TRIG:EDGE:SOUR CHAN2"]


# --- limits ----------------------------------------------------------------

@pytest.mark.parametrize("count", [1, 0, 257, 512])
def test_average_count_outside_the_supported_range_is_rejected(count):
    scope, _ = make_scope()
    with pytest.raises(ScopeError):
        scope.set_acquire(average=count)


def test_average_count_forces_the_acquisition_type():
    scope, transport = make_scope()
    scope.set_acquire(average=16)
    assert ":ACQ:TYPE AVERAGE" in transport.written
    assert ":ACQ:AVER 16" in transport.written


# --- measurements ----------------------------------------------------------

def measurement_responses(value="1.5"):
    return {f":MEAS:{cmd}? CHAN1": value for _, cmd in Scope.MEASUREMENTS}


def test_measure_reads_every_label():
    scope, _ = make_scope(measurement_responses())
    stats = scope.measure(1)
    assert set(stats) == {label for label, _ in Scope.MEASUREMENTS}
    assert stats["Vpp"] == 1.5


def test_unavailable_measurements_become_none():
    # The scope answers with a huge sentinel instead of an error.
    scope, _ = make_scope(measurement_responses("9.9e37"))
    assert all(v is None for v in scope.measure(1).values())


# --- capture ---------------------------------------------------------------

CAPTURE_RESPONSES = {
    ":CHAN1:DISP?": "1",
    ":CHAN2:DISP?": "0",
    ":CHAN1:SCAL?": "1",
    ":CHAN1:OFFS?": "0",
    ":TIM:SCAL?": "0.001",
    ":TIM:OFFS?": "0",
}


def test_capture_skips_channels_that_are_off():
    payload = bytes([255 - 130] * 600)
    scope, _ = make_scope(CAPTURE_RESPONSES, [block(payload)])
    wave = scope.capture((1, 2), points="normal")
    assert wave.channel_ids == [1]
    assert wave.npoints == 600
    assert wave.timebase == 0.001
    assert wave.points_mode == "normal"


def test_capture_raises_when_every_channel_is_off():
    scope, _ = make_scope({":CHAN1:DISP?": "0", ":CHAN2:DISP?": "0"})
    with pytest.raises(ScopeError):
        scope.capture((1, 2))


def test_raw_mode_stops_the_scope_and_uses_the_long_timeout():
    scope, transport = make_scope(CAPTURE_RESPONSES, [block(bytes([120] * 16))])
    scope.read_channel(1, points="raw")
    assert ":STOP" in transport.written
    assert ":WAV:POIN:MODE RAW" in transport.written
    assert transport.timeouts == [TIMEOUT_RAW_MS]


def test_normal_mode_does_not_stop_the_scope():
    scope, transport = make_scope(CAPTURE_RESPONSES, [block(bytes([120] * 16))])
    scope.read_channel(1, points="normal")
    assert ":STOP" not in transport.written
    assert ":WAV:POIN:MODE NORM" in transport.written


def test_a_malformed_block_is_reported_with_the_channel():
    scope, _ = make_scope(CAPTURE_RESPONSES, [b"garbage"])
    with pytest.raises(ScopeError, match="CH1"):
        scope.read_channel(1)


# --- lifecycle -------------------------------------------------------------

def test_close_hands_the_front_panel_back():
    scope, transport = make_scope()
    scope.close()
    assert ":KEY:FORC" in transport.written
    assert transport.closed


def test_context_manager_closes():
    scope, transport = make_scope()
    with scope:
        pass
    assert transport.closed


# --- snapshot --------------------------------------------------------------

SNAPSHOT_RESPONSES = dict(CAPTURE_RESPONSES, **{
    ":ACQ:TYPE?": "NORMAL",
    ":ACQ:AVER?": "16",
    ":ACQ:MEMD?": "NORMAL",
    ":TRIG:MODE?": "EDGE",
    ":CHAN1:COUP?": "DC",
    ":CHAN2:SCAL?": "0.5",
    ":CHAN2:COUP?": "AC",
    ":TRIG:EDGE:SOUR?": "CHAN1",
    ":TRIG:EDGE:SLOP?": "POSITIVE",
    ":TRIG:EDGE:SWE?": "AUTO",
    ":TRIG:EDGE:LEV?": "1.2",
})


def test_snapshot_collects_the_front_panel_state():
    scope, _ = make_scope(SNAPSHOT_RESPONSES)
    settings = scope.snapshot((1, 2))
    assert settings.timebase == 0.001
    assert settings.average == 16
    assert settings.trigger_mode == "EDGE"
    assert settings.trigger_level == 1.2
    assert settings.channels[1].on is True
    assert settings.channels[2].on is False
    assert settings.channels[2].coupling == "AC"


def test_snapshot_tolerates_a_trigger_mode_without_level_or_slope():
    responses = {k: v for k, v in SNAPSHOT_RESPONSES.items()
                 if not k.startswith(":TRIG:EDGE:")}
    scope, _ = make_scope(responses)
    settings = scope.snapshot((1, 2))
    assert settings.trigger_level is None
    assert settings.timebase == 0.001
