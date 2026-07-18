<template>
  <Teleport to="body" :disabled="!isMobile">
    <Transition name="audio-player" @after-leave="$emit('after-hide')">
      <!-- v-if, not v-show: a teleported v-show toggle (mobile) doesn't fire the
           transition classes, so the enter/leave would be instant. -->
      <div v-if="visible" class="audio-player" :class="playerClasses">
        <!-- Background image - heavily zoomed and blurred -->
        <div class="player-art-background">
          <img v-if="validArtwork" :src="validArtwork" alt="" class="background-image" />
          <div v-else-if="fallbackSvg" v-html="fallbackSvg" class="background-image" />
          <img v-else-if="placeholderArtwork" :src="placeholderArtwork" alt="" class="background-image" />
        </div>

        <div class="player-content">
          <!-- Artwork: falls back to inline-SVG avatar (font-aware) when no valid artwork,
             then to placeholderArtwork for sources that ship a static image (e.g. podcasts). -->
          <img v-if="validArtwork" :src="validArtwork" :alt="title" class="player-artwork" @load="handleArtworkLoad"
            @error="artworkError = true" />
          <div v-else-if="fallbackSvg" v-html="fallbackSvg" class="player-artwork" :aria-label="title" />
          <img v-else :src="placeholderArtwork" :alt="title" class="player-artwork placeholder" />

          <div class="player-info">
            <slot name="info">
              <p :class="['player-title', source === 'radio' ? 'heading-1' : 'heading-3']">{{ title }}</p>
              <p v-if="subtitle" class="player-subtitle text-mono">{{ subtitle }}</p>
            </slot>
            <slot name="progress"></slot>

          </div>


          <div class="controls">
            <slot name="controls">
              <!-- Default: Simple play/pause -->
              <div class="playback-controls">
                <IconButton :icon="isPlaying ? 'pause' : 'play'" variant="on-dark" size="medium" :loading="isLoading"
                  @click="$emit('toggle-play')" />
              </div>
            </slot>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import IconButton from '@/components/ui/IconButton.vue'
import episodePlaceholder from '@/assets/podcasts/podcast-placeholder.jpg'
import { useIsMobile } from '@/composables/useIsMobile'
import { generateStationAvatarSvg } from '@/utils/stationAvatar'
import { MIN_IMAGE_SIZE } from '@/constants/imageQuality'

const { isMobile } = useIsMobile()

const props = defineProps({
  /**
   * Audio source type ('radio', 'podcast', 'bluetooth', etc.)
   */
  source: {
    type: String,
    required: true,
    validator: (value) => ['radio', 'podcast', 'bluetooth', 'mac', 'music_library'].includes(value)
  },

  /**
   * Visibility control (replaces v-if in parent)
   */
  visible: {
    type: Boolean,
    default: false
  },

  /**
   * Artwork/image URL for the current item
   */
  artwork: {
    type: String,
    default: null
  },

  /**
   * Placeholder artwork URL — used when no valid artwork and no fallbackName.
   * Sources with a deterministic name (radio stations) should pass fallbackName
   * instead so the avatar is generated inline with the correct font; this prop
   * stays for sources that ship a static placeholder asset (e.g. podcasts).
   */
  placeholderArtwork: {
    type: String,
    default: episodePlaceholder
  },

  /**
   * Name used to generate an inline SVG avatar when no valid artwork loads.
   * Inline rendering (v-html) inherits document @font-face — using an <img>
   * data URL would lose Space Mono Bold and fall back to the system monospace.
   */
  fallbackName: {
    type: String,
    default: null
  },

  /**
   * Main title (station name, episode name, etc.)
   */
  title: {
    type: String,
    default: 'No title'
  },

  /**
   * Subtitle (genre/bitrate, podcast name, etc.)
   */
  subtitle: {
    type: String,
    default: null
  },

  /**
   * Playback state
   */
  isPlaying: {
    type: Boolean,
    default: false
  },

  /**
   * Loading/buffering state
   */
  isLoading: {
    type: Boolean,
    default: false
  }
})

defineEmits(['toggle-play', 'after-hide'])

// Artwork validation — falls back to inline SVG / placeholder on error or tiny image (e.g. 1x1 tracking pixel)
const artworkError = ref(false)
watch(() => props.artwork, () => { artworkError.value = false })
const validArtwork = computed(() => props.artwork && !artworkError.value ? props.artwork : null)
const fallbackSvg = computed(() => props.fallbackName ? generateStationAvatarSvg(props.fallbackName) : '')

function handleArtworkLoad(e) {
  if (e.target.naturalWidth < MIN_IMAGE_SIZE || e.target.naturalHeight < MIN_IMAGE_SIZE) {
    artworkError.value = true
  }
}

