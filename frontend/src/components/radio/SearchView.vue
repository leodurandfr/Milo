<template>
  <div class="search-view fade-in">
    <!-- Search filters -->
    <div class="filters">
      <InputText
        v-model="searchQuery"
        :placeholder="t('audioSources.radioSource.searchPlaceholder')"
        size="small"
        icon="search"
        :icon-size="24"
        @update:modelValue="$emit('search')"
      />
      <Dropdown
        v-model="countryFilter"
        :options="countryOptions"
        size="small"
        @change="$emit('search')"
      />
      <Dropdown
        v-model="genreFilter"
        :options="genreOptions"
        size="small"
        @change="$emit('search')"
      />
    </div>

    <!-- Loading state -->
    <div
      v-if="transitionState === 'loading' || isLoading"
      class="message-wrapper"
      :class="{
        'message-fading-in': messageState === 'fading-in',
        'message-fading-out': messageState === 'fading-out'
      }"
    >
      <div class="message-content">
        <SvgIcon name="radio" :size="96" color="var(--color-background-medium-16)" />
        <p class="text-mono">{{ t('audioSources.radioSource.loadingStations') }}</p>
      </div>
    </div>

    <!-- Error state -->
    <div
      v-else-if="hasError && searchResults.length === 0"
      class="message-wrapper"
      :class="{
        'message-fading-in': messageState === 'fading-in',
        'message-fading-out': messageState === 'fading-out'
      }"
    >
      <div class="message-content">
        <SvgIcon name="stop" :size="96" color="var(--color-background-medium-16)" />
        <p class="text-mono">{{ t('audioSources.radioSource.connectionError') }}</p>
        <p class="text-body-small" style="color: var(--color-text-secondary);">
          {{ t('audioSources.radioSource.cannotLoadStations') }}
        </p>
        <Button variant="toggle" :active="false" @click="$emit('retry')">
          {{ t('audioSources.radioSource.retry') }}
        </Button>
      </div>
    </div>

    <!-- Empty state -->
    <div
      v-else-if="searchResults.length === 0"
      class="message-wrapper"
      :class="{
        'message-fading-in': messageState === 'fading-in',
        'message-fading-out': messageState === 'fading-out'
      }"
    >
      <div class="message-content">
        <SvgIcon name="radio" :size="96" color="var(--color-background-medium-16)" />
        <p class="text-mono">{{ t('audioSources.radioSource.noStationsFound') }}</p>
      </div>
    </div>

    <!-- Search results grid -->
    <div
      v-else-if="transitionState !== 'loading'"
      class="search-grid"
      :class="{
        'transition-fading-out': transitionState === 'fading-out',
        'transition-fading-in': transitionState === 'fading-in'
      }"
    >
      <StationCard
        v-for="station in searchResults"
        :key="`search-${station.id}`"
        :station="station"
        variant="card"
        :is-active="currentStation?.id === station.id"
        :is-playing="currentStation?.id === station.id && isPlaying"
        :is-loading="bufferingStationId === station.id"
        :show-controls="true"
        :show-country="true"
        @click="$emit('play-station', station.id)"
        @play="$emit('play-station', station.id)"
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
import InputText from '@/components/ui/InputText.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import Button from '@/components/ui/Button.vue'

const { t } = useI18n()
const radioStore = useRadioStore()

const props = defineProps({
  /**
   * Available country options for filter
   */
  countryOptions: {
    type: Array,
    required: true
  },

  /**
   * Available genre options for filter
   */
  genreOptions: {
    type: Array,
    required: true
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
   * Loading state
   */
  isLoading: {
    type: Boolean,
    default: false
  },

  /**
   * Error state
   */
  hasError: {
    type: Boolean,
    default: false
  },

  /**
   * Transition animation state
   */
  transitionState: {
    type: String,
    default: 'idle'
  },

  /**
   * Message animation state
   */
  messageState: {
    type: String,
    default: 'idle'
  }
})

defineEmits(['search', 'retry', 'play-station'])

// Two-way binding for filters (v-model on store properties)
const searchQuery = computed({
  get: () => radioStore.searchQuery,
  set: (value) => { radioStore.searchQuery = value }
})

const countryFilter = computed({
  get: () => radioStore.countryFilter,
  set: (value) => { radioStore.countryFilter = value }
})

const genreFilter = computed({
  get: () => radioStore.genreFilter,
  set: (value) => { radioStore.genreFilter = value }
})

// Search results sorted by popularity
const searchResults = computed(() => {
  const stations = radioStore.displayedStations || []
  return [...stations].sort((a, b) => {
    const clicksA = a.votes || 0
    const clicksB = b.votes || 0
    return clicksB - clicksA // Higher clicks first
  })
})
</script>

<style scoped>
.search-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  flex: 1;
  min-height: 0;
}

/* Filters */
.filters {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-01);
  color: var(--color-text-secondary);
}

/* Message wrapper (loading, error, empty states) */
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

.message-content .text-mono,
.message-content .heading-2 {
  text-align: center;
  color: var(--color-text-secondary);
}

.message-content .text-body-small {
  text-align: center;
}

/* Mobile: increase height for better visibility */
@media (max-aspect-ratio: 4/3) {
  .message-content {
    min-height: 364px;
  }

  .filters {
    grid-template-columns: repeat(1, 1fr);
  }
}

/* Search results grid */
.search-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-01);
  padding-bottom: var(--space-07);
}

/* Mobile: single column */
@media (max-aspect-ratio: 4/3) {
  .search-grid {
    grid-template-columns: 1fr;
  }
}

/* Transition animations */
@keyframes fadeOutUp {
  from {
    opacity: 1;
    transform: translateY(0);
  }
  to {
    opacity: 0;
    transform: translateY(-20px);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.search-grid.transition-fading-out {
  animation: fadeOutUp 300ms ease-out forwards;
}

.search-grid.transition-fading-in {
  animation: fadeInUp 400ms ease-out forwards;
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
