<!-- frontend/src/components/gallery/demos/LayoutDemo.vue -->
<template>
  <GalleryItem id="AudioPlayer">
    <GalleryVariant label="desktop — the docked sidebar card, slots filled as the three sources fill them" contain :contain-height="420">
      <div class="player-pane">
        <AudioPlayer source="music_library" visible :artwork="musicPlaceholder" title="Says" is-playing>
          <template #info>
            <PlayerInfoText kicker="Liked Songs" title="Says" secondary="Nils Frahm" />
          </template>
          <template #progress>
            <ProgressBar :current-position="192000" :duration="511000" :progress-percentage="37.6"
              variant="dark" :interactive="false" />
          </template>
        </AudioPlayer>
      </div>
    </GalleryVariant>
    <GalleryVariant label="controls slot — replaces the built-in play/pause" contain :contain-height="420">
      <div class="player-pane">
        <AudioPlayer source="radio" visible :artwork="musicPlaceholder" title="Radio Nova" is-playing>
          <template #info>
            <PlayerInfoText kicker="Radio Nova" title="Ainsi parlait Zarathoustra" secondary="Alain Bashung" />
          </template>
          <template #controls>
            <PlaybackControls is-playing />
          </template>
        </AudioPlayer>
      </div>
    </GalleryVariant>
    <GalleryVariant label=":is-loading — the built-in play/pause spins" contain :contain-height="420">
      <div class="player-pane">
        <AudioPlayer source="podcast" visible :artwork="musicPlaceholder" title="Épisode 214" is-loading>
          <template #info>
            <PlayerInfoText kicker="Le Code a changé" title="Épisode 214" secondary="France Inter" />
          </template>
        </AudioPlayer>
      </div>
    </GalleryVariant>
    <GalleryVariant label="the mobile form is a viewport, not a prop — open the Playground tab and pick Phone" />
  </GalleryItem>

  <!-- No variants grid: it reads the app's own store, and this tab renders in
       the app document rather than the canvas iframe — mounting it here would
       show the unit's real now-playing state, and its buttons would drive it. -->
  <GalleryItem id="AudioPlayerFull" />

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
      <AudioSourceLayout gradient="music_library" header-title="Music Library"
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
    <GalleryVariant label=":display-state=&quot;starting&quot; — a spinner replaces the source icon">
      <AudioSourceStatus source-type="spotify" display-state="starting" />
    </GalleryVariant>
    <GalleryVariant label=":display-state=&quot;ready&quot; — one of two phrases, by who opens the session">
      <AudioSourceStatus source-type="bluetooth" display-state="ready" />
      <AudioSourceStatus source-type="cd" display-state="ready" />
    </GalleryVariant>
    <GalleryVariant label=":display-state=&quot;active&quot; + :device-name — a string, or an array for ROC">
      <AudioSourceStatus source-type="bluetooth" display-state="active" device-name="Leo’s iPhone" />
      <AudioSourceStatus source-type="mac" display-state="active"
        :device-name="['Leo’s MacBook', 'Studio iMac']" />
    </GalleryVariant>
    <GalleryVariant label=":display-state=&quot;active&quot; with no sender to name — the generic playing line">
      <AudioSourceStatus source-type="dlna" display-state="active" />
    </GalleryVariant>
    <GalleryVariant label="the four CTAs — retry, Bluetooth disconnect, Qobuz connect, network settings">
      <AudioSourceStatus source-type="spotify" display-state="error" @retry="log = 'retry'" />
      <AudioSourceStatus source-type="bluetooth" display-state="active" device-name="Leo’s iPhone"
        @disconnect="log = 'disconnect'" />
      <AudioSourceStatus source-type="qobuz" display-state="ready" unavailable-reason="no_account"
        @connect="log = 'connect'" />
      <AudioSourceStatus source-type="radio" display-state="ready" unavailable-reason="no_internet"
        @open-network-settings="log = 'network-settings'" />
    </GalleryVariant>
    <GalleryVariant label=":unavailable-reason — the prerequisite outranks the state it replaces">
      <AudioSourceStatus source-type="airplay" display-state="ready" unavailable-reason="no_network"
        @open-network-settings="log = 'network-settings'" />
      <AudioSourceStatus source-type="dlna" display-state="active" device-name="Leo’s iPhone"
        unavailable-reason="no_network" @open-network-settings="log = 'network-settings'" />
      <AudioSourceStatus source-type="cd" display-state="ready" unavailable-reason="no_drive" />
    </GalleryVariant>
    <GalleryVariant label="the CD operations — loading_disc / ejecting">
      <AudioSourceStatus source-type="cd" display-state="loading_disc" />
      <AudioSourceStatus source-type="cd" display-state="ejecting" />
    </GalleryVariant>
    <GalleryVariant :label="`last event: ${log || 'none'}`" />
  </GalleryItem>

  <GalleryItem id="AudioScreensaver">
    <GalleryVariant label=":mode=&quot;media&quot; — artwork, title, station bar" contain :contain-height="300">
      <AudioScreensaver is-visible :artwork="musicPlaceholder"
        title="Ainsi parlait Zarathoustra" subtitle="Alain Bashung" station-name="Radio Nova" />
    </GalleryVariant>
    <GalleryVariant label="no artwork — the generated station avatar takes its place" contain :contain-height="300">
      <AudioScreensaver is-visible title="Le Code a changé" subtitle="France Inter"
        station-name="France Inter" use-mono-subtitle />
    </GalleryVariant>
    <GalleryVariant label=":progress — the read-only bar at the bottom right" contain :contain-height="300">
      <AudioScreensaver is-visible :artwork="musicPlaceholder" title="Épisode 214"
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
import AudioPlayer from '@/components/audio/AudioPlayer.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';
import PlayerInfoText from '@/components/audio/PlayerInfoText.vue';
import PlaybackControls from '@/components/audio/PlaybackControls.vue';
import ProgressBar from '@/components/audio/ProgressBar.vue';
import AudioSourceStatus from '@/components/audio/AudioSourceStatus.vue';
import AudioScreensaver from '@/components/audio/AudioScreensaver.vue';
import Button from '@/components/ui/Button.vue';
import IconButton from '@/components/ui/IconButton.vue';
import { musicPlaceholder } from '@/constants/placeholders';

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

<style scoped>
/* The 340px sticky pane AudioSourceLayout gives the player — it sizes itself to
   its host, so without one it spans the card. */
.player-pane {
  width: 340px;
  height: 100%;
}
</style>
