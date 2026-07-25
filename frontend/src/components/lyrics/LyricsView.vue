<!-- LyricsView.vue — Lyrics app, rendered by AudioSourceView as one more slot in
     its source-switching Transition, instead of overlaying a modal. Both
     opening and closing are a plain opacity fade — no slide/scale (see the
     .lyrics-slot override in AudioSourceView.vue, which skips the generic
     spring/slide used for regular source switches). Mounted only while
     lyricsStore.isOpen, so lyrics are fetched on open (see lyricsStore.open())
     and refetched when the track changes while open. -->

<template>
  <div class="lyrics-view">
    <!-- Blurred, darkened current-track artwork as the backdrop. Same
         technique as AudioPlayerFull's .artwork-blur, scaled to fill the slot
         and dimmed so the lyrics stay legible over it. Keyed on the URL so a
         track change crossfades the two covers, and the layer drifts slowly on
         its own for a living backdrop. -->
    <Transition name="lyrics-bg" appear>
      <div class="lyrics-view-bg" :key="artworkUrl"
        :style="{ backgroundImage: artworkUrl ? `url(${artworkUrl})` : 'none' }">
      </div>
    </Transition>

    <div ref="closeButtonWrapper" class="lyrics-view-close">
      <IconButton ref="closeButtonRef" icon="close" variant="rounded" size="large"
        :aria-label="t('common.close')" @click="lyricsStore.close()" />
    </div>

    <div class="lyrics-view-body">
      <Transition name="lyrics-fade" mode="out-in">
        <!-- Deliberately empty while the lookup is in flight: the loader below
             covers this slot, and keeping the slot blank is what lets the real
             state animate in afterwards instead of being merely revealed. -->
        <div v-if="lyricsStore.loading" key="pending" class="lyrics-view-state"></div>

        <div v-else-if="!lyricsStore.found" key="empty" class="lyrics-view-state">
          <MessageContent variant="dark" icon="lyrics" :title="emptyState.message"
            :details="emptyState.showTrack ? lyricsStore.trackLine : null" />
        </div>

        <LyricsContent v-else :key="`content-${activeSource}`" :source="activeSource"
          :synced="lyricsStore.synced" :plain="lyricsStore.plain"
          @update:ready="contentReady = $event" />
      </Transition>

      <!-- One loader for both waits — the LRCLIB lookup and then LyricsContent's
           centring. It sits outside the Transition above so it stays put across
           that swap: the reader sees a single uninterrupted message rather than
           two loading screens trading places mid-wait. -->
      <Transition name="lyrics-loader">
        <div v-if="showLoader" class="lyrics-view-loader">
          <MessageContent variant="dark" loading :loading-delay="0" :title="t('lyrics.loading')" />
        </div>
      </Transition>

      <LyricsPlaybackBar v-if="showPlaybackBar" :source="activeSource" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted, onUnmounted, nextTick } from 'vue';
import { useI18n } from '@/services/i18n';
import { useTimer } from '@/composables/useTimer';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLyricsStore, isLyricsCompatible, getTrackIdentity } from '@/stores/lyricsStore';
import { getFaviconUrl } from '@/utils/faviconUrl';

import IconButton from '@/components/ui/IconButton.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import LyricsContent from './LyricsContent.vue';
import LyricsPlaybackBar from './LyricsPlaybackBar.vue';

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const lyricsStore = useLyricsStore();
const timer = useTimer();

const closeButtonWrapper = ref(null);
const closeButtonRef = ref(null);

const activeSource = computed(() => unifiedStore.systemState.active_source);

// Reported by LyricsContent once it has centred (or immediately, for plain
// lyrics). The loading screen spans both waits — the LRCLIB lookup and this one —
// so it is the same message throughout, never two in a row.
const contentReady = ref(false);
const showLoader = computed(() =>
  lyricsStore.loading || (lyricsStore.found && !contentReady.value)
);

// A new lookup unmounts LyricsContent (the blank slot below wins the v-if), and an
// unmount emits nothing — so without this reset the previous track's `true` would
// survive, hiding the loader the moment the fetch resolves and letting it flash
// back in once the remounted content reports it hasn't centred yet.
watch(() => lyricsStore.loading, (isLoading) => {
  if (isLoading) contentReady.value = false;
});

