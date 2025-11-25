<template>
  <div class="audio-source-layout">
    <!-- Content area: scrollable views -->
    <div
      class="content-container"
      :class="{ 'with-player': showPlayer }"
    >
      <slot name="content"></slot>
    </div>

    <!-- Player wrapper: animates width on desktop, transparent on mobile -->
    <div
      :class="['player-wrapper', { 'has-player': showPlayer }]"
    >
      <slot name="player"></slot>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  /**
   * Controls layout animation (shows/hides player space)
   */
  showPlayer: {
    type: Boolean,
    default: false
  }
})
</script>

<style scoped>
/* Layout wrapper */
.audio-source-layout {
  --audio-player-wrapper-width: 340px;
  display: flex;
  justify-content: center;
  width: 100%;
  height: 100%;
  padding: 0 var(--space-07);
  transition: all var(--transition-spring-slow);
  overflow-y: auto;
}

/* Content container: animates width to make space for player */
.content-container {
  position: relative;
  width: 84%;
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: var(--space-07) 0;
  gap: var(--space-04);
  min-height: 0;
  flex-shrink: 0;
  touch-action: pan-y;
  transition: width 0.6s cubic-bezier(0.5, 0, 0, 1);
}

.content-container.with-player {
  width: calc(100% - var(--audio-player-wrapper-width));
  transition: width var(--transition-spring);
}

/* Player wrapper: animates width to create space for player */
.player-wrapper {
  box-sizing: border-box;
  width: 0;
  max-width: var(--audio-player-wrapper-width);
  padding-left: 0;
  opacity: 0;
  flex-shrink: 0;
  transition:
    width 0.6s cubic-bezier(0.5, 0, 0, 1),
    padding-left 0.6s cubic-bezier(0.5, 0, 0, 1),
    opacity 0.6s cubic-bezier(0.5, 0, 0, 1);
  pointer-events: none;
}

.player-wrapper.has-player {
  width: var(--audio-player-wrapper-width);
  max-width: var(--audio-player-wrapper-width);
  padding-left: var(--space-06); /* 32px spacing (animated with width) */
  opacity: 1;
  transition:
    width var(--transition-spring),
    padding-left var(--transition-spring),
    opacity 0.4s ease-out;
  pointer-events: all;
}

/* Mobile: full width content + fixed player (wrapper transparent) */
@media (max-aspect-ratio: 4/3) {
  .audio-source-layout {
    padding: 0 var(--space-05);
  }

  .content-container {
    width: 100%;
    max-width: none;
    height: auto;
    min-height: 100%;
    padding-bottom: var(--space-04);
    padding-top: var(--space-09);
  }

  .content-container.with-player {
    width: 100%;
    margin-right: 0;
  }

  /* Spacer pour le player via pseudo-élément */
  .content-container.with-player::after {
    content: '';
    display: block;
    height: 160px;
    flex-shrink: 0;
  }

  .player-wrapper {
    display: contents;
    width: auto;
    opacity: 1;
  }

  .player-wrapper.has-player {
    width: auto;
    opacity: 1;
  }
}
</style>
