<!-- frontend/src/components/ui/AppIcon.vue -->
<template>
  <div 
    class="app-icon" 
    :style="iconStyle"
    :class="{ 'size-large': props.size === 'large' || props.size === 72 }"
  >
    <div class="app-icon-content">
      <div v-html="svgContent" class="app-icon-svg" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

// Props
const props = defineProps({
  name: { 
    type: String, 
    required: true,
    validator: (value) => ['bluetooth', 'spotify', 'roc', 'multiroom', 'equalizer'].includes(value)
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

// Icône de chargement animée
const loadingIcon = `<svg width="32" height="32" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
<rect width="32" height="32" rx="8" fill="#F7F7F7"/>

<!-- Tracé 1: Top vertical (12h) -->
<path fill="#767C76" d="M15.25 10V7a.75.75 0 0 1 1.5 0v3a.75.75 0 0 1-1.5 0" opacity="0.16">
  <animate attributeName="opacity" values="1;0.64;0.6;0.16;0.16;0.16;0.16;0.16;1" dur="1.4s" repeatCount="indefinite"/>
</path>

<!-- Tracé 2: Top-right diagonal (1h30) -->
<path fill="#767C76" d="M21.833 9.106a.751.751 0 0 1 1.062 1.06l-2.123 2.123a.75.75 0 0 1-1.06-1.062l2.121-2.121Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;1;0.64;0.6;0.16;0.16;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>

<!-- Tracé 3: Right horizontal (3h) -->
<path fill="#767C76" d="M25 15.25a.75.75 0 0 1 0 1.5h-3a.75.75 0 0 1 0-1.5h3Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;1;0.64;0.6;0.16;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>

<!-- Tracé 4: Bottom-right diagonal (4h30) -->
<path fill="#767C76" d="M22.895 21.833a.751.751 0 0 1-1.061 1.062l-2.122-2.123a.75.75 0 0 1 1.061-1.06l2.122 2.121Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;1;0.64;0.6;0.16;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>

<!-- Tracé 5: Bottom vertical (6h) -->
<path fill="#767C76" d="M15.25 25v-3a.75.75 0 0 1 1.5 0v3a.75.75 0 0 1-1.5 0Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;0.16;1;0.64;0.6;0.16;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>

<!-- Tracé 6: Bottom-left diagonal (7h30) -->
<path fill="#767C76" d="M11.227 19.712a.751.751 0 0 1 1.062 1.06l-2.123 2.123a.75.75 0 0 1-1.06-1.062l2.121-2.121Z" opacity="0.16">
  <animate attributeName="opacity" values="0.16;0.16;0.16;0.16;0.16;1;0.64;0.6;0.16" dur="1.4s" repeatCount="indefinite"/>
</path>

<!-- Tracé 7: Left horizontal (9h) -->
<path fill="#767C76" d="M10 15.25a.75.75 0 0 1 0 1.5H7a.75.75 0 0 1 0-1.5h3Z" opacity="0.16">
  <animate attributeName="opacity" values="0.6;0.16;0.16;0.16;0.16;0.16;1;0.64;0.6" dur="1.4s" repeatCount="indefinite"/>
</path>

<!-- Tracé 8: Top-left diagonal (10h30) -->
<path fill="#767C76" d="M9.106 10.167a.751.751 0 0 1 1.06-1.061l2.123 2.122a.75.75 0 0 1-1.062 1.06l-2.121-2.121Z" opacity="0.16">
  <animate attributeName="opacity" values="0.64;0.6;0.16;0.16;0.16;0.16;0.16;1;0.64" dur="1.4s" repeatCount="indefinite"/>
</path>

</svg>`;

// Icônes SVG avec IDs uniques
const appIcons = {
  bluetooth: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><path fill="#0046EB" d="M0 28.8C0 18.72 0 13.679 1.962 9.828a18 18 0 0 1 7.866-7.866C13.678 0 18.72 0 28.8 0h14.4c10.08 0 15.121 0 18.972 1.962a18 18 0 0 1 7.866 7.866C72 13.678 72 18.72 72 28.8v14.4c0 10.08 0 15.121-1.962 18.972a18 18 0 0 1-7.866 7.866C58.322 72 53.28 72 43.2 72H28.8c-10.08 0-15.121 0-18.972-1.962a18 18 0 0 1-7.866-7.866C0 58.322 0 53.28 0 43.2V28.8Z"/><g clip-path="url(#bluetooth-clip)"><path fill="#fff" d="M35.218 13.434a1.75 1.75 0 0 1 1.832.166l14 10.5a1.75 1.75 0 0 1 0 2.8L38.917 36l12.133 9.1a1.75 1.75 0 0 1 0 2.8l-14 10.5a1.75 1.75 0 0 1-2.8-1.4V39.5l-11.2 8.4a1.75 1.75 0 0 1-2.1-2.8l12.133-9.101L20.95 26.9l-.138-.115A1.75 1.75 0 0 1 22.902 24l.148.1 11.2 8.399V15c0-.663.375-1.269.968-1.566ZM37.75 53.5l9.333-6.999-9.333-7v13.999Zm0-21 9.333-6.999-9.333-7v13.999Z"/></g><defs><clipPath id="bluetooth-clip"><path fill="#fff" d="M8 8h56v56H8z"/></clipPath></defs></svg>`,
  
  spotify: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><g clip-path="url(#spotify-outer-clip)"><path fill="#000" d="M0 28.8C0 18.72 0 13.679 1.962 9.828a18 18 0 0 1 7.866-7.866C13.678 0 18.72 0 28.8 0h14.4c10.08 0 15.121 0 18.972 1.962a18 18 0 0 1 7.866 7.866C72 13.678 72 18.72 72 28.8v14.4c0 10.08 0 15.121-1.962 18.972a18 18 0 0 1-7.866 7.866C58.322 72 53.28 72 43.2 72H28.8c-10.08 0-15.121 0-18.972-1.962a18 18 0 0 1-7.866-7.866C0 58.322 0 53.28 0 43.2V28.8Z"/><g clip-path="url(#spotify-inner-clip)"><path fill="#1ED760" d="M36 8.875C21.027 8.875 8.875 21.027 8.875 36S21.027 63.125 36 63.125 63.125 50.973 63.125 36 50.973 8.875 36 8.875Z"/><path fill="#000" d="M53.347 33.277c-.569 0-.919-.143-1.411-.427-7.788-4.648-21.711-5.764-30.724-3.248-.393.109-.885.284-1.41.284-1.444 0-2.549-1.127-2.549-2.581 0-1.488.919-2.33 1.903-2.614 3.85-1.127 8.16-1.663 12.852-1.663 7.984 0 16.351 1.663 22.465 5.228.854.492 1.411 1.17 1.411 2.472a2.534 2.534 0 0 1-2.537 2.549Zm-3.39 8.334c-.57 0-.952-.252-1.346-.46-6.836-4.046-17.03-5.676-26.097-3.215-.525.142-.81.284-1.302.284a2.124 2.124 0 0 1-2.121-2.122c0-1.17.568-1.946 1.695-2.264 3.04-.853 6.147-1.487 10.697-1.487 7.098 0 13.956 1.76 19.36 4.977.885.525 1.235 1.203 1.235 2.154-.01 1.181-.93 2.133-2.122 2.133Zm-2.943 7.175c-.46 0-.744-.142-1.17-.394-6.825-4.112-14.766-4.287-22.608-2.68-.427.11-.984.285-1.302.285-1.06 0-1.728-.842-1.728-1.728 0-1.127.667-1.663 1.488-1.838 8.958-1.98 18.112-1.804 25.922 2.866.667.426 1.06.81 1.06 1.805 0 .995-.776 1.684-1.662 1.684Z"/></g></g><defs><clipPath id="spotify-outer-clip"><path fill="#fff" d="M0 0h72v72H0z"/></clipPath><clipPath id="spotify-inner-clip"><path fill="#fff" d="M8.875 8h54.25v56H8.875z"/></clipPath></defs></svg>`,
  
  roc: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" fill="#1D1D1F" rx="18"/><path fill="#F7F7F7" fill-rule="evenodd" d="M25.74 52.74c5.04 0 8.195-3.812 8.195-9.867 0-6.079-3.156-9.89-8.196-9.89-5.04 0-8.194 3.811-8.194 9.889 0 6.035 3.154 9.868 8.194 9.868h.002Zm14.968-29.509a1.88 1.88 0 0 0 .254-.956v-.764l-2.202.149c-.873.06-1.406.294-1.647.705a1.22 1.22 0 0 0-.154.63c0 .846.7 1.312 1.674 1.312.932 0 1.698-.424 2.075-1.076Zm8.347 2.531c-2.626 0-4.236-1.78-4.236-4.702.002-2.857 1.61-4.637 4.236-4.615 2.266 0 3.621 1.313 3.854 3.049h-1.737c-.19-.867-.931-1.546-2.117-1.546-1.482 0-2.393 1.166-2.393 3.113 0 1.969.911 3.177 2.393 3.177 1.123 0 1.884-.529 2.118-1.504h1.736c-.233 1.78-1.63 3.028-3.854 3.028Zm-28.44-.17v-8.98l1.717-.02v1.44h.127c.38-1.016 1.29-1.61 2.496-1.61 1.23 0 2.077.636 2.479 1.61h.148c.445-.973 1.483-1.61 2.732-1.61 1.822 0 2.88 1.102 2.88 2.965v6.206h-1.778V19.83c0-1.25-.574-1.842-1.737-1.842-1.165 0-1.906.847-1.906 1.927v5.675h-1.737v-5.95c0-1.017-.677-1.652-1.737-1.652-1.08 0-1.905.931-1.905 2.096v5.506h-1.779Zm5.124 4.552c6.989 0 11.352 4.913 11.352 12.727 0 7.793-4.363 12.706-11.352 12.706-6.987.001-11.35-4.891-11.35-12.706 0-7.814 4.363-12.727 11.352-12.727h-.002Zm15.163-5.824c-.551.912-1.524 1.419-2.711 1.419-1.736 0-3.006-1.037-2.986-2.647 0-1.63 1.207-2.562 3.347-2.689l2.435-.148v-.784c0-.995-.635-1.546-1.842-1.546-.996 0-1.694.36-1.886 1.017h-1.714c.191-1.502 1.631-2.498 3.684-2.498 2.266 0 3.537 1.122 3.537 3.007v6.14h-1.715v-1.27h-.149Zm9.551 17.196c5.125 1.122 7.158 3.156 7.158 6.649 0 4.553-3.579 7.412-9.064 7.412-5.378 0-8.958-2.753-9.254-7.008h3.07c.36 2.645 2.816 4.255 6.353 4.255 3.325 0 5.76-1.758 5.76-4.172 0-2.055-1.377-3.43-4.785-4.172l-2.732-.593c-4.934-1.08-7.074-3.324-7.074-6.818 0-4.045 3.58-6.904 8.683-6.904 4.913 0 8.449 2.944 8.597 7.115h-3.07c-.276-2.711-2.393-4.361-5.611-4.361-3.072 0-5.443 1.503-5.443 4.001 0 1.927 1.313 3.262 4.68 4.003l2.732.593Z" clip-rule="evenodd"/></svg>`,

  multiroom: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><path fill="#EE642D" d="M0 28.8C0 18.72 0 13.679 1.962 9.828a18 18 0 0 1 7.866-7.866C13.678 0 18.72 0 28.8 0h14.4c10.08 0 15.121 0 18.972 1.962a18 18 0 0 1 7.866 7.866C72 13.678 72 18.72 72 28.8v14.4c0 10.08 0 15.121-1.962 18.972a18 18 0 0 1-7.866 7.866C58.322 72 53.28 72 43.2 72H28.8c-10.08 0-15.121 0-18.972-1.962a18 18 0 0 1-7.866-7.866C0 58.322 0 53.28 0 43.2V28.8Z"/><g fill="#fff" clip-path="url(#multiroom-clip)"><path d="M41.25 36a5.25 5.25 0 1 0-10.5 0 5.25 5.25 0 0 0 10.5 0Zm3.5 0a8.75 8.75 0 1 1-17.5 0 8.75 8.75 0 0 1 17.5 0Z" opacity=".95"/><path d="M22.956 24.333a1.75 1.75 0 0 1 2.607 2.334 13.99 13.99 0 0 0-.42 18.177l.42.49.114.139a1.751 1.751 0 0 1-2.72 2.194 17.487 17.487 0 0 1 0-23.334Zm23.618-.136a1.75 1.75 0 0 1 2.47.136A17.487 17.487 0 0 1 53.504 36l-.018.805a17.487 17.487 0 0 1-4.444 10.862 1.75 1.75 0 0 1-2.606-2.334 13.99 13.99 0 0 0 3.555-8.688l.013-.645c0-3.444-1.27-6.767-3.568-9.333a1.75 1.75 0 0 1 .137-2.47Z" opacity=".7"/><path d="M17.25 17.629a1.75 1.75 0 0 1 2.498 2.45 22.726 22.726 0 0 0-.76 31.027l.76.814.119.134a1.75 1.75 0 0 1-2.616 2.318 26.228 26.228 0 0 1 0-36.743Zm35.024-.024a1.75 1.75 0 0 1 2.475.024 26.223 26.223 0 0 1 0 36.743 1.75 1.75 0 0 1-2.497-2.452 22.726 22.726 0 0 0 0-31.84 1.75 1.75 0 0 1 .022-2.475Z" opacity=".4"/></g><defs><clipPath id="multiroom-clip"><path fill="#fff" d="M8 8h56v56H8z"/></clipPath></defs></svg>`,

  equalizer: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><path fill="url(#equalizer-bg)" d="M0 28.8C0 18.72 0 13.679 1.962 9.828a18 18 0 0 1 7.866-7.866C13.678 0 18.72 0 28.8 0h14.4c10.08 0 15.121 0 18.972 1.962a18 18 0 0 1 7.866 7.866C72 13.678 72 18.72 72 28.8v14.4c0 10.08 0 15.121-1.962 18.972a18 18 0 0 1-7.866 7.866C58.322 72 53.28 72 43.2 72H28.8c-10.08 0-15.121 0-18.972-1.962a18 18 0 0 1-7.866-7.866C0 58.322 0 53.28 0 43.2V28.8Z"/><g clip-path="url(#equalizer-clip)"><path fill="url(#equalizer-text)" d="M20.25 15c.966 0 1.75.784 1.75 1.75v14.22a7.003 7.003 0 0 1 0 13.558V55.25a1.75 1.75 0 1 1-3.5 0V44.528a7.002 7.002 0 0 1 0-13.557V16.75c0-.966.784-1.75 1.75-1.75ZM36 15c.967 0 1.75.784 1.75 1.75v3.72a7.003 7.003 0 0 1 0 13.558V55.25a1.75 1.75 0 1 1-3.5 0V34.028a7.002 7.002 0 0 1 0-13.557V16.75c0-.966.783-1.75 1.75-1.75Zm15.75 0c.967 0 1.75.784 1.75 1.75v21.22a7.003 7.003 0 0 1 0 13.558v3.722a1.75 1.75 0 1 1-3.5 0v-3.722a7.002 7.002 0 0 1 0-13.557V16.75c0-.966.783-1.75 1.75-1.75Zm0 26.25a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm-31.5-7a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM36 23.75a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Z"/></g><defs><linearGradient id="equalizer-bg" x1="36" x2="36" y1="0" y2="72" gradientUnits="userSpaceOnUse"><stop stop-color="#393E3B"/><stop offset="1" stop-color="#1D201E"/></linearGradient><linearGradient id="equalizer-text" x1="36" x2="36" y1="15" y2="57" gradientUnits="userSpaceOnUse"><stop stop-color="#fff"/><stop offset="1" stop-color="#C7D6E5"/></linearGradient><clipPath id="equalizer-clip"><path fill="#fff" d="M8 8h56v56H8z"/></clipPath></defs></svg>`,
};

// Style computed avec taille forcée
const iconStyle = computed(() => {
  let sizeInPx = 32; // Default
  
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

// Contenu SVG sans modification nécessaire
const svgContent = computed(() => {
  // Si on est en état loading, retourner l'icône de chargement
  if (props.state === 'loading') {
    return loadingIcon;
  }
  
  // Sinon, retourner l'icône normale
  const icon = appIcons[props.name];
  if (!icon) {
    console.warn(`AppIcon "${props.name}" not found`);
    return '';
  }
  
  // Retourner directement l'icône (les IDs sont déjà uniques)
  return icon;
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
  /* Force la taille via les variables CSS */
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

/* Mobile responsive - Override taille large */
@media (max-aspect-ratio: 4/3) {
  .app-icon.size-large {
    width: 64px !important;
    height: 64px !important;
    --icon-size: 64px;
  }
}
</style>