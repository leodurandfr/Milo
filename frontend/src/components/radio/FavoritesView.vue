<template>
  <div class="favorites-view">
    <Transition name="fade-slide" mode="out-in">
      <!-- Empty state -->
      <div v-if="!isLoading && favoriteStations.length === 0" key="empty" class="message-wrapper">
        <MessageContent>
          <SvgIcon name="radio" :size="64" color="var(--color-background-medium-16)" />
          <p class="heading-2">{{ t('audioSources.radioSource.noFavorites') }}</p>
        </MessageContent>
      </div>

      <!-- Favorites grid -->
      <div v-else key="favorites" class="favorites-grid">
        <StationCard
          v-for="station in favoriteStations"
          :key="`fav-${station.id}`"
          :station="station"
          variant="image"
          :is-active="currentStation?.id === station.id"
          :is-playing="currentStation?.id === station.id && isPlaying"
          :is-loading="bufferingStationId === station.id"
          @click="$emit('play-station', station.id)"
        />
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRadioStore } from '@/stores/radioStore'
import { useI18n } from '@/services/i18n'
import StationCard from './StationCard.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'
import MessageContent from '@/components/ui/MessageContent.vue'

const { t } = useI18n()
const radioStore = useRadioStore()

const props = defineProps({
  /**
   * Currently active/playing station
   */
  currentStation: {
    type: Object,
    default: null
  },

  /**
   * Whether audio is currently playing
   */
  isPlaying: {
    type: Boolean,
    default: false
  },

  /**
   * ID of the station currently buffering
   */
  bufferingStationId: {
    type: [String, Number],
    default: null
  },

  /**
   * Whether stations are currently loading
   */
  isLoading: {
    type: Boolean,
    default: false
  }
})

defineEmits(['play-station'])

// Favorite stations sorted alphabetically
const favoriteStations = computed(() => {
  const stations = radioStore.favoriteStations || []
  return [...stations].sort((a, b) => {
    const nameA = (a.name || '').toLowerCase()
    const nameB = (b.name || '').toLowerCase()
    return nameA.localeCompare(nameB)
  })
})
</script>

<style scoped>
.favorites-view {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

/* Message wrapper (empty state) */
.message-wrapper {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
}

/* Favorites grid */
.favorites-grid {
  display: grid;
  gap: var(--space-03);
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

/* Mobile: Responsive adaptations */
@media (max-aspect-ratio: 4/3) {
  .favorites-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}
</style>