const playerClasses = computed(() => ({
  [`source-${props.source}`]: true
}))
</script>

<style scoped>
/* Desktop: Vertical sidebar layout */
.audio-player {
  display: flex;
  width: 100%;
  margin: 0;
  height: 100%;
  max-height: 720px;
  flex-direction: column;
  gap: var(--space-04);
  padding: 0 var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-07);
  backdrop-filter: blur(var(--blur-02));
  -webkit-backdrop-filter: blur(var(--blur-02));
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
  position: relative;
  overflow: hidden;
  z-index: 50;
}

/* Glass stroke border effect (matching both radio and podcast players exactly) */
.audio-player::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  opacity: 0.8;
  background: var(--stroke-glass);
  border-radius: var(--radius-07);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: 1;
  pointer-events: none;
}

/* Background artwork - heavily blurred and saturated */
.player-art-background {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 100%;
  height: 100%;
  z-index: 0;
  pointer-events: none;
  overflow: hidden;
}

/* Overlay to darken the background image */
.player-art-background::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--color-background-contrast-32);
  z-index: 1;
  pointer-events: none;
}

.background-image {
  filter: blur(var(--blur-04)) saturate(1.6);
  transform: scale(1.5) translateZ(0);
  width: 100%;
  height: 100%;
  object-fit: cover;
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
}

/* Player content (sits above background) */
.player-content {
  height: 100%;
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  padding: var(--space-04) 0;
  gap: var(--space-04);
  overflow-y: auto;
}

.player-artwork {
  width: 100%;
  aspect-ratio: 1;
  border-radius: var(--radius-04);
  object-fit: cover;
  background: var(--color-background-neutral);
  /* Clip the inline-SVG fallback to the rounded corners. (For <img>, content
     is clipped natively by border-radius — this matters only for the <div>
     wrapper case.) */
  overflow: hidden;
  /* In the parent flex column, flex-shrink: 1 (default) lets aspect-ratio be
     overridden when vertical space is tight; pinning it preserves the 1:1
     box for both <img> (which has intrinsic size) and the <div v-html=svg>
     wrapper (whose content is the SVG sized below). */
  flex-shrink: 0;
}

/* Inline-SVG fallback: let the SVG sit in normal flow with width:100% and
   height derived from its 1024×1024 viewBox (height: auto). This gives the
   wrapper a real, square content height — no circular dependency with the
   wrapper's aspect-ratio (which would otherwise fall back to the SVG default
   300×150 / ~1.94:1 box). */
.player-artwork :deep(svg) {
  display: block;
  width: 100%;
  height: auto;
}

.player-artwork.placeholder {
  object-fit: cover;
}

.player-info {
  display: flex;
  justify-content: center;
  height: 100%;
  flex-direction: column;
  padding: var(--space-05) 0;
  gap: var(--space-04);
}

:deep(.player-title) {
  color: var(--color-text-contrast);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  margin: 0;
}

