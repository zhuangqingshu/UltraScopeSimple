"""Command-layer behaviour, driven entirely off FakeTransport."""

import pytest

from ultrascope.scope import Scope, ScopeError, ScopeSettings
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


def test_a_truncated_deep_read_fails_instead_of_returning_short_data():
    # This is what a real RAW read does: 1M declared, a fraction delivered.
    short = block(bytes(1_048_576))[:12_288]
    scope, _ = make_scope(CAPTURE_RESPONSES, [short])
    with pytest.raises(ScopeError, match="truncated"):
        scope.read_channel(1, points="raw")


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
    ":CHAN1:PROB?": "10",
    ":CHAN2:SCAL?": "0.5",
    ":CHAN2:OFFS?": "0.25",
    ":CHAN2:COUP?": "AC",
    ":CHAN2:PROB?": "1",
    ":TRIG:EDGE:SOUR?": "CHAN1",
    ":TRIG:EDGE:SLOP?": "POSITIVE",
    ":TRIG:EDGE:SWE?": "AUTO",
    ":TRIG:EDGE:LEV?": "1.2",
    ":TRIG:HOLD?": "5e-7",
})


def test_snapshot_collects_the_front_panel_state():
    scope, _ = make_scope(SNAPSHOT_RESPONSES)
    settings = scope.snapshot((1, 2))
    assert settings.timebase == 0.001
    assert settings.average == 16
    assert settings.trigger_mode == "EDGE"
    assert settings.trigger_level == 1.2
    assert settings.trigger_holdoff == 5e-7
    assert settings.channels[1].on is True
    assert settings.channels[1].probe == 10.0
    assert settings.channels[2].on is False
    assert settings.channels[2].coupling == "AC"
    assert settings.channels[2].volt_offset == 0.25


def test_snapshot_tolerates_a_trigger_mode_without_level_or_slope():
    responses = {k: v for k, v in SNAPSHOT_RESPONSES.items()
                 if not k.startswith(":TRIG:EDGE:")}
    scope, _ = make_scope(responses)
    settings = scope.snapshot((1, 2))
    assert settings.trigger_level is None
    assert settings.timebase == 0.001


def test_snapshot_survives_the_setup_file_round_trip():
    scope, _ = make_scope(SNAPSHOT_RESPONSES)
    settings = scope.snapshot((1, 2))
    assert ScopeSettings.from_dict(settings.to_dict()) == settings


def test_setup_file_keys_stay_stable():
    # This dict is a published file format; renaming a key breaks saved setups.
    scope, _ = make_scope(SNAPSHOT_RESPONSES)
    state = scope.snapshot((1, 2)).to_dict()
    assert set(state) == {"idn", "timebase", "time_offset", "acq_type",
                          "acq_average", "acq_memdepth", "trigger", "channels"}
    assert set(state["channels"]) == {"1", "2"}
    assert set(state["channels"]["1"]) == {"on", "probe", "scale", "offset",
                                           "coupling"}


# --- probe attenuation -----------------------------------------------------

def test_probe_ratio_must_be_a_supported_step():
    scope, _ = make_scope()
    with pytest.raises(ScopeError):
        scope.set_probe(1, 3.0)


@pytest.mark.parametrize("ratio, written", [
    (1.0, ":CHAN1:PROB 1"), (10.0, ":CHAN1:PROB 10"),
    (1000.0, ":CHAN1:PROB 1000"),
])
def test_probe_ratio_is_written_without_a_trailing_zero(ratio, written):
    scope, transport = make_scope()
    scope.set_probe(1, ratio)
    assert written in transport.written


