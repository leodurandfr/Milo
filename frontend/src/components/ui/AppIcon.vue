<template>
  <div class="app-icon" :style="iconStyle" :class="{
    'size-large': props.size === 'tile-lg' || props.size === 72,
    'app-icon--loading': loading
  }">
    <div class="app-icon-content">
      <!-- Loading replaces the artwork, not the tile: the slot keeps its size
           and shape so nothing around it reflows when the source settles. -->
      <LoadingSpinner v-if="loading" size="inherit" />
      <div v-else v-html="svgContent" class="app-icon-svg" />
    </div>
  </div>
</template>

<script>
// Global counter to generate unique IDs for each instance
let instanceCounter = 0;

const iconMapping = {
  'spotify': 'spotify',
  'mac': 'macos',
  'bluetooth': 'bluetooth',
  'radio': 'radio',
  'podcast': 'podcast',
  'airplay': 'airplay',
  'cd': 'cd',
  'dlna': 'dlna',
  'qobuz': 'qobuz',
  'tidal': 'tidal',
  'music_library': 'music-library',
  'multiroom': 'multiroom',
  'equalizer': 'equalizer',
  'lyrics': 'lyrics',
  'settings': 'settings',
  'milo': 'milo',
  'milo-client': 'milo-client'
};

/**
 * Every name `<AppIcon>` accepts. The mapping above is the single source of
 * truth: it drives the `name` validator and the component gallery's icon grid,
 * so neither can list a name the component cannot actually resolve.
 */
export const APP_ICON_NAMES = Object.keys(iconMapping);

/**
 * The tile rungs, in pixels. Exported so the gallery reads the accepted set
 * from here rather than from a validator it would have to parse.
 */
export const TILE_SIZE_PX = {
  'tile-sm': 32,
  'tile-md': 64,
  'tile-lg': 72
};

const TILE_SIZES = Object.keys(TILE_SIZE_PX);
</script>

<script setup>
import { computed } from 'vue';
import { logger } from '@/services/logger';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';

const instanceId = ++instanceCounter;

const props = defineProps({
  name: {
    type: String,
    required: true,
    validator: (value) => APP_ICON_NAMES.includes(value)
  },
  // A tile, not a glyph — which is why the named rungs are `tile-*` and not the
  // `small|medium|large` SvgIcon and IconButton use. The two scales are an
  // order of magnitude apart (a Dock tile is 72px where a large glyph is 32),
  // so sharing the vocabulary meant `size="small"` silently meaning 32 here and
  // 24 four lines below in the same template.
  size: {
    type: [String, Number],
    default: 32,
    validator: (value) =>
      typeof value === 'number' || TILE_SIZES.includes(value)
  },
  // The tile while its source is still coming up — a spinner on the plate the
  // artwork would otherwise cover. `name` still applies: it is the same icon,
  // in a state, not a different one.
  loading: {
    type: Boolean,
    default: false
  }
});

const svgModules = import.meta.glob('@/assets/app-icons/*.svg', {
  query: '?raw',
  eager: true
});

const prepareSvg = (svgString, prefix) => {
  let result = svgString;

  // Extract and rebuild the <svg> tag without width/height
  const svgTagMatch = result.match(/<svg([^>]*)>/);
  if (svgTagMatch) {
    let svgAttributes = svgTagMatch[1];
        // Remove width and height

    svgAttributes = svgAttributes.replace(/\s*width="[^"]*"/g, '');
    svgAttributes = svgAttributes.replace(/\s*height="[^"]*"/g, '');
    // Replace the original svg tag
    result = result.replace(/<svg[^>]*>/, `<svg${svgAttributes}>`);
  }

  // Make IDs unique
  const idPattern = /id="([^"]+)"/g;
  const ids = new Set();
  let match;

  while ((match = idPattern.exec(result)) !== null) {
    ids.add(match[1]);
  }

  ids.forEach(id => {
    const newId = `${prefix}-${id}`;
    result = result.replace(new RegExp(`id="${id}"`, 'g'), `id="${newId}"`);
    result = result.replace(new RegExp(`url\\(#${id}\\)`, 'g'), `url(#${newId})`);
    result = result.replace(new RegExp(`clip-path="url\\(#${id}\\)"`, 'g'), `clip-path="url(#${newId})"`);
    result = result.replace(new RegExp(`filter="url\\(#${id}\\)"`, 'g'), `filter="url(#${newId})"`);
  });

  return result;
};

const appIconsOriginal = Object.keys(svgModules).reduce((acc, path) => {
  const name = path.match(/\/([^/]+)\.svg$/)[1];
  acc[name] = svgModules[path].default;
  return acc;
}, {});

const iconStyle = computed(() => {
  let sizeInPx = 32;

  if (typeof props.size === 'number') {
    sizeInPx = props.size;
  } else if (typeof props.size === 'string') {
    sizeInPx = TILE_SIZE_PX[props.size] ?? 32;
  }

  return {
    width: `${sizeInPx}px`,
    height: `${sizeInPx}px`,
    '--icon-size': `${sizeInPx}px`
  };
});

const svgContent = computed(() => {
  const iconFileName = iconMapping[props.name] || props.name;
  const icon = appIconsOriginal[iconFileName];

  if (!icon) {
    logger.warn('component', `AppIcon "${props.name}" (mapped to "${iconFileName}") not found`);
    return '';
  }

  // Per-instance id prefix so duplicate icons don't collide via url(#id) in the DOM
  return prepareSvg(icon, `${iconFileName}-${instanceId}`);
});
</script>

<style scoped>
.app-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: var(--radius-02);
  overflow: hidden;
  width: var(--icon-size);
  height: var(--icon-size);
  max-width: 100%;
  max-height: 100%;
  isolation: isolate; /* Prevent mix-blend-mode from SVGs leaking to other elements */
}

/* App icons are drawn full-bleed, so the loading state has to paint the plate
   the artwork was carrying — otherwise the tile disappears and the row reads as
   a gap. The spinner is inset rather than full-bleed: it is a mark on a plate,
   where the artwork is the plate. No glyph is being swapped here — there is
   only a plate to sit on — so the transport's sizing does not apply, and at the
   tile's own size the ring crowds it. The multiplier is the plate's margin, not
   a size correction. */
.app-icon--loading {
  --spinner-size: calc(var(--icon-size) * 0.8);

  background: var(--color-background);
  color: var(--color-text);
}

.app-icon-content {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
}

.app-icon-svg {
  display: block;
  width: 100%;
  height: 100%;
}

.app-icon-svg :deep(svg) {
  width: 100% !important;
  height: 100% !important;
  display: block;
}

@media (max-aspect-ratio: 4/3) {
  .app-icon.size-large {
    width: 64px !important;
    height: 64px !important;
    --icon-size: 64px;
  }
}
</style>