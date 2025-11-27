<!-- frontend/src/views/CardsStyleGuide.vue -->
<template>
  <div class="cards-style-guide">
    <header class="style-guide__header">
      <h1 class="display-1">Cards Style Guide</h1>
      <p class="text-body-small text-secondary">Podcast and Radio card components reference</p>
    </header>

    <!-- PodcastCard Section -->
    <section class="style-guide__section">
      <h2 class="heading-1">PodcastCard</h2>
      <p class="text-mono text-secondary">Variants: card, row | Props: isLoading, showActions, contrast, clickable, position</p>

      <div class="controls-panel">
        <label class="control-item">
          <span class="text-mono">variant</span>
          <select v-model="podcastState.variant">
            <option value="card">card</option>
            <option value="row">row</option>
          </select>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="podcastState.isLoading" />
          <span class="text-mono">isLoading</span>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="podcastState.showActions" />
          <span class="text-mono">showActions</span>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="podcastState.isSubscribed" />
          <span class="text-mono">isSubscribed</span>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="podcastState.contrast" />
          <span class="text-mono">contrast</span>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="podcastState.clickable" />
          <span class="text-mono">clickable</span>
        </label>
        <label class="control-item">
          <span class="text-mono">position</span>
          <input type="number" v-model.number="podcastState.position" min="0" max="10" placeholder="null" />
        </label>
      </div>

      <div class="preview-container" :class="{ 'contrast-bg': podcastState.contrast }">
        <div class="preview-wrapper" :class="podcastState.variant === 'card' ? 'card-width' : 'row-width'">
          <PodcastCard
            :podcast="mockPodcast"
            :variant="podcastState.variant"
            :is-loading="podcastState.isLoading"
            :show-actions="podcastState.showActions"
            :contrast="podcastState.contrast"
            :clickable="podcastState.clickable"
            :position="podcastState.position || null"
          />
        </div>
      </div>
    </section>

    <!-- EpisodeCard Section -->
    <section class="style-guide__section">
      <h2 class="heading-1">EpisodeCard</h2>
      <p class="text-mono text-secondary">Props: contrast, showCompleteButton, clickable | Note: playing/buffering states come from podcastStore</p>

      <div class="controls-panel">
        <label class="control-item">
          <input type="checkbox" v-model="episodeState.contrast" />
          <span class="text-mono">contrast</span>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="episodeState.showCompleteButton" />
          <span class="text-mono">showCompleteButton</span>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="episodeState.clickable" />
          <span class="text-mono">clickable</span>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="episodeState.hasProgress" />
          <span class="text-mono">hasProgress (mock)</span>
        </label>
      </div>

      <div class="preview-container" :class="{ 'contrast-bg': episodeState.contrast }">
        <div class="preview-wrapper row-width">
          <EpisodeCard
            :episode="mockEpisodeComputed"
            :contrast="episodeState.contrast"
            :show-complete-button="episodeState.showCompleteButton"
            :clickable="episodeState.clickable"
          />
        </div>
      </div>
    </section>

    <!-- StationCard Section -->
    <section class="style-guide__section">
      <h2 class="heading-1">StationCard</h2>
      <p class="text-mono text-secondary">Variants: image, card | Props: isPlaying, isLoading</p>

      <div class="controls-panel">
        <label class="control-item">
          <span class="text-mono">variant</span>
          <select v-model="stationState.variant">
            <option value="image">image</option>
            <option value="card">card</option>
          </select>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="stationState.isPlaying" />
          <span class="text-mono">isPlaying</span>
        </label>
        <label class="control-item">
          <input type="checkbox" v-model="stationState.isLoading" />
          <span class="text-mono">isLoading</span>
        </label>
      </div>

      <div class="preview-container">
        <div class="preview-wrapper" :class="stationState.variant === 'image' ? 'image-width' : 'row-width'">
          <StationCard
            :station="mockStation"
            :variant="stationState.variant"
            :is-playing="stationState.isPlaying"
            :is-loading="stationState.isLoading"
          >
            <template v-if="stationState.variant === 'card'" #actions>
              <IconButton icon="heart" variant="background-strong" size="medium" />
            </template>
          </StationCard>
        </div>
      </div>
    </section>

    <!-- Skeletons Section -->
    <section class="style-guide__section">
      <h2 class="heading-1">Skeleton Components</h2>
      <p class="text-mono text-secondary">Loading placeholders for cards</p>

      <div class="skeleton-grid">
        <div class="skeleton-item">
          <h3 class="heading-2">SkeletonPodcastCard (card)</h3>
          <div class="preview-wrapper card-width">
            <SkeletonPodcastCard variant="card" />
          </div>
        </div>

        <div class="skeleton-item">
          <h3 class="heading-2">SkeletonPodcastCard (row)</h3>
          <div class="preview-wrapper row-width">
            <SkeletonPodcastCard variant="row" />
          </div>
        </div>

        <div class="skeleton-item">
          <h3 class="heading-2">SkeletonEpisodeCard</h3>
          <div class="preview-wrapper row-width">
            <SkeletonEpisodeCard />
          </div>
        </div>

        <div class="skeleton-item">
          <h3 class="heading-2">SkeletonStationCard</h3>
          <div class="preview-wrapper image-width">
            <SkeletonStationCard />
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

