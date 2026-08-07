<!-- frontend/src/components/gallery/demos/MediaDemo.vue -->
<template>
  <GalleryItem id="LazyImage">
    <GalleryVariant label="src resolves / fallbackName generates an avatar / src 404s onto fallback">
      <LazyImage class="art" :src="albumPlaceholder" alt="A resolving source" />
      <LazyImage class="art" fallback-name="Radio Nova" alt="Generated from a name" />
      <LazyImage class="art" src="/does-not-exist.jpg" :fallback="albumPlaceholder"
        alt="Falls back to a static asset" />
      <LazyImage class="art" src="/does-not-exist.jpg" fallback-name="Nick Cave" alt="Falls back to an avatar" />
    </GalleryVariant>
    <GalleryVariant label="the default slot overlays the image">
      <LazyImage class="art" :src="albumPlaceholder" alt="With an overlay">
        <div class="art__overlay">
          <LoadingSpinner :size="48" />
        </div>
      </LazyImage>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="SvgIcon">
    <GalleryVariant :label="`the registry — ${UI_ICONS.length} names, size 24`">
      <div class="icon-grid">
        <div v-for="name in UI_ICONS" :key="name" class="icon-cell">
          <SvgIcon :name="name" :size="24" />
          <span class="icon-cell__name text-mono-small">{{ name }}</span>
        </div>
      </div>
    </GalleryVariant>
    <GalleryVariant
      :label="`the ${KEYBOARD_ICONS.length} keyboard glyphs — on the key colours they were drawn for`">
      <div class="icon-grid icon-grid--dark">
        <div v-for="name in KEYBOARD_ICONS" :key="name" class="icon-cell icon-cell--dark">
          <SvgIcon :name="name" :size="24" />
          <span class="icon-cell__name text-mono-small">{{ name }}</span>
        </div>
      </div>
    </GalleryVariant>
    <GalleryVariant label="size — numeric sets attributes, a string sizes from CSS">
      <SvgIcon name="play" :size="16" />
      <SvgIcon name="play" :size="24" />
      <SvgIcon name="play" :size="48" />
      <SvgIcon name="play" size="small" />
      <SvgIcon name="play" size="large" />
    </GalleryVariant>
    <GalleryVariant label="color — the SVG is recoloured to currentColor, so this wins">
      <SvgIcon name="heart" :size="32" color="var(--color-brand)" />
      <SvgIcon name="heart" :size="32" color="var(--color-error)" />
      <SvgIcon name="heart" :size="32" color="var(--color-success)" />
      <SvgIcon name="heart" :size="32" />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="AppIcon">
    <GalleryVariant :label="`every accepted name — ${APP_ICON_NAMES.length}, size 48`">
      <div class="icon-grid">
        <div v-for="name in APP_ICON_NAMES" :key="name" class="icon-cell">
          <AppIcon :name="name" :size="48" />
          <span class="icon-cell__name text-mono-small">{{ name }}</span>
        </div>
      </div>
    </GalleryVariant>
    <GalleryVariant label="size — 32 (small) / 64 (medium) / 72 (large), clamped to 64 below 4:3">
      <AppIcon name="spotify" size="small" />
      <AppIcon name="spotify" size="medium" />
      <AppIcon name="spotify" size="large" />
    </GalleryVariant>
    <GalleryVariant label="loading — the artwork gives way to a spinner, the tile stays">
      <AppIcon name="spotify" :size="32" loading />
      <AppIcon name="spotify" :size="48" loading />
      <AppIcon name="spotify" size="medium" loading />
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="Logo">
    <GalleryVariant label='position="center" — 48px' contain :contain-height="140">
      <Logo position="center" />
    </GalleryVariant>
    <GalleryVariant label='position="top" — 32px' contain :contain-height="140">
      <Logo position="top" />
    </GalleryVariant>
    <GalleryVariant label="visible=false — fades and lifts, it does not unmount" contain :contain-height="140">
      <Logo position="top" :visible="false" />
    </GalleryVariant>
  </GalleryItem>
</template>

<script setup>
import GalleryItem from '../GalleryItem.vue';
import GalleryVariant from '../GalleryVariant.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import SvgIcon, { ICON_NAMES } from '@/components/ui/SvgIcon.vue';
import AppIcon, { APP_ICON_NAMES } from '@/components/ui/AppIcon.vue';
import Logo from '@/components/ui/Logo.vue';
import albumPlaceholder from '@/assets/images/album-placeholder.svg';

// The keyboard glyphs get their own strip, and it is not cosmetic. SvgIcon
// rewrites every `fill="#…"` to currentColor, including one inside a <mask>, so
// an icon masked by luminance disappears when currentColor is dark. Those five
// are only ever drawn on VirtualKeyboard's keys, which set a light colour.
const KEYBOARD_ICONS = ICON_NAMES.filter(name => name.startsWith('keyboard'));
const UI_ICONS = ICON_NAMES.filter(name => !name.startsWith('keyboard'));
</script>

<style scoped>
/* The class goes on LazyImage itself, as AlbumCard and StationCard do: its
   layers are absolutely positioned, so the root collapses unless it is sized. */
.art {
  width: 120px;
  height: 120px;
  border-radius: var(--radius-03);
  background: var(--color-background-strong);
}

/* Same scrim and light currentColor as the design system's .card-loading-overlay,
   which is what the cards actually put in this slot. */
.art__overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-contrast-32);
  color: var(--color-text-contrast);
}

.icon-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
  gap: var(--space-03);
  width: 100%;
}

.icon-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-01);
  padding: var(--space-02);
  text-align: center;
  background: var(--color-background-neutral);
  border-radius: var(--radius-02);
}

.icon-cell__name {
  color: var(--color-text-light);
  overflow-wrap: anywhere;
}

.icon-grid--dark {
  padding: var(--space-03);
  background: var(--color-background-contrast);
  border-radius: var(--radius-03);
}

.icon-cell--dark {
  color: var(--color-text-contrast);
  background: var(--color-background-neutral-12);
}

.icon-cell--dark .icon-cell__name {
  color: var(--color-text-contrast-50);
}
</style>
