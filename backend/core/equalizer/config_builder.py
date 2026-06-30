# backend/core/equalizer/config_builder.py
"""Pure builders for CamillaDSP config fragments.

Single source of truth for the daemon dict shapes (EQ biquad, compressor
processor, loudness shelves). Both the live-apply paths (set_filter /
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


def loudness_filter_defs(loudness: Dict[str, Any]) -> Dict[str, Any]:
    """Build the low/high shelf filter definitions for loudness compensation.

    Returns a dict keyed by filter name, ready to merge into config["filters"].
    """
    return {
        "loudness_low": {
            "type": "Biquad",
            "parameters": {
                "type": "Lowshelf",
                "freq": 100,
                "gain": loudness["low_boost"],
                "slope": 6.0,
            },
        },
        "loudness_high": {
            "type": "Biquad",
            "parameters": {
                "type": "Highshelf",
                "freq": 8000,
                "gain": loudness["high_boost"],
                "slope": 6.0,
            },
        },
    }
