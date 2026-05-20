# backend/hardware/__init__.py
"""
Hardware module for Milo audio system.

Controllers live in their own submodules and are imported lazily by the
service registry (see dependencies.py::_import). The package intentionally
performs NO eager imports: pulling in RotaryVolumeController would drag in
`lgpio` (Pi-only GPIO native lib, absent on x86 dev/CI hosts), which would
break unrelated submodules (ir_remote, keymap_writer, …) at import time.

Submodules:
- rotary.RotaryVolumeController: Rotary encoder for volume control (needs lgpio)
- screen.ScreenController: Screen brightness and power management
- service.HardwareService: Hardware configuration service
- bt_remote.BtRemoteController: Bluetooth HID remote for volume/playback control
- ir_remote.IrRemoteController: IR remote (Apple Remote 1st gen) via TSOP4838 on GPIO17
"""
