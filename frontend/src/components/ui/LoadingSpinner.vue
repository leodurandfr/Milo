<template>
  <div class="loading-spinner" :class="spinnerClass" :style="spinnerStyle">
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

const spinnerClass = computed(() => {
  // Use CSS classes for named sizes (enables responsive behavior)
  // 'inherit' means parent controls size via --spinner-size variable
  if (typeof props.size === 'string' && props.size !== 'inherit') {
    return `loading-spinner--${props.size}`;
  }
  return '';
});

const spinnerStyle = computed(() => {
  // Only apply inline styles for numeric sizes
  if (typeof props.size === 'number') {
    return {
      width: `${props.size}px`,
      height: `${props.size}px`,
      '--spinner-size': `${props.size}px`
    };
  }
  // For 'inherit', let parent CSS control the size via --spinner-size
  // For other string sizes (small, medium, large), styles are handled by CSS classes
  return {};
});

const svgContent = computed(() => {
  // Use currentColor to inherit from parent's text color
  const fillColor = 'currentColor';

  // Build the SVG with or without background
  const backgroundRect = props.variant === 'background'
    ? '<rect width="24" height="24" rx="6" fill="#F7F7F7"/>'
    : '';

  // Use cropped viewBox for transparent variant to make spinner appear larger
  const viewBox = props.variant === 'background' ? '0 0 24 24' : '1.5 1.5 21 21';

  return `<svg viewBox="${viewBox}" fill="none" xmlns="http://www.w3.org/2000/svg">
${backgroundRect}
<path fill="${fillColor}" d="M11.241 6.258V3.388c0-.19.076-.373.21-.508a.718.718 0 0 1 1.016 0c.135.135.21.318.21.508v2.87c0 .19-.076.373-.21.508a.718.718 0 0 1-1.016 0 .718.718 0 0 1-.21-.508Z" opacity="0.16">
  <animate attributeName="opacity" values="1;0.64;0.6;0.16;0.16;0.16;0.16;0.16;1" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M17.54 5.403a.718.718 0 0 1 .508-.211.718.718 0 0 1 .508.21.718.718 0 0 1 .21.516.718.718 0 0 1-.209.508l-2.032 2.033a.718.718 0 0 1-.506.209.718.718 0 0 1-.508-.211.718.718 0 0 1-.21-.507.718.718 0 0 1 .21-.507l2.03-2.04Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;1;0.64;0.6;0.16;0.16;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M20.571 11.282c.19 0 .373.076.508.21.134.135.21.318.21.508s-.076.373-.21.508a.718.718 0 0 1-.508.21h-2.87a.718.718 0 0 1-.508-.21.718.718 0 0 1-.21-.508c0-.19.076-.373.21-.508a.718.718 0 0 1 .508-.21h2.87Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;1;0.64;0.6;0.16;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M18.557 17.582a.718.718 0 0 1-.19.497.718.718 0 0 1-.48.212.718.718 0 0 1-.497-.187l-2.031-2.032a.718.718 0 0 1-.207-.503.718.718 0 0 1 .213-.492.718.718 0 0 1 .493-.212.718.718 0 0 1 .501.19l2.031 2.03.167-.003Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;1;0.64;0.6;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M11.241 20.612v-2.87c0-.19.076-.373.21-.508a.718.718 0 0 1 1.016 0c.135.135.21.318.21.508v2.87c0 .19-.076.373-.21.508a.718.718 0 0 1-1.016 0 .718.718 0 0 1-.21-.508Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;0.16;1;0.64;0.6;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M7.392 15.552a.718.718 0 0 1 .5-.186c.184.005.36.08.49.21.13.13.205.306.21.49a.718.718 0 0 1-.185.5l-2.032 2.032a.718.718 0 0 1-1.015-1.016l2.032-2.03Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;0.16;0.16;1;0.64;0.6;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M6.217 11.282c.19 0 .373.076.508.21.134.135.21.318.21.508s-.076.373-.21.508a.718.718 0 0 1-.508.21h-2.87a.718.718 0 0 1-.508-.21.718.718 0 0 1-.21-.508c0-.19.076-.373.21-.508a.718.718 0 0 1 .508-.21h2.87Z" opacity="0.16">
  <animate attributeName="opacity" values="0.6;0.16;0.16;0.16;0.16;0.16;1;0.64;0.6" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M5.362 6.418a.718.718 0 0 1 .517-.196.718.718 0 0 1 .496.204l2.032 2.03a.718.718 0 0 1-1.015 1.016l-2.03-2.03a.718.718 0 0 1-.016-1.008l.016-.016Z" opacity="0.16">
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

/* Size variants matching SvgIcon dimensions - Desktop */
.loading-spinner--small {
  --spinner-size: 24px;
}

.loading-spinner--medium {
  --spinner-size: 28px;
}

.loading-spinner--large {
  --spinner-size: 32px;
}

/* Size variants matching SvgIcon dimensions - Mobile */
@media (max-aspect-ratio: 4/3) {
  .loading-spinner--small {
    --spinner-size: 20px;
  }

  .loading-spinner--medium {
    --spinner-size: 24px;
  }

  .loading-spinner--large {
    --spinner-size: 28px;
  }
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
</style>
