<template>
  <div
    class="podcast-card"
    :class="{ 'is-subscribed': isSubscribed, 'has-new': hasNewEpisodes }"
    @click="$emit('select', podcast)"
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
      <div v-if="hasNewEpisodes" class="badge-new">{{ t('podcasts.new') }}</div>
    </div>

    <div class="card-info">
      <span v-if="tagText" class="podcast-tag text-mono">{{ tagText }}</span>
      <h3 class="podcast-name text-body-small">{{ podcast.name }}</h3>
      <p v-if="podcast.publisher" class="podcast-publisher text-mono">{{ podcast.publisher }}</p>
    </div>

    <div v-if="showActions" class="card-actions" @click.stop>
      <Button
        v-if="!isSubscribed"
        type="background-strong"
        size="small"
        @click="$emit('subscribe', podcast.uuid)"
      >
        {{ t('podcasts.subscribe') }}
      </Button>
      <Button
        v-else
        type="background-strong"
        size="small"
        @click="$emit('unsubscribe', podcast.uuid)"
      >
        {{ t('podcasts.unsubscribe') }}
      </Button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue'
import { useI18n } from '@/services/i18n'
import Button from '@/components/ui/Button.vue'
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
  hasNewEpisodes: {
    type: Boolean,
    default: false
  }
})

defineEmits(['select', 'subscribe', 'unsubscribe'])

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
.podcast-card {
  display: flex;
  flex-direction: column;
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
  padding: var(--space-03);
  gap: var(--space-03);
}



.podcast-card:active {
  transform: translateY(0);
}

.card-image {
  position: relative;
  aspect-ratio: 1;
  overflow: hidden;
  border-radius: var(--radius-03);
}

.card-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  position: absolute;
  inset: 0;
}

.card-image .main-image {
  opacity: 0;
  transition: opacity var(--transition-normal);
  z-index: 1;
}

.card-image .main-image.loaded {
  opacity: 1;
}

.card-image .placeholder-image {
  opacity: 1;
  z-index: 0;
}

.badge-new {
  position: absolute;
  top: var(--space-02);
  right: var(--space-02);
  background: var(--color-brand);
  color: var(--color-text-contrast);
  font-family: 'Neue Montreal Medium';
  font-size: var(--font-size-mono);
  font-weight: 500;
  padding: var(--space-01) var(--space-02);
  border-radius: var(--radius-02);
}

.card-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.podcast-name {
  color: var(--color-text);
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.podcast-tag {
  color: var(--color-brand);
  margin: 0;
}

.podcast-publisher {
  color: var(--color-text-secondary);
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-actions {
  padding: 0;
}
</style>
