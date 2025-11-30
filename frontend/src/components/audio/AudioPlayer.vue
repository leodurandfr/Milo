<template>
  <Teleport to="body" :disabled="!isMobile">
    <Transition name="audio-player" appear @after-leave="$emit('after-hide')">
      <div v-show="visible" class="audio-player" :class="playerClasses" :style="playerStyle">
      <!-- Background image - heavily zoomed and blurred -->
      <div class="player-art-background">
        <img v-if="artwork" :src="artwork" alt="" class="background-image" />
      </div>

      <div class="player-content">
        <!-- Artwork -->
        <img v-if="artwork" :src="artwork" :alt="title" class="player-artwork" />
        <img v-else :src="placeholderArtwork" :alt="title" class="player-artwork placeholder" />

        <!-- Info section with slot for flexible content -->
        <div class="player-info">
          <slot name="info">
            <p v-if="subtitle" class="player-subtitle text-mono">{{ subtitle }}</p>
            <p :class="['player-title', source === 'radio' ? 'heading-1' : 'heading-4']">{{ title }}</p>
          </slot>
          <slot name="progress"></slot>

        </div>


        <!-- Controls section with slot for flexible controls -->
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
import { computed, ref, onMounted, onUnmounted } from 'vue'
import IconButton from '@/components/ui/IconButton.vue'
import episodePlaceholder from '@/assets/podcasts/podcast-placeholder.jpg'

// Mobile detection for Teleport
const isMobile = ref(false)

const checkMobile = () => {
  isMobile.value = window.matchMedia('(max-aspect-ratio: 4/3)').matches
}

onMounted(() => {
  checkMobile()
  window.addEventListener('resize', checkMobile)
})

onUnmounted(() => {
  window.removeEventListener('resize', checkMobile)
})

const props = defineProps({
  /**
   * Audio source type ('radio', 'podcast', 'bluetooth', etc.)
   */
  source: {
    type: String,
    required: true,
    validator: (value) => ['radio', 'podcast', 'bluetooth', 'mac'].includes(value)
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
   * Placeholder artwork when no image is available
   */
  placeholderArtwork: {
    type: String,
    default: episodePlaceholder
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
  },

  /**
   * Fixed width for leave animation (in pixels)
   * Passed from AudioSourceLayout to maintain width during exit transition
   */
  width: {
    type: Number,
    default: null
  }
})

const emit = defineEmits(['toggle-play', 'after-hide'])

// Computed classes for styling based on source
const playerClasses = computed(() => ({
  [`source-${props.source}`]: true
}))

// Computed style for fixed width during leave animation
const playerStyle = computed(() => {
  if (props.width) {
    return { '--player-fixed-width': `${props.width}px` }
  }
  return {}
})
</script>

<style scoped>
/* Desktop: Vertical sidebar layout */
.audio-player {
  display: flex;
  width: 100%;
  margin: 0;
  height: 100%;
  max-height: 500px;
  flex-direction: column;
  gap: var(--space-04);
  padding: var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-07);
  backdrop-filter: blur(16px);
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
  filter: blur(40px) saturate(1.6);
  transform: scale(1.5);
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* Player content (sits above background) */
.player-content {
  height: 100%;
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  overflow-y: auto;
}

/* Artwork image */
.player-artwork {
  width: 100%;
  aspect-ratio: 1;
  border-radius: var(--radius-04);
  object-fit: cover;
  background: var(--color-background-neutral);
}

.player-artwork.placeholder {
  object-fit: cover;
}

/* Player info section */
.player-info {
  display: flex;
  justify-content: center;
  height: 100%;
  flex-direction: column;
  gap: var(--space-02);
}

:deep(.player-title) {
  color: var(--color-text-contrast);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  margin: 0;
}

:deep(.player-subtitle) {
  color: var(--color-text-contrast-50);
  cursor: pointer;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}



/* Controls section */
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
}

/* Mobile: Horizontal bottom panel layout */
@media (max-aspect-ratio: 4/3) {
  .audio-player {
    position: fixed;
    bottom: var(--space-08);
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
    gap: var(--space-02);
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
    min-width: 0;
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
    width: 100%;
    order: 2;
    /* Progress bar between info and controls */
  }

  /* Podcasts mobile: Compact vertical layout with 3 lines */
  .audio-player.source-podcast {
    flex-direction: column !important;
    gap: var(--space-03);
    padding: var(--space-03);
  }

  .audio-player.source-podcast .player-content {
    flex-direction: row !important;
    flex-wrap: wrap !important;
    align-items: center;
    row-gap: var(--space-01);
  }

  /* Line 1: Artwork (48px) + Info side by side */
  .audio-player.source-podcast .player-artwork {
    order: 0;
  }

  .audio-player.source-podcast .player-info {
    order: 1;
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: var(--space-01);
    padding-right: var(--space-01)
  }

  /* Line 3: All controls visible for podcasts */
  .audio-player.source-podcast .controls {
    order: 3;
    width: 100%;
  }

  .audio-player.source-podcast :deep(.speed-selector),
  .audio-player.source-podcast :deep(.playback-controls > :first-child) {
    display: flex !important;
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
    width: var(--player-fixed-width, 100%);
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
    bottom: var(--space-08);
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
