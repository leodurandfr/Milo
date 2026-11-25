// frontend/src/composables/useLeftScreenRefusal.js
import { watch, onMounted, onBeforeUnmount } from 'vue';
import { useSettingsStore } from '@/stores/settingsStore';

/**
 * How long SettingsModal says a write refused after its screen left
 * (`settingsStore.leftScreenRefusal`, set by useSettingsAPI): until the reader
 * moves to another view, or closes the modal. Both clears run before the
 * screen that leaves sends what it held — a navigation's watcher before the
 * view is patched out, the modal's beforeUnmount before its children's — so
 * the leaving screen's own refusal, answered later, is never the one cleared.
 * One answered after the modal closed is said at its next opening, whichever
 * view it opens on: the view a deep link opens (Screen after a kiosk restart,
 * Multiroom when a speaker presents itself) is set during setup, which is not
 * a navigation of the reader's.
 *
 * @param {import('vue').Ref<string>} currentView - the modal's view
 */
export function useLeftScreenRefusal(currentView) {
  const settingsStore = useSettingsStore();
  let opened = false;

  watch(currentView, () => {
    if (opened) settingsStore.updateLeftScreenRefusal(null);
  });
  onMounted(() => {
    opened = true;
  });
  onBeforeUnmount(() => settingsStore.updateLeftScreenRefusal(null));
}
