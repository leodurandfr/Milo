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
      <LoadingSpinner :size="48" />
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

      <!-- Loading overlay -->
      <div v-if="isLoading" class="loading-overlay">
        <LoadingSpinner :size="32" />
      </div>
    </div>

    <div class="station-details">
      <p class="station-title text-body">{{ station.name }}</p>
      <p v-if="cardMetadata" class="station-subtitle text-mono">{{ cardMetadata }}</p>
    </div>

    <!-- Custom actions (0, 1 or 2 buttons) -->
    <div v-if="$slots.actions" class="actions-wrapper">
      <slot name="actions"></slot>
    </div>
  </div>

</template>

<script setup>
import { ref, computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { getTranslatedCountryName } from '@/constants/countries';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
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
  z-index: 10;
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

.station-image.loading,
.station-card.loading {
  opacity: 0.9;
}

</style>