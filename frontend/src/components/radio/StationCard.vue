<template>
  <!-- "image" variant: Image only for favorites grid -->
  <div v-if="variant === 'image'" v-press class="station-image-wrapper" @click="$emit('click')">
    <LazyImage
      :src="getFaviconUrl(station.favicon)"
      :fallback-name="station.name"
      :alt="station.name"
      priority="high"
      skeleton
      :class="['station-image', { playing: isPlaying, loading: isLoading }]"
    >
      <transition name="loading-fade">
        <div v-if="isLoading" class="card-loading-overlay">
          <LoadingSpinner :size="48" />
        </div>
      </transition>
    </LazyImage>
  </div>

  <!-- "card" variant: Horizontal layout for lists -->
  <div v-else-if="variant === 'card'" v-press :class="['station-card', {
    playing: isPlaying,
    loading: isLoading
  }]" @click="$emit('click')">
    <LazyImage
      :src="getFaviconUrl(station.favicon)"
      :fallback-name="station.name"
      :alt="station.name"
      lazy
      class="station-logo"
    >
      <transition name="loading-fade">
        <div v-if="isLoading" class="card-loading-overlay">
          <LoadingSpinner :size="32" />
        </div>
      </transition>
    </LazyImage>

    <div class="station-details">
      <p class="station-title heading-3">{{ station.name }}</p>
      <p v-if="cardMetadata" class="station-subtitle text-mono-medium">{{ cardMetadata }}</p>
    </div>

    <!-- Custom actions (0, 1 or 2 buttons) -->
    <div v-if="$slots.actions" class="actions-wrapper">
      <slot name="actions"></slot>
    </div>
  </div>

</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { getTranslatedCountryName } from '@/constants/countries';
import { getTranslatedGenreName } from '@/constants/musicGenres';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import { getFaviconUrl } from '@/utils/faviconUrl';

const { getCurrentLanguage } = useI18n();

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

defineEmits(['click']);


const cardMetadata = computed(() => {
  const { country, countrycode } = props.station || {};
  const translatedCountry = getTranslatedCountryName(getCurrentLanguage(), countrycode, country || '');
  const genre = getTranslatedGenreName(getCurrentLanguage(), props.station?.genre || '');

  if (translatedCountry && genre) {
    return `${translatedCountry} • ${genre}`;
  }

  if (translatedCountry) {
    return translatedCountry;
  }

  if (genre) {
    return genre;
  }

  return '';
});

</script>

<style scoped>
/* === "IMAGE" VARIANT: Image only for grid === */

/* Wrapper for grid overlay pattern */
.station-image-wrapper {
  position: relative;
  cursor: pointer;
}




/* Station image container */
.station-image {
  aspect-ratio: 1 / 1;
  width: 100%;
  border-radius: var(--radius-05);
  background: var(--color-surface-glass);
  transition: transform var(--transition-fast);
}

.station-image.playing {
  box-shadow: 0 0 0 3px var(--color-brand);
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
  background: var(--color-surface-glass);
  position: relative;
  min-width: 0;
}


.station-card.playing {
  border-color: var(--color-brand);
  background: var(--color-inset);
}

.station-logo {
  flex-shrink: 0;
  width: 60px;
  height: 60px;
  border-radius: var(--radius-02);
  background: var(--color-skeleton);
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
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.station-subtitle {
  margin: 0;
  color: var(--color-text-tertiary);
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