// Radio has no canonical album_art_url: the recognized track's artwork lives
// under track_artwork, falling back to the station favicon (same fallback as
// RadioSource.vue's playerArtwork) so the backdrop isn't blank while no track
// has been recognized yet.
const artworkUrl = computed(() => {
  const m = unifiedStore.systemState.metadata || {};
  if (activeSource.value === 'radio') {
    return m.track_artwork || getFaviconUrl(m.favicon) || '';
  }
  return m.album_art_url || '';
});

// Track identity — refetch on change (a new track, or a mid-stream Shazam hit on
// radio). Position updates mutate metadata but not artist/title, so they don't
// retrigger. The component only exists while lyricsStore.isOpen (AudioSourceView
// mounts/unmounts it), so the initial fetch is lyricsStore.open()'s job.
const trackKey = computed(() => {
  const identity = getTrackIdentity(activeSource.value, unifiedStore.systemState.metadata);
  return `${identity.artist}|||${identity.title}`;
});
watch(trackKey, () => lyricsStore.loadLyrics());

// Empty-state message, most specific case first:
// - no active source at all → "nothing playing"
// - a source with no plausible track metadata (bluetooth/mac/podcast) → "not compatible"
// - radio playing but no track recognized yet (neither Shazam nor in-band) → "no song detected"
// - a compatible source with no track loaded → "nothing playing"
// - a track identity is known and a lookup completed with found=false → "no lyrics found for",
//   with the searched title/artist shown below (same track values loadLyrics() searched with).
const emptyState = computed(() => {
  const source = activeSource.value;
  if (!source || source === 'none') {
    return { message: t('lyrics.notPlaying'), showTrack: false };
  }
  if (!isLyricsCompatible(source)) {
    return { message: t('lyrics.notCompatible'), showTrack: false };
  }
  const identity = getTrackIdentity(source, unifiedStore.systemState.metadata);
  if (!identity.artist || !identity.title) {
    return {
      message: source === 'radio' ? t('lyrics.noTrackDetected') : t('lyrics.notPlaying'),
      showTrack: false
    };
  }
  return { message: t('lyrics.noLyrics'), showTrack: true };
});

// The playback bar needs an actual track identity to show anything useful —
// same presence check as emptyState's "no lyrics found for" branch, but
// independent of whether lyrics were actually found for it.
const showPlaybackBar = computed(() => {
  const source = activeSource.value;
  if (!isLyricsCompatible(source)) return false;
  const identity = getTrackIdentity(source, unifiedStore.systemState.metadata);
  return !!(identity.artist && identity.title);
});

// Escape to close.
function handleKeydown(event) {
  if (event.key === 'Escape') lyricsStore.close();
}
onMounted(async () => {
  document.addEventListener('keydown', handleKeydown, { passive: true });

  // Delayed pop-in for the close button — same choreography as Modal.vue's
  // close button: wrapper slides in on the spring curve, button fades in
  // separately, both held back until the view has settled.
  await nextTick();
  if (!closeButtonWrapper.value || !closeButtonRef.value) return;

  closeButtonWrapper.value.style.transition = 'none';
  closeButtonWrapper.value.classList.remove('visible');
  closeButtonRef.value.$el.style.transition = 'none';
  closeButtonRef.value.$el.style.opacity = '0';

  // Force reflow so the hidden state above is committed before animating in.
  closeButtonWrapper.value.offsetHeight;

  timer.setTimeout(() => {
    if (!closeButtonWrapper.value || !closeButtonRef.value) return;
    closeButtonWrapper.value.style.transition = 'transform var(--transition-spring-snappy)';
    closeButtonWrapper.value.classList.add('visible');
    closeButtonRef.value.$el.style.transition = 'opacity 350ms var(--easeOutCubic)';
    closeButtonRef.value.$el.style.opacity = '1';
  }, 500);
});
onUnmounted(() => document.removeEventListener('keydown', handleKeydown));
</script>

<style scoped>
.lyrics-view {
  position: absolute;
  inset: 0;
  overflow: hidden;
  /* Solid dark base so the view reads as dark even with no artwork, and so the
     dimmed backdrop blends toward dark rather than the page behind it. */
  background: var(--color-background-contrast);
}

