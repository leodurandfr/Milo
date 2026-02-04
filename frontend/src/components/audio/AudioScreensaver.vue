<template>
  <div v-if="isVisible" class="screensaver-overlay" :class="{ closing: isClosing }" @click.stop="handleClose"
    @touchstart.stop="handleClose">
    <!-- Full-screen blurred background -->
    <div class="artwork-background">
      <img v-if="displayArtwork" :src="displayArtwork" alt="" class="background-image" />
    </div>

    <!-- Centered blur halo (positioned relative to overlay) -->
    <div class="album-art-blur"
      :style="{ backgroundImage: displayArtwork ? `url(${displayArtwork})` : 'none' }">
    </div>

    <!-- Main content: full-width horizontal layout -->
    <div class="now-playing-screensaver">
      <!-- Left: Artwork -->
      <div class="album-art-section stagger-1">
        <div class="album-art-container">
          <div class="album-art">
            <img v-if="displayArtwork" :src="displayArtwork" :alt="title" />
          </div>
        </div>
      </div>

      <!-- Right: Title + subtitle centered, station bar at bottom -->
      <div class="content-section stagger-2">
        <div class="track-info stagger-3">
          <h1 class="track-title heading-1">{{ title }}</h1>
          <p v-if="subtitle" class="track-subtitle heading-2">{{ subtitle }}</p>
        </div>

        <div v-if="showBottomBar" class="station-bar stagger-4">
          <img :src="stationFavicon" alt="" class="station-favicon" />
          <span class="station-name heading-4">{{ stationName }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';

const props = defineProps({
  isVisible: {
    type: Boolean,
    required: true
  },
  artwork: {
    type: String,
    default: null
  },
  title: {
    type: String,
    required: true
  },
  subtitle: {
    type: String,
    default: null
  },
  stationFavicon: {
    type: String,
    default: null
  },
  stationName: {
    type: String,
    default: null
  }
});

const emit = defineEmits(['close']);

const isClosing = ref(false);

const displayArtwork = computed(() => props.artwork || null);
const showBottomBar = computed(() => !!props.stationFavicon && !!props.stationName);

function handleClose(event) {
  event.preventDefault();
  event.stopPropagation();

  // Trigger closing animation
  isClosing.value = true;

  // Wait for the end of the animation (300ms) before actually closing
  setTimeout(() => {
    isClosing.value = false;
    emit('close');
  }, 300);
}

// Reset state when the screensaver reappears
watch(() => props.isVisible, (visible) => {
  if (visible) {
    isClosing.value = false;
  }
});
</script>

<style scoped>
.screensaver-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: #000000;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 7000;
  animation: fadeIn 400ms ease-out;
}

/* Closing animation */
.screensaver-overlay.closing {
  animation: fadeOut 300ms ease-out forwards;
}

@keyframes fadeIn {
  from {
    opacity: 0;
  }

  to {
    opacity: 1;
  }
}

@keyframes fadeOut {
  from {
    opacity: 1;
  }

  to {
    opacity: 0;
  }
}

/* Hide the screensaver in mobile/portrait mode */
@media (max-aspect-ratio: 4/3) {
  .screensaver-overlay {
    display: none !important;
  }
}

/* Full-screen blurred background */
.artwork-background {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 100%;
  height: 100%;
  z-index: 0;
  pointer-events: none;
  display: flex;
  align-items: center;
  justify-content: center;
}

.artwork-background .background-image {
  max-width: none;
  max-height: none;
  width: auto;
  height: auto;
  min-width: 200%;
  min-height: 200%;
  object-fit: contain;
  transform: scale(2);
  filter: blur(64px) saturate(1.5) contrast(1.5) brightness(0.5);
  opacity: 0.16;
}

/* === LAYOUT === */

.now-playing-screensaver {
  display: flex;
  width: 100%;
  height: 100%;
  padding: var(--space-05);
  gap: var(--space-06);
  position: relative;
  z-index: 1;
}

/* Album Art */
.album-art-section {
  flex-shrink: 0;
  aspect-ratio: 1;
  z-index: 2;
}

.album-art-container {
  position: relative;
  width: 100%;
  height: 100%;
}

.album-art-blur {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 116vw;
  height: 116vw;
  transform: translate(-50%, -50%) translateZ(0);
  z-index: 1;
  background-size: cover;
  background-position: center;
  filter: blur(64px) saturate(1.5);
  opacity: .12;
  will-change: transform, filter;
  backface-visibility: hidden;
}

.album-art {
  position: relative;
  z-index: 3;
  width: 100%;
  height: 100%;
  border-radius: var(--radius-07);
  overflow: hidden;
  box-shadow: 0px 0px 96px 0px #0000000d;
  pointer-events: none;
}

.album-art img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* Content Section */
.content-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  z-index: 1;
  min-width: 0;
}

.track-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  text-align: center;
  gap: var(--space-03);
}

.track-title {
  color: var(--color-text-contrast);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.track-subtitle {
  color: var(--color-text-contrast);
  opacity: 0.8;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
}

/* Station bar (radio + Shazam only) */
.station-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-03);
  padding-bottom: var(--space-06);
}

.station-favicon {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-02);
  object-fit: cover;
  flex-shrink: 0;
}

.station-name {
  color: var(--color-text-contrast);
  opacity: 0.8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* === STAGGER ANIMATIONS === */

.stagger-1,
.stagger-2,
.stagger-3,
.stagger-4 {
  opacity: 0;
  transform: translateY(var(--space-05));
}

.screensaver-overlay .stagger-1,
.screensaver-overlay .stagger-2,
.screensaver-overlay .stagger-3,
.screensaver-overlay .stagger-4 {
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

.screensaver-overlay .stagger-1 { animation-delay: 400ms; }
.screensaver-overlay .stagger-2 { animation-delay: 400ms; }
.screensaver-overlay .stagger-3 { animation-delay: 500ms; }
.screensaver-overlay .stagger-4 { animation-delay: 600ms; }

@keyframes stagger-transform {
  to {
    transform: translateY(0);
  }
}

@keyframes stagger-opacity {
  to {
    opacity: 1;
  }
}
</style>
