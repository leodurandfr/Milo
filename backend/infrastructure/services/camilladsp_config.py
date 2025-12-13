# backend/infrastructure/services/camilladsp_config.py
"""
CamillaDSP YAML configuration generator for Milo
Generates dynamic configurations based on current settings
"""
import os
import logging
from typing import Dict, List, Any, Optional


class CamillaDSPConfigGenerator:
    """
    Generates CamillaDSP YAML configuration files

    Supports:
    - 10+ parametric EQ bands (Biquad filters)
    - Dynamic sample rate configuration
    - ALSA loopback input/output routing
    """

    DEFAULT_SAMPLE_RATE = 48000
    DEFAULT_CHANNELS = 2
    DEFAULT_BUFFER_SIZE = 1024
    DEFAULT_CHUNKSIZE = 1024

    # Default parametric EQ frequencies (10 bands)
    DEFAULT_EQ_FREQUENCIES = [
        31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000
    ]

    def __init__(self, config_path: str = "/var/lib/milo/camilladsp"):
        self.logger = logging.getLogger(__name__)
        self.config_path = config_path
        self.config_file = os.path.join(config_path, "config.yml")

        # Ensure config directory exists
        os.makedirs(config_path, exist_ok=True)

    def generate_default_config(
        self,
        sample_rate: int = None,
        capture_device: str = "hw:Loopback,1,0",
        playback_device: str = "hw:sndrpihifiberry,0,0"
    ) -> Dict[str, Any]:
        """
        Generate a default CamillaDSP configuration

        Args:
            sample_rate: Audio sample rate (default: 48000)
            capture_device: ALSA capture device (loopback input)
            playback_device: ALSA playback device (HiFiBerry output)

        Returns:
            Complete CamillaDSP configuration dictionary
        """
        sample_rate = sample_rate or self.DEFAULT_SAMPLE_RATE

        config = {
            # Device configuration
            "devices": {
                "samplerate": sample_rate,
                "chunksize": self.DEFAULT_CHUNKSIZE,
                "queuelimit": 4,
                "capture": {
                    "type": "Alsa",
                    "channels": self.DEFAULT_CHANNELS,
                    "device": capture_device,
                    "format": "S32LE"
                },
                "playback": {
                    "type": "Alsa",
                    "channels": self.DEFAULT_CHANNELS,
                    "device": playback_device,
                    "format": "S32LE"
                }
            },

            # Filters (10-band parametric EQ)
            "filters": self._generate_default_filters(sample_rate),

            # Mixers (pass-through for now)
            "mixers": {},

            # Pipeline
            "pipeline": self._generate_pipeline()
        }

        return config

    def _generate_default_filters(self, sample_rate: int) -> Dict[str, Any]:
        """Generate default 10-band parametric EQ filters"""
        filters = {}

        for i, freq in enumerate(self.DEFAULT_EQ_FREQUENCIES):
            band_id = f"eq_band_{i:02d}"

            # Calculate appropriate Q for EQ band spacing
            # Q = f / bandwidth, for 1 octave spacing Q ~= 1.41
            q = 1.41

            filters[band_id] = {
                "type": "Biquad",
                "parameters": {
                    "type": "Peaking",
                    "freq": freq,
                    "gain": 0.0,  # Flat by default
                    "q": q
                }
            }

        return filters

    def _generate_pipeline(self) -> List[Dict[str, Any]]:
        """Generate the filter pipeline"""
        pipeline = []

        # Apply EQ filters to both channels
        filters_list = [f"eq_band_{i:02d}" for i in range(10)]

        pipeline.append({
            "type": "Filter",
            "channel": 0,
            "names": filters_list
        })

        pipeline.append({
            "type": "Filter",
            "channel": 1,
            "names": filters_list
        })

        return pipeline

    def generate_config_yaml(self, config: Dict[str, Any]) -> str:
        """Convert configuration dictionary to YAML string"""
        import yaml

        # Custom representer for cleaner output
        def represent_float(dumper, value):
            if value == int(value):
                return dumper.represent_int(int(value))
            return dumper.represent_float(value)

        yaml.add_representer(float, represent_float)

        return yaml.dump(config, default_flow_style=False, sort_keys=False)

    def write_config(self, config: Dict[str, Any] = None) -> str:
        """Write configuration to file"""
        if config is None:
            config = self.generate_default_config()

        yaml_content = self.generate_config_yaml(config)

        # Atomic write
        temp_file = self.config_file + ".tmp"
        with open(temp_file, "w") as f:
            f.write(yaml_content)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_file, self.config_file)

        self.logger.info(f"Wrote CamillaDSP config to {self.config_file}")
        return self.config_file

    def read_config(self) -> Optional[Dict[str, Any]]:
        """Read existing configuration from file"""
        if not os.path.exists(self.config_file):
            return None

        try:
            import yaml
            with open(self.config_file, "r") as f:
                return yaml.safe_load(f)
        except Exception as e:
            self.logger.error(f"Error reading config: {e}")
            return None

    def update_filter(
        self,
        filter_id: str,
        freq: float = None,
        gain: float = None,
        q: float = None,
        filter_type: str = None
    ) -> bool:
        """Update a single filter in the config file"""
        config = self.read_config()
        if not config:
            return False

        if "filters" not in config:
            return False

        if filter_id not in config["filters"]:
            return False

        params = config["filters"][filter_id]["parameters"]

        if freq is not None:
            params["freq"] = freq
        if gain is not None:
            params["gain"] = gain
        if q is not None:
            params["q"] = q
        if filter_type is not None:
            params["type"] = filter_type

        self.write_config(config)
        return True

    def get_filters_from_config(self) -> List[Dict[str, Any]]:
        """Extract filter information from current config"""
        config = self.read_config()
        if not config or "filters" not in config:
            return []

        filters = []
        for filter_id, filter_data in config["filters"].items():
            if filter_data.get("type") == "Biquad":
                params = filter_data.get("parameters", {})
                filters.append({
                    "id": filter_id,
                    "type": params.get("type", "Peaking"),
                    "freq": params.get("freq", 1000),
                    "gain": params.get("gain", 0),
                    "q": params.get("q", 1.0),
                    "enabled": True
                })

        # Sort by frequency
        filters.sort(key=lambda x: x["freq"])

        return filters

    def reset_filters_to_flat(self) -> bool:
        """Reset all filters to 0 dB gain"""
        config = self.read_config()
        if not config or "filters" not in config:
            return False

        for filter_id in config["filters"]:
            if config["filters"][filter_id].get("type") == "Biquad":
                config["filters"][filter_id]["parameters"]["gain"] = 0.0

        self.write_config(config)
        return True

    def generate_multiroom_config(
        self,
        capture_device: str = "hw:Loopback,1,0",
        playback_device: str = "hw:Loopback,0,0"
    ) -> Dict[str, Any]:
        """
        Generate config for multiroom mode

        In multiroom mode:
        - Capture from snapclient output (loopback)
        - Playback to snapserver input (another loopback)
        """
        return self.generate_default_config(
            capture_device=capture_device,
            playback_device=playback_device
        )

    def generate_direct_config(
        self,
        capture_device: str = "hw:Loopback,1,0",
        playback_device: str = "hw:sndrpihifiberry,0,0"
    ) -> Dict[str, Any]:
        """
        Generate config for direct mode

        In direct mode:
        - Capture from source output (loopback)
        - Playback directly to HiFiBerry
        """
        return self.generate_default_config(
            capture_device=capture_device,
            playback_device=playback_device
        )
