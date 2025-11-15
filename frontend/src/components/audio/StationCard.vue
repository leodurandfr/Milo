<template>
  <!-- "image" variant: Image only for favorites grid -->
  <div v-if="variant === 'image'" :class="['station-image', {
    active: isActive,
    playing: isPlaying,
    loading: isLoading
  }]" @click="$emit('click')">
    <img v-if="station.favicon" :src="getFaviconUrl(station.favicon)" alt="" class="station-img"
      @error="handleImageError" />
    <img :src="placeholderImg" :alt="t('audioSources.radioSource.stationNoImage')" class="image-placeholder"
      :class="{ visible: !station.favicon || imageError }" />

    <!-- Loading overlay -->
    <div v-if="isLoading" class="loading-overlay">
      <div class="loading-spinner"></div>
    </div>
  </div>

  <!-- "card" variant: Horizontal layout for lists -->
  <div v-else-if="variant === 'card'" :class="['station-card', {
    active: isActive,
    playing: isPlaying,
    loading: isLoading
  }]" @click="$emit('click')">
    <div class="station-logo" :class="imageSize">
      <img v-if="station.favicon" :src="getFaviconUrl(station.favicon)" alt="" class="station-favicon"
        @error="handleImageError" />
      <img :src="placeholderImg" :alt="t('audioSources.radioSource.stationNoImage')" class="logo-placeholder"
        :class="{ visible: !station.favicon || imageError }" />
    </div>

    <div class="station-details">
      <p class="station-title text-body">{{ station.name }}</p>
      <p v-if="cardMetadata" class="station-subtitle text-mono">{{ cardMetadata }}</p>
    </div>

    <!-- Loading spinner -->
    <div v-if="isLoading" class="loading-spinner-small"></div>

    <!-- Custom actions (0, 1 or 2 buttons) -->
    <div v-else-if="$slots.actions" class="actions-wrapper">
      <slot name="actions"></slot>
    </div>
  </div>

  <!-- "now-playing" variant: Player with controls -->
  <div v-else-if="variant === 'now-playing'" class="now-playing">
    <!-- Background image - heavily zoomed and blurred -->
    <div class="station-art-background">
      <img v-if="station.favicon" :src="getFaviconUrl(station.favicon)" alt="" class="background-station-favicon" />
    </div>

    <div class="station-art">
      <img v-if="station.favicon" :src="getFaviconUrl(station.favicon)" alt="Station logo"
        class="current-station-favicon" @error="handleImageError" />
      <img :src="placeholderImg" :alt="t('audioSources.radioSource.stationNoImage')" class="placeholder-logo"
        :class="{ visible: !station.favicon || imageError }" />
    </div>

    <div class="station-info">
      <p class="station-name display-1">{{ station.name }}</p>
      <p v-if="nowPlayingMetadata" class="station-meta text-mono">{{ nowPlayingMetadata }}</p>
    </div>

    <div v-if="showControls" class="controls-wrapper">
      <CircularIcon :icon="station.is_favorite ? 'heart' : 'heartOff'" variant="background-light"
        @click="$emit('favorite')" />
      <!-- Desktop: Button with text -->
      <Button v-if="!isMobile" variant="background-light" :left-icon="isPlaying ? 'stop' : 'play'"
        @click="$emit('play')">
        {{ isPlaying ? t('audioSources.radioSource.stopRadio') : t('audioSources.radioSource.playRadio') }}
      </Button>
      <!-- Mobile: CircularIcon without text -->
      <CircularIcon v-else :icon="isPlaying ? 'stop' : 'play'" variant="background-light" @click="$emit('play')" />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import CircularIcon from '@/components/ui/CircularIcon.vue';
import Button from '@/components/ui/Button.vue';
import placeholderImg from '@/assets/radio/station-placeholder.jpg';

const { t } = useI18n();

// Responsive detection for desktop vs mobile
const isMobile = ref(false);

const updateMediaQuery = () => {
  isMobile.value = window.matchMedia('(max-aspect-ratio: 4/3)').matches;
};

onMounted(() => {
  updateMediaQuery();
  window.addEventListener('resize', updateMediaQuery);
});

