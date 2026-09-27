# backend/core/equalizer/eq_response.py
"""The magnitude response of an EQ curve, and the headroom it needs.

A band that boosts is a gain stage: at the maximum volume, a +6 dB band at
16 kHz plays a full-scale signal at +6 dBFS and the DAC clips. Measured on
Bureau: bands 1k +2 / 2k +3 / 4k +4 / 8k +5 / 16k +6 at Q 1.41 overlap to a
+6.41 dB peak at 15.9 kHz — more than the largest band, which is why the peak is
computed from the real curve and not read off the band gains.

The coefficients are the Audio EQ Cookbook's (RBJ), which is what CamillaDSP's
Biquad filters implement. Pure: no I/O, no state.
"""
import cmath
import math
from typing import Iterable, Tuple

# The sample rate both CamillaDSP configs run at (rootfs/var/lib/milo/camilladsp/
# config.yml and milo-client/configs/camilladsp/config.yml, held equal to this
# by tests/test_eq_response.py). The response near 16 kHz depends on it.
SAMPLERATE = 48000

# 1/48 octave from 20 Hz up to just below Nyquist: finer than any band a user
# can draw, and past 20 kHz because a 48 kHz stream carries content up to 24 kHz
# and a treble shelf keeps boosting it — inaudible, but it clips all the same.
_GRID = [20.0 * 2 ** (step / 48) for step in range(int(48 * math.log2(0.999 * SAMPLERATE / 2 / 20)) + 1)]

Band = Tuple[str, float, float, float]  # (filter type, freq Hz, gain dB, q)


def _coefficients(filter_type: str, freq: float, gain: float, q: float, samplerate: int):
    """(b0, b1, b2, a0, a1, a2) of one RBJ biquad."""
    a = 10 ** (gain / 40)
    w0 = 2 * math.pi * freq / samplerate
    cos_w0, alpha = math.cos(w0), math.sin(w0) / (2 * q)
    if filter_type == "Peaking":
        return 1 + alpha * a, -2 * cos_w0, 1 - alpha * a, 1 + alpha / a, -2 * cos_w0, 1 - alpha / a
    shelf = 2 * math.sqrt(a) * alpha
    if filter_type == "Lowshelf":
        return (a * ((a + 1) - (a - 1) * cos_w0 + shelf), 2 * a * ((a - 1) - (a + 1) * cos_w0),
                a * ((a + 1) - (a - 1) * cos_w0 - shelf), (a + 1) + (a - 1) * cos_w0 + shelf,
                -2 * ((a - 1) + (a + 1) * cos_w0), (a + 1) + (a - 1) * cos_w0 - shelf)
    if filter_type == "Highshelf":
        return (a * ((a + 1) + (a - 1) * cos_w0 + shelf), -2 * a * ((a - 1) + (a + 1) * cos_w0),
                a * ((a + 1) + (a - 1) * cos_w0 - shelf), (a + 1) - (a - 1) * cos_w0 + shelf,
                2 * ((a - 1) - (a + 1) * cos_w0), (a + 1) - (a - 1) * cos_w0 - shelf)
    denominator = (1 + alpha, -2 * cos_w0, 1 - alpha)
    numerators = {
        "Lowpass": ((1 - cos_w0) / 2, 1 - cos_w0, (1 - cos_w0) / 2),
        "Highpass": ((1 + cos_w0) / 2, -(1 + cos_w0), (1 + cos_w0) / 2),
        "Notch": (1, -2 * cos_w0, 1),
        "Allpass": (1 - alpha, -2 * cos_w0, 1 + alpha),
    }
    return (*numerators[filter_type], *denominator)


# A notch is exactly zero at its centre, which is on the grid; its log would
# raise. Any floor far below audibility does, since only the peak is kept.
_FLOOR = 1e-12


def _magnitude(coefficients, z: complex) -> float:
    b0, b1, b2, a0, a1, a2 = coefficients
    return max(abs((b0 + b1 * z + b2 * z * z) / (a0 + a1 * z + a2 * z * z)), _FLOOR)


def band_db(band: Band, freq: float, samplerate: int = SAMPLERATE) -> float:
    """Gain in dB of one band at `freq`."""
    z = cmath.exp(-2j * math.pi * freq / samplerate)
    return 20 * math.log10(_magnitude(_coefficients(*band, samplerate), z))


def eq_peak_db(bands: Iterable[Band], samplerate: int = SAMPLERATE) -> float:
    """The highest gain of the cascaded bands, in dB, from 20 Hz to Nyquist."""
    bands = [band for band in bands if band[2] != 0 or band[0] not in ("Peaking", "Lowshelf", "Highshelf")]
    if not bands:
        return 0.0
    # Coefficients once per band, not once per band and frequency: a band drag
    # recomputes this on every step, on the event loop.
    coefficients = [_coefficients(*band, samplerate) for band in bands]
    freqs = sorted(set(_GRID) | {band[1] for band in bands if 20.0 <= band[1] < samplerate / 2})
    peak = 0.0
    for freq in freqs:
        z = cmath.exp(-2j * math.pi * freq / samplerate)
        gain = 1.0
        for band in coefficients:
            gain *= _magnitude(band, z)
        peak = max(peak, gain)
    return 20 * math.log10(peak)


def headroom_db(bands: Iterable[Band], samplerate: int = SAMPLERATE) -> float:
    """The attenuation that keeps the curve's peak at 0 dB: never positive.

    Rounded up to the next 0.1 dB of attenuation, so the rounding never lets a
    peak through. A curve that only cuts needs none.
    """
    peak = round(eq_peak_db(bands, samplerate), 6)
    if peak <= 0:
        return 0.0
    return -math.ceil(peak * 10) / 10


def record_headroom_db(filters) -> float:
    """`headroom_db` of an EqualizerSettings' filters — what a satellite is sent
    with its bands, so the one computation lives here and not in two trees."""
    return headroom_db((f.filter_type.value, f.frequency, f.gain, f.q) for f in filters)
