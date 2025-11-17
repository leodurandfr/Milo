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
      <h3 class="podcast-name">{{ podcast.name }}</h3>
      <p v-if="podcast.publisher" class="podcast-publisher">{{ podcast.publisher }}</p>
      <p v-if="showEpisodeCount" class="episode-count">
        {{ podcast.total_episodes || 0 }} épisodes
      </p>
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
  background: var(--color-background-subtle);
  border-radius: var(--radius-04);
  overflow: hidden;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
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
  background: var(--color-accent);
  color: white;
  font-size: var(--font-size-xs);
  font-weight: var(--font-weight-semibold);
  padding: var(--space-01) var(--space-02);
  border-radius: var(--radius-02);
}

.badge-subscribed {
  position: absolute;
  bottom: var(--space-02);
  right: var(--space-02);
  background: var(--color-success);
  color: white;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.card-info {
  padding: var(--space-03);
}

.podcast-name {
  font-size: var(--font-size-sm);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text);
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.podcast-publisher {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  margin: var(--space-01) 0 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.episode-count {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  margin: var(--space-01) 0 0;
}

.card-actions {
  padding: 0 var(--space-03) var(--space-03);
}

.is-subscribed {
  border: 2px solid var(--color-success);
}
</style>
