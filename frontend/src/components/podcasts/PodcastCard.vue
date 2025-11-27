<template>
  <!-- Card variant (default) -->
  <div
    v-if="variant === 'card'"
    class="podcast-card variant-card"
    :class="{ 'is-subscribed': isSubscribed, clickable, contrast }"
    @click="handleCardClick"
  >
    <div class="card-image">
      <img
        ref="imgRef"
        :src="imageUrl"
        :alt="podcast.name"
        loading="lazy"
        @load="handleImageLoad"
        @error="handleImageError"
        :class="{ loaded: imageLoaded }"
        class="main-image"
      />
      <img
        v-if="!imageLoaded && !imageError"
        :src="podcastPlaceholder"
        class="placeholder-image"
        alt=""
      />
      <!-- Loading overlay -->
      <div v-if="isLoading" class="loading-overlay">
        <LoadingSpinner :size="48" />
      </div>
    </div>

    <div class="card-info">
      <span v-if="tagText" class="podcast-tag text-mono">{{ tagText }}</span>
      <h3 class="podcast-name heading-4">{{ podcast.name }}</h3>
      <p v-if="podcast.publisher" class="podcast-publisher text-mono">{{ podcast.publisher }}</p>
    </div>

    <div v-if="showActions" class="card-actions" @click.stop>
      <Button
        v-if="!isSubscribed"
        variant="brand"
        size="small"
        @click="$emit('subscribe', podcast.uuid)"
      >
        {{ t('podcasts.subscribe') }}
      </Button>
      <Button
        v-else
        :variant="contrast ? 'on-dark' : 'background-strong'"
        size="small"
        @click="$emit('unsubscribe', podcast.uuid)"
      >
        {{ t('podcasts.unsubscribe') }}
      </Button>
    </div>
  </div>

  <!-- Row variant -->
  <div
    v-else
    class="podcast-card variant-row"
    :class="{ 'is-subscribed': isSubscribed, clickable, contrast }"
    @click="handleCardClick"
  >
    <div class="row-image">
      <img
        ref="imgRef"
        :src="imageUrl"
        :alt="podcast.name"
        loading="lazy"
        @load="handleImageLoad"
        @error="handleImageError"
        :class="{ loaded: imageLoaded }"
        class="main-image"
      />
      <img
        v-if="!imageLoaded && !imageError"
        :src="podcastPlaceholder"
        class="placeholder-image"
        alt=""
      />

      <!-- Loading overlay -->
      <div v-if="isLoading" class="loading-overlay">
        <LoadingSpinner :size="32" />
      </div>
    </div>

    <div class="row-content">
      <div class="row-info">
        <h3 class="podcast-name heading-3">{{ podcast.name }}</h3>
        <p v-if="podcast.publisher || podcast.author" class="podcast-publisher text-mono">
          {{ podcast.publisher || podcast.author }}
        </p>
        <p class="podcast-meta text-mono">
          {{ podcast.total_episodes }} {{ t('podcasts.episodesCount2') }}
        </p>
      </div>

      <div v-if="showActions" class="row-actions" @click.stop>
        <Button
          v-if="!isSubscribed"
          variant="brand"
          @click="$emit('subscribe', podcast.uuid)"
        >
          {{ t('podcasts.subscribe') }}
        </Button>
        <Button
          v-else
          :variant="contrast ? 'on-dark' : 'background-strong'"
          @click="$emit('unsubscribe', podcast.uuid)"
        >
          {{ t('podcasts.unsubscribe') }}
        </Button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useI18n } from '@/services/i18n'
import Button from '@/components/ui/Button.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import podcastPlaceholder from '@/assets/podcasts/podcast-placeholder.jpg'

const { t } = useI18n()

const props = defineProps({
  podcast: {
    type: Object,
    required: true
  },
  position: {
    type: Number,
    default: null
  },
  showActions: {
    type: Boolean,
    default: false
  },
  variant: {
    type: String,
    default: 'card',
    validator: (value) => ['card', 'row'].includes(value)
  },
  clickable: {
    type: Boolean,
    default: true
  },
  contrast: {
    type: Boolean,
    default: false
  },
  isLoading: {
    type: Boolean,
    default: false
  }
})

const $emit = defineEmits(['select', 'subscribe', 'unsubscribe'])

const imageError = ref(false)
const imageLoaded = ref(false)
const imgRef = ref(null)

