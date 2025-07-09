<!-- frontend/src/components/ui/Logo.vue -->
<template>
  <div class="logo-container" :class="positionClass">
    <svg 
      xmlns="http://www.w3.org/2000/svg" 
      class="logo-svg"
      :class="sizeClass"
      viewBox="0 0 197 48"
      preserveAspectRatio="xMidYMid meet"
    >
      <g fill="#A6ACA6" clip-path="url(#a)">
        <path d="M24.298 35.676c2.198-3.798 7.43-5.325 13.958-4.665l6.74 11.7H23.59c-.64-2.618-.448-5.036.708-7.035Z"/>
        <path d="M17.526 29.165c2.226 3.847 1.973 8.684-.247 13.544l-14.28.003 10.313-17.76c1.766 1.063 3.206 2.466 4.216 4.212h-.002Z" opacity=".7"/>
        <path d="M28.18 27.427c2.408 0 4.691-.797 6.73-2.223L24.087 6.405l-7.68 13.218c2.884 4.788 7.09 7.803 11.77 7.803l.003.002Z" opacity=".34"/>
        <path fill-rule="evenodd" d="M83.904 30.727c0-6.583-4.773-7.94-11.067-9.546l-.407-.102c-3.889-.967-6.465-1.61-6.465-4.711 0-2.181 2.099-3.497 5.596-3.497 4.073 0 6.212 1.81 6.665 5.225h4.897c-.412-6.048-5.102-9.422-11.563-9.422-6.416 0-10.449 2.92-10.449 8.145 0 5.597 4.977 7.365 9.586 8.517 5.802 1.44 8.311 2.1 8.311 5.595 0 2.265-1.688 4.197-6.048 4.197-5.265 0-7.816-2.467-8.064-6.211H60c.288 6.664 5.76 10.41 12.96 10.41 7.406 0 10.944-3.91 10.944-8.6Zm97.645-21.435h-4.32v29.415h4.32v-7.61l2.923-2.756 6.624 10.368h5.183l-8.845-13.21 7.981-7.816h-5.308l-8.558 8.435V9.29v.001Zm-16.967 11.23c2.88 0 3.784 1.153 3.784 2.675 0 2.016-1.974 2.427-5.224 3.045-5.267 1.03-8.681 2.47-8.681 7.077 0 3.537 2.633 6.006 6.912 6.006 3.249 0 5.636-1.275 6.953-3.126h.082c.411 2.057 1.48 2.88 3.784 2.88a6.493 6.493 0 0 0 2.51-.453v-2.058c-1.686.165-2.016-.616-2.016-2.097v-9.999c0-5.595-3.456-7.365-8.104-7.365-6.336 0-8.928 3.17-9.134 7.2h4.197c.206-2.715 1.481-3.784 4.937-3.784Zm3.784 10.205c0 3.414-2.468 5.184-6.048 5.184-2.345 0-3.497-1.07-3.497-3.004 0-2.14 1.563-3.168 5.144-3.95 2.016-.41 3.702-.904 4.401-1.398v3.168Zm-26.271 8.598c-6.583 0-10.575-4.566-10.575-11.109 0-6.54 3.992-11.107 10.575-11.107 6.582 0 10.573 4.566 10.573 11.107 0 6.543-3.99 11.11-10.575 11.11h.002Zm0-3.538c4.115 0 6.254-3.25 6.254-7.57 0-4.361-2.14-7.57-6.256-7.57-4.113 0-6.252 3.209-6.252 7.57 0 4.32 2.14 7.57 6.254 7.57Zm-13.413-11.603c0-4.813-3.086-7.075-6.994-7.075-3.622 0-5.803 1.645-6.913 3.29h-.082v-2.714h-4.32v21.024h4.32V25.914c0-3.128 2.139-5.145 5.472-5.145 2.963 0 4.155 1.812 4.155 4.65v13.29h4.362V24.184ZM96.813 39.325c-6.582 0-10.573-4.566-10.573-11.109 0-6.54 3.99-11.107 10.575-11.107 6.581 0 10.572 4.566 10.572 11.107 0 6.543-3.991 11.11-10.574 11.11Zm0-3.538c4.114 0 6.255-3.25 6.255-7.57 0-4.361-2.141-7.57-6.255-7.57-4.115 0-6.254 3.209-6.254 7.57 0 4.32 2.138 7.57 6.254 7.57Z" clip-rule="evenodd"/>
      </g>
      <defs>
        <clipPath id="a">
          <path fill="#fff" d="M0 0h196.5v48H0z"/>
        </clipPath>
      </defs>
    </svg>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  position: {
    type: String,
    default: 'center', // 'center' | 'top'
    validator: (value) => ['center', 'top'].includes(value)
  },
  size: {
    type: String,
    default: 'large', // 'large' | 'small'
    validator: (value) => ['large', 'small'].includes(value)
  },
  visible: {
    type: Boolean,
    default: true
  }
});

const positionClass = computed(() => ({
  'logo-center': props.position === 'center',
  'logo-top': props.position === 'top',
  'logo-hidden': !props.visible
}));

const sizeClass = computed(() => ({
  'logo-large': props.size === 'large',
  'logo-small': props.size === 'small'
}));
</script>

<style scoped>
.logo-container {
  position: fixed;
  left: 50%;
  transform: translateX(-50%);
  z-index: 100;
  transition: all var(--transition-spring-soft);
  pointer-events: none;
}

/* Positions */
.logo-center {
  top: 50%;
  transform: translateX(-50%) translateY(-50%);
}

.logo-top {
  top: var(--space-06); /* 32px */
  transform: translateX(-50%);
}

.logo-hidden {
  opacity: 0;
  transform: translateX(-50%) translateY(-50%) scale(0.8);
}

/* Tailles */
.logo-svg {
  display: block;
  transition: all var(--transition-spring-soft);
}

.logo-large {
  height: 48px;
  width: auto;
}

.logo-small {
  height: 32px;
  width: auto;
}

/* Responsive - Mobile */
@media (max-aspect-ratio: 4/3) {
  .logo-large {
    height: 40px; /* 40px au lieu de 48px sur mobile */
  }
}
</style>