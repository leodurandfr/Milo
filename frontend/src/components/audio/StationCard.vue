<template>
  <!-- Variante "image" : Image seule pour grille de favoris -->
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

  <!-- Variante "card" : Layout horizontal pour listes -->
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
      <p class="station-subtitle text-mono">
        <template v-if="showCountry && station.country">{{ station.country }} • </template>{{ station.genre }}
      </p>
    </div>

    <!-- Loading spinner -->
    <div v-if="isLoading" class="loading-spinner-small"></div>

    <!-- Actions personnalisées (0, 1 ou 2 boutons) -->
    <div v-else-if="$slots.actions" class="actions-wrapper">
      <slot name="actions"></slot>
    </div>
  </div>

  <!-- Variante "now-playing" : Lecteur avec contrôles -->
  <div v-else-if="variant === 'now-playing'" class="now-playing">
    <!-- Background image - très zoomée et blurrée -->
    <div class="station-art-background">
      <img v-if="station.favicon" :src="getFaviconUrl(station.favicon)" alt=""
        class="background-station-favicon" />
    </div>

    <div class="station-art">
      <img v-if="station.favicon" :src="getFaviconUrl(station.favicon)" alt="Station logo"
        class="current-station-favicon" @error="handleImageError" />
      <img :src="placeholderImg" :alt="t('audioSources.radioSource.stationNoImage')" class="placeholder-logo"
        :class="{ visible: !station.favicon || imageError }" />
    </div>

    <div class="station-info">
      <p class="station-name display-1">{{ station.name }}</p>
      <p class="station-meta text-mono">{{ station.country }} • {{ station.genre }}</p>
    </div>

    <div v-if="showControls" class="controls-wrapper">
      <CircularIcon :icon="station.is_favorite ? 'heart' : 'heartOff'" variant="overlay"
        @click="$emit('favorite')" />
      <CircularIcon :icon="isPlaying ? 'stop' : 'play'" variant="overlay" @click="$emit('play')" />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useI18n } from '@/services/i18n';
import CircularIcon from '@/components/ui/CircularIcon.vue';
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

function getFaviconUrl(faviconUrl) {
  // Pas de favicon
  if (!faviconUrl) {
    return '';
  }

  // Image locale déjà hébergée par le backend
  if (faviconUrl.startsWith('/api/radio/images/')) {
    return faviconUrl;
  }

  // Image externe : utiliser le proxy backend pour éviter CORS
  return `/api/radio/favicon?url=${encodeURIComponent(faviconUrl)}`;
}

function handleImageError() {
  imageError.value = true;
}
</script>

<style scoped>
/* === VARIANTE "IMAGE" : Image seule pour grille === */
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

.station-image:hover {
  transform: scale(1.02);
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

/* Loading overlay pour les stations en mode image */
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

/* Spinner pour mode image (overlay) */
.loading-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid rgba(255, 255, 255, 0.2);
  border-top-color: var(--color-brand);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

/* === VARIANTE "CARD" : Layout horizontal === */
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

.station-card:hover {
  background: var(--color-background);
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

/* Actions wrapper pour boutons personnalisés */
.actions-wrapper {
  display: flex;
  flex-direction: row;
  gap: var(--space-02);
  align-items: center;
  flex-shrink: 0;
}

/* Spinner pour mode card (petit, à la place du bouton) */
.loading-spinner-small {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-brand);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

/* Réduire légèrement l'opacité des stations en loading */
.station-image.loading,
.station-card.loading {
  opacity: 0.9;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* === VARIANTE "NOW-PLAYING" : Lecteur avec contrôles === */

/* Background image - très zoomée et blurrée (commun Desktop + Mobile) */
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

/* Desktop : Layout vertical */
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
  filter: blur(60px);
  opacity: 0.72;
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
  flex-direction: row;
  flex-wrap: nowrap;
  gap: var(--space-02);
  justify-content: space-between;
  z-index: 1;
}

/* Mobile : Layout horizontal sticky en bas */
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
    padding: var(--space-03);
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
}
</style>