.lyrics-view-bg {
  position: absolute;
  inset: -40px;
  z-index: 1;
  background-size: cover;
  background-position: center;
  /* Darken + soften so large light lyrics stay legible over any cover. */
  filter: blur(var(--blur-05)) saturate(1.4) brightness(0.38);
  opacity: 0.62;
  will-change: transform, opacity;
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
  /* Slow ambient drift so the backdrop keeps changing gently. */
  animation: lyrics-bg-drift 24s ease-in-out infinite alternate;
}

@keyframes lyrics-bg-drift {
  from { transform: scale(1.12) translate3d(0, 0, 0); }
  to { transform: scale(1.24) translate3d(0, -3%, 0); }
}

/* Crossfade the two covers when the track changes (both layers overlap). */
.lyrics-bg-enter-active,
.lyrics-bg-leave-active {
  transition: opacity 1200ms var(--easeOutCubic);
}
.lyrics-bg-enter-from,
.lyrics-bg-leave-to {
  opacity: 0;
}

.lyrics-view-close {
  position: absolute;
  top: var(--space-07);
  right: var(--space-07);
  z-index: 3;
  transform: translateY(-24px);
  visibility: hidden;
}

.lyrics-view-close.visible {
  transform: translateY(0);
  visibility: visible;
}

/* Mobile: same spot and slide-in as Modal.vue's mobile close button — its
   overlay padding-top (76px + safe-area term) plus its own wrapper offset
   (-space-03 - 52px), collapsed into one top value since this view has no
   overlay of its own to carry that padding. */
@media (max-aspect-ratio: 4/3) {
  .lyrics-view-close {
    top: calc(76px + env(safe-area-inset-top, 0px) - min(env(safe-area-inset-top, 0px), var(--space-03)) - var(--space-03) - 52px);
    left: 50%;
    right: auto;
    transform: translateX(-50%) translateY(-24px);
  }

  .lyrics-view-close.visible {
    transform: translateX(-50%) translateY(0);
  }
}

.lyrics-view-body {
  position: relative;
  z-index: 2;
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* Empty state: MessageContent owns its layout and light-on-dark copy
   (variant="dark"), so this only centers it over the backdrop. */
.lyrics-view-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Desktop only: a third of the view rather than shrink-to-fit, so the card
   reads as a deliberate column over the backdrop instead of an arbitrary
   width driven by its longest line. Reset back to auto on mobile below. */
.lyrics-view-state :deep(.message-content),
.lyrics-view-loader :deep(.message-content) {
  width: 33.333%;
}

@media (max-aspect-ratio: 4/3) {
  .lyrics-view-state :deep(.message-content),
  .lyrics-view-loader :deep(.message-content) {
    width: auto;
  }
}

/* The loading screen is a layer, not one of the states above — that's what lets
   it survive the state swap underneath it. Later in the DOM than the Transition,
   so it paints over it without needing a z-index (the playback bar's own layer
   carries z-index: 3 and still wins). */
.lyrics-view-loader {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Lifts away upward when both waits are over. No enter counterpart: on opening,
   the loader is the first thing on screen and has nothing to arrive from. */
.lyrics-loader-leave-active {
  transition: opacity var(--transition-fast-leave), transform var(--transition-fast-leave);
}
.lyrics-loader-leave-to {
  opacity: 0;
  transform: translateY(calc(-1 * var(--space-06)));
}

/* Keyed state transition (pending → empty → content): the outgoing state lifts
   away upward, then the incoming one rises from below. mode="out-in" makes them
   strictly sequential, never overlapping — no crossfade. Asymmetric on purpose:
   easeIn accelerates the exit away, easeOut decelerates the arrival into place. */
.lyrics-fade-leave-active {
  transition: opacity var(--transition-fast-leave), transform var(--transition-fast-leave);
}
.lyrics-fade-enter-active {
  transition: opacity var(--transition-normal), transform var(--transition-normal);
  /* Matches the loader's leave duration (--transition-fast-leave, 200ms). The
     loader lives in its own Transition, so out-in can't sequence them — without
     this the two would overlap and read as a crossfade. */
  transition-delay: 200ms;
}
.lyrics-fade-enter-from,
.lyrics-fade-leave-to {
  opacity: 0;
}
.lyrics-fade-enter-from {
  transform: translateY(var(--space-06));
}
.lyrics-fade-leave-to {
  transform: translateY(calc(-1 * var(--space-06)));
}
</style>
