# backend/tests/test_equalizer_models.py
"""
Unit tests for Equalizer domain models.

Tests cover:
- EqFilter dataclass validation and serialization
- CompressorSettings dataclass validation and serialization
- LoudnessSettings dataclass validation and serialization
- EqualizerSettings model with typed sub-models
- Backward compatibility with existing settings.json format
"""
from backend.core.multiroom.models import (
    EqFilter,
    CompressorSettings,
    LoudnessSettings,
    EqualizerSettings,
    FilterType,
    DEFAULT_EQ_FREQUENCIES,
)


# =============================================================================
# EqFilter Tests
# =============================================================================

class TestEqFilter:
    """Test EqFilter dataclass"""

    def test_create_with_defaults(self):
        """Should create filter with default values"""
        f = EqFilter(id="eq_band_00", frequency=1000)
        assert f.id == "eq_band_00"
        assert f.frequency == 1000
        assert f.gain == 0.0
        assert f.q == 1.41
        assert f.filter_type == FilterType.PEAKING
        assert f.enabled is True

    def test_create_with_all_values(self):
        """Should create filter with all custom values"""
        f = EqFilter(
            id="eq_band_05",
            frequency=2000,
            gain=5.0,
            q=2.0,
            filter_type=FilterType.HIGHSHELF,
            enabled=False
        )
        assert f.id == "eq_band_05"
        assert f.frequency == 2000
        assert f.gain == 5.0
        assert f.q == 2.0
        assert f.filter_type == FilterType.HIGHSHELF
        assert f.enabled is False

    def test_to_dict(self):
        """Should serialize to dictionary"""
        f = EqFilter(id="eq_band_00", frequency=100, gain=3.0, q=1.5)
        d = f.to_dict()
        assert d == {
            "id": "eq_band_00",
            "frequency": 100,
            "gain": 3.0,
            "q": 1.5,
            "filter_type": "Peaking",
            "enabled": True
        }

    def test_to_wire_dict(self):
        """Should serialize to the frontend/WS wire shape (freq/type, NOT frequency/filter_type)."""
        f = EqFilter(id="eq_band_00", frequency=100, gain=3.0, q=1.5)
        d = f.to_wire_dict()
        assert d == {
            "id": "eq_band_00",
            "freq": 100,
            "gain": 3.0,
            "q": 1.5,
            "type": "Peaking",
            "enabled": True
        }

    def test_from_dict(self):
        """Should deserialize from dictionary"""
        data = {
            "id": "eq_band_01",
            "frequency": 500,
            "gain": -2.0,
            "q": 0.7,
            "filter_type": "Lowshelf",
            "enabled": False
        }
        f = EqFilter.from_dict(data)
        assert f.id == "eq_band_01"
        assert f.frequency == 500
        assert f.gain == -2.0
        assert f.q == 0.7
        assert f.filter_type == FilterType.LOWSHELF
        assert f.enabled is False

    def test_from_dict_with_defaults(self):
        """Should use defaults for missing keys"""
        data = {"id": "eq_band_00", "frequency": 1000}
        f = EqFilter.from_dict(data)
        assert f.gain == 0.0
        assert f.q == 1.41
        assert f.filter_type == FilterType.PEAKING
        assert f.enabled is True

    def test_roundtrip_serialization(self):
        """Should preserve values through to_dict/from_dict cycle"""
        original = EqFilter(
            id="eq_band_09",
            frequency=16000,
            gain=-5.5,
            q=0.5,
            filter_type=FilterType.HIGHSHELF,
            enabled=True
        )
        restored = EqFilter.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.frequency == original.frequency
        assert restored.gain == original.gain
        assert restored.q == original.q
        assert restored.filter_type == original.filter_type
        assert restored.enabled == original.enabled

    # Validation boundary tests
    def test_frequency_at_lower_bound(self):
        """Should accept frequency at lower bound (20 Hz)"""
        f = EqFilter(id="test", frequency=20)
        assert f.frequency == 20

    def test_frequency_at_upper_bound(self):
        """Should accept frequency at upper bound (20000 Hz)"""
        f = EqFilter(id="test", frequency=20000)
        assert f.frequency == 20000

    def test_gain_at_lower_bound(self):
        """Should accept gain at lower bound (-15 dB)"""
        f = EqFilter(id="test", frequency=1000, gain=-15.0)
        assert f.gain == -15.0

    def test_gain_at_upper_bound(self):
        """Should accept gain at upper bound (+15 dB)"""
        f = EqFilter(id="test", frequency=1000, gain=15.0)
        assert f.gain == 15.0

    def test_q_at_lower_bound(self):
        """Should accept Q at lower bound (0.1)"""
        f = EqFilter(id="test", frequency=1000, q=0.1)
        assert f.q == 0.1

    def test_q_at_upper_bound(self):
        """Should accept Q at upper bound (10.0)"""
        f = EqFilter(id="test", frequency=1000, q=10.0)
        assert f.q == 10.0

    def test_all_filter_types(self):
        """Should accept all valid filter types"""
        for ft in FilterType:
            f = EqFilter(id="test", frequency=1000, filter_type=ft)
            assert f.filter_type == ft


