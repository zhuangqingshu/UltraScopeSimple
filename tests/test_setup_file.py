"""Setup files are a published format: saved files must keep loading."""

import json

import pytest

from ultrascope.scope import ChannelSettings, ScopeSettings
from ultrascope.setup_file import load_setup, save_setup


def sample() -> ScopeSettings:
    return ScopeSettings(
        timebase=1e-3, acquire_type="NORMAL", average=16, memory_depth="LONG",
        trigger_mode="EDGE", time_offset=2e-4, idn="RIGOL,DS1102E,TEST",
        channels={1: ChannelSettings(True, 1.0, "DC", probe=10.0, volt_offset=0.25),
                  2: ChannelSettings(False, 0.5, "AC", probe=1.0, volt_offset=-0.5)},
        trigger_source="CH1", trigger_slope="POSITIVE", trigger_sweep="AUTO",
        trigger_level=1.5, trigger_holdoff=5e-7)


def test_round_trip_through_a_file(tmp_path):
    path = tmp_path / "setup.json"
    save_setup(str(path), sample())
    assert load_setup(str(path)) == sample()


def test_saved_file_is_readable_json(tmp_path):
    path = tmp_path / "setup.json"
    save_setup(str(path), sample())
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["channels"]["1"]["probe"] == 10.0
    assert state["trigger"]["holdoff"] == 5e-7
    assert state["idn"] == "RIGOL,DS1102E,TEST"


def test_a_setup_missing_optional_keys_still_loads(tmp_path):
    # Files written before a field existed must not break the loader.
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"timebase": 1e-3,
                                "trigger": {"mode": "EDGE"},
                                "channels": {"1": {"on": True, "scale": 1.0}}}),
                    encoding="utf-8")
    settings = load_setup(str(path))
    assert settings.timebase == 1e-3
    assert settings.channels[1].probe is None
    assert settings.trigger_holdoff is None


def test_unreadable_file_raises_rather_than_returning_junk(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_setup(str(path))