:deep(.player-subtitle) {
  color: var(--color-text-contrast);
  cursor: pointer;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

:deep(.player-subtitle.text-mono) {
  color: var(--color-text-contrast-50);
}



.controls {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  position: relative;
}

:deep(.playback-controls) {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: var(--space-02);
  width: 100%;
}

/* Mobile: Horizontal bottom panel layout */
@media (max-aspect-ratio: 4/3) {
  .audio-player {
    position: fixed;
    /* bottom: calc(max(var(--space-06), env(safe-area-inset-bottom, 0px)) + var(--space-05)); */
    /* bottom: env(safe-area-inset-bottom, 0px); */
    bottom: calc(env(safe-area-inset-bottom, 0px) + var(--space-08));

    margin: 0;
    left: 50%;
    transform: translate(-50%, 0);
    width: calc(100% - var(--space-02) * 2);
    height: auto;
    max-height: none;
    flex-direction: row;
    align-items: center;
    padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
    border-radius: var(--radius-06);
  }

  .audio-player::before {
    border-radius: var(--radius-06);
  }

  .player-content {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    overflow-y: visible;
    padding: 0;
    gap: var(--space-04);
    width: 100%;
  }

  .player-artwork {
    width: 48px;
    height: 48px;
    min-width: 48px;
    border-radius: var(--radius-03);
  }

  .player-info {
    flex: 1;
    text-align: left;
    padding: 0;
    min-width: 0;
    gap: var(--space-01);
  }



  /* Apply same styles to slotted content (fixes scoped CSS limitation) */
  :deep(.player-title) {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    -webkit-line-clamp: unset;
    -webkit-box-orient: unset;
    display: block;
  }

  :deep(.player-subtitle) {
    display: none;
  }

  /* Hide progress bar on mobile by default */
  .player-content :deep(.progress-bar) {
    display: none;
  }

  /* Show progress bar for podcasts on mobile */
  .audio-player.source-podcast .player-content :deep(.progress-bar) {
    display: flex;
  }

  /* Podcasts mobile: Compact vertical layout with 3 lines */
  .audio-player.source-podcast {
    flex-direction: column !important;
    gap: var(--space-03);
    padding: var(--space-03);
  }

  .audio-player.source-podcast .player-content {
    display: grid !important;
    grid-template-columns: 88px 1fr;
    grid-template-rows: 1fr auto;
    column-gap: var(--space-03);
    row-gap: var(--space-01);
  }

  .audio-player.source-podcast .player-artwork {
    grid-row: 1 / -1;
    width: 88px;
    height: auto;
    min-width: 88px;
    align-self: center;
  }

  .audio-player.source-podcast .player-info {
    grid-column: 2;
    grid-row: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: var(--space-01);
    min-width: 0;
  }

  .audio-player.source-podcast .controls {
    grid-column: 2;
    grid-row: 2;
  }

  .audio-player.source-podcast :deep(.speed-selector),
  .audio-player.source-podcast :deep(.playback-controls > :first-child) {
    display: flex !important;
  }

  /* Show progress bar for music library on mobile */
  .audio-player.source-music_library .player-content :deep(.progress-bar) {
    display: flex;
  }

  /* Music library mobile: compact layout (art | title + progress / controls) */
  .audio-player.source-music_library {
    flex-direction: column !important;
    gap: var(--space-03);
    padding: var(--space-03);
  }

  .audio-player.source-music_library .player-content {
    display: grid !important;
    grid-template-columns: 88px 1fr;
    grid-template-rows: 1fr auto;
    column-gap: var(--space-03);
    row-gap: var(--space-01);
  }

  .audio-player.source-music_library .player-artwork {
    grid-row: 1 / -1;
    width: 88px;
    height: auto;
    min-width: 88px;
    align-self: center;
  }

  .audio-player.source-music_library .player-info {
    grid-column: 2;
    grid-row: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: var(--space-01);
    min-width: 0;
  }

  .audio-player.source-music_library .controls {
    grid-column: 2;
    grid-row: 2;
  }

  /* Compact mobile player drops the favorite/queue row (available in the UI). */
  .audio-player.source-music_library :deep(.ml-controls) {
    flex-direction: row;
    gap: var(--space-02);
  }

  .audio-player.source-music_library :deep(.ml-secondary) {
    display: none;
  }

  .controls {
    gap: var(--space-02);
    justify-content: center;
    flex-shrink: 0;
  }

}

/* Vue Transition: Desktop - slide from right with fade */
@media (min-aspect-ratio: 4/3) {

  .audio-player-enter-active,
  .audio-player-leave-active {
    /* Pin to the rendered width (wrapper minus its left padding) so the player
       doesn't reflow to 100% while .player-wrapper collapses during the slide.
       Inherits the layout's single source of truth — no JS prop needed. */
    width: calc(var(--audio-player-wrapper-width) - var(--space-06));
  }

  .audio-player-enter-active {
    transition:
      transform var(--transition-spring-slow),
      opacity 0.4s ease-out;
  }

  .audio-player-leave-active {
    transition:
      transform 0.6s cubic-bezier(0.5, 0, 0, 1),
      opacity 0.6s cubic-bezier(0.5, 0, 0, 1);
  }

  .audio-player-enter-from {
    opacity: 0;
    transform: translateX(100px);
  }

  .audio-player-leave-to {
    opacity: 0;
    transform: translateX(100px);
  }
}

/* Vue Transition: Mobile */
@media (max-aspect-ratio: 4/3) {

  .audio-player-enter-active,
  .audio-player-leave-active {
    position: fixed;
    bottom: calc(env(safe-area-inset-bottom, 0px) + var(--space-08));
    left: 50%;
  }

  .audio-player-enter-active {
    transition:
      transform var(--transition-spring),
      opacity 0.4s ease-out;
  }

  .audio-player-leave-active {
    transition:
      transform 0.6s cubic-bezier(0.5, 0, 0, 1),
      opacity 0.6s cubic-bezier(0.5, 0, 0, 1);
  }

  .audio-player-enter-from {
    opacity: 0;
    transform: translate(-50%, 120px);
  }

  .audio-player-enter-to,
  .audio-player-leave-from {
    opacity: 1;
    transform: translate(-50%, 0);
  }

  .audio-player-leave-to {
    opacity: 0;
    transform: translate(-50%, 120px);
  }
}
</style>
