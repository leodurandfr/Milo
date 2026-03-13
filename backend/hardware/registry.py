# backend/hardware/registry.py
"""
Hardware registry — single source of truth for supported audio cards and screens.

Used by HardwareService (validation, config resolution), API routes (dropdown options),
and install.sh (via save_hardware_config).
"""

# =============================================================================
# AUDIO CARDS
# =============================================================================
# Each entry maps an ID to its hardware properties:
# - label: Human-readable name for the UI dropdown
# - overlay: dtoverlay value for /boot/firmware/config.txt
# - card_name: ALSA card name (all HiFiBerry HATs share "sndrpihifiberry")
# - alsa_control: ALSA mixer control name ("Digital" or "DAC")
# - brand: Manufacturer (for future grouping in the UI)

AUDIO_CARDS = {
    "none": {
        "label": "No audio card",
        "overlay": None,
        "card_name": None,
        "alsa_control": None,
        "brand": None,
        "category": None,
    },
    "hifiberry_amp2": {
        "label": "HiFiBerry Amp2",
        "overlay": "hifiberry-dacplus-std",
        "card_name": "sndrpihifiberry",
        "alsa_control": "Digital",
        "brand": "HiFiBerry",
        "category": "amplifier",
    },
    "hifiberry_amp4": {
        "label": "HiFiBerry Amp4",
        "overlay": "hifiberry-dacplus-std",
        "card_name": "sndrpihifiberry",
        "alsa_control": "Digital",
        "brand": "HiFiBerry",
        "category": "amplifier",
    },
    "hifiberry_amp4pro": {
        "label": "HiFiBerry Amp4 Pro",
        "overlay": "hifiberry-amp4pro",
        "card_name": "sndrpihifiberry",
        "alsa_control": "Digital",
        "brand": "HiFiBerry",
        "category": "amplifier",
    },
    "hifiberry_amp100": {
        "label": "HiFiBerry Amp100",
        "overlay": "hifiberry-amp100",
        "card_name": "sndrpihifiberry",
        "alsa_control": "Digital",
        "brand": "HiFiBerry",
        "category": "amplifier",
    },
    "hifiberry_beocreate": {
        "label": "HiFiBerry Beocreate 4CA",
        "overlay": "hifiberry-dac",
        "card_name": "sndrpihifiberry",
        "alsa_control": "DAC",
        "brand": "HiFiBerry",
        "category": "speaker",
    },
    "hifiberry_dac2hd": {
        "label": "HiFiBerry DAC2 HD",
        "overlay": "hifiberry-dacplushd",
        "card_name": "sndrpihifiberry",
        "alsa_control": "DAC",
        "brand": "HiFiBerry",
        "category": "dac",
    },
    "hifiberry_dacplus_pro": {
        "label": "HiFiBerry DAC+ Pro",
        "overlay": "hifiberry-dacplus",
        "card_name": "sndrpihifiberry",
        "alsa_control": "Digital",
        "brand": "HiFiBerry",
        "category": "dac",
    },
}

# =============================================================================
# SCREENS
# =============================================================================
# Each entry maps a screen type ID to its properties:
# - label: Human-readable name for the UI dropdown
# - resolution: "WIDTHxHEIGHT" string (None for "none")

SCREENS = {
    "waveshare_7_usb": {
        "label": "Waveshare 7\" 1024x600 (USB)",
        "resolution": "1024x600",
    },
    "waveshare_8_dsi": {
        "label": "Waveshare 8\" 1280x800 (DSI)",
        "resolution": "1280x800",
    },
    "none": {
        "label": "No screen",
        "resolution": None,
    },
}

# =============================================================================
# DEFAULT GPIO PINS (rotary encoder)
# =============================================================================
DEFAULT_ROTARY_PINS = {
    "clk_pin": 22,
    "dt_pin": 27,
    "sw_pin": 23,
}
