import pytest

from activity_probe import ActivityProbe, ProbeError, counters_disabled
from pm_sampling import PMSampling


@pytest.mark.parametrize("value,expected", [("off", True), ("0", True), ("OFF ", True), ("on", False), ("", False)])
def test_counters_disabled_values(monkeypatch, value, expected):
    monkeypatch.setenv("GPU_INFO_COUNTERS", value)
    assert counters_disabled() is expected


def test_counters_default_on(monkeypatch):
    monkeypatch.delenv("GPU_INFO_COUNTERS", raising=False)
    assert counters_disabled() is False


def test_probes_refuse_when_disabled(monkeypatch):
    monkeypatch.setenv("GPU_INFO_COUNTERS", "off")
    with pytest.raises(ProbeError, match="GPU_INFO_COUNTERS"):
        ActivityProbe().capture(0)
    with pytest.raises(ProbeError, match="GPU_INFO_COUNTERS"):
        PMSampling.start(object(), 0, "0000:01:00.0")
