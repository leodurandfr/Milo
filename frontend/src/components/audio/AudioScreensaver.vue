<template>
  <div v-if="isVisible" class="screensaver-overlay" :class="{ closing: isClosing }" @click.stop="handleClose"
    @touchstart.stop="handleClose">
    <!-- Full-screen blurred background -->
    <div class="artwork-background">
      <img v-if="displayArtwork" :src="displayArtwork" alt="" class="background-image" />
    </div>

    <!-- Centered card with progressive animation -->
    <div class="now-playing-card stagger-1">
      <!-- Background image - heavily zoomed and blurred (inside the card) -->
      <div class="artwork-background-card">
        <img v-if="displayArtwork" :src="displayArtwork" alt="" class="background-card-image" />
      </div>

      <!-- Artwork (stagger 2 animation) -->
      <div class="artwork-container stagger-2">
        <img v-if="displayArtwork" :src="displayArtwork" :alt="title"
          class="artwork-image" @error="handleImageError" />
        <img :src="placeholderImage" alt="" class="placeholder-logo"
          :class="{ visible: !displayArtwork || imageError }" />
      </div>

      <!-- Info (stagger 3 animation) -->
      <div class="info-container stagger-3">
        <p class="info-title display-1">{{ title }}</p>
        <p v-if="subtitle" class="info-subtitle heading-3">{{ subtitle }}</p>
        <p v-if="metadata" class="info-metadata text-mono">{{ metadata }}</p>
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
  placeholderImage: {
    type: String,
    required: true
  },
  title: {
    type: String,
    required: true
  },
  subtitle: {
    type: String,
    default: null
  },
  metadata: {
    type: String,
    default: null
  }
});

const emit = defineEmits(['close']);

const imageError = ref(false);
const isClosing = ref(false);

const displayArtwork = computed(() => props.artwork || null);

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

function handleImageError() {
  imageError.value = true;
}

// Reset states when the screensaver reappears
watch(() => props.isVisible, (visible) => {
  if (visible) {
    isClosing.value = false;
    imageError.value = false;
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

.now-playing-card {
  position: relative;
  display: flex;
  flex-direction: row;
  align-items: center;
  overflow: hidden;
  width: 90%;
  max-width: 634px;
  gap: var(--space-05);
  padding: var(--space-04);
  border-radius: var(--radius-08);
  backdrop-filter: blur(16px);
}

.now-playing-card::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  opacity: 0.8;
  background: var(--stroke-glass);
  border-radius: var(--radius-08);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: 1;
  pointer-events: none;
}

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

.artwork-background-card {
  position: absolute;
  background: var(--color-background-contrast);
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

.artwork-background-card .background-card-image {
  max-width: none;
  max-height: none;
  width: auto;
  height: auto;
  min-width: 200%;
  min-height: 200%;
  object-fit: contain;
  transform: scale(2);
  filter: blur(96px) saturate(1.6) contrast(1) brightness(0.6);
}

.artwork-container {
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  z-index: 1;
  aspect-ratio: 1 / 1;
  flex-shrink: 0;
  width: 240px;
  height: 240px;
  border-radius: var(--radius-06);
}

.artwork-container img {
  background: var(--color-background-neutral);
}

.artwork-container .artwork-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  position: absolute;
  top: 0;
  left: 0;
  z-index: 2;
  display: block;
}

.placeholder-logo {
  display: none;
  z-index: 1;
  font-size: 64px;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.placeholder-logo.visible {
  display: flex;
}

.info-container {
  position: relative;
  z-index: 1;
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: var(--space-03);
}

.info-title {
  margin: 0;
  color: var(--color-text-contrast);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.info-subtitle {
  margin: 0;
  color: var(--color-text-contrast);
  opacity: 0.8;
}

.info-metadata {
  margin: 0;
  color: var(--color-text-contrast-50);
}

/* === STAGGERING WITH SPRING === */

.stagger-1,
.stagger-2,
.stagger-3 {
  opacity: 0;
  transform: scale(0.9);
}

.stagger-2,
.stagger-3 {
  transform: translateY(var(--space-05));
}


.screensaver-overlay .stagger-1,
.screensaver-overlay .stagger-2,
.screensaver-overlay .stagger-3 {
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

.screensaver-overlay .stagger-1 {
  animation-delay: 400ms;
}

.screensaver-overlay .stagger-2 {
  animation-delay: 500ms;
}

.screensaver-overlay .stagger-3 {
  animation-delay: 600ms;
}

@keyframes stagger-transform {
  to {
    transform: translateY(0) scale(1);
  }
}

@keyframes stagger-opacity {
  to {
    opacity: 1;
  }
}
</style>