onUnmounted(() => {
  window.removeEventListener('resize', updateMediaQuery);
});

const props = defineProps({
  station: {
    type: Object,
    required: true
  },
  variant: {
    type: String,
    required: true,
    validator: (value) => ['card', 'image', 'now-playing'].includes(value)
  },
  showControls: {
    type: Boolean,
    default: true
  },
  isPlaying: {
    type: Boolean,
    default: false
  },
  isActive: {
    type: Boolean,
    default: false
  },
  isLoading: {
    type: Boolean,
    default: false
  },
  showCountry: {
    type: Boolean,
    default: false
  },
  imageSize: {
    type: String,
    default: 'small',
    validator: (value) => ['small', 'medium'].includes(value)
  }
});

defineEmits(['click', 'play', 'favorite']);

const imageError = ref(false);

// Helper function to capitalize first letter
function capitalizeGenre(genre) {
  if (!genre) return '';
  return genre.charAt(0).toUpperCase() + genre.slice(1);
}

// Computed metadata for now-playing variant: genre + bitrate
const nowPlayingMetadata = computed(() => {
  const genre = capitalizeGenre(props.station?.genre);
  const bitrate = props.station?.bitrate;

  // Both genre and bitrate
  if (genre && bitrate && bitrate > 0) {
    return `${genre} • ${bitrate} kbps`;
  }

  // Only genre
  if (genre) {
    return genre;
  }

  // Only bitrate
  if (bitrate && bitrate > 0) {
    return `${bitrate} kbps`;
  }

  // Neither - return empty string
  return '';
});

// Computed metadata for card variant: country + genre
const cardMetadata = computed(() => {
  const country = props.station?.country;
  const genre = capitalizeGenre(props.station?.genre);

  // Both country and genre
  if (country && genre) {
    return `${country} • ${genre}`;
  }

  // Only country
  if (country) {
    return country;
  }

  // Only genre
  if (genre) {
    return genre;
  }

  // Neither - return empty string
  return '';
});

function getFaviconUrl(faviconUrl) {
  // No favicon
  if (!faviconUrl) {
    return '';
  }

  // Local image already hosted by the backend
  if (faviconUrl.startsWith('/api/radio/images/')) {
    return faviconUrl;
  }

  // External image: use backend proxy to avoid CORS
  return `/api/radio/favicon?url=${encodeURIComponent(faviconUrl)}`;
}

function handleImageError() {
  imageError.value = true;
}
</script>

<style scoped>
/* === "IMAGE" VARIANT: Image only for grid === */
.station-image {
  aspect-ratio: 1 / 1;
  width: 100%;
  border-radius: var(--radius-05);
  overflow: hidden;
  cursor: pointer;
  transition: transform var(--transition-fast);
  position: relative;
  background: var(--color-background-neutral);
  display: flex;
  align-items: center;
  justify-content: center;
}


.station-image.playing {
  outline: 3px solid hsla(0, 0%, 0%, 0.08);
  outline-offset: -3px;
}

.station-image .station-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  top: 0;
  left: 0;
  display: block;
}

.station-image .image-placeholder {
  font-size: 48px;
  display: none;
  z-index: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.station-image .image-placeholder.visible {
  display: flex;
}


.loading-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-05);
  z-index: 10;
}


.loading-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid rgba(255, 255, 255, 0.2);
  border-top-color: var(--color-brand);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

/* === "CARD" VARIANT: Horizontal layout === */
.station-card {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-02);
  padding: var(--space-02);
  border: 2px solid var(--color-border);
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: all var(--transition-fast);
  background: var(--color-background-neutral-64);
  position: relative;
  min-width: 0;
}


.station-card.active {
  border-color: var(--color-brand);
}

.station-card.playing {
  border-color: var(--color-brand);
  background: var(--color-background);
}

.station-logo {
  flex-shrink: 0;
  width: 52px;
  height: 52px;
  position: relative;
  border-radius: var(--radius-02);
  overflow: hidden;
  background: var(--color-background);
}

.station-logo.medium {
  width: 60px;
  height: 60px;
}

.station-logo .station-favicon {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  display: block;
}

