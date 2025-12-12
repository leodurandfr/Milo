<template>
  <!-- "image" variant: Image only for favorites grid -->
  <div v-if="variant === 'image'" class="station-image-wrapper interactive-press" @click="$emit('click')">
    <!-- Real content (always present so image can load) -->
    <div
      :class="['station-image', {
        playing: isPlaying,
        loading: isLoading
      }]"
    >
      <img
        ref="imgRef"
        :src="getFaviconUrl(station.favicon)"
        :alt="station.name"
        class="station-img"
        :class="{ loaded: imageLoaded }"
        @load="handleImageLoad"
        @error="handleImageError"
      />
      <img
        :src="placeholderImg"
        :alt="t('audioSources.radioSource.stationNoImage')"
        class="image-placeholder"
      />

      <!-- Loading overlay (buffering) -->
      <div v-if="isLoading" class="loading-overlay">
        <LoadingSpinner :size="48" />
      </div>
    </div>

    <!-- Skeleton overlay (on top, fades out when loaded) -->
    <transition name="content-fade">
      <SkeletonStationCard
        v-if="!imageLoaded && !imageError && station.favicon"
        class="skeleton-overlay"
      />
    </transition>
  </div>

  <!-- "card" variant: Horizontal layout for lists -->
  <div v-else-if="variant === 'card'" :class="['station-card', 'interactive-press', {
    playing: isPlaying,
    loading: isLoading
  }]" @click="$emit('click')">
    <div class="station-logo">
      <img
        ref="imgRef"
        :src="getFaviconUrl(station.favicon)"
        :alt="station.name"
        class="station-favicon"
        :class="{ loaded: imageLoaded }"
        @load="handleImageLoad"
        @error="handleImageError"
      />
      <img
        :src="placeholderImg"
        :alt="t('audioSources.radioSource.stationNoImage')"
        class="logo-placeholder"
      />

      <!-- Loading overlay -->
      <div v-if="isLoading" class="loading-overlay">
        <LoadingSpinner :size="32" />
      </div>
    </div>

    <div class="station-details">
      <p class="station-title heading-3">{{ station.name }}</p>
      <p v-if="cardMetadata" class="station-subtitle text-mono">{{ cardMetadata }}</p>
    </div>

    <!-- Custom actions (0, 1 or 2 buttons) -->
    <div v-if="$slots.actions" class="actions-wrapper">
      <slot name="actions"></slot>
    </div>
  </div>

</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { getTranslatedCountryName } from '@/constants/countries';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import SkeletonStationCard from './SkeletonStationCard.vue';
import placeholderImg from '@/assets/radio/station-placeholder.jpg';

const { t } = useI18n();

const props = defineProps({
  station: {
    type: Object,
    required: true
  },
  variant: {
    type: String,
    required: true,
    validator: (value) => ['card', 'image'].includes(value)
  },
  isPlaying: {
    type: Boolean,
    default: false
  },
  isLoading: {
    type: Boolean,
    default: false
  }
});

defineEmits(['click', 'play', 'favorite']);

const imageError = ref(false);
const imageLoaded = ref(false);
const imgRef = ref(null);

// Helper function to capitalize first letter
function capitalizeGenre(genre) {
  if (!genre) return '';
  return genre.charAt(0).toUpperCase() + genre.slice(1);
}

// Computed metadata for card variant: country + genre
const cardMetadata = computed(() => {
  const country = props.station?.country;
  const translatedCountry = country ? getTranslatedCountryName(t, country) : '';
  const genre = capitalizeGenre(props.station?.genre);

  // Both country and genre
  if (translatedCountry && genre) {
    return `${translatedCountry} • ${genre}`;
  }

  // Only country
  if (translatedCountry) {
    return translatedCountry;
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

function handleImageLoad() {
  imageLoaded.value = true;
}

function checkImageLoaded() {
  if (imgRef.value && imgRef.value.complete && imgRef.value.naturalHeight !== 0) {
    imageLoaded.value = true;
  }
}

onMounted(() => {
  checkImageLoaded();
});
</script>

<style scoped>
/* === "IMAGE" VARIANT: Image only for grid === */

/* Wrapper for grid overlay pattern */
.station-image-wrapper {
  position: relative;
  cursor: pointer;
}

.skeleton-overlay {
  position: absolute;
  inset: 0;
  z-index: 2;
}

/* Transition animations */
.content-fade-enter-active,
.content-fade-leave-active {
  transition: opacity var(--transition-normal);
}

.content-fade-enter-from,
.content-fade-leave-to {
  opacity: 0;
}

/* Station image container */
.station-image {
  aspect-ratio: 1 / 1;
  width: 100%;
  border-radius: var(--radius-05);
  overflow: hidden;
  transition: transform var(--transition-fast);
  position: relative;
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
  position: absolute;
  inset: 0;
  opacity: 0;
  transition: opacity var(--transition-normal);
  z-index: 1;
  background: var(--color-background-neutral);
}

.station-image .station-img.loaded {
  opacity: 1;
}

.station-image .image-placeholder {
  width: 100%;
  height: 100%;
  object-fit: cover;
  position: absolute;
  inset: 0;
  opacity: 1;
  z-index: 0;
}


.loading-overlay {
  position: absolute;
  inset: 0;
  background: var(--color-background-contrast-32);
  backdrop-filter: blur(4px);
    border-radius: var(--radius-03);

  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
  color: var(--color-text-contrast);
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
  background: var(--color-background-neutral-50);
  position: relative;
  min-width: 0;
}


.station-card.playing {
  border-color: var(--color-brand);
  background: var(--color-background);
}

.station-logo {
  flex-shrink: 0;
  width: 60px;
  height: 60px;
  position: relative;
  border-radius: var(--radius-02);
  overflow: hidden;
  background: var(--color-background);
}

.station-logo .station-favicon {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: center;
  opacity: 0;
  transition: opacity var(--transition-normal);
  z-index: 1;
}

.station-logo .station-favicon.loaded {
  opacity: 1;
}

.logo-placeholder {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  opacity: 1;
  z-index: 0;
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
  font-size: var(--font-size-h4);
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



</style>