# =============================================================================
# CompressorSettings Tests
# =============================================================================

class TestCompressorSettings:
    """Test CompressorSettings dataclass"""

    def test_create_with_defaults(self):
        """Should create with sensible defaults matching CamillaDSPService"""
        c = CompressorSettings()
        assert c.enabled is False
        assert c.threshold == -20.0
        assert c.ratio == 4.0
        assert c.attack == 10.0
        assert c.release == 100.0
        assert c.makeup_gain == 0.0

    def test_create_with_custom_values(self):
        """Should create with custom values"""
        c = CompressorSettings(
            enabled=True,
            threshold=-30.0,
            ratio=8.0,
            attack=5.0,
            release=200.0,
            makeup_gain=6.0
        )
        assert c.enabled is True
        assert c.threshold == -30.0
        assert c.ratio == 8.0
        assert c.attack == 5.0
        assert c.release == 200.0
        assert c.makeup_gain == 6.0

    def test_to_dict(self):
        """Should serialize to dictionary"""
        c = CompressorSettings(enabled=True, threshold=-25.0)
        d = c.to_dict()
        assert d == {
            "enabled": True,
            "threshold": -25.0,
            "ratio": 4.0,
            "attack": 10.0,
            "release": 100.0,
            "makeup_gain": 0.0
        }

    def test_from_dict(self):
        """Should deserialize from dictionary"""
        data = {
            "enabled": True,
            "threshold": -15.0,
            "ratio": 6.0,
            "attack": 20.0,
            "release": 150.0,
            "makeup_gain": 3.0
        }
        c = CompressorSettings.from_dict(data)
        assert c.enabled is True
        assert c.threshold == -15.0
        assert c.ratio == 6.0
        assert c.attack == 20.0
        assert c.release == 150.0
        assert c.makeup_gain == 3.0

    def test_from_dict_with_defaults(self):
        """Should use defaults for missing keys"""
        data = {"enabled": True}
        c = CompressorSettings.from_dict(data)
        assert c.enabled is True
        assert c.threshold == -20.0
        assert c.ratio == 4.0

    def test_from_dict_none(self):
        """Should return default instance for None input"""
        c = CompressorSettings.from_dict(None)
        assert c.enabled is False
        assert c.threshold == -20.0

    def test_roundtrip_serialization(self):
        """Should preserve values through to_dict/from_dict cycle"""
        original = CompressorSettings(
            enabled=True,
            threshold=-40.0,
            ratio=12.0,
            attack=50.0,
            release=500.0,
            makeup_gain=10.0
        )
        restored = CompressorSettings.from_dict(original.to_dict())
        assert restored.enabled == original.enabled
        assert restored.threshold == original.threshold
        assert restored.ratio == original.ratio
        assert restored.attack == original.attack
        assert restored.release == original.release
        assert restored.makeup_gain == original.makeup_gain

    # Validation boundary tests
    def test_threshold_at_lower_bound(self):
        """Should accept threshold at lower bound (-60 dB)"""
        c = CompressorSettings(threshold=-60.0)
        assert c.threshold == -60.0

    def test_threshold_at_upper_bound(self):
        """Should accept threshold at upper bound (0 dB)"""
        c = CompressorSettings(threshold=0.0)
        assert c.threshold == 0.0

    def test_ratio_at_lower_bound(self):
        """Should accept ratio at lower bound (1)"""
        c = CompressorSettings(ratio=1.0)
        assert c.ratio == 1.0

    def test_ratio_at_upper_bound(self):
        """Should accept ratio at upper bound (20)"""
        c = CompressorSettings(ratio=20.0)
        assert c.ratio == 20.0

    def test_attack_at_lower_bound(self):
        """Should accept attack at lower bound (0.1 ms)"""
        c = CompressorSettings(attack=0.1)
        assert c.attack == 0.1

    def test_attack_at_upper_bound(self):
        """Should accept attack at upper bound (100 ms)"""
        c = CompressorSettings(attack=100.0)
        assert c.attack == 100.0

    def test_release_at_lower_bound(self):
        """Should accept release at lower bound (10 ms)"""
        c = CompressorSettings(release=10.0)
        assert c.release == 10.0

    def test_release_at_upper_bound(self):
        """Should accept release at upper bound (1000 ms)"""
        c = CompressorSettings(release=1000.0)
        assert c.release == 1000.0


