# backend/tests/test_bluetooth_disconnect.py
"""`sources/bluetooth/source.py` — the command table against the AVRCP table.

What each command does to the phone (the disconnect, the single-device rule,
the AVRCP dispatch) is driven through the measured world in
test_bluetooth_behavior.py.
"""
from backend.sources.bluetooth.source import BluetoothSource


class TestTheAvrcpDispatch:
    async def test_every_declared_command_has_an_avrcp_verb(self):
        """Derived from the production tables rather than restated: a command
        in COMMANDS with no entry here is a KeyError on a button press."""
        transport = set(BluetoothSource.COMMANDS) - {"disconnect"}

        assert transport == set(BluetoothSource.AVRCP_COMMANDS)
