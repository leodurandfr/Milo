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
        @submit="onSearchSubmit"
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
        <MessageContent v-if="isLoading" key="loading" loading :loading-delay="0" :title="t('audioSources.radioSource.loadingStations')" />

        <!-- Error state -->
        <MessageContent
          v-else-if="hasError && searchResults.length === 0"
          key="error"
          icon="stop"
          :title="t('audioSources.radioSource.connectionError')"
          :subtitle="t('audioSources.radioSource.cannotLoadStations')"
          :cta-label="t('audioSources.radioSource.retry')"
          cta-variant="background-strong"
          :cta-click="() => $emit('retry')"
        />

        <!-- Minimum characters message -->
        <MessageContent v-else-if="showMinCharMessage" key="min-chars" icon="search" :title="t('audioSources.radioSource.minCharactersRequired')" />

        <!-- Empty state -->
        <MessageContent v-else-if="searchResults.length === 0" key="empty" icon="radio" :title="t('audioSources.radioSource.noStationsFound')" />

        <!-- Search results -->
        <div v-else key="results" class="results-content">
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
            <span class="loading-more">{{ t('audioSources.radioSource.loadingMore') }}</span>
          </div>
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRadioStore } from '@/stores/radioStore'
import { useI18n } from '@/services/i18n'
import StationCard from './StationCard.vue'
import InputText from '@/components/ui/InputText.vue'
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
  }
})

const emit = defineEmits(['search', 'retry', 'play-station'])

// Minimum characters required for text search
const MIN_SEARCH_CHARS = 3

// State for showing minimum characters message
const showMinCharMessage = ref(false)

// Debounce timer for search input
const searchDebounceTimer = ref(null)

// Sentinel element ref for IntersectionObserver
const scrollSentinel = ref(null)

// IntersectionObserver instance
let observer = null

// Check if any filter (country or genre) is active
function hasActiveFilters() {
  return radioStore.countryFilter !== '' || radioStore.genreFilter !== ''
}

function onSearchInput() {
  if (searchDebounceTimer.value) {
    clearTimeout(searchDebounceTimer.value)
  }

  const query = radioStore.searchQuery.trim()

  // Hide message only when returning to top stations (empty field)
  if (query.length === 0) {
    showMinCharMessage.value = false
    searchDebounceTimer.value = setTimeout(() => {
      emit('search')
    }, 400)
  } else if (query.length >= MIN_SEARCH_CHARS) {
    // For 3+ chars: trigger search
    searchDebounceTimer.value = setTimeout(() => {
      showMinCharMessage.value = false
      emit('search')
    }, 400)
  }
  // For 1-2 chars: do nothing (wait for more input or Enter key)
}

function onSearchSubmit() {
  // Clear any pending debounce
  if (searchDebounceTimer.value) {
    clearTimeout(searchDebounceTimer.value)
    searchDebounceTimer.value = null
  }

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

// Search results from store
const searchResults = computed(() => radioStore.displayedStations || [])

// Has more stations to load
const hasMoreStations = computed(() => radioStore.hasMoreStations)

// Setup IntersectionObserver for infinite scroll
function setupIntersectionObserver() {
  if (observer) {
    observer.disconnect()
  }

  observer = new IntersectionObserver(
    (entries) => {
      const [entry] = entries
      if (entry.isIntersecting && radioStore.hasMoreStations && !radioStore.loading) {
        console.log('📻 Sentinel visible, loading more...')
        radioStore.loadMore()
      }
    },
    {
      rootMargin: '100px', // Start loading 100px before sentinel is visible
      threshold: 0
    }
  )

  if (scrollSentinel.value) {
    observer.observe(scrollSentinel.value)
  }
}

// Watch for sentinel ref changes (when results appear/disappear)
watch(scrollSentinel, (newRef) => {
  if (newRef && observer) {
    observer.observe(newRef)
  }
})

// Watch for hasMoreStations changes to reconnect observer
watch(hasMoreStations, (hasMore) => {
  if (hasMore && scrollSentinel.value && observer) {
    observer.observe(scrollSentinel.value)
  }
})

onMounted(() => {
  setupIntersectionObserver()
})

onBeforeUnmount(() => {
  if (observer) {
    observer.disconnect()
    observer = null
  }
  if (searchDebounceTimer.value) {
    clearTimeout(searchDebounceTimer.value)
  }
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

.loading-more {
  font-size: var(--text-sm);
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