# =============================================================================
# LoudnessSettings Tests
# =============================================================================

class TestLoudnessSettings:
    """Test LoudnessSettings dataclass"""

    def test_create_with_defaults(self):
        """Should create with sensible defaults matching CamillaDSPService"""
        ln = LoudnessSettings()
        assert ln.enabled is False
        assert ln.high_boost == 5.0
        assert ln.low_boost == 8.0

    def test_create_with_custom_values(self):
        """Should create with custom values"""
        ln = LoudnessSettings(
            enabled=True,
            high_boost=3.0,
            low_boost=6.0
        )
        assert ln.enabled is True
        assert ln.high_boost == 3.0
        assert ln.low_boost == 6.0

    def test_to_dict(self):
        """Should serialize to dictionary"""
        ln = LoudnessSettings(enabled=True, high_boost=7.5)
        d = ln.to_dict()
        assert d == {
            "enabled": True,
            "high_boost": 7.5,
            "low_boost": 8.0
        }

    def test_from_dict(self):
        """Should deserialize from dictionary"""
        data = {
            "enabled": True,
            "high_boost": 10.0,
            "low_boost": 12.0
        }
        ln = LoudnessSettings.from_dict(data)
        assert ln.enabled is True
        assert ln.high_boost == 10.0
        assert ln.low_boost == 12.0

    def test_from_dict_with_defaults(self):
        """Should use defaults for missing keys"""
        data = {"enabled": True}
        ln = LoudnessSettings.from_dict(data)
        assert ln.enabled is True
        assert ln.high_boost == 5.0

    def test_from_dict_none(self):
        """Should return default instance for None input"""
        ln = LoudnessSettings.from_dict(None)
        assert ln.enabled is False
        assert ln.high_boost == 5.0

    def test_roundtrip_serialization(self):
        """Should preserve values through to_dict/from_dict cycle"""
        original = LoudnessSettings(
            enabled=True,
            high_boost=7.5,
            low_boost=10.0
        )
        restored = LoudnessSettings.from_dict(original.to_dict())
        assert restored.enabled == original.enabled
        assert restored.high_boost == original.high_boost
        assert restored.low_boost == original.low_boost

    def test_high_boost_at_lower_bound(self):
        """Should accept high_boost at lower bound (0 dB)"""
        ln = LoudnessSettings(high_boost=0.0)
        assert ln.high_boost == 0.0

    def test_high_boost_at_upper_bound(self):
        """Should accept high_boost at upper bound (15 dB)"""
        ln = LoudnessSettings(high_boost=15.0)
        assert ln.high_boost == 15.0

    def test_low_boost_at_lower_bound(self):
        """Should accept low_boost at lower bound (0 dB)"""
        ln = LoudnessSettings(low_boost=0.0)
        assert ln.low_boost == 0.0

    def test_low_boost_at_upper_bound(self):
        """Should accept low_boost at upper bound (15 dB)"""
        ln = LoudnessSettings(low_boost=15.0)
        assert ln.low_boost == 15.0


