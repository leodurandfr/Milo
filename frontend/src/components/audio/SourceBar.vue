<!-- SourceBar.vue - What the full player is playing from: an icon, and a label
     that says the most specific thing the source knows — the station a detected
     song plays on, the show an episode belongs to, the device sending to a
     receiver, the Spotify account, else the source's own name. Always an icon:
     the source's AppIcon, or, given `image`, that image in its place — the
     station's logo once a song with a cover of its own is detected (the
     generated avatar of the label for a station with no logo), as the playing
     bar draws a station.
     AudioPlayerFull decides both; this draws them, on every source. -->
<template>
  <div class="source-bar" :class="`source-bar--${size}`">
    <LazyImage v-if="image !== null" :src="image" :fallback-name="label" alt=""
      class="source-bar-icon source-bar-image" />
    <AppIcon v-else :name="source" :size="24" class="source-bar-icon" />
    <span class="source-bar-label" :class="size === 'small' ? 'text-body-small' : 'heading-4'">{{ label }}</span>
  </div>
</template>

<script setup>
import AppIcon from '@/components/ui/AppIcon.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';

defineProps({
  /** The active source, whose AppIcon is drawn. */
  source: {
    type: String,
    required: true,
    validator: (value) => ALL_AUDIO_SOURCES.includes(value)
  },
  label: {
    type: String,
    required: true
  },
  /**
   * An image drawn in place of the source's icon, null for the icon. An empty
   * string is an image with nothing to load: the label's generated avatar.
   */
  image: {
    type: String,
    default: null
  },
  /** The label's rung: the full player's heading, or the playing bar's text. */
  size: {
    type: String,
    default: 'medium',
    validator: (value) => ['medium', 'small'].includes(value)
  }
});
</script>

<style scoped>
.source-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-02);
  min-width: 0;
}

.source-bar-icon {
  flex-shrink: 0;
}

/* The AppIcon's own size and corner, so the two read as one slot. */
.source-bar-image {
  width: 24px;
  height: 24px;
  border-radius: var(--radius-02);
  overflow: hidden;
}

.source-bar-label {
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
