<!-- LyricsView.vue — full-screen Lyrics app (replaces the old modal card).
     Opened from the dock over the current source; fetches lyrics on open and
     refetches when the track changes while open. The blurred, darkened album
     art of the current track is the backdrop; the synced lyrics render large
     over it (uniform size, three-level opacity highlight lives in LyricsContent). -->
<template>
  <Teleport to="body">
    <Transition name="lyrics-view">
      <div v-if="isOpen" class="lyrics-view">
        <!-- Blurred, darkened current-track artwork as the backdrop. Same
             technique as AudioPlayerFull's .artwork-blur, scaled to fill the
             viewport and dimmed so the lyrics stay legible over it. Keyed on the
             URL so a track change crossfades the two covers, and the layer
             drifts slowly on its own for a living backdrop. -->
        <Transition name="lyrics-bg">
          <div class="lyrics-view-bg" :key="artworkUrl"
            :style="{ backgroundImage: artworkUrl ? `url(${artworkUrl})` : 'none' }">
          </div>
        </Transition>

        <div class="lyrics-view-close">
          <IconButton icon="close" variant="rounded" size="large"
            :aria-label="t('common.close')" @click="close" />
        </div>

        <div class="lyrics-view-body">
          <Transition name="lyrics-fade" mode="out-in">
            <div v-if="lyricsStore.loading" key="loading" class="lyrics-view-state">
              <LoadingSpinner :size="56" />
              <div class="lyrics-view-loading">
                <p class="text-body lyrics-view-loading-label">{{ t('lyrics.loading') }}</p>
                <p class="heading-3 lyrics-view-loading-track">
                  <span>{{ lyricsStore.trackTitle }}</span>
                  <span class="lyrics-view-loading-sep">{{ t('lyrics.loadingConnector') }}</span>
                  <span>{{ lyricsStore.trackArtist }}</span>
                </p>
              </div>
            </div>

            <div v-else-if="!lyricsStore.found" key="empty" class="lyrics-view-state">
              <SvgIcon name="lyrics" :size="64" color="var(--color-text-contrast-50)" />
              <p class="heading-3 lyrics-view-msg">{{ emptyTitle }}</p>
            </div>

            <LyricsContent v-else :key="`content-${activeSource}`" :source="activeSource"
              :synced="lyricsStore.synced" :plain="lyricsStore.plain" />
          </Transition>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, watch, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLyricsStore } from '@/stores/lyricsStore';

import IconButton from '@/components/ui/IconButton.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import LyricsContent from './LyricsContent.vue';

const props = defineProps({
  isOpen: { type: Boolean, required: true }
});
const emit = defineEmits(['close']);

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const lyricsStore = useLyricsStore();

const activeSource = computed(() => unifiedStore.systemState.active_source);
const artworkUrl = computed(() => unifiedStore.systemState.metadata?.album_art_url || '');

function close() {
  emit('close');
}

// Track identity — refetch on change (a new track, or a mid-stream Shazam hit on
// radio). Position updates mutate metadata but not artist/title, so they don't
// retrigger. Only fetch while open (the view stays mounted; the backdrop/body
// are gated by isOpen), and do the initial fetch when the view opens.
const trackKey = computed(() => {
  const m = unifiedStore.systemState.metadata || {};
  return `${m.artist || ''}|||${m.title || ''}`;
});
watch(() => props.isOpen, (open) => { if (open) lyricsStore.loadLyrics(); });
watch(trackKey, () => { if (props.isOpen) lyricsStore.loadLyrics(); });

const emptyTitle = computed(() => {
  const m = unifiedStore.systemState.metadata || {};
  return m.artist && m.title ? t('lyrics.noLyrics') : t('lyrics.notPlaying');
});

// Escape to close; lock body scroll while open.
function handleKeydown(event) {
  if (event.key === 'Escape' && props.isOpen) close();
}
watch(() => props.isOpen, (open) => {
  document.body.style.overflow = open ? 'hidden' : '';
});
onMounted(() => document.addEventListener('keydown', handleKeydown, { passive: true }));
onUnmounted(() => {
  document.removeEventListener('keydown', handleKeydown);
  document.body.style.overflow = '';
});
</script>

<style scoped>
.lyrics-view {
  position: fixed;
  inset: 0;
  z-index: 5000;
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

.lyrics-view-state > :deep(.loading-spinner) {
  color: var(--color-text-contrast);
}

.lyrics-view-msg {
  color: var(--color-text-contrast-50);
}

/* Loading copy: dimmed label above, the searched track highlighted below. */
.lyrics-view-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-01);
  text-align: center;
  padding: 0 var(--space-05);
}

.lyrics-view-loading-label {
  color: var(--color-text-contrast-50);
}

/* Title + artist bright, the connector ("de"/"by"/…) dimmed. Flex gap gives the
   inter-word spacing so it wraps cleanly on a long title/artist. */
.lyrics-view-loading-track {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 0 var(--space-02);
  color: var(--color-text-contrast);
}

.lyrics-view-loading-sep {
  color: var(--color-text-contrast-50);
}

/* Full-screen enter/leave. */
.lyrics-view-enter-active {
  transition: opacity var(--transition-in-out);
}
.lyrics-view-leave-active {
  transition: opacity var(--transition-fast-leave);
}
.lyrics-view-enter-from,
.lyrics-view-leave-to {
  opacity: 0;
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
