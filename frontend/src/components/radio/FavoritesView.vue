<template>
  <div class="favorites-view">
    <!-- Empty state -->
    <div v-if="!isLoading && favoriteStations.length === 0" class="message-wrapper" :class="{
      'message-fading-in': messageState === 'fading-in',
      'message-fading-out': messageState === 'fading-out'
    }">
      <div class="message-content">
        <SvgIcon name="radio" :size="96" color="var(--color-background-medium-16)" />
        <p class="text-mono">{{ t('audioSources.radioSource.noFavorites') }}</p>
      </div>
    </div>

    <!-- Favorites grid -->
    <div v-else class="favorites-grid fade-in" :class="{
      'has-now-playing': hasNowPlaying
    }">
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
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRadioStore } from '@/stores/radioStore'
import { useI18n } from '@/services/i18n'
import StationCard from './StationCard.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'

const { t } = useI18n()
const radioStore = useRadioStore()

const props = defineProps({
  /**
   * Whether a station is currently playing (to adjust layout)
   */
  hasNowPlaying: {
    type: Boolean,
    default: false
  },

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
   * Message animation state
   */
  messageState: {
    type: String,
    default: 'idle'
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

.message-content {
  display: flex;
  min-height: 232px;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  padding: var(--space-05);
}

.message-content .text-mono {
  text-align: center;
  color: var(--color-text-secondary);
}

/* Mobile: increase height for better visibility */
@media (max-aspect-ratio: 4/3) {
  .message-content {
    min-height: 364px;
  }
}

/* Favorites grid */
.favorites-grid {
  display: grid;
  gap: var(--space-03);
  grid-template-columns: repeat(4, minmax(0, 1fr));
  padding-bottom: var(--space-07);
}

/* Mobile: Responsive adaptations */
@media (max-aspect-ratio: 4/3) {
  .favorites-grid {
    grid-template-columns: repeat(3, 1fr);
  }

  .favorites-grid.has-now-playing {
    padding-bottom: 144px;
  }
}

/* Message animations */
@keyframes messageFadeIn {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes messageFadeOut {
  from {
    opacity: 1;
    transform: translateY(0);
  }
  to {
    opacity: 0;
    transform: translateY(-20px);
  }
}

.message-wrapper.message-fading-in {
  animation: messageFadeIn 300ms ease-out forwards;
}

.message-wrapper.message-fading-out {
  animation: messageFadeOut 300ms ease-out forwards;
}
</style>