# =============================================================================
# EqualizerSettings Tests
# =============================================================================

class TestEqualizerSettings:
    """Test EqualizerSettings model with typed sub-models"""

    def test_create_with_defaults(self):
        """Should create with default flat configuration"""
        eq = EqualizerSettings()
        assert eq.enabled is True
        assert eq.filters == []
        assert isinstance(eq.compressor, CompressorSettings)
        assert eq.compressor.enabled is False
        assert isinstance(eq.loudness, LoudnessSettings)
        assert eq.loudness.enabled is False

    def test_create_with_custom_values(self):
        """Should create with custom values"""
        filters = [EqFilter(id="eq_band_00", frequency=100, gain=3.0)]
        compressor = CompressorSettings(enabled=True, threshold=-25.0)
        loudness = LoudnessSettings(enabled=True, high_boost=7.5)

        eq = EqualizerSettings(
            enabled=False,
            filters=filters,
            compressor=compressor,
            loudness=loudness
        )
        assert eq.enabled is False
        assert len(eq.filters) == 1
        assert eq.filters[0].gain == 3.0
        assert eq.compressor.enabled is True
        assert eq.loudness.enabled is True

    def test_to_dict(self):
        """Should serialize to dictionary"""
        eq = EqualizerSettings(
            enabled=True,
            filters=[EqFilter(id="eq_band_00", frequency=500, gain=2.0)],
            compressor=CompressorSettings(enabled=True),
            loudness=LoudnessSettings(enabled=False)
        )
        d = eq.to_dict()
        assert d["enabled"] is True
        assert len(d["filters"]) == 1
        assert d["filters"][0]["frequency"] == 500
        assert d["compressor"]["enabled"] is True
        assert d["loudness"]["enabled"] is False

    def test_to_wire_dict(self):
        """Should serialize like to_dict() but with filters in the wire shape (freq/type)."""
        eq = EqualizerSettings(
            enabled=True,
            filters=[EqFilter(id="eq_band_00", frequency=500, gain=2.0)],
            compressor=CompressorSettings(enabled=True),
            loudness=LoudnessSettings(enabled=False),
            active_preset="rock",
            mono=True,
            custom_gains=[1.0] * 10,
        )
        d = eq.to_wire_dict()
        # Same scalar keys as to_dict (persistence shape) ...
        assert d["enabled"] is True
        assert d["active_preset"] == "rock"
        assert d["mono"] is True
        assert d["custom_gains"] == [1.0] * 10
        assert d["compressor"]["enabled"] is True
        assert d["loudness"]["enabled"] is False
        # ... but filters use freq/type, NOT frequency/filter_type.
        assert d["filters"][0] == {
            "id": "eq_band_00", "freq": 500, "gain": 2.0, "q": 1.41,
            "type": "Peaking", "enabled": True,
        }

    def test_from_dict(self):
        """Should deserialize from dictionary"""
        data = {
            "enabled": False,
            "filters": [
                {"id": "eq_band_00", "frequency": 100, "gain": -3.0, "q": 1.0, "filter_type": "Peaking", "enabled": True}
            ],
            "compressor": {"enabled": True, "threshold": -30.0, "ratio": 6.0, "attack": 15.0, "release": 150.0, "makeup_gain": 2.0},
            "loudness": {"enabled": True, "high_boost": 4.0, "low_boost": 7.0}
        }
        eq = EqualizerSettings.from_dict(data)
        assert eq.enabled is False
        assert len(eq.filters) == 1
        assert eq.filters[0].gain == -3.0
        assert eq.compressor.enabled is True
        assert eq.compressor.threshold == -30.0
        assert eq.loudness.enabled is True
        assert eq.loudness.high_boost == 4.0

    def test_from_dict_none(self):
        """Should return default instance for None input"""
        eq = EqualizerSettings.from_dict(None)
        assert eq.enabled is True
        assert eq.filters == []

    def test_from_dict_empty(self):
        """Should return default instance for empty dict"""
        eq = EqualizerSettings.from_dict({})
        assert eq.enabled is True
        assert eq.filters == []

    def test_roundtrip_serialization(self):
        """Should preserve values through to_dict/from_dict cycle"""
        original = EqualizerSettings(
            enabled=False,
            filters=[
                EqFilter(id="eq_band_00", frequency=63, gain=4.0),
                EqFilter(id="eq_band_01", frequency=250, gain=-2.0)
            ],
            compressor=CompressorSettings(enabled=True, threshold=-35.0),
            loudness=LoudnessSettings(enabled=True, low_boost=10.0)
        )
        restored = EqualizerSettings.from_dict(original.to_dict())
        assert restored.enabled == original.enabled
        assert len(restored.filters) == len(original.filters)
        assert restored.filters[0].frequency == original.filters[0].frequency
        assert restored.compressor.threshold == original.compressor.threshold
        assert restored.loudness.low_boost == original.loudness.low_boost

    def test_default_factory_method(self):
        """Should create flat configuration via default() method"""
        eq = EqualizerSettings.default()
        assert eq.enabled is True
        assert len(eq.filters) == 10  # 10-band parametric EQ
        # All filters should have 0 dB gain (flat)
        for f in eq.filters:
            assert f.gain == 0.0
        assert eq.compressor.enabled is False
        assert eq.loudness.enabled is False

    def test_default_eq_frequencies(self):
        """Default EQ should use standard 10-band frequencies"""
        eq = EqualizerSettings.default()
        expected_freqs = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
        actual_freqs = [f.frequency for f in eq.filters]
        assert actual_freqs == expected_freqs

    def test_none_compressor_loudness(self):
        """Should default compressor/loudness when payload sets them to None."""
        data = {
            "filters": [],
            "compressor": None,
            "loudness": None
        }
        eq = EqualizerSettings.from_dict(data)
        assert eq.compressor.enabled is False
        assert eq.loudness.enabled is False


# =============================================================================
# FilterType Tests
# =============================================================================

class TestFilterTypeEnum:
    """Test FilterType enum in models"""

    def test_all_filter_types_defined(self):
        """Should have all required filter types"""
        assert FilterType.PEAKING.value == "Peaking"
        assert FilterType.LOWSHELF.value == "Lowshelf"
        assert FilterType.HIGHSHELF.value == "Highshelf"
        assert FilterType.LOWPASS.value == "Lowpass"
        assert FilterType.HIGHPASS.value == "Highpass"
        assert FilterType.NOTCH.value == "Notch"
        assert FilterType.ALLPASS.value == "Allpass"

    def test_string_conversion(self):
        """Should convert to/from string"""
        assert FilterType("Peaking") == FilterType.PEAKING
        assert str(FilterType.LOWSHELF) == "FilterType.LOWSHELF"


# =============================================================================
# Constants Tests
# =============================================================================

class TestDspConstants:
    """Test Equalizer-related constants"""

    def test_default_eq_frequencies(self):
        """Should have standard 10-band EQ frequencies"""
        assert DEFAULT_EQ_FREQUENCIES == [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
        assert len(DEFAULT_EQ_FREQUENCIES) == 10
