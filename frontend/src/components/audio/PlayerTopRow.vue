<!-- PlayerTopRow.vue - The full player's row of buttons: the way back to the
     navigation and what the source adds at the start (CD's tracklist), what the
     source adds at the end (the favorite, the star, the like — CD's eject,
     Bluetooth's disconnect). AudioPlayerFull places it: heading the column on
     the kiosk, under the cover on the phone. -->
<template>
  <div class="player-top-row">
    <div class="player-top-row-zone">
      <slot name="start" />
    </div>
    <div class="player-top-row-zone player-top-row-zone--end">
      <slot name="end" />
    </div>
  </div>
</template>

<style scoped>
.player-top-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
  flex-shrink: 0;
  min-width: 0;
}

.player-top-row-zone {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  min-width: 0;
}

.player-top-row-zone--end {
  justify-content: flex-end;
}
</style>