// Podcast components
import PodcastCard from '@/components/podcasts/PodcastCard.vue'
import EpisodeCard from '@/components/podcasts/EpisodeCard.vue'
import SkeletonPodcastCard from '@/components/podcasts/SkeletonPodcastCard.vue'
import SkeletonEpisodeCard from '@/components/podcasts/SkeletonEpisodeCard.vue'

// Radio components
import StationCard from '@/components/radio/StationCard.vue'
import SkeletonStationCard from '@/components/radio/SkeletonStationCard.vue'

// UI components
import IconButton from '@/components/ui/IconButton.vue'

// State for PodcastCard
const podcastState = ref({
  variant: 'card',
  isLoading: false,
  showActions: true,
  isSubscribed: false,
  contrast: false,
  clickable: true,
  position: null
})

// State for EpisodeCard
const episodeState = ref({
  contrast: false,
  showCompleteButton: false,
  clickable: true,
  hasProgress: false
})

// State for StationCard
const stationState = ref({
  variant: 'image',
  isPlaying: false,
  isLoading: false
})

// Mock data
const mockPodcast = computed(() => ({
  uuid: 'mock-podcast-123',
  name: 'The Daily Tech News',
  publisher: 'Tech Media Inc.',
  author: 'John Doe',
  image_url: 'https://picsum.photos/seed/podcast/400/400',
  is_subscribed: podcastState.value.isSubscribed,
  total_episodes: 142
}))

const mockEpisode = {
  uuid: 'mock-episode-123',
  name: 'Episode 42: The Future of AI in Software Development',
  date_published: Math.floor(Date.now() / 1000) - 86400, // Yesterday
  duration: 3600, // 1 hour
  image_url: 'https://picsum.photos/seed/episode/400/400',
  podcast: {
    name: 'Tech Podcast',
    image_url: 'https://picsum.photos/seed/podcast2/400/400'
  }
}

const mockEpisodeComputed = computed(() => ({
  ...mockEpisode,
  progress_seconds: episodeState.value.hasProgress ? 1800 : 0
}))

const mockStation = {
  uuid: 'mock-station-123',
  name: 'Jazz FM Paris',
  favicon: 'https://picsum.photos/seed/radio/400/400',
  country: 'France',
  genre: 'jazz'
}
</script>

<style scoped>
.cards-style-guide {
  padding: var(--space-06);
  max-width: 1200px;
  margin: 0 auto;
  background: var(--color-background);
  min-height: 100vh;
}

.style-guide__header {
  margin-bottom: var(--space-07);
}

.style-guide__section {
  margin-bottom: var(--space-07);
}

.style-guide__section h2 {
  margin-bottom: var(--space-02);
  color: var(--color-text);
}

.style-guide__section > p {
  margin-bottom: var(--space-04);
}

.text-secondary {
  color: var(--color-text-secondary);
}

/* Controls Panel */
.controls-panel {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-03);
  padding: var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  margin-bottom: var(--space-04);
}

.control-item {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  cursor: pointer;
}

.control-item input[type="checkbox"] {
  width: 18px;
  height: 18px;
  cursor: pointer;
}

.control-item select,
.control-item input[type="number"] {
  padding: var(--space-02);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-02);
  background: var(--color-background);
  color: var(--color-text);
  font-family: 'Space Mono Regular';
  font-size: var(--font-size-mono);
}

.control-item input[type="number"] {
  width: 60px;
}

/* Preview Container */
.preview-container {
  padding: var(--space-05);
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  display: flex;
  justify-content: center;
}

.preview-container.contrast-bg {
  background: var(--color-background-contrast);
}

.preview-wrapper {
  width: 100%;
}

.preview-wrapper.card-width {
  max-width: 200px;
}

.preview-wrapper.row-width {
  max-width: 500px;
}

.preview-wrapper.image-width {
  max-width: 150px;
}

/* Skeleton Grid */
.skeleton-grid {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

.skeleton-item h3 {
  margin-bottom: var(--space-03);
  color: var(--color-text-secondary);
}

.skeleton-item .preview-wrapper {
  background: var(--color-background-strong);
  padding: var(--space-04);
  border-radius: var(--radius-04);
}

@media (max-aspect-ratio: 4/3) {
  .cards-style-guide {
    padding: var(--space-04);
  }

  .controls-panel {
    flex-direction: column;
    gap: var(--space-02);
  }

  .preview-wrapper.row-width {
    max-width: 100%;
  }
}
</style>
