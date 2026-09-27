# backend/core/equalizer/config_builder.py
"""Pure builders for CamillaDSP config fragments.

Single source of truth for the daemon dict shapes (EQ biquad, compressor
processor, native loudness, EQ headroom). Both the live-apply paths (set_filter /
_apply_compressor_config / _apply_loudness_config) and restore_effects build
these via the functions here, so the ms->s / ratio->factor mapping can never
drift between the two. Stateless and side-effect free.
"""
from typing import Any, Dict


def eq_filter_def(freq: float, gain: float, q: float, filter_type: str = "Peaking") -> Dict[str, Any]:
    """Build a Biquad EQ filter definition."""
    return {
        "type": "Biquad",
        "parameters": {"type": filter_type, "freq": freq, "gain": gain, "q": q},
    }


def compressor_processor_def(compressor: Dict[str, Any]) -> Dict[str, Any]:
    """Build a Compressor processor definition from the cached settings dict.

    Maps Milo's UI units to CamillaDSP's: ratio->factor, attack/release ms->s.
    """
    return {
        "type": "Compressor",
        "parameters": {
            "channels": 2,
            "threshold": compressor["threshold"],
            "factor": compressor["ratio"],
            "attack": compressor["attack"] / 1000.0,  # ms to s
            "release": compressor["release"] / 1000.0,
            "makeup_gain": compressor["makeup_gain"],
        },
    }


def loudness_filter_def(loudness: Dict[str, Any], reference_level: float) -> Dict[str, Any]:
    """Build CamillaDSP's native Loudness filter, following the main fader.

    It boosts below 70 Hz and above 3.5 kHz by an amount that grows as the
    volume falls: none at `reference_level` and above, the full `low_boost` /
    `high_boost` 20 dB below it, linear in between. The two static shelves it
    replaces boosted the same at every level, loud included. Its boost can never
    push the output past `reference_level` (the boost rises at most 1 dB per dB
    of fader), so it needs no headroom of its own.
    """
    return {
        "type": "Loudness",
        "parameters": {
            "fader": "Main",
            "reference_level": reference_level,
            "high_boost": loudness["high_boost"],
            "low_boost": loudness["low_boost"],
            "attenuate_mid": False,
        },
    }


def headroom_filter_def(gain_db: float) -> Dict[str, Any]:
    """Build the Gain stage that keeps the EQ curve's peak at 0 dB (see eq_response)."""
    return {"type": "Gain", "parameters": {"gain": gain_db, "inverted": False, "mute": False}}
