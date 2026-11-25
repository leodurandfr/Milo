// frontend/src/composables/useHotspotPolling.js
import { watch, onBeforeUnmount } from 'vue';
import { useDiscoveryStore } from '@/stores/discoveryStore';

/**
 * Hold the discovery store's hotspot poll for as long as `shown` is true and
 * the calling view is mounted. Every tick is a WiFi scan on the server (some
 * five nmcli calls, a forced rescan on a unit on Ethernet), so a view polls
 * only while it draws the list: Settings › Multiroom shows no hotspot while
 * multiroom is off or switching, and App.vue opens it off whenever a speaker
 * presents itself. The poll is reference-counted, so this releases only what
 * it took. Turning on scans at once (`startPolling`).
 *
 * @param {() => boolean} shown - whether the view draws the hotspot list now
 */
export function useHotspotPolling(shown) {
  const discovery = useDiscoveryStore();
  let polling = false;

  function follow(on) {
    if (on === polling) return;
    polling = on;
    if (on) discovery.startPolling();
    else discovery.stopPolling();
  }

  watch(shown, follow, { immediate: true });
  onBeforeUnmount(() => follow(false));
}
