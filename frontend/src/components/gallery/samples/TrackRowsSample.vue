<!-- frontend/src/components/gallery/samples/TrackRowsSample.vue -->
<!--
  Three rows, the second one playing, as a tracklist actually fills TrackList.

  TrackList draws the surface and drops the divider under its last row, so a
  single row would show neither the dividers between rows nor the missing one
  at the end.
-->
<template>
  <TrackRow v-for="(song, idx) in songs" :key="song.title" :song="song" :number="idx + 1"
    :current="idx === 1" playing show-artist show-menu show-cover :cover-url="musicPlaceholder" />
</template>

<script setup>
import TrackRow from '@/components/audio/TrackRow.vue';
import { musicPlaceholder } from '@/constants/placeholders';

const songs = [
  { title: 'Says', artist: 'Nils Frahm', duration: 511 },
  { title: 'Loon', artist: 'Nils Frahm, Ólafur Arnalds', duration: 412 },
  { title: 'Ambre', artist: 'Nils Frahm', duration: 264 },
];
</script>
