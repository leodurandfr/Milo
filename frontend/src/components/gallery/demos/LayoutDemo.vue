<!-- frontend/src/components/gallery/demos/LayoutDemo.vue -->
<template>
  <GalleryItem id="AudioSourceLayout">
    <GalleryVariant label=":gradient=&quot;radio&quot; — header + content, no player" contain :contain-height="360">
      <AudioSourceLayout gradient="radio" header-title="Radio" header-subtitle="24 stations"
        content-key="home">
        <template #content>
          <FillerBlock label="content slot" :height="600" />
        </template>
      </AudioSourceLayout>
    </GalleryVariant>

    <GalleryVariant label=":show-player — the content gives up 340px, both widths animate" contain :contain-height="360">
      <AudioSourceLayout gradient="podcast" header-title="Podcasts" header-show-back
        :show-player="playerShown" content-key="home">
        <template #content>
          <FillerBlock label="content slot" :height="600" />
        </template>
        <template #player>
          <FillerBlock label="player slot" />
        </template>
        <template #header-actions>
          <IconButton icon="search" variant="ghost" />
        </template>
      </AudioSourceLayout>
    </GalleryVariant>
    <GalleryVariant :label="`showPlayer: ${playerShown}`">
      <Button size="small" @click="playerShown = !playerShown">Toggle the player pane</Button>
    </GalleryVariant>

    <GalleryVariant label="contentKey — changing it cross-fades the content out and the next in" contain :contain-height="280">
      <AudioSourceLayout gradient="music-library" header-title="Music Library"
        :content-key="`view-${viewIndex}`">
        <template #content>
          <FillerBlock :label="`content slot — view ${viewIndex}`" :height="200" />
        </template>
      </AudioSourceLayout>
    </GalleryVariant>
    <GalleryVariant :label="`contentKey: view-${viewIndex}`">
      <Button size="small" @click="viewIndex++">Navigate</Button>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="AudioSourceStatus">
    <GalleryVariant label=":source-state=&quot;starting&quot; — a spinner replaces the source icon">
      <AudioSourceStatus source-type="spotify" source-state="starting" />
    </GalleryVariant>
    <GalleryVariant label=":source-state=&quot;waiting&quot; — the idle line, per source">
      <AudioSourceStatus source-type="bluetooth" source-state="waiting" />
      <AudioSourceStatus source-type="mac" source-state="waiting" />
    </GalleryVariant>
    <GalleryVariant label=":source-state=&quot;active&quot; + :device-name — a string, or an array for ROC">
      <AudioSourceStatus source-type="bluetooth" source-state="active" device-name="Leo’s iPhone" />
      <AudioSourceStatus source-type="mac" source-state="active"
        :device-name="['Leo’s MacBook', 'Studio iMac']" />
    </GalleryVariant>
    <GalleryVariant label="the two CTAs — Bluetooth disconnect, Qobuz connect">
      <AudioSourceStatus source-type="bluetooth" source-state="active" device-name="Leo’s iPhone"
        @disconnect="log = 'disconnect'" />
      <AudioSourceStatus source-type="qobuz" source-state="waiting" :account-connected="false"
        @connect="log = 'connect'" />
    </GalleryVariant>
    <GalleryVariant label="the CD states — ejecting / loading_disc / no_drive">
      <AudioSourceStatus source-type="cd" source-state="loading_disc" />
      <AudioSourceStatus source-type="cd" source-state="no_drive" />
    </GalleryVariant>
    <GalleryVariant :label="`last event: ${log || 'none'}`" />
  </GalleryItem>

  <GalleryItem id="AudioScreensaver">
    <GalleryVariant label=":mode=&quot;media&quot; — artwork, title, station bar" contain :contain-height="300">
      <AudioScreensaver is-visible :artwork="albumPlaceholder"
        title="Ainsi parlait Zarathoustra" subtitle="Alain Bashung" station-name="Radio Nova" />
    </GalleryVariant>
    <GalleryVariant label="no artwork — the generated station avatar takes its place" contain :contain-height="300">
      <AudioScreensaver is-visible title="Le Code a changé" subtitle="France Inter"
        station-name="France Inter" use-mono-subtitle />
    </GalleryVariant>
    <GalleryVariant label=":progress — the read-only bar at the bottom right" contain :contain-height="300">
      <AudioScreensaver is-visible :artwork="albumPlaceholder" title="Épisode 214"
        subtitle="Le Code a changé" :progress="progress" />
    </GalleryVariant>
    <GalleryVariant label=":mode=&quot;simple&quot; — icon and two lines (Bluetooth, Mac)" contain :contain-height="300">
      <AudioScreensaver is-visible mode="simple" source-type="bluetooth"
        title="Connected to" subtitle="Leo’s iPhone" />
    </GalleryVariant>
  </GalleryItem>
</template>

<script setup>
import { ref } from 'vue';
import GalleryItem from '../GalleryItem.vue';
import GalleryVariant from '../GalleryVariant.vue';
import FillerBlock from '../samples/FillerBlock.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';
import AudioSourceStatus from '@/components/audio/AudioSourceStatus.vue';
import AudioScreensaver from '@/components/audio/AudioScreensaver.vue';
import Button from '@/components/ui/Button.vue';
import IconButton from '@/components/ui/IconButton.vue';
import albumPlaceholder from '@/assets/images/album-placeholder.svg';

const playerShown = ref(false);
const viewIndex = ref(1);
const log = ref('');

const progress = {
  currentPosition: 812000,
  duration: 2940000,
  progressPercentage: 27.6,
  isReady: true
};
</script>
