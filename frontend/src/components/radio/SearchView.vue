<template>
  <div class="search-view">
    <!-- Filters -->
    <div class="filters-bar">
      <InputText
        v-model="searchQuery"
        :placeholder="t('audioSources.radioSource.searchPlaceholder')"
        size="small"
        icon="search"
        :icon-size="24"
        @update:modelValue="onSearchInput"
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

    <!-- Results with transitions -->
    <div class="results">
      <Transition name="fade-slide" mode="out-in">
        <!-- Loading state -->
        <div v-if="isLoading" key="loading" class="message-wrapper">
          <MessageContent loading>
            <p class="heading-2">{{ t('audioSources.radioSource.loadingStations') }}</p>
          </MessageContent>
        </div>

        <!-- Error state -->
        <div v-else-if="hasError && searchResults.length === 0" key="error" class="message-wrapper">
          <MessageContent>
            <SvgIcon name="stop" :size="64" color="var(--color-background-medium-16)" />
            <p class="heading-2">{{ t('audioSources.radioSource.connectionError') }}</p>
            <p class="text-mono">{{ t('audioSources.radioSource.cannotLoadStations') }}</p>
            <Button variant="background-strong" @click="$emit('retry')">
              {{ t('audioSources.radioSource.retry') }}
            </Button>
          </MessageContent>
        </div>

        <!-- Empty state -->
        <div v-else-if="searchResults.length === 0" key="empty" class="message-wrapper">
          <MessageContent>
            <SvgIcon name="radio" :size="64" color="var(--color-background-medium-16)" />
            <p class="heading-2">{{ t('audioSources.radioSource.noStationsFound') }}</p>
          </MessageContent>
        </div>

        <!-- Search results -->
        <div v-else key="results" class="results-content">
          <StationCard
            v-for="station in searchResults"
            :key="`search-${station.id}`"
            :station="station"
            variant="card"
            :is-playing="currentStation?.id === station.id && isPlaying"
            :is-loading="bufferingStationId === station.id"
            :show-controls="true"
            :show-country="true"
            @click="$emit('play-station', station.id)"
            @play="$emit('play-station', station.id)"
          />
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRadioStore } from '@/stores/radioStore'
import { useI18n } from '@/services/i18n'
import StationCard from './StationCard.vue'
import SvgIcon from '@/components/ui/SvgIcon.vue'
import InputText from '@/components/ui/InputText.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import Button from '@/components/ui/Button.vue'
import MessageContent from '@/components/ui/MessageContent.vue'

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
  }
})

const emit = defineEmits(['search', 'retry', 'play-station'])

// Debounce timer for search input
const searchDebounceTimer = ref(null)

function onSearchInput() {
  if (searchDebounceTimer.value) {
    clearTimeout(searchDebounceTimer.value)
  }
  searchDebounceTimer.value = setTimeout(() => {
    emit('search')
  }, 400)
}

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
}

/* Filters */
.filters-bar {
  display: flex;
  gap: var(--space-02);
  align-items: center;
  flex-wrap: wrap;
  color: var(--color-text-secondary);
  min-height: 48px;
}

.filters-bar > * {
  flex: 1;
  min-width: 220px;
}

/* Results container */
.results {
  display: flex;
  flex-direction: column;
}

/* Results content (grid) */
.results-content {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-01);
}

/* Message wrapper (loading, error, empty states) */
.message-wrapper {
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
}

/* Mobile */
@media (max-aspect-ratio: 4/3) {
  .filters-bar {
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;

    /* Full-bleed: compense le padding du parent AudioSourceLayout */
    margin-left: calc(-1 * var(--space-05));
    margin-right: calc(-1 * var(--space-05));
    padding-left: var(--space-05);
    padding-right: var(--space-05);

    /* Masquer la scrollbar */
    scrollbar-width: none;
    -ms-overflow-style: none;
  }

  .filters-bar::-webkit-scrollbar {
    display: none;
  }

  .filters-bar > * {
    flex-shrink: 0;
  }

  .results-content {
    grid-template-columns: 1fr;
  }
}
</style>
