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

    <div class="lyrics-view-close">
      <IconButton icon="close" variant="rounded" size="large"
        :aria-label="t('common.close')" @click="lyricsStore.close()" />
    </div>

    <div class="lyrics-view-body">
      <Transition name="lyrics-fade" mode="out-in">
        <div v-if="lyricsStore.loading" key="loading" class="lyrics-view-state">
          <LyricsLoadingState :track-title="lyricsStore.trackTitle" :track-artist="lyricsStore.trackArtist" />
        </div>

        <div v-else-if="!lyricsStore.found" key="empty" class="lyrics-view-state">
          <SvgIcon name="lyrics" :size="64" color="var(--color-text-contrast-50)" />
          <p class="heading-3 lyrics-view-msg">{{ emptyState.message }}</p>
          <p v-if="emptyState.showTrack" class="text-body lyrics-view-track-line">
            <span>{{ lyricsStore.trackTitle }}</span>
            <span class="lyrics-view-track-sep">·</span>
            <span>{{ lyricsStore.trackArtist }}</span>
          </p>
        </div>

        <LyricsContent v-else :key="`content-${activeSource}`" :source="activeSource"
          :synced="lyricsStore.synced" :plain="lyricsStore.plain" />
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, watch, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLyricsStore, isLyricsCompatible, getTrackIdentity } from '@/stores/lyricsStore';
import { getFaviconUrl } from '@/utils/faviconUrl';

import IconButton from '@/components/ui/IconButton.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import LyricsContent from './LyricsContent.vue';
import LyricsLoadingState from './LyricsLoadingState.vue';

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const lyricsStore = useLyricsStore();

const activeSource = computed(() => unifiedStore.systemState.active_source);

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

// Escape to close.
function handleKeydown(event) {
  if (event.key === 'Escape') lyricsStore.close();
}
onMounted(() => document.addEventListener('keydown', handleKeydown, { passive: true }));
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
  top: max(var(--space-05), env(safe-area-inset-top, 0px));
  left: 50%;
  transform: translateX(-50%);
  z-index: 3;
}

.lyrics-view-body {
  position: relative;
  z-index: 2;
  height: 100%;
  display: flex;
  flex-direction: column;
}

/* Loading / empty states, centered over the backdrop with light-on-dark text. */
.lyrics-view-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-04);
}

.lyrics-view-msg {
  color: var(--color-text-contrast-50);
}

/* Title + artist bright, the connector ("de"/"by"/…) dimmed. Flex gap gives the
   inter-word spacing so it wraps cleanly on a long title/artist. Used by the
   "no lyrics found for" empty state (the loading state has its own copy in
   LyricsLoadingState). */
.lyrics-view-track-line {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 0 var(--space-02);
  padding-inline: var(--space-05);
  color: var(--color-text-contrast);
}

.lyrics-view-track-sep {
  color: var(--color-text-contrast);
}

/* Keyed state cross-fade (loading → empty → content). The content enters with a
   gentle rise so the lyrics settle in progressively rather than popping. */
.lyrics-fade-leave-active {
  transition: opacity var(--transition-fast-leave);
}
.lyrics-fade-enter-active {
  transition: opacity var(--transition-slow), transform var(--transition-slow);
  transition-delay: 120ms;
}
.lyrics-fade-enter-from,
.lyrics-fade-leave-to {
  opacity: 0;
}
.lyrics-fade-enter-from {
  transform: translateY(var(--space-06));
}
</style>