.logo-placeholder {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  font-size: 32px;
  display: none;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.logo-placeholder.visible {
  display: flex;
}

.station-details {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: var(--space-01);
  overflow: hidden;
}

.station-title {
  margin: 0;
  font-size: var(--font-size-body-small);
  font-weight: 500;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.station-subtitle {
  margin: 0;
  font-size: var(--font-size-small);
  color: var(--color-text-light);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}


.actions-wrapper {
  display: flex;
  flex-direction: row;
  gap: var(--space-02);
  align-items: center;
  flex-shrink: 0;
}


.loading-spinner-small {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-brand);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}


.station-image.loading,
.station-card.loading {
  opacity: 0.9;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* Desktop: Vertical layout */
.now-playing {
  top: var(--space-07);
  right: var(--space-06);
  margin: var(--space-07) var(--space-07) 0 0;
  display: flex;
  justify-content: space-between;
  overflow: hidden;
  width: 310px;
  height: calc(100% - 2 * var(--space-07));
  max-height: 560px;
  flex-shrink: 0;
  flex-direction: column;
  gap: var(--space-04);
  padding: var(--space-04);
  background: var(--color-text);
  border-radius: var(--radius-07);
  backdrop-filter: blur(16px);
}

.now-playing .station-art-background .background-station-favicon {
  filter: blur(96px) saturate(1.6) contrast(1) brightness(0.6);
}

.now-playing::before {
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
  z-index: -1;
  pointer-events: none;
}

/* === "NOW-PLAYING" VARIANT: Player with controls === */


.now-playing .station-art-background {
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

.now-playing .station-art-background .background-station-favicon {
  max-width: none;
  max-height: none;
  width: auto;
  height: auto;
  min-width: 200%;
  min-height: 200%;
  object-fit: contain;
  transform: scale(2);
}

.now-playing .station-art {
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  z-index: 1;
  aspect-ratio: 1 / 1;
  flex-shrink: 0;
}

.now-playing .station-art .current-station-favicon {
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

.now-playing .placeholder-logo {
  display: none;
  z-index: 1;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.now-playing .placeholder-logo.visible {
  display: flex;
}

.now-playing .station-info {
  position: relative;
  z-index: 1;
}

.now-playing .station-name {
  margin: 0;
}

.now-playing .station-meta {
  margin: 0;
}


.now-playing .station-art {
  width: 100%;
  border-radius: var(--radius-05);
}

.now-playing .station-info {
  display: flex;
  height: 100%;
  flex-direction: column;
  gap: var(--space-02);
}

.now-playing .station-name {
  color: var(--color-text-contrast);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.now-playing .station-meta {
  color: var(--color-text-contrast-50);
}

.controls-wrapper {
  display: flex;
  flex-direction: row-reverse;
  flex-wrap: nowrap;
  gap: var(--space-02);
  justify-content: space-between;
  z-index: 1;
}

/* Mobile: Horizontal sticky layout at bottom */
@media (max-aspect-ratio: 4/3) {
  .now-playing {
    position: fixed;
    bottom: var(--space-08);
    top: auto;
    left: 50%;
    transform: translateX(-50%);
    width: calc(100% - var(--space-02) * 2);
    height: auto;
    flex-direction: row;
    align-items: center;
    gap: var(--space-03);
    padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
    border-radius: var(--radius-06);
    box-shadow: 0 var(--space-04) var(--space-07) rgba(0, 0, 0, 0.2);
    z-index: 1000;
  }

  .now-playing .station-art {
    width: 48px;
    height: 48px;
    border-radius: var(--radius-03);
  }

  .now-playing .station-info {
    flex: 1;
    min-width: 0;
  }

  .now-playing .station-name {
    font-size: var(--font-size-h1);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .now-playing .station-meta {
    display: none;
  }

  .now-playing::before {
    border-radius: var(--radius-06);
  }

  .controls-wrapper {
    flex-direction: row;
  }

  /* Réduire la taille des CircularIcon et de leurs icônes en mobile */
  .controls-wrapper :deep(.circular-icon--background-light) {
    width: 40px;
    height: 40px;
  }

  .controls-wrapper :deep(.svg-responsive) {
    width: 28px;
    height: 28px;
  }

}
</style>