def test_restore_writes_probe_before_scale_and_offset():
    # Changing the probe ratio rescales volts/div and offset underneath, so
    # the order here is load-bearing, not cosmetic.
    scope, transport = make_scope({":TRIG:MODE?": "EDGE"})
    scope.restore({"channels": {"1": {"probe": 10.0, "scale": 1.0,
                                      "offset": 0.5, "coupling": "DC",
                                      "on": True}}})
    order = [c.split()[0] for c in transport.written
             if c.startswith((":CHAN1:PROB", ":CHAN1:SCAL", ":CHAN1:OFFS"))]
    assert order.index(":CHAN1:PROB") < order.index(":CHAN1:SCAL")
    assert order.index(":CHAN1:PROB") < order.index(":CHAN1:OFFS")


# --- holdoff ---------------------------------------------------------------

@pytest.mark.parametrize("seconds", [1e-9, 50e-9, 2.0, 60.0])
def test_holdoff_outside_the_supported_range_is_rejected(seconds):
    scope, _ = make_scope()
    with pytest.raises(ScopeError):
        scope.set_holdoff(seconds)


@pytest.mark.parametrize("seconds, written", [
    # 100 ns is the instrument's own "Holdoff Reset" value, so it must be legal.
    (100e-9, ":TRIG:HOLD 0.0000001"), (500e-9, ":TRIG:HOLD 0.0000005"),
    (1e-3, ":TRIG:HOLD 0.001"), (1.5, ":TRIG:HOLD 1.5"),
])
def test_holdoff_inside_the_range_is_written(seconds, written):
    # Plain decimals only: the scope ignores ":TRIG:HOLD 5e-07" outright.
    scope, transport = make_scope()
    scope.set_holdoff(seconds)
    assert sent(transport, ":TRIG:HOLD") == [written]


def test_the_scopes_own_reset_default_round_trips():
    # Front panel "Holdoff Reset" gives 100 ns; a setup saved just after that
    # has to be restorable, which a 500 ns floor used to prevent.
    scope, transport = make_scope({":TRIG:MODE?": "EDGE"})
    warnings = scope.restore({"trigger": {"mode": "EDGE", "holdoff": 100e-9}})
    assert warnings == []
    assert ":TRIG:HOLD 0.0000001" in transport.written


def test_set_trigger_range_checks_holdoff_too():
    scope, _ = make_scope({":TRIG:MODE?": "EDGE"})
    with pytest.raises(ScopeError):
        scope.set_trigger(holdoff=60.0)


# --- trigger level helpers -------------------------------------------------

@pytest.mark.parametrize("source, expected", [
    ("CHAN1", 1), ("CH1", 1), ("CHAN2", 2), ("CH2", 2),
    ("EXT", None), ("ACLINE", None),
])
def test_trigger_channel_reads_through_the_reported_source(source, expected):
    scope, _ = make_scope({":TRIG:MODE?": "EDGE", ":TRIG:EDGE:SOUR?": source})
    assert scope.trigger_channel() == expected


def test_level_is_clamped_to_six_divisions():
    # 1 V/div * 6 divisions = +/-6 V; the scope ignores anything beyond that.
    scope, _ = make_scope({":TRIG:MODE?": "EDGE", ":TRIG:EDGE:SOUR?": "CHAN1",
                           ":CHAN1:SCAL?": "1"})
    assert scope.clamp_trigger_level(99.0) == 6.0
    assert scope.clamp_trigger_level(-99.0) == -6.0
    assert scope.clamp_trigger_level(2.5) == 2.5


def test_level_is_not_clamped_for_external_trigger():
    scope, _ = make_scope({":TRIG:MODE?": "EDGE", ":TRIG:EDGE:SOUR?": "EXT"})
    assert scope.clamp_trigger_level(99.0) == 99.0


def test_50_percent_takes_the_midpoint_of_the_source_channel():
    scope, transport = make_scope({
        ":TRIG:MODE?": "EDGE", ":TRIG:EDGE:SOUR?": "CHAN1",
        ":MEAS:VMAX? CHAN1": "3.0", ":MEAS:VMIN? CHAN1": "-1.0"})
    assert scope.trigger_level_50() == 1.0
    assert ":TRIG:EDGE:LEV 1" in transport.written