const imageUrl = computed(() => {
  if (imageError.value) return podcastPlaceholder
  return props.podcast.image_url || podcastPlaceholder
})

const isSubscribed = computed(() => {
  return props.podcast.is_subscribed || false
})

const tagText = computed(() => {
  const hasPosition = props.position !== null
  const subscribed = isSubscribed.value

  if (hasPosition && subscribed) {
    return `${props.position} · ${t('podcasts.subscribed').toUpperCase()}`
  } else if (subscribed) {
    return t('podcasts.subscribed').toUpperCase()
  } else if (hasPosition) {
    return props.position.toString()
  }
  return null
})

function handleCardClick() {
  if (props.clickable) {
    $emit('select', props.podcast)
  }
}

function handleImageError() {
  imageError.value = true
}

function handleImageLoad() {
  imageLoaded.value = true
}

onMounted(() => {
  if (imgRef.value && imgRef.value.complete && imgRef.value.naturalHeight !== 0) {
    imageLoaded.value = true
  }
})
</script>

<style scoped>
/* === SHARED STYLES === */
.podcast-card {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  overflow: hidden;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.podcast-card.clickable {
  cursor: pointer;
}

.podcast-card:active {
  transform: translateY(0);
}

.podcast-name {
  color: var(--color-text);
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.podcast-publisher {
  color: var(--color-text-secondary);
  margin: 0;
}

/* === CARD VARIANT === */
.variant-card {
  display: flex;
  flex-direction: column;
  padding: var(--space-03);
  gap: var(--space-03);
}

.variant-card .card-image {
  position: relative;
  aspect-ratio: 1;
  overflow: hidden;
  border-radius: var(--radius-02);
}

.variant-card .card-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  position: absolute;
  inset: 0;
}

.variant-card .card-image .main-image {
  opacity: 0;
  transition: opacity var(--transition-normal);
  z-index: 1;
}

.variant-card .card-image .main-image.loaded {
  opacity: 1;
}

.variant-card .card-image .placeholder-image {
  opacity: 1;
  z-index: 0;
}

.variant-card .card-image .loading-overlay {
  position: absolute;
  inset: 0;
  background: var(--color-background-contrast-32);
  border-radius: var(--radius-02);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
  color: var(--color-text-contrast);
}

.variant-card .card-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.variant-card .podcast-tag {
  color: var(--color-brand);
  margin: 0;
}

.variant-card .podcast-publisher {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.variant-card .card-actions {
  padding: 0;
}

/* === ROW VARIANT === */
.variant-row {
  display: flex;
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
  gap: var(--space-03);
}

.variant-row .row-image {
  position: relative;
  width: 128px;
  height: 128px;
  flex-shrink: 0;
  border-radius: var(--radius-02);
  overflow: hidden;
}

.variant-row .row-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  position: absolute;
  inset: 0;
}

.variant-row .row-image .main-image {
  opacity: 0;
  transition: opacity var(--transition-normal);
  z-index: 1;
}

.variant-row .row-image .main-image.loaded {
  opacity: 1;
}

.variant-row .row-image .placeholder-image {
  opacity: 1;
  z-index: 0;
}

.variant-row .row-image .loading-overlay {
  position: absolute;
  inset: 0;
  background: var(--color-background-contrast-32);
  border-radius: var(--radius-02);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
  color: var(--color-text-contrast);
}

.variant-row .row-content {
  flex: 1;
  min-width: 0;
  display: flex;
  gap: var(--space-03);
  align-items: center;
}

.variant-row .row-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.variant-row .podcast-meta {
  color: var(--color-text-secondary);
  margin: 0;
}

.variant-row .row-actions {
  flex-shrink: 0;
}

/* === ROW VARIANT MOBILE === */
@media (max-aspect-ratio: 4/3) {
  .variant-row .row-image {
    width: 64px;
    height: 64px;
  }

  .variant-row .podcast-meta {
    display: none;
  }
}

/* === CONTRAST VARIANT === */
.podcast-card.contrast {
  background: var(--color-background-contrast);
}

.podcast-card.contrast .podcast-name {
  color: var(--color-text-contrast);
}

.podcast-card.contrast .podcast-publisher {
  color: var(--color-text-contrast-50);
}

.podcast-card.contrast .podcast-tag {
  color: var(--color-brand);
}

.podcast-card.contrast .podcast-meta {
  color: var(--color-text-contrast-50);
}
</style>
