<template>
  <div class="loading-spinner" :style="spinnerStyle">
    <div class="loading-spinner-content">
      <div v-html="svgContent" class="loading-spinner-svg" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  size: {
    type: [String, Number],
    default: 32
  },
  variant: {
    type: String,
    default: 'default',
    validator: (value) => ['default', 'background'].includes(value)
  }
});

const spinnerStyle = computed(() => {
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
    '--spinner-size': `${sizeInPx}px`
  };
});

const svgContent = computed(() => {
  // Choose color based on variant
  const fillColor = props.variant === 'background' ? '#767C76' : '#FFFFFF';

  // Build the SVG with or without background
  const backgroundRect = props.variant === 'background'
    ? '<rect width="32" height="32" rx="8" fill="#F7F7F7"/>'
    : '';

  // Use cropped viewBox for transparent variant to make spinner appear larger
  const viewBox = props.variant === 'background' ? '0 0 32 32' : '2 2 28 28';

  return `<svg viewBox="${viewBox}" fill="none" xmlns="http://www.w3.org/2000/svg">
${backgroundRect}
<path fill="${fillColor}" d="M15.25 10V7a.75.75 0 0 1 1.5 0v3a.75.75 0 0 1-1.5 0" opacity="0.16">
  <animate attributeName="opacity" values="1;0.64;0.6;0.16;0.16;0.16;0.16;0.16;1" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M21.833 9.106a.751.751 0 0 1 1.062 1.06l-2.123 2.123a.75.75 0 0 1-1.06-1.062l2.121-2.121Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;1;0.64;0.6;0.16;0.16;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M25 15.25a.75.75 0 0 1 0 1.5h-3a.75.75 0 0 1 0-1.5h3Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;1;0.64;0.6;0.16;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M22.895 21.833a.751.751 0 0 1-1.061 1.062l-2.122-2.123a.75.75 0 0 1 1.061-1.06l2.122 2.121Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;1;0.64;0.6;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M15.25 25v-3a.75.75 0 0 1 1.5 0v3a.75.75 0 0 1-1.5 0Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;0.16;1;0.64;0.6;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M11.227 19.712a.751.751 0 0 1 1.062 1.06l-2.123 2.123a.75.75 0 0 1-1.06-1.062l2.121-2.121Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;0.16;0.16;1;0.64;0.6;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M10 15.25a.75.75 0 0 1 0 1.5H7a.75.75 0 0 1 0-1.5h3Z" opacity="0.16">
  <animate attributeName="opacity" values="0.6;0.16;0.16;0.16;0.16;0.16;1;0.64;0.6" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M9.106 10.167a.751.751 0 0 1 1.06-1.061l2.123 2.122a.75.75 0 0 1-1.062 1.06l-2.121-2.121Z" opacity="0.16">
  <animate attributeName="opacity" values="0.64;0.6;0.16;0.16;0.16;0.16;0.16;1;0.64" dur="1.4s" repeatCount="indefinite"/>
</path>
</svg>`;
});
</script>

<style scoped>
.loading-spinner {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: var(--radius-02);
  overflow: hidden;
  width: var(--spinner-size);
  height: var(--spinner-size);
}

.loading-spinner-content {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
}

.loading-spinner-svg {
  display: block;
  width: 100%;
  height: 100%;
}

.loading-spinner-svg :deep(svg) {
  width: 100% !important;
  height: 100% !important;
  display: block;
}

@media (max-aspect-ratio: 4/3) {
  .loading-spinner {
    width: 64px !important;
    height: 64px !important;
    --spinner-size: 64px;
  }
}
</style>
