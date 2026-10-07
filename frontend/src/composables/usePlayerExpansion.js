// frontend/src/composables/usePlayerExpansion.js
// Whether a browser source (radio, podcast, music library, Spotify) shows its
// navigation or its full player. Module state, because the screen has one
// answer: the shell that draws the two views (BrowserSourceViews) and the
// automatic expansion after inactivity (useAutoPlayer) read the same flag.
//
// It holds only "expanded or not" and never the source it was expanded for:
// another source opens on its own default view, so a change of source drops
// it. Besides the full player's cover, only an ending with nothing to resume does
// (BrowserSourceViews) — a pause or a stop that keeps something to resume
// leaves the player on screen.
import { computed, effectScope, readonly, ref, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

const expanded = ref(false);

function expand() {
  expanded.value = true;
}

function collapse() {
  expanded.value = false;
}

// The source-change rule, held once for the whole app in a detached scope, so
// it neither multiplies with the callers nor dies with one of them. Bound to
// the store it was first asked with; a test that installs a fresh Pinia gets it
// bound again to that one.
let ruleScope = null;
let ruleStore = null;

function holdSourceRule(store) {
  if (store === ruleStore) return;
  ruleScope?.stop();
  ruleStore = store;
  ruleScope = effectScope(true);
  ruleScope.run(() => watch(() => store.systemState.source, collapse));
}

/**
 * @returns {{
 *   expanded: import('vue').Ref<boolean>,
 *   expand: () => void,
 *   collapse: () => void
 * }}
 */
export function usePlayerExpansion() {
  holdSourceRule(useUnifiedAudioStore());
  return { expanded: readonly(expanded), expand, collapse };
}

/**
 * The view one browser source draws: true for its full player. It follows the
 * flag only while that source is the selected one; on the way out — another
 * source, which drops the flag at once, or a switch that hands the screen to
 * the status card — it keeps the view it had until the source has left, rather
 * than cross-fading back to the navigation mid-leave.
 *
 * @param {string} source - the browser source drawing the two views
 * @returns {import('vue').Ref<boolean>}
 */
export function useExpandedView(source) {
  const unifiedStore = useUnifiedAudioStore();
  const selected = computed(() => unifiedStore.systemState.source === source);
  const shown = ref(expanded.value && selected.value);
  holdSourceRule(unifiedStore);
  watch([expanded, selected], ([isExpanded, isSelected]) => {
    if (isSelected) shown.value = isExpanded;
  });
  return shown;
}

/**
 * Provided by BrowserSourceViews to the players it mounts: the way back to the
 * navigation (the full player's cover), whether the title (the album) has a
 * page to open there, and the artist line's names (`{ name, link }`). A full
 * player with nothing provided has neither — it is the only view of its
 * source. `{ back, canOpenAlbum, artists }`, the last two as refs.
 */
export const PLAYER_NAVIGATION = Symbol('playerNavigation');
