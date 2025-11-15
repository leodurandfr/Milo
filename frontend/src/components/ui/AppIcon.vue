<template>
  <div class="app-icon" :style="iconStyle" :class="{ 'size-large': props.size === 'large' || props.size === 72 }">
    <LoadingSpinner v-if="props.state === 'loading'" :size="props.size" variant="background" />
    <div v-else class="app-icon-content">
      <div v-html="svgContent" class="app-icon-svg" />
    </div>
  </div>
</template>

<script>
// Global counter to generate unique IDs for each instance
let instanceCounter = 0;
</script>

<script setup>
import { computed } from 'vue';
import LoadingSpinner from './LoadingSpinner.vue';

// Generate a unique ID for this component instance
const instanceId = ++instanceCounter;

const props = defineProps({
  name: {
    type: String,
    required: true,
    validator: (value) => ['bluetooth', 'librespot', 'roc', 'radio', 'multiroom', 'equalizer', 'settings'].includes(value)
  },
  size: {
    type: [String, Number],
    default: 32
  },
  state: {
    type: String,
    default: 'normal',
    validator: (value) => ['normal', 'loading'].includes(value)
  }
});


const iconMapping = {
  'librespot': 'spotify',
  'roc': 'macos',
  'bluetooth': 'bluetooth',
  'radio': 'radio',
  'multiroom': 'multiroom',
  'equalizer': 'equalizer',
  'settings': 'settings'
};

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
    switch (props.size) {
      case 'large': sizeInPx = 72; break;
      case 'medium': sizeInPx = 64; break;
      case 'small': sizeInPx = 32; break;
      default: sizeInPx = 32;
    }
  }

  return {
    width: `${sizeInPx}px`,
    height: `${sizeInPx}px`,
    '--icon-size': `${sizeInPx}px`
  };
});

const svgContent = computed(() => {
  // Use the mapping to find the correct SVG file
  const iconFileName = iconMapping[props.name] || props.name;
  const icon = appIconsOriginal[iconFileName];

  if (!icon) {
    console.warn(`AppIcon "${props.name}" (mapped to "${iconFileName}") not found`);
    return '';
  }

  // Apply prepareSvg with a unique prefix for this instance
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