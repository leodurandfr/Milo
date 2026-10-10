<template>
  <!-- "image" variant: Image only for favorites grid -->
  <div v-if="variant === 'image'" v-press class="station-image-wrapper" @click="$emit('click')">
    <LazyImage
      :src="getFaviconUrl(station.favicon)"
      :fallback-name="station.name"
      :alt="station.name"
      priority="high"
      skeleton
      :blurred="isLoading"
      :class="['station-image', { playing: isPlaying, loading: isLoading }]"
    >
      <transition name="loading-fade">
        <div v-if="isLoading" class="card-loading-overlay">
          <LoadingSpinner :size="48" />
        </div>
      </transition>
    </LazyImage>
  </div>

  <!-- "card" variant: the row of the search and settings lists -->
  <ListItemButton v-else-if="variant === 'card'" variant="inset" icon-variant="full" :action="action"
    :class="['station-row', { playing: isPlaying }]" @click="$emit('click')">
    <template #icon>
      <LazyImage
        :src="getFaviconUrl(station.favicon)"
        :fallback-name="station.name"
        alt=""
        lazy
        :blurred="isLoading"
        class="station-logo"
      >
        <transition name="loading-fade">
          <div v-if="isLoading" class="card-loading-overlay">
            <LoadingSpinner :size="24" />
          </div>
        </transition>
      </LazyImage>
    </template>
    <template #title="{ headingClass }">
      <span :class="['station-row__line', headingClass]">{{ station.name }}</span>
    </template>
    <template v-if="cardMetadata" #subtitle>
      <span class="station-row__line station-row__subtitle text-body-small">{{ cardMetadata }}</span>
    </template>
  </ListItemButton>

</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { getTranslatedCountryName } from '@/constants/countries';
import { getTranslatedGenreName } from '@/constants/musicGenres';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
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
  },
  // "card" only: a caret where the row opens a page (the settings' edit
  // screen), none where it plays the station.
  action: {
    type: String,
    default: 'none',
    validator: (value) => ['none', 'caret'].includes(value)
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

/* === "CARD" VARIANT: a ListItemButton row === */

/* The station on air: the row's hairline becomes the brand ring. */
.station-row.playing {
  box-shadow: inset 0 0 0 2px var(--color-brand);
}

.station-logo {
  width: 100%;
  height: 100%;
  background: var(--color-fill-faint);
}

/* The row sizes every svg in its icon to the icon's box; the spinner keeps its own. */
.station-logo :deep(.loading-spinner svg) {
  width: 100%;
  height: 100%;
}

.station-row__line {
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.station-row__subtitle {
  color: var(--color-text-secondary);
}
</style>