<template>
  <div class="search-view">
    <!-- Search -->
    <InputText v-model="searchTerm" :placeholder="t('podcasts.searchPlaceholder')" variant="background-neutral"
      icon="search" @update:modelValue="onSearchInput" />

    <!-- Results -->
    <div class="results">
      <!-- Loading state -->
      <MessageContent v-if="loading" loading :loading-delay="0" :title="t('podcasts.loading')" />

      <!-- Podcast Index did not answer — subscriptions and playback are unaffected -->
      <MessageContent
        v-else-if="podcastStore.apiError"
        icon="network"
        :title="t('podcasts.catalogUnavailable')"
        :subtitle="t('podcasts.catalogUnavailableHint')"
        :cta-label="t('podcasts.retry')"
        cta-variant="background-strong"
        :cta-click="() => podcastStore.search()"
      />

      <!-- Search results -->
      <div v-else-if="hasSearched && searchResults.podcasts.length > 0" class="results-content fade-in">
        <!-- Podcasts results -->
        <section class="section">
          <h2 class="heading-2">
            {{ t('podcasts.podcastsTitle') }}
          </h2>
          <div class="podcasts-grid">
            <PodcastCard v-for="podcast in searchResults.podcasts" :key="podcast.itunes_id || podcast.uuid"
              :podcast="podcast" :isLoading="isPodcastLoading(podcast)"
              @select="$emit('select-podcast', podcast)" />
          </div>
          <div v-if="searchCurrentPage.podcasts < searchPagination.podcasts.pages" class="load-more-container">
            <Button variant="brand" :loading="searchLoadingMore.podcasts" @click="podcastStore.loadMoreSearchResults">
              {{ t('podcasts.loadMorePodcasts') }}
            </Button>
          </div>
        </section>
      </div>

      <!-- No results -->
      <MessageContent
        v-else-if="hasSearched"
        icon="search"
        :title="lastSearchTerm ? t('podcasts.noResultsFor', { query: lastSearchTerm }) : t('podcasts.noResults')"
      />

      <!-- Initial state -->
      <MessageContent v-else icon="search" :title="t('podcasts.searchPrompt')" />
    </div>
  </div>
</template>

<script setup>
import { storeToRefs } from 'pinia'
import { usePodcastStore } from '@/stores/podcastStore'
import { useDebounce } from '@/composables/useDebounce'
import { useI18n } from '@/services/i18n'
import PodcastCard from './PodcastCard.vue'
import InputText from '@/components/ui/InputText.vue'
import Button from '@/components/ui/Button.vue'
import MessageContent from '@/components/ui/MessageContent.vue'

const props = defineProps({
  loadingPodcastId: {
    type: [String, Number],
    default: null
  }
})

const emit = defineEmits(['select-podcast'])
const podcastStore = usePodcastStore()
const { t } = useI18n()

// True while the tapped iTunes search hit is being resolved to a feedId.
function isPodcastLoading(podcast) {
  if (!props.loadingPodcastId) return false
  return podcast.itunes_id === props.loadingPodcastId || podcast.uuid === props.loadingPodcastId
}

// Get reactive refs from store (persisted across navigation)
const {
  searchTerm,
  lastSearchTerm,
  searchResults,
  searchPagination,
  searchCurrentPage,
  hasSearched,
  searchLoading: loading,
  searchLoadingMore
} = storeToRefs(podcastStore)

const { debounced: debouncedSearch } = useDebounce(() => podcastStore.search())

// Handle search input with debounce
function onSearchInput() {
  // Reset to initial state when the term is cleared — through the store's own
  // reset, which also drops the request a previous keystroke left in flight.
  if (!searchTerm.value) {
    podcastStore.clearSearch()
    return
  }

  debouncedSearch()
}

</script>

<style scoped>

.search-view {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.results {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
}

.results-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
}

.section {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.podcasts-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--space-02);
}

.load-more-container {
  display: flex;
  justify-content: center;
  padding: var(--space-04) 0;
  margin-top: var(--space-02);
}

</style>
