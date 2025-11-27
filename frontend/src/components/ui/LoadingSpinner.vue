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
  if (typeof props.size === 'string') {
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
  // For string sizes, styles are handled by CSS classes
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
<path fill="${fillColor}" d="M11.2413 6.25841V3.38762C11.2413 3.19727 11.3169 3.01473 11.4515 2.88013C11.5861 2.74554 11.7687 2.66992 11.959 2.66992C12.1494 2.66992 12.3319 2.74554 12.4665 2.88013C12.6011 3.01473 12.6767 3.19727 12.6767 3.38762V6.25841C12.6767 6.44876 12.6011 6.63131 12.4665 6.7659C12.3319 6.9005 12.1494 6.97611 11.959 6.97611C11.7687 6.97611 11.5861 6.9005 11.4515 6.7659C11.3169 6.63131 11.2413 6.44876 11.2413 6.25841Z" opacity="0.16">
  <animate attributeName="opacity" values="1;0.64;0.6;0.16;0.16;0.16;0.16;0.16;1" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M17.5407 5.40285C17.6771 5.27791 17.8565 5.21041 18.0415 5.2144C18.2264 5.21839 18.4027 5.29355 18.5337 5.42424C18.6646 5.55494 18.7401 5.7311 18.7445 5.91605C18.7488 6.10101 18.6816 6.28051 18.557 6.41719L16.5254 8.44876C16.3906 8.58327 16.208 8.65873 16.0175 8.65856C15.8271 8.65838 15.6446 8.58256 15.5101 8.4478C15.3756 8.31304 15.3001 8.13036 15.3003 7.93995C15.3005 7.74954 15.3763 7.56701 15.511 7.4325L17.5407 5.40285Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;1;0.64;0.6;0.16;0.16;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M20.5714 11.2823C20.7617 11.2823 20.9443 11.3579 21.0789 11.4925C21.2135 11.6271 21.2891 11.8096 21.2891 12C21.2891 12.1903 21.2135 12.3729 21.0789 12.5075C20.9443 12.6421 20.7617 12.7177 20.5714 12.7177H17.7006C17.5103 12.7177 17.3277 12.6421 17.1931 12.5075C17.0585 12.3729 16.9829 12.1903 16.9829 12C16.9829 11.8096 17.0585 11.6271 17.1931 11.4925C17.3277 11.3579 17.5103 11.2823 17.7006 11.2823H20.5714Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;1;0.64;0.6;0.16;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M18.557 17.5817C18.6845 17.7178 18.7541 17.8981 18.7512 18.0846C18.7482 18.2711 18.6728 18.4491 18.541 18.5811C18.4092 18.713 18.2312 18.7885 18.0447 18.7917C17.8583 18.7948 17.6779 18.7254 17.5417 18.598L15.511 16.5664C15.3804 16.431 15.3081 16.2496 15.3098 16.0615C15.3116 15.8733 15.3871 15.6933 15.5203 15.5603C15.6534 15.4273 15.8334 15.3519 16.0216 15.3504C16.2098 15.3488 16.3911 15.4212 16.5264 15.552L18.557 17.5817Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;1;0.64;0.6;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M11.2413 20.6124V17.7416C11.2413 17.5513 11.3169 17.3687 11.4515 17.2341C11.5861 17.0995 11.7687 17.0239 11.959 17.0239C12.1494 17.0239 12.3319 17.0995 12.4665 17.2341C12.6011 17.3687 12.6767 17.5513 12.6767 17.7416V20.6124C12.6767 20.8028 12.6011 20.9853 12.4665 21.1199C12.3319 21.2545 12.1494 21.3301 11.959 21.3301C11.7687 21.3301 11.5861 21.2545 11.4515 21.1199C11.3169 20.9853 11.2413 20.8028 11.2413 20.6124Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;0.16;1;0.64;0.6;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M7.39153 15.5521C7.52798 15.4272 7.70735 15.3597 7.89232 15.3637C8.07728 15.3677 8.25358 15.4428 8.38452 15.5735C8.51546 15.7042 8.59096 15.8804 8.59529 16.0653C8.59963 16.2503 8.53246 16.4298 8.40779 16.5665L6.37623 18.598C6.24146 18.7326 6.05878 18.808 5.86838 18.8078C5.67797 18.8077 5.49543 18.7319 5.36092 18.5971C5.22641 18.4623 5.15094 18.2796 5.15112 18.0892C5.1513 17.8988 5.22711 17.7163 5.36188 17.5818L7.39153 15.5521Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;0.16;0.16;1;0.64;0.6;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M6.2174 11.2823C6.40774 11.2823 6.59029 11.3579 6.72488 11.4925C6.85948 11.6271 6.93509 11.8096 6.93509 12C6.93509 12.1903 6.85948 12.3729 6.72488 12.5075C6.59029 12.6421 6.40774 12.7177 6.2174 12.7177H3.3466C3.15626 12.7177 2.97371 12.6421 2.83912 12.5075C2.70452 12.3729 2.62891 12.1903 2.62891 12C2.62891 11.8096 2.70452 11.6271 2.83912 11.4925C2.97371 11.3579 3.15626 11.2823 3.3466 11.2823H6.2174Z" opacity="0.16">
  <animate attributeName="opacity" values="0.6;0.16;0.16;0.16;0.16;0.16;1;0.64;0.6" dur="1.4s" repeatCount="indefinite"/>
</path>
<path fill="${fillColor}" d="M5.36203 6.4182C5.23925 6.28139 5.17356 6.10273 5.17848 5.91897C5.1834 5.73521 5.25855 5.56032 5.38847 5.43027C5.5184 5.30023 5.69322 5.22491 5.87697 5.21982C6.06073 5.21473 6.23945 5.28025 6.37638 5.4029L8.40794 7.43351C8.54245 7.56827 8.61792 7.75095 8.61774 7.94136C8.61756 8.13176 8.54175 8.3143 8.40699 8.44881C8.27222 8.58332 8.08954 8.65879 7.89914 8.65861C7.70873 8.65843 7.52619 8.58262 7.39168 8.44785L5.36203 6.4182Z" opacity="0.16">
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
