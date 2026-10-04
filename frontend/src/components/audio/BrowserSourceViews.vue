<!-- BrowserSourceViews.vue - The two views of a browser source (radio, podcast,
     music library, Spotify): its navigation, and the full player it expands
     into (usePlayerExpansion). The navigation stays mounted under the player
     (v-show), so going back finds the same page at the same scroll; the two
     cross-fade with the same `audio-content` swap a source change uses. -->
<template>
  <div class="browser-source">
    <Transition name="audio-content">
      <div v-show="!playerShown" class="browser-view browser-nav">
        <!-- bar: the one thing the source binds on its AudioPlayer
             (v-bind="bar") — when it shows, what expands it, what releases the
             source's latch once it has left. -->
        <slot name="navigation" :bar="bar" />
      </div>
    </Transition>

    <Transition name="audio-content">
      <AudioPlayerFull v-if="playerShown" class="browser-view" :source="source"
        @artwork-click="openInNavigation('artwork-click')"
        @secondary-click="openInNavigation('secondary-click')">
        <template v-if="$slots['top-end']" #top-end>
          <slot name="top-end" />
        </template>
      </AudioPlayerFull>
    </Transition>
  </div>
</template>

<script setup>
import { computed, nextTick, provide, watch } from 'vue';
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
  /** The playing track names an album to open (the cover's link). */
  canOpenAlbum: {
    type: Boolean,
    default: false
  },
  /** The playing track names an artist to open (the artist line's link). */
  canOpenArtist: {
    type: Boolean,
    default: false
  }
});

// Re-emitted once the navigation is drawn again: the source opens the page.
const emit = defineEmits(['artwork-click', 'secondary-click']);

const { expand, collapse } = usePlayerExpansion();
const playerShown = useExpandedView(props.source);
const { isMobile } = useIsMobile();

provide(PLAYER_NAVIGATION, {
  back: collapse,
  canOpenAlbum: computed(() => props.canOpenAlbum),
  canOpenArtist: computed(() => props.canOpenArtist)
});

const hasSomethingToShow = computed(() => props.playback?.shouldShowPlayer.value ?? true);

// The bar has to leave under the player only on the phone, where it is
// teleported to <body> out of reach of the v-show; the kiosk's sidebar card is
// hidden with the navigation and is just there again on the way back.
const bar = computed(() => ({
  visible: hasSomethingToShow.value && !(playerShown.value && isMobile.value),
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
async function openInNavigation(event) {
  collapse();
  await nextTick();
  emit(event);
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

/* The navigation's own box, which the source's layout fills. */
.browser-nav {
  display: grid;
  grid-template: minmax(0, 1fr) / minmax(0, 1fr);
}
</style>
