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
    <GalleryVariant label="variant=&quot;dark&quot; :interactive=&quot;false&quot; — the screensaver / lyrics surfaces" stacked>
      <div class="dark-strip">
        <ProgressBar :current-position="812000" :duration="2940000" :progress-percentage="27.6"
          variant="dark" :interactive="false" />
      </div>
    </GalleryVariant>
    <GalleryVariant label=":duration=&quot;0&quot; — renders nothing at all (radio, Qobuz)" stacked>
      <ProgressBar :current-position="0" :duration="0" :progress-percentage="0" />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="PlaybackControls">
    <GalleryVariant label=":is-playing" stacked>
      <PlaybackControls :is-playing="false" />
      <PlaybackControls :is-playing="true" />
    </GalleryVariant>
    <GalleryVariant label=":is-buffering — the play glyph becomes a spinner" stacked>
      <PlaybackControls is-buffering />
    </GalleryVariant>
    <GalleryVariant label=":has-next=&quot;false&quot; — last track, next is inert" stacked>
      <PlaybackControls :is-playing="true" :has-next="false" />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="PlayerInfoText">
    <GalleryVariant label="title only" stacked>
      <PlayerInfoText title="Ainsi parlait Zarathoustra" />
    </GalleryVariant>
    <GalleryVariant label="kicker + title + secondary" stacked>
      <PlayerInfoText kicker="Radio Nova" title="Ainsi parlait Zarathoustra" secondary="Alain Bashung" />
    </GalleryVariant>
    <GalleryVariant label="kicker with its own thumbnail (kickerIcon)" stacked>
      <PlayerInfoText :kicker-icon="albumPlaceholder" kicker="Le Code a changé"
        title="Épisode 214 — Les gens qui parlent aux plantes" secondary="France Inter" />
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
      <TrackRow :song="track" :number="4" show-artist show-menu show-cover :cover-url="albumPlaceholder" />
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
      <DetailHeader :image-src="albumPlaceholder" title="Spaces" subtitle="Nils Frahm"
        subtitle-meta="2013 · 17 tracks · 1 h 21" />
    </GalleryVariant>
    <GalleryVariant label=":icon — a tinted tile instead of a cover (the virtual headers)" stacked>
      <DetailHeader icon="heart" title="Liked Songs" subtitle-meta="128 tracks"
        :show-shuffle="false" show-favorite is-favorite />
    </GalleryVariant>
    <GalleryVariant label="actions slot — renders before the built-in buttons" stacked>
      <DetailHeader :image-src="albumPlaceholder" title="Morning playlist" subtitle="42 tracks"
        :show-shuffle="false">
        <template #actions>
          <IconButton icon="threeDots" variant="on-dark" size="small" />
        </template>
      </DetailHeader>
    </GalleryVariant>
    <GalleryVariant label=":subtitle-clickable — the artist line becomes a link" stacked>
      <DetailHeader :image-src="albumPlaceholder" title="Spaces" subtitle="Nils Frahm"
        subtitle-clickable :show-play="false" :show-shuffle="false"
        @select-artist="artistHits++" />
      <span class="text-mono-small">select-artist: {{ artistHits }}</span>
    </GalleryVariant>
  </GalleryItem>
</template>

<script setup>
import { ref } from 'vue';
import GalleryItem from '../GalleryItem.vue';
import GalleryVariant from '../GalleryVariant.vue';
import ProgressBar from '@/components/audio/ProgressBar.vue';
import PlaybackControls from '@/components/audio/PlaybackControls.vue';
import PlayerInfoText from '@/components/audio/PlayerInfoText.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import IconButton from '@/components/ui/IconButton.vue';
import albumPlaceholder from '@/assets/images/album-placeholder.svg';

const position = ref(192000);
const artistHits = ref(0);

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
  background: var(--color-background-contrast);
  border-radius: var(--radius-03);
}
</style>
