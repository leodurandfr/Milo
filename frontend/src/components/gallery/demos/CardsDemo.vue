<!-- frontend/src/components/gallery/demos/CardsDemo.vue -->
<!--
  Each skeleton is shown beside the card it stands in for.

  That pairing is the whole reason this group exists: a placeholder's only job is
  to have the shape of the thing it replaces, and nowhere in the app do the two
  ever appear at once — the card renders when the skeleton stops. A skeleton that
  has drifted from its card is invisible until someone puts them side by side.
-->
<template>
  <GalleryItem id="StationCard">
    <GalleryVariant label='variant="card" — the row of the search and favourites lists' stacked>
      <div class="column">
        <StationCard :station="station" variant="card" />
        <StationCard :station="stationPlain" variant="card" />
      </div>
    </GalleryVariant>
    <GalleryVariant label=':is-playing / :is-loading — the row states' stacked>
      <div class="column">
        <StationCard :station="station" variant="card" is-playing />
        <StationCard :station="station" variant="card" is-loading />
      </div>
    </GalleryVariant>
    <GalleryVariant label="actions slot — 0, 1 or 2 buttons, right-aligned" stacked>
      <div class="column">
        <StationCard :station="station" variant="card">
          <template #actions>
            <IconButton icon="heart" variant="background-strong" size="small" />
          </template>
        </StationCard>
      </div>
    </GalleryVariant>
    <GalleryVariant label='variant="image" — the favourites grid tile'>
      <div class="tile">
        <StationCard :station="station" variant="image" />
      </div>
      <div class="tile">
        <StationCard :station="station" variant="image" is-playing />
      </div>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="SkeletonStationCard">
    <GalleryVariant label="the placeholder beside the tile it covers — same box, same radius">
      <div class="tile">
        <SkeletonStationCard />
      </div>
      <div class="tile">
        <StationCard :station="station" variant="image" />
      </div>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="PodcastCard">
    <GalleryVariant label=":show-actions — the button follows podcast.is_subscribed" stacked>
      <div class="column">
        <PodcastCard :podcast="podcast" show-actions />
        <PodcastCard :podcast="{ ...podcast, is_subscribed: true }" show-actions />
      </div>
    </GalleryVariant>
    <GalleryVariant label=":position — the chart rank prefix" stacked>
      <div class="column">
        <PodcastCard :podcast="podcast" :position="1" />
        <PodcastCard :podcast="podcast" :position="12" />
      </div>
    </GalleryVariant>
    <GalleryVariant label="no artwork, and :is-loading" stacked>
      <div class="column">
        <PodcastCard :podcast="{ uuid: 'x', name: 'Affaires sensibles', publisher: 'France Inter' }" />
        <PodcastCard :podcast="podcast" is-loading />
      </div>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="SkeletonPodcastCard">
    <GalleryVariant label='variant="card" (default) beside the PodcastCard it replaces in the home grid'>
      <div class="tile">
        <SkeletonPodcastCard variant="card" />
      </div>
      <div class="tile">
        <PodcastCard :podcast="podcast" />
      </div>
    </GalleryVariant>
    <GalleryVariant label='variant="row" beside its real counterpart — DetailHeader, not a PodcastCard' stacked>
      <div class="column">
        <SkeletonPodcastCard variant="row" />
        <DetailHeader :image-src="musicPlaceholder" title="Le Code a changé" subtitle="France Inter"
          subtitle-meta="214 épisodes" :show-play="false" :show-shuffle="false" />
      </div>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="EpisodeCard">
    <GalleryVariant label="show name + duration + date — the meta line is computed, not passed" stacked>
      <div class="column">
        <EpisodeCard :episode="episode" />
      </div>
    </GalleryVariant>
    <GalleryVariant label=":show-complete-button — the queue's dismiss affordance" stacked>
      <div class="column">
        <EpisodeCard :episode="episode" show-complete-button />
      </div>
    </GalleryVariant>
    <GalleryVariant label="no show, no date — the meta line falls back to the duration alone" stacked>
      <div class="column">
        <EpisodeCard :episode="{ uuid: 'e9', name: 'Épisode sans métadonnées', duration: 1800 }" />
      </div>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="SkeletonEpisodeCard">
    <GalleryVariant label="the placeholder above the row it replaces" stacked>
      <div class="column">
        <SkeletonEpisodeCard />
        <EpisodeCard :episode="episode" />
      </div>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="GenreCard">
    <GalleryVariant label="the artwork is picked from value, not passed in">
      <div class="tile"><GenreCard label="True Crime" value="true_crime" /></div>
      <div class="tile"><GenreCard label="Comedy" value="comedy" /></div>
      <div class="tile"><GenreCard label="Science" value="science" /></div>
    </GalleryVariant>
    <GalleryVariant label="a value the component has no artwork for — the tile stays, the image does not">
      <div class="tile"><GenreCard label="Unknown genre" value="not_a_genre" /></div>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="SkeletonPodcastDetails">
    <GalleryVariant label="a whole show page: the header block, then the episode run" stacked>
      <div class="column">
        <SkeletonPodcastDetails />
      </div>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="SkeletonEpisodeDetails">
    <GalleryVariant label="the single-episode page — cover, titles, description bars" stacked>
      <div class="column">
        <SkeletonEpisodeDetails />
      </div>
    </GalleryVariant>
  </GalleryItem>
</template>

<script setup>
import GalleryItem from '../GalleryItem.vue';
import GalleryVariant from '../GalleryVariant.vue';
import IconButton from '@/components/ui/IconButton.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import StationCard from '@/components/radio/StationCard.vue';
import SkeletonStationCard from '@/components/radio/SkeletonStationCard.vue';
import PodcastCard from '@/components/podcasts/PodcastCard.vue';
import SkeletonPodcastCard from '@/components/podcasts/SkeletonPodcastCard.vue';
import EpisodeCard from '@/components/podcasts/EpisodeCard.vue';
import SkeletonEpisodeCard from '@/components/podcasts/SkeletonEpisodeCard.vue';
import GenreCard from '@/components/podcasts/GenreCard.vue';
import SkeletonPodcastDetails from '@/components/podcasts/SkeletonPodcastDetails.vue';
import SkeletonEpisodeDetails from '@/components/podcasts/SkeletonEpisodeDetails.vue';
import { musicPlaceholder } from '@/constants/placeholders';

// `favicon: ''` takes the generated-avatar path, so nothing here needs network.
const station = { name: 'Radio Nova', favicon: '', countrycode: 'FR', genre: 'eclectic' };
const stationPlain = { name: 'FIP', favicon: '' };

const podcast = {
  uuid: 'p1',
  name: 'Le Code a changé',
  publisher: 'France Inter',
  image_url: musicPlaceholder,
  is_subscribed: false
};

// Seconds and epoch-seconds, the Podcast Index units. Fixed rather than relative
// to now, so the rendered date does not change from one day to the next.
const episode = {
  uuid: 'e1',
  name: 'Les gens qui parlent à leurs plantes',
  image_url: musicPlaceholder,
  duration: 2940,
  date_published: 1750000000,
  podcast: { name: 'Le Code a changé', image_url: musicPlaceholder }
};
</script>

<style scoped>
/* The cards fill their list column in the app; the demo card is wider than that,
   so they are boxed rather than stretched to the full width. */
.column {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  width: 100%;
  max-width: 560px;
}

.tile {
  width: 160px;
}
</style>
