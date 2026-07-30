<template>
  <div class="search-view">
    <!-- Filters -->
    <div class="filters-bar">
      <InputText
        v-model="searchQuery"
        :placeholder="t('audioSources.radioSource.searchPlaceholder')"
        variant="background-neutral"
        icon="search"
        :icon-size="24"
        @update:modelValue="onSearchInput"
        @submit="onSearchSubmit"
      />
      <Dropdown
        v-model="countryFilter"
        :options="countryOptions"
        variant="background-neutral"
        @change="$emit('search')"
      />
      <Dropdown
        v-model="genreFilter"
        :options="genreOptions"
        variant="background-neutral"
        @change="$emit('search')"
      />
    </div>

    <!-- Results -->
    <div class="results">
      <!-- Loading state -->
      <MessageContent v-if="isLoading" loading :loading-delay="0" :title="t('audioSources.radioSource.loadingStations')" />

      <!-- Network error state -->
      <MessageContent
        v-else-if="networkError && searchResults.length === 0"
        icon="network"
        :title="t('audioSources.radioSource.noInternet')"
        :subtitle="t('audioSources.radioSource.noInternetHint')"
        :cta-label="t('audioSources.radioSource.retry')"
        cta-variant="background-strong"
        :cta-click="() => $emit('retry')"
      />

      <!-- Generic error state -->
      <MessageContent
        v-else-if="hasError && searchResults.length === 0"
        icon="stop"
        :title="t('audioSources.radioSource.connectionError')"
        :subtitle="t('audioSources.radioSource.cannotLoadStations')"
        :cta-label="t('audioSources.radioSource.retry')"
        cta-variant="background-strong"
        :cta-click="() => $emit('retry')"
      />

      <!-- Minimum characters message -->
      <MessageContent v-else-if="showMinCharMessage" icon="search" :title="t('audioSources.radioSource.minCharactersRequired')" />

      <!-- Empty state -->
      <MessageContent v-else-if="searchResults.length === 0" icon="radio" :title="t('audioSources.radioSource.noStationsFound')" />

      <!-- Search results -->
      <div v-else class="results-content fade-in">
        <StationCard
          v-for="station in searchResults"
          :key="`search-${station.id}`"
          :station="station"
          variant="card"
          :is-playing="currentStation?.id === station.id && isPlaying"
          :is-loading="bufferingStationId === station.id"
          @click="$emit('play-station', station.id)"
          @play="$emit('play-station', station.id)"
        />

        <!-- Sentinel for infinite scroll -->
        <div
          v-if="hasMoreStations"
          ref="scrollSentinel"
          class="scroll-sentinel"
        >
          <Button variant="background-strong" disabled loading>
            {{ t('audioSources.radioSource.loadingStations') }}
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRadioStore } from '@/stores/radioStore'
import { useDebounce } from '@/composables/useDebounce'
import { useInfiniteScroll } from '@/composables/useInfiniteScroll'
import { useI18n } from '@/services/i18n'
import StationCard from './StationCard.vue'
import InputText from '@/components/ui/InputText.vue'
import Button from '@/components/ui/Button.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import MessageContent from '@/components/ui/MessageContent.vue'

const { t } = useI18n()
const radioStore = useRadioStore()

defineProps({
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
   * Network error (DNS/connectivity)
   */
  networkError: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['search', 'retry', 'play-station'])

// Minimum characters required for text search
const MIN_SEARCH_CHARS = 3

const showMinCharMessage = ref(false)

// Check if any filter (country or genre) is active
function hasActiveFilters() {
  return radioStore.countryFilter !== '' || radioStore.genreFilter !== ''
}

const { debounced: debouncedSearch, cancel: cancelSearch } = useDebounce((query) => {
  if (query.length === 0) {
    emit('search')
  } else {
    showMinCharMessage.value = false
    emit('search')
  }
})

function onSearchInput() {
  const query = radioStore.searchQuery.trim()

  // Hide message only when returning to top stations (empty field)
  if (query.length === 0) {
    showMinCharMessage.value = false
    debouncedSearch(query)
  } else if (query.length >= MIN_SEARCH_CHARS) {
    debouncedSearch(query)
  }
  // For 1-2 chars: do nothing (wait for more input or Enter key)
}

function onSearchSubmit() {
  cancelSearch()

  const query = radioStore.searchQuery.trim()

  // If query has 1-2 chars and no filters are active, show message
  if (query.length > 0 && query.length < MIN_SEARCH_CHARS && !hasActiveFilters()) {
    showMinCharMessage.value = true
    return
  }

  // Valid search: emit immediately
  showMinCharMessage.value = false
  emit('search')
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

const searchResults = computed(() => radioStore.displayedStations || [])

const hasMoreStations = computed(() => radioStore.hasMoreStations)

const { sentinelRef: scrollSentinel } = useInfiniteScroll({
  onLoadMore: () => radioStore.loadMore(),
  canLoadMore: hasMoreStations,
  isLoading: computed(() => radioStore.loading)
})
</script>

<style scoped>
.search-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  flex: 1;
}

.filters-bar {
  display: flex;
  gap: var(--space-02);
  align-items: center;
  flex-wrap: wrap;
  color: var(--color-text-secondary);
  min-height: 48px;
}

.filters-bar > :deep(*) {
  flex: 1;
  min-width: 180px;
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

/* Scroll sentinel for infinite scroll */
.scroll-sentinel {
  grid-column: 1 / -1;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: var(--space-04);
  color: var(--color-text-tertiary);
}

/* Mobile */
@media (max-aspect-ratio: 4/3) {
  .filters-bar {
    flex-wrap: nowrap;
    overflow-x: auto;
    margin-left: calc(-1 * var(--space-05));
    margin-right: calc(-1 * var(--space-05));
    padding-left: var(--space-05);
    padding-right: var(--space-05);
    scrollbar-width: none;
    -ms-overflow-style: none;
  }

  .filters-bar::-webkit-scrollbar {
    display: none;
  }

  .filters-bar > :deep(*) {
    flex-shrink: 0;
  }

  .results-content {
    grid-template-columns: 1fr;
  }
}
</style>
