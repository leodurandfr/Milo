<template>
  <div class="favorites-view">
    <div class="swap-stack">
      <Transition name="fade-slide">
        <!-- Loading state: skeleton grid while favorites load -->
        <div v-if="isLoading || !radioStore.favoritesInitialized" key="loading" class="favorites-grid">
          <SkeletonStationCard v-for="i in 16" :key="`skeleton-${i}`" />
        </div>

        <!-- Empty state: only after initialization confirms no favorites -->
        <MessageContent v-else-if="favoriteStations.length === 0" key="empty" icon="radio" :title="t('audioSources.radioSource.noFavorites')" />

        <!-- Favorites grid -->
        <div v-else key="loaded" class="favorites-grid">
          <StationCard
            v-for="station in favoriteStations"
            :key="`fav-${station.id}`"
            :station="station"
            variant="image"
            :is-playing="currentStation?.id === station.id && isPlaying"
            :is-loading="bufferingStationId === station.id"
            @click="$emit('play-station', station.id)"
          />
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRadioStore } from '@/stores/radioStore'
import { useI18n } from '@/services/i18n'
import StationCard from './StationCard.vue'
import SkeletonStationCard from './SkeletonStationCard.vue'
import MessageContent from '@/components/ui/MessageContent.vue'

const { t } = useI18n()
const radioStore = useRadioStore()

defineProps({
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

// Favorite stations (ordered by the backend — see radioStore.enrichedFavorites)
const favoriteStations = computed(() => radioStore.favoriteStations || [])
</script>

<style scoped>
.favorites-view {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

/* Favorites grid */
.favorites-grid {
  display: grid;
  gap: var(--space-03);
  grid-template-columns: repeat(var(--card-grid-columns), minmax(0, 1fr));
}
</style>
