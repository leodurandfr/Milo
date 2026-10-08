<!-- BrowserSourceViews.vue - The two views of a browser source (radio, podcast,
     music library, Spotify): its navigation, and the full player it expands
     into (usePlayerExpansion). The player is a sheet over the navigation, which
     stays mounted and drawn under it (useCover), so going back finds the same
     page at the same scroll and rebuilds nothing; the player comes and goes
     with the same `audio-content` fade a source change uses. -->
<template>
  <div class="browser-source">
    <div ref="navEl" class="browser-view browser-nav"
      :class="{ 'browser-nav--under': playerOnScreen, 'is-covered': covered }">
      <!-- bar: the one thing the source binds on its AudioPlayer
           (v-bind="bar") — when it shows, what expands it, what releases the
           source's latch once it has left. -->
      <slot name="navigation" :bar="bar" />
    </div>

    <!-- The veil between the navigation and the player being pulled down: as
         dark as a modal's at rest, clear once the player is out. -->
    <div v-if="pullState" class="browser-view browser-veil" aria-hidden="true" :style="veilStyle" />

    <Transition name="audio-content" @after-enter="onOverlayEntered" @after-leave="playerOnScreen = false">
      <AudioPlayerFull v-if="playerShown" class="browser-view browser-player" :source="source"
        @title-click="openInNavigation('title-click')"
        @secondary-click="openInNavigation('secondary-click', $event)">
        <template v-if="$slots['transport-end']" #transport-end="slotProps">
          <slot name="transport-end" v-bind="slotProps" />
        </template>
      </AudioPlayerFull>
    </Transition>
  </div>
</template>

<script setup>
import { computed, inject, nextTick, provide, ref, watch } from 'vue';
import { UNDER_OVERLAY, useCover } from '@/composables/useCover';
import { useIsMobile } from '@/composables/useIsMobile';
import { PLAYER_NAVIGATION, useExpandedView, usePlayerExpansion } from '@/composables/usePlayerExpansion';
import { BROWSER_SOURCES } from '@/constants/audioSources';
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';

const props = defineProps({
  /** The browser source these two views belong to. */
  source: {
    type: String,
    required: true,
    validator: (value) => BROWSER_SOURCES.includes(value)
  },
  /**
   * The source's useSourcePlaybackVisibility(): whether its bar has something
   * to show, and the release of what the bar latched. Null for a stage with no
   * playback behind it (the gallery), whose bar is always up.
   */
  playback: {
    type: Object,
    default: null
  },
  /** The playing track names an album to open (the title's link). */
  canOpenAlbum: {
    type: Boolean,
    default: false
  },
  /**
   * The artist line's names, in its order: the source's own entries, each
   * `{ name, link }` (+ whatever the source needs to open it), `link` where it
   * has a page to open. `secondary-click` hands back the entry pressed. Empty:
   * the line opens nothing.
   */
  artists: {
    type: Array,
    default: () => []
  }
});

// Re-emitted once the navigation is drawn again: the source opens the page.
const emit = defineEmits(['title-click', 'secondary-click']);

const { expand, collapse } = usePlayerExpansion();
const playerShown = useExpandedView(props.source);
const { isMobile } = useIsMobile();

// The phone's pull down on the player, as the player reports it ({ progress,
// timing }, null when there is none): the navigation is drawn under it, so the
// pull uncovers the page it returns to.
const pullState = ref(null);
watch(playerShown, (shown) => {
  if (!shown) pullState.value = null;
});

// Covered once the player is in, drawn again for the pull and from the moment
// the player starts leaving, rising as it goes.
// The player is drawn from its expansion to the end of its leave, and the
// navigation stays a layer under it all that time (`browser-nav--under`).
const playerOnScreen = ref(playerShown.value);
watch(playerShown, (shown) => {
  if (shown) playerOnScreen.value = true;
});

const navEl = ref(null);
const { covered, onOverlayEntered } = useCover(playerShown, {
  peek: computed(() => !!pullState.value),
  reveal: navEl
});

// Lyrics, laid over this source by AudioSourceView (none on a stage).
const underOverlay = inject(UNDER_OVERLAY, ref(false));

const veilStyle = computed(() => {
  const { progress, timing } = pullState.value;
  return { opacity: 1 - progress, transition: timing ? `opacity ${timing}` : 'none' };
});

provide(PLAYER_NAVIGATION, {
  back: collapse,
  pull: (state) => { pullState.value = state; },
  canOpenAlbum: computed(() => props.canOpenAlbum),
  artists: computed(() => props.artists)
});

const hasSomethingToShow = computed(() => props.playback?.shouldShowPlayer.value ?? true);

// The bar has to leave under the player — or Lyrics — only on the phone, where
// it is teleported to <body> out of reach of the cover; the kiosk's sidebar
// card is covered with the navigation and is just there again on the way back.
const bar = computed(() => ({
  visible: hasSomethingToShow.value && !(isMobile.value && (playerShown.value || underOverlay.value)),
  onExpand: expand,
  onAfterHide: () => props.playback?.onAfterHide()
}));

// An ending with nothing to resume closes the player: the bar is gone with it,
// so the player would have nothing left to drive. The source's latch is
// released at once rather than by the bar's leave — the phone's bar is already
// gone, the kiosk's leaves unseen — so a finished album is not one tap away,
// nor a station keeps its heart.
watch(hasSomethingToShow, (present) => {
  if (present || !playerShown.value) return;
  props.playback?.onAfterHide();
  collapse();
});

// From the player: back to the navigation, then the page — once the navigation
// is drawn again, so the page left behind keeps its scroll for back(). The
// player emits a link only when the flag above says there is one to open.
async function openInNavigation(event, payload) {
  collapse();
  await nextTick();
  emit(event, payload);
}
</script>

<style scoped>
/* The two views share one cell, so the leaving one and the entering one overlap
   while they cross-fade. */
.browser-source {
  display: grid;
  grid-template: minmax(0, 1fr) / minmax(0, 1fr);
  width: 100%;
  height: 100%;
}

.browser-view {
  grid-area: 1 / 1;
  min-width: 0;
  min-height: 0;
}

/* The navigation under the player is a stacking context of its own, so nothing
   in it — the back-to-top button is fixed at z 3 — rises over the veil or the
   player. Only then: on its own the navigation must leave its layers to the
   page. */
.browser-nav--under {
  position: relative;
  z-index: 0;
}

.browser-veil {
  z-index: 1;
  background: var(--color-overlay);
  pointer-events: none;
}

/* The player is a sheet over the navigation and stays over it in every phase:
   the audio-content swap ranks a leaving view at 0, which would drop the player
   under the navigation as it leaves. Two classes here outrank the swap's one. */
.browser-source > .browser-player {
  z-index: 2;
}

/* The sheet comes in on a short ease-out rather than the swap's 400 ms: it is
   an answer to a tap, and the navigation does not fade out under it. The
   transform only declares the envelope of the rise inside (.source-motion). */
.browser-source > .browser-player.audio-content-enter-active {
  transition: opacity var(--transition-fast), transform var(--transition-spring);
}

/* The navigation's own box, which the source's layout fills. */
.browser-nav {
  display: grid;
  grid-template: minmax(0, 1fr) / minmax(0, 1fr);
}
</style>
