<template>
  <!-- "image" variant: Image only for favorites grid -->
  <div v-if="variant === 'image'" v-press class="station-image-wrapper" @click="$emit('click')">
    <LazyImage
      ref="lazyImg"
      :src="getFaviconUrl(station.favicon)"
      :fallback-name="station.name"
      :alt="station.name"
      priority="high"
      :class="['station-image', { playing: isPlaying, loading: isLoading }]"
    >
      <transition name="loading-fade">
        <div v-if="isLoading" class="card-loading-overlay">
          <LoadingSpinner :size="48" />
        </div>
      </transition>
    </LazyImage>

    <!-- Skeleton overlay: hides the placeholder/favicon until content is
         ready, then fades out. Shown for every station (favicon or not) so
         the SVG fallback never "pops" into view. -->
    <transition name="content-fade">
      <SkeletonStationCard
        v-if="!contentReady"
        class="skeleton-overlay"
      />
    </transition>
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
      <p v-if="cardMetadata" class="station-subtitle text-mono">{{ cardMetadata }}</p>
    </div>

    <!-- Custom actions (0, 1 or 2 buttons) -->
    <div v-if="$slots.actions" class="actions-wrapper">
      <slot name="actions"></slot>
    </div>
  </div>

</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { getTranslatedCountryName } from '@/constants/countries';
import { getTranslatedGenreName } from '@/constants/musicGenres';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import SkeletonStationCard from './SkeletonStationCard.vue';
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

defineEmits(['click', 'play']);

const lazyImg = ref(null);
const contentReady = ref(false);

// The skeleton overlay covers everything until we know what the final visible
// content is. Three paths to "ready":
//   - favicon loaded successfully → favicon visible
//   - favicon errored             → SVG fallback visible
//   - no favicon URL at all       → SVG fallback visible
// In all paths the flip to `contentReady` is deferred via requestAnimationFrame
// so the skeleton is painted at full opacity once before its leave transition
// starts — without this, a cached favicon (synchronous imageLoaded=true) would
// skip the skeleton paint entirely and the leave animation would not show.
function markReady() {
  requestAnimationFrame(() => { contentReady.value = true; });
}

onMounted(() => {
  if (!props.station.favicon) markReady();
});

watch(
  () => lazyImg.value?.imageLoaded || lazyImg.value?.imageError,
  (settled) => { if (settled) markReady(); }
);

// Computed metadata for card variant: country + genre
const cardMetadata = computed(() => {
  const { country, countrycode } = props.station || {};
  const translatedCountry = getTranslatedCountryName(getCurrentLanguage(), countrycode, country || '');
  const genre = getTranslatedGenreName(getCurrentLanguage(), props.station?.genre || '');

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
  /* Force opaque shimmer here so the SVG fallback underneath cannot bleed through
     during the favicon load. Defaults (--color-background-neutral-50/12) are
     translucent and intended for skeletons sitting on a darker backdrop. */
  --shimmer-base: var(--color-background-strong);
  --shimmer-highlight: var(--color-background-neutral);
}

/* Skeleton overlay fade-out — leave-only; the skeleton is mounted at full
   opacity on first paint, then fades when content is ready. */
.content-fade-leave-active {
  transition: opacity var(--transition-normal-leave);
}

.content-fade-leave-to {
  opacity: 0;
}

/* Station image container */
.station-image {
  aspect-ratio: 1 / 1;
  width: 100%;
  border-radius: var(--radius-05);
  background: var(--color-background-neutral-50);
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
  border-radius: var(--radius-02);
  background: var(--color-background-neutral-12);
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