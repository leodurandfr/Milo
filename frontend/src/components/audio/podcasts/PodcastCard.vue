<template>
  <div
    class="podcast-card"
    :class="{ 'is-subscribed': isSubscribed, 'has-new': hasNewEpisodes }"
    @click="$emit('select', podcast)"
  >
    <div class="card-image">
      <img
        :src="imageUrl"
        :alt="podcast.name"
        loading="lazy"
        @error="handleImageError"
      />
      <div v-if="hasNewEpisodes" class="badge-new">Nouveau</div>
      <div v-if="isSubscribed" class="badge-subscribed">
        <Icon name="heart" :size="16" />
      </div>
    </div>

    <div class="card-info">
      <span v-if="position" class="podcast-position text-mono">{{ position }}</span>
      <h3 class="podcast-name text-body-small">{{ podcast.name }}</h3>
      <p v-if="podcast.publisher" class="podcast-publisher text-mono">{{ podcast.publisher }}</p>
    </div>

    <div v-if="showActions" class="card-actions" @click.stop>
      <Button
        v-if="!isSubscribed"
        variant="secondary"
        size="small"
        @click="$emit('subscribe', podcast.uuid)"
      >
        S'abonner
      </Button>
      <Button
        v-else
        variant="secondary"
        size="small"
        @click="$emit('unsubscribe', podcast.uuid)"
      >
        Désabonner
      </Button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import Icon from '@/components/ui/Icon.vue'
import Button from '@/components/ui/Button.vue'

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
  showEpisodeCount: {
    type: Boolean,
    default: true
  },
  hasNewEpisodes: {
    type: Boolean,
    default: false
  }
})

defineEmits(['select', 'subscribe', 'unsubscribe'])

const fallbackImage = '/default-podcast.png'
const imageError = ref(false)

const imageUrl = computed(() => {
  if (imageError.value) return fallbackImage
  return props.podcast.image_url || fallbackImage
})

const isSubscribed = computed(() => {
  return props.podcast.is_subscribed || false
})

function handleImageError() {
  imageError.value = true
}
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

.podcast-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
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

.badge-subscribed {
  position: absolute;
  bottom: var(--space-02);
  right: var(--space-02);
  background: var(--color-success);
  color: var(--color-text-contrast);
  width: 24px;
  height: 24px;
  border-radius: var(--radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
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

.podcast-position {
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

.is-subscribed {
  border: 2px solid var(--color-success);
}
</style>
