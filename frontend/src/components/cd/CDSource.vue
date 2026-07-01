<!-- CDSource.vue - CD Player (wrapper around AudioPlayerFull) -->
<template>
  <AudioPlayerFull source="cd" :hideContent="showContentReplace" :keepArtwork="isIdle"
    :fallbackArtworkUrl="cdStore.discInfo?.album_art_url || ''">
    <template #action-buttons>
      <div class="action-buttons">
        <IconButton v-if="!isIdle" :icon="cdStore.showTracklist ? 'close' : 'queue'"
          :variant="isMobile ? 'on-grey' : 'background-strong'" size="medium" @click="cdStore.toggleTracklist()" />
        <IconButton icon="eject" :variant="isMobile ? 'on-grey' : 'background-strong'" size="medium"
          @click="cdStore.eject()" />
      </div>
    </template>

    <template #content-replace>
      <div class="tracklist-content">
        <div class="tracklist-header">
          <div class="tracklist-titles">
            <span class="heading-3 tracklist-artist">{{ artistName }}</span>
            <span class="heading-4 tracklist-album">{{ albumTitle }}</span>
          </div>
          <span v-if="releaseYear" class="text-mono-small tracklist-year">{{ releaseYear }}</span>
        </div>

        <!-- Idle disc: a prominent play resumes the last track/position (no live
             progress bar, since the position is stale in WAITING). -->
        <div v-if="isIdle" class="idle-actions">
          <button v-press class="idle-play" :aria-label="t('audioSources.cdSource.play')" @click="resumePlayback">
            <SvgIcon name="play" size="large" color="var(--color-text-contrast)" />
          </button>
        </div>
        <div class="tracklist-scroll">
          <TrackCard v-for="track in cdStore.tracks" :key="track.number" :track="track"
            :isCurrent="track.number === cdStore.currentTrack"
            :isPlaying="track.number === cdStore.currentTrack && cdStore.isPlaying"
            @play="cdStore.playTrack($event)" />
        </div>
      </div>
    </template>
  </AudioPlayerFull>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useCdStore } from '@/stores/cdStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
import IconButton from '@/components/ui/IconButton.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import TrackCard from './TrackCard.vue';
import { useIsMobile } from '@/composables/useIsMobile';

const { t } = useI18n();
const cdStore = useCdStore();
const unifiedStore = useUnifiedAudioStore();
const { isMobile } = useIsMobile();

// A disc is loaded but nothing is playing or spinning up (WAITING): show the
// tracklist + a prominent play button instead of a paused transport with a stale
// progress bar. Buffering is excluded so tapping play swaps to the now-playing
// view (with its spinner) immediately, rather than leaving a static play button.
const isIdle = computed(() =>
  cdStore.discPresent && !cdStore.isPlaying && !cdStore.isBuffering
);

// The tracklist replaces the now-playing view when the user opens it, and always
// while idle (there is no live playback to show).
const showContentReplace = computed(() => cdStore.showTracklist || isIdle.value);

// Resume picks up the last played track/position (backend command), unlike a
// per-track play which would restart from 0:00.
function resumePlayback() {
  unifiedStore.sendCommand('cd', 'resume');
}

const artistName = computed(() =>
  cdStore.discInfo?.artist || t('audioSources.cdSource.unknownArtist')
);

const albumTitle = computed(() =>
  cdStore.discInfo?.album || t('audioSources.cdSource.unknownAlbum')
);

// Release year (MusicBrainz "YYYY" or empty) — shown next to the album when known
const releaseYear = computed(() => cdStore.discInfo?.year || '');
</script>

<style scoped>
/* === ACTION BUTTONS === */
.action-buttons {
  display: flex;
  justify-content: space-between;
  flex-shrink: 0;
}

/* === TRACKLIST === */
.tracklist-content {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.tracklist-header {
  display: flex;
  justify-content: space-between;
  align-items: last baseline;
  gap: var(--space-03);
  padding-top: var(--space-06);
  padding-bottom: var(--space-04);
  flex-shrink: 0;
}

.tracklist-titles {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  min-width: 0;
}

.tracklist-artist {
  color: var(--color-text);
}

.tracklist-album {
  color: var(--color-text-secondary);
}

.tracklist-year {
  flex-shrink: 0;
  white-space: nowrap;
  color: var(--color-text-secondary);
}

/* === IDLE PLAY === */
.idle-actions {
  display: flex;
  justify-content: flex-end;
  padding-bottom: var(--space-04);
  flex-shrink: 0;
}

.idle-play {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  border: none;
  border-radius: 50%;
  background: var(--color-brand);
  cursor: pointer;
  transition: var(--transition-press);
}

.tracklist-scroll {
  flex: 1;
  overflow-y: auto;
  border-top: 1px solid var(--color-border);
  margin-bottom: calc(-1 * var(--space-05));
  padding-bottom: var(--space-05);
}

@media (max-aspect-ratio: 4/3) {
  .action-buttons {
    position: absolute;
    top: calc(max(var(--space-05), env(safe-area-inset-top, 0px)) + var(--space-04));
    left: calc(var(--space-05) + var(--space-04));
    right: calc(var(--space-05) + var(--space-04));
    z-index: 10;
  }

  .tracklist-scroll {
    margin-bottom: calc(-1 * max(var(--space-06), env(safe-area-inset-bottom, 0px)));
    padding-bottom: max(var(--space-06), env(safe-area-inset-bottom, 0px));
  }
}

/* === TRACKLIST STAGGER === */
.tracklist-header,
.tracklist-scroll {
  opacity: 0;
  transform: translateY(var(--space-05));
  animation:
    stagger-in-transform var(--transition-spring) forwards,
    stagger-in-opacity 0.4s ease forwards;
}

.tracklist-header {
  animation-delay: 0ms;
}

.tracklist-scroll {
  animation-delay: 80ms;
}

@keyframes stagger-in-transform {
  to {
    transform: translateY(0);
  }
}

@keyframes stagger-in-opacity {
  to {
    opacity: 1;
  }
}
</style>
