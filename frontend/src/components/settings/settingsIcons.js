// frontend/src/components/settings/settingsIcons.js
// The glyphs of the settings home's tiles, by name. Kept out of SvgIcon's
// registry on purpose: only SettingsModal draws them, and SettingsModal is
// loaded on demand, so importing them here keeps ~20 KB of paths in its chunk
// instead of the main bundle every boot downloads. Each glyph paints in
// currentColor, which SettingsModal's tile sets from a token, so the glyph
// follows the theme with the tile behind it.
import languages from '@/assets/settings-icons/languages.svg?raw';
import dock from '@/assets/settings-icons/applications.svg?raw';
import volume from '@/assets/settings-icons/volume.svg?raw';
import display from '@/assets/settings-icons/display.svg?raw';
import audioPlayback from '@/assets/settings-icons/audio-playback.svg?raw';
import remoteControls from '@/assets/settings-icons/remote-controls.svg?raw';
import multiroom from '@/assets/settings-icons/multiroom.svg?raw';
import updates from '@/assets/settings-icons/updates.svg?raw';
import system from '@/assets/settings-icons/system.svg?raw';
import radio from '@/assets/settings-icons/radio.svg?raw';
import mac from '@/assets/settings-icons/macos.svg?raw';
import spotify from '@/assets/settings-icons/spotify.svg?raw';
import qobuz from '@/assets/settings-icons/qobuz.svg?raw';
import musicLibrary from '@/assets/settings-icons/music-library.svg?raw';
import hardware from '@/assets/settings-icons/hardware.svg?raw';
import network from '@/assets/settings-icons/network.svg?raw';
import reboot from '@/assets/settings-icons/reboot.svg?raw';
import shutdown from '@/assets/settings-icons/shutdown.svg?raw';

export const SETTINGS_ICONS = {
  languages, dock, volume, display, audioPlayback, remoteControls, multiroom,
  updates, system, radio, mac, spotify, qobuz, musicLibrary, hardware, network,
  reboot, shutdown
};