def test_50_percent_reports_when_there_is_no_measurable_signal():
    scope, _ = make_scope({
        ":TRIG:MODE?": "EDGE", ":TRIG:EDGE:SOUR?": "CHAN1",
        ":MEAS:VMAX? CHAN1": "99e36", ":MEAS:VMIN? CHAN1": "99e36"})
    with pytest.raises(ScopeError, match="No measurable signal"):
        scope.trigger_level_50()


def test_50_percent_needs_a_channel_source():
    scope, _ = make_scope({":TRIG:MODE?": "EDGE", ":TRIG:EDGE:SOUR?": "EXT"})
    with pytest.raises(ScopeError):
        scope.trigger_level_50()


# --- restore ---------------------------------------------------------------

def test_restore_collects_warnings_instead_of_aborting():
    # An out-of-range holdoff must not stop the timebase from being applied.
    scope, transport = make_scope({":TRIG:MODE?": "EDGE"})
    warnings = scope.restore({"timebase": 0.001,
                              "trigger": {"mode": "EDGE", "holdoff": 60.0}})
    assert any("trigger" in w for w in warnings)
    assert ":TIM:SCAL 0.001" in transport.written


def test_restore_of_an_empty_setup_touches_almost_nothing():
    scope, transport = make_scope({":TRIG:MODE?": "EDGE"})
    scope.restore({})
    assert sent(transport, ":CHAN") == []


# --- timed trigger conditions (PULSE / SLOPE) -------------------------------
# The SCPI spellings live in profile.TimedTriggerSpec and are unverified; these
# tests pin how commands are *composed* from the spec, so correcting a spelling
# there does not require rewriting the suite.

def timed_scope(mode="PULSE"):
    return make_scope({":TRIG:MODE?": mode})


@pytest.mark.parametrize("mode, subtree, leaf", [
    ("PULSE", "PULS", "WIDT"),
    ("SLOPE", "SLOP", "TIME"),
])
def test_condition_and_time_go_to_the_mode_own_subtree(mode, subtree, leaf):
    scope, transport = timed_scope(mode)
    spec = scope.profile.timed_triggers[mode]
    label = list(spec.conditions)[1]
    scope.set_trigger_condition(condition=label, seconds=1e-6)
    assert f":TRIG:{subtree}:{spec.condition_leaf} {spec.conditions[label]}" \
        in transport.written
    assert f":TRIG:{subtree}:{leaf} 0.000001" in transport.written


def test_condition_writes_nothing_when_given_nothing():
    scope, transport = timed_scope()
    scope.set_trigger_condition()
    assert sent(transport, ":TRIG:PULS:") == []


def test_only_the_supplied_half_is_written():
    scope, transport = timed_scope()
    scope.set_trigger_condition(seconds=1e-3)
    assert sent(transport, ":TRIG:PULS:MODE") == []
    assert sent(transport, ":TRIG:PULS:WIDT") == [":TRIG:PULS:WIDT 0.001"]


@pytest.mark.parametrize("mode", ["EDGE", "VIDEO", "PATTERN", "ALTERNATION"])
def test_modes_without_a_timed_condition_say_so(mode):
    scope, _ = timed_scope(mode)
    assert scope.has_timed_trigger() is False
    with pytest.raises(ScopeError, match="no width/time condition"):
        scope.set_trigger_condition(seconds=1e-6)


@pytest.mark.parametrize("mode", ["PULSE", "SLOPE"])
def test_the_timed_modes_are_recognised(mode):
    scope, _ = timed_scope(mode)
    assert scope.has_timed_trigger() is True


def test_a_condition_can_be_given_as_a_label_or_as_the_keyword():
    scope, transport = timed_scope()
    scope.set_trigger_condition(condition="+Width <")
    scope.set_trigger_condition(condition="+LESSthan")
    written = sent(transport, ":TRIG:PULS:MODE")
    assert written[0] == written[1]


