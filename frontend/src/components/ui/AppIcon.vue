<template>
  <div class="app-icon" :style="iconStyle" :class="{ 'size-large': props.size === 'large' || props.size === 72 }">
    <div class="app-icon-content">
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

const loadingIcon = `<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
<rect width="32" height="32" rx="8" fill="#F7F7F7"/>
<path fill="#767C76" d="M15.25 10V7a.75.75 0 0 1 1.5 0v3a.75.75 0 0 1-1.5 0" opacity="0.16">
  <animate attributeName="opacity" values="1;0.64;0.6;0.16;0.16;0.16;0.16;0.16;1" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="#767C76" d="M21.833 9.106a.751.751 0 0 1 1.062 1.06l-2.123 2.123a.75.75 0 0 1-1.06-1.062l2.121-2.121Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;1;0.64;0.6;0.16;0.16;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="#767C76" d="M25 15.25a.75.75 0 0 1 0 1.5h-3a.75.75 0 0 1 0-1.5h3Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;1;0.64;0.6;0.16;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="#767C76" d="M22.895 21.833a.751.751 0 0 1-1.061 1.062l-2.122-2.123a.75.75 0 0 1 1.061-1.06l2.122 2.121Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;1;0.64;0.6;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="#767C76" d="M15.25 25v-3a.75.75 0 0 1 1.5 0v3a.75.75 0 0 1-1.5 0Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;0.16;1;0.64;0.6;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="#767C76" d="M11.227 19.712a.751.751 0 0 1 1.062 1.06l-2.123 2.123a.75.75 0 0 1-1.06-1.062l2.121-2.121Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;0.16;0.16;1;0.64;0.6;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="#767C76" d="M10 15.25a.75.75 0 0 1 0 1.5H7a.75.75 0 0 1 0-1.5h3Z" opacity="0.16">
  <animate attributeName="opacity" values="0.6;0.16;0.16;0.16;0.16;0.16;1;0.64;0.6" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="#767C76" d="M9.106 10.167a.751.751 0 0 1 1.06-1.061l2.123 2.122a.75.75 0 0 1-1.062 1.06l-2.121-2.121Z" opacity="0.16">
  <animate attributeName="opacity" values="0.64;0.6;0.16;0.16;0.16;0.16;0.16;1;0.64" dur="1.4s" repeatCount="indefinite"/>
</path>
</svg>`;

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
  if (props.state === 'loading') {
    return loadingIcon;
  }

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