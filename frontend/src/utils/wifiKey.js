// frontend/src/utils/wifiKey.js
// Whether NetworkManager takes a key as a WPA-PSK: 8 to 63 characters, or 64
// hexadecimal digits. Mirrors backend/core/network/models.py::is_wpa_psk, which
// answers 422 to any other: refused here first, a short key is a hint under the
// field instead of nmcli's error in English and a system-fault banner.
export function isWpaKey(key) {
  if (key.length === 64) return /^[0-9a-fA-F]{64}$/.test(key);
  return key.length >= 8 && key.length <= 63;
}