def test_an_unknown_condition_is_rejected_rather_than_sent():
    scope, transport = timed_scope()
    with pytest.raises(ScopeError, match="Unknown trigger condition"):
        scope.set_trigger_condition(condition="sideways")
    assert sent(transport, ":TRIG:PULS:MODE") == []


@pytest.mark.parametrize("seconds", [1e-9, 19e-9, 11.0, 100.0])
def test_width_outside_the_documented_range_is_rejected(seconds):
    # User's Guide: pulse width range 20 ns ~ 10 s.
    scope, _ = timed_scope()
    with pytest.raises(ScopeError):
        scope.set_trigger_condition(seconds=seconds)


@pytest.mark.parametrize("seconds", [20e-9, 1e-6, 10.0])
def test_width_inside_the_documented_range_is_written(seconds):
    scope, transport = timed_scope()
    scope.set_trigger_condition(seconds=seconds)
    assert len(sent(transport, ":TRIG:PULS:WIDT")) == 1


def test_the_width_is_sent_as_a_plain_decimal():
    # 20 ns would otherwise render as 2e-08 and be silently ignored.
    scope, transport = timed_scope()
    scope.set_trigger_condition(seconds=20e-9)
    assert sent(transport, ":TRIG:PULS:WIDT") == [":TRIG:PULS:WIDT 0.00000002"]


def test_every_condition_label_survives_the_round_trip():
    scope, _ = timed_scope()
    spec = scope.profile.timed_triggers["PULSE"]
    assert len(spec.conditions) == 6      # (>, <, =) for each polarity
    for label, keyword in spec.conditions.items():
        assert spec.keyword_for(label) == keyword
        assert spec.keyword_for(keyword) == keyword


TIMED_SNAPSHOT = dict(SNAPSHOT_RESPONSES, **{
    ":TRIG:MODE?": "PULSE",
    ":TRIG:PULS:SOUR?": "CH1",
    ":TRIG:PULS:SLOP?": "POSITIVE",
    ":TRIG:PULS:SWE?": "AUTO",
    ":TRIG:PULS:LEV?": "1.2",
    ":TRIG:PULS:MODE?": "+LESSthan",
    ":TRIG:PULS:WIDT?": "1e-6",
})


def test_snapshot_picks_up_the_condition_in_pulse_mode():
    scope, _ = make_scope(TIMED_SNAPSHOT)
    settings = scope.snapshot((1, 2))
    assert settings.trigger_condition == "+LESSthan"
    assert settings.trigger_condition_time == 1e-6


def test_snapshot_leaves_the_condition_empty_in_edge_mode():
    scope, _ = make_scope(SNAPSHOT_RESPONSES)
    settings = scope.snapshot((1, 2))
    assert settings.trigger_condition is None
    assert settings.trigger_condition_time is None


def test_the_condition_survives_the_setup_file_round_trip():
    scope, _ = make_scope(TIMED_SNAPSHOT)
    settings = scope.snapshot((1, 2))
    assert ScopeSettings.from_dict(settings.to_dict()) == settings


def test_restore_applies_the_condition_after_the_mode():
    # Setting a width before the mode switch would land in the wrong subtree.
    scope, transport = make_scope({":TRIG:MODE?": "PULSE"})
    scope.restore({"trigger": {"mode": "PULSE", "condition": "+LESSthan",
                               "condition_time": 1e-6}})
    order = [c for c in transport.written
             if c.startswith((":TRIG:MODE ", ":TRIG:PULS:WIDT"))]
    assert order[0].startswith(":TRIG:MODE ")
    assert order[-1].startswith(":TRIG:PULS:WIDT")


def test_restore_of_an_edge_setup_does_not_attempt_a_condition():
    scope, transport = make_scope({":TRIG:MODE?": "EDGE"})
    warnings = scope.restore({"trigger": {"mode": "EDGE"}})
    assert warnings == []
    assert sent(transport, ":TRIG:PULS:") == []
