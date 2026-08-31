<template>
  <div v-press="clickable" class="podcast-card" :class="{ 'is-subscribed': isSubscribed, clickable }"
    @click="handleCardClick">
    <LazyImage :src="podcast.image_url" :fallback="podcastPlaceholder" :alt="podcast.name" lazy class="card-image">
      <div v-if="isLoading" class="card-loading-overlay">
        <LoadingSpinner :size="48" />
      </div>
    </LazyImage>

    <div class="card-info">
      <span v-if="tagText" class="podcast-tag text-mono">{{ tagText }}</span>
      <h3 class="podcast-name heading-4">{{ podcast.name }}</h3>
      <p v-if="podcast.publisher" class="podcast-publisher text-mono">{{ podcast.publisher }}</p>
    </div>

    <div v-if="showActions" class="card-actions" @click.stop>
      <Button v-if="!isSubscribed" variant="brand" size="small" @click="emit('subscribe', podcast.uuid)">
        {{ t('podcasts.subscribe') }}
      </Button>
      <Button v-else variant="background-strong" size="small" @click="emit('unsubscribe', podcast.uuid)">
        {{ t('podcasts.unsubscribe') }}
      </Button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from '@/services/i18n'
import Button from '@/components/ui/Button.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import LazyImage from '@/components/ui/LazyImage.vue'
import { podcastPlaceholder } from '@/constants/placeholders'

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
  clickable: {
    type: Boolean,
    default: true
  },
  isLoading: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['select', 'subscribe', 'unsubscribe'])

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
    emit('select', props.podcast)
  }
}
</script>

<style scoped>
.podcast-card {
  display: flex;
  flex-direction: column;
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  overflow: hidden;
  padding: var(--space-03) var(--space-03) var(--space-04) var(--space-03);
  gap: var(--space-03);
  transition: transform var(--transition-fast), box-shadow var(--transition-fast);
}

.podcast-card.clickable {
  cursor: pointer;
}

.card-image {
  aspect-ratio: 1;
  border-radius: var(--radius-02);
}

.card-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.podcast-tag {
  color: var(--color-brand);
  margin: 0;
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
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-actions {
  padding: 0;
}
</style>
