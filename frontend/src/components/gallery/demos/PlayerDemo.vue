<!-- frontend/src/components/gallery/demos/PlayerDemo.vue -->
<template>
  <GalleryItem id="ProgressBar">
    <GalleryVariant label="variant=&quot;light&quot; (default) — interactive, click to seek" stacked>
      <ProgressBar
        :current-position="position"
        :duration="245000"
        :progress-percentage="(position / 245000) * 100"
        @seek="position = $event"
      />
      <span class="text-mono-small">seek → {{ position }} ms</span>
    </GalleryVariant>
    <GalleryVariant label="variant=&quot;on-contrast&quot; :interactive=&quot;false&quot; — the lyrics surface" stacked>
      <div class="dark-strip">
        <ProgressBar :current-position="812000" :duration="2940000" :progress-percentage="27.6"
          variant="on-contrast" :interactive="false" />
      </div>
    </GalleryVariant>
    <GalleryVariant label=":duration=&quot;0&quot; — renders nothing at all (radio, Qobuz)" stacked>
      <ProgressBar :current-position="0" :duration="0" :progress-percentage="0" />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="PlayerInfoText">
    <GalleryVariant label="title only" stacked>
      <PlayerInfoText title="Ainsi parlait Zarathoustra" />
    </GalleryVariant>
    <GalleryVariant label="title + secondary" stacked>
      <PlayerInfoText title="Ainsi parlait Zarathoustra" secondary="Alain Bashung" />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="TrackRow">
    <GalleryVariant label="default — number, title, duration" stacked>
      <TrackRow :song="track" :number="4" />
    </GalleryVariant>
    <GalleryVariant label=":current :playing — the number becomes the equaliser bars" stacked>
      <TrackRow :song="track" :number="4" current playing />
      <TrackRow :song="track" :number="4" current />
    </GalleryVariant>
    <GalleryVariant label=":show-artist :show-cover :show-menu" stacked>
      <TrackRow :song="track" :number="4" show-artist show-menu show-cover :cover-url="musicPlaceholder" />
    </GalleryVariant>
    <GalleryVariant label=":editing — duration + menu give way to remove + drag grip" stacked>
      <TrackRow :song="track" :number="4" show-artist editing />
    </GalleryVariant>
    <GalleryVariant label=":feat — a second line inside the title row" stacked>
      <TrackRow :song="track" :number="4" feat="Ólafur Arnalds" />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="DetailHeader">
    <GalleryVariant label="cover + three lines + the built-in shuffle / play" stacked>
      <DetailHeader :image-src="musicPlaceholder" title="Spaces" subtitle="Nils Frahm"
        subtitle-meta="2013 · 17 tracks · 1 h 21" />
    </GalleryVariant>
    <GalleryVariant label=":icon — a tinted tile instead of a cover (the virtual headers)" stacked>
      <DetailHeader icon="heart" title="Liked Songs" subtitle-meta="128 tracks"
        :show-shuffle="false" />
    </GalleryVariant>
    <GalleryVariant label="actions slot — renders before the built-in buttons" stacked>
      <DetailHeader :image-src="musicPlaceholder" title="Morning playlist" subtitle="42 tracks"
        :show-shuffle="false">
        <template #actions>
          <IconButton icon="threeDots" variant="on-contrast" size="medium" />
        </template>
      </DetailHeader>
    </GalleryVariant>
    <GalleryVariant label=":subtitle-clickable — the artist line becomes a link" stacked>
      <DetailHeader :image-src="musicPlaceholder" title="Spaces" subtitle="Nils Frahm"
        subtitle-clickable :show-play="false" :show-shuffle="false"
        @select-artist="artistHits++" />
      <span class="text-mono-small">select-artist: {{ artistHits }}</span>
    </GalleryVariant>
    <GalleryVariant label=":subtitle-artists — several names, one link each (Spotify's album)" stacked>
      <DetailHeader :image-src="musicPlaceholder" title="Trance Frendz" subtitle="Ólafur Arnalds, Nils Frahm"
        :subtitle-artists="[{ name: 'Ólafur Arnalds', link: true }, { name: 'Nils Frahm', link: true }]"
        :show-play="false" :show-shuffle="false" @select-artist="artistPicked = $event" />
      <span class="text-mono-small">select-artist: {{ artistPicked ?? '—' }}</span>
    </GalleryVariant>
  </GalleryItem>
</template>

<script setup>
import { ref } from 'vue';
import GalleryItem from '../GalleryItem.vue';
import GalleryVariant from '../GalleryVariant.vue';
import ProgressBar from '@/components/audio/ProgressBar.vue';
import PlayerInfoText from '@/components/audio/PlayerInfoText.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import IconButton from '@/components/ui/IconButton.vue';
import { musicPlaceholder } from '@/constants/placeholders';

const position = ref(192000);
const artistHits = ref(0);
const artistPicked = ref(null);

// Seconds, not milliseconds — the row formats what the catalogue gives it.
const track = { title: 'Says', artist: 'Nils Frahm', duration: 511 };
</script>

<style scoped>
.dark-strip {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  width: 100%;
  padding: var(--space-04);
  background: var(--color-contrast);
  border-radius: var(--radius-03);
}
</style>
