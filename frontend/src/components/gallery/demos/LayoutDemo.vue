<!-- frontend/src/components/gallery/demos/LayoutDemo.vue -->
<template>
  <!-- No variants grid, for the full player's reason below: the bar reads the
       app's own store now, and this tab renders in the app document. The
       Playground replays a now-playing record into the canvas instead. -->
  <GalleryItem id="AudioPlayer" />

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
    <GalleryVariant label=":display-state=&quot;connected&quot; + :device-name — one sender, or several for ROC">
      <AudioSourceStatus source-type="bluetooth" display-state="connected" :device-name="['Leo’s iPhone']" />
      <AudioSourceStatus source-type="mac" display-state="connected"
        :device-name="['Leo’s MacBook', 'Studio iMac']" />
    </GalleryVariant>
    <GalleryVariant label="a session with no sender to name — the phase is the line">
      <AudioSourceStatus source-type="qobuz" display-state="loading" />
      <AudioSourceStatus source-type="qobuz" display-state="playing" />
      <AudioSourceStatus source-type="qobuz" display-state="paused" />
    </GalleryVariant>
    <GalleryVariant label="the five CTAs — retry, Bluetooth disconnect, Qobuz connect, eject, network settings">
      <AudioSourceStatus source-type="spotify" display-state="error" @retry="log = 'retry'" />
      <AudioSourceStatus source-type="bluetooth" display-state="connected" :device-name="['Leo’s iPhone']"
        @disconnect="log = 'disconnect'" />
      <AudioSourceStatus source-type="qobuz" display-state="ready" unavailable-reason="no_account"
        @connect="log = 'connect'" />
      <AudioSourceStatus source-type="cd" display-state="ready" unavailable-reason="unreadable_disc"
        @eject="log = 'eject'" />
      <AudioSourceStatus source-type="radio" display-state="ready" unavailable-reason="no_internet"
        @open-network-settings="log = 'network-settings'" />
    </GalleryVariant>
    <GalleryVariant label=":unavailable-reason — the prerequisite outranks the state it replaces">
      <AudioSourceStatus source-type="airplay" display-state="ready" unavailable-reason="no_network"
        @open-network-settings="log = 'network-settings'" />
      <AudioSourceStatus source-type="airplay" display-state="connected" :device-name="['Leo’s iPhone']"
        unavailable-reason="no_network" @open-network-settings="log = 'network-settings'" />
      <AudioSourceStatus source-type="cd" display-state="ready" unavailable-reason="no_drive" />
      <AudioSourceStatus source-type="cd" display-state="ready" unavailable-reason="no_disc" />
    </GalleryVariant>
    <GalleryVariant label="the CD operations — loading_disc / ejecting">
      <AudioSourceStatus source-type="cd" display-state="loading_disc" />
      <AudioSourceStatus source-type="cd" display-state="ejecting" />
    </GalleryVariant>
    <GalleryVariant :label="`last event: ${log || 'none'}`" />
  </GalleryItem>
</template>

<script setup>
import { ref } from 'vue';
import GalleryItem from '../GalleryItem.vue';
import GalleryVariant from '../GalleryVariant.vue';
import FillerBlock from '../samples/FillerBlock.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';
import AudioSourceStatus from '@/components/audio/AudioSourceStatus.vue';
import Button from '@/components/ui/Button.vue';
import IconButton from '@/components/ui/IconButton.vue';

const playerShown = ref(false);
const viewIndex = ref(1);
const log = ref('');
</script>
