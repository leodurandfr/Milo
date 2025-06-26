<!-- frontend/src/components/ui/AppIcon.vue -->
<template>
  <div 
    class="app-icon" 
    :style="iconStyle"
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
    validator: (value) => ['bluetooth', 'spotify', 'roc'].includes(value)
  },
  size: { 
    type: [String, Number], 
    default: 32
    // Suppression du validator qui peut causer des problèmes
  }
});

// Icônes SVG intégrées - SANS width/height pour être scalables
const appIcons = {
  bluetooth: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 72 72"><rect width="72" height="72" fill="#0046EB" rx="18"/><path fill="#fff" d="M35.104 13.21a2.001 2.001 0 0 1 2.094.19l14 10.5a2 2 0 0 1 0 3.2L39.331 36l11.867 8.9a2 2 0 0 1 0 3.2l-14 10.5a2.001 2.001 0 0 1-3.2-1.6V39.999l-10.8 8.1a2 2 0 0 1-2.4-3.199L32.665 36l-11.867-8.9-.157-.13a2 2 0 0 1 2.387-3.184l.17.115 10.8 8.1V15a2 2 0 0 1 1.106-1.79ZM37.998 53l8.667-6.5-8.667-6.501v13Zm0-21 8.667-6.5-8.667-6.501v13Z"/></svg>`,
  
  spotify: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 72 72"><g clip-path="url(#a)"><path fill="#000" d="M0 0h72v72H0z"/><g clip-path="url(#b)"><path fill="#1ED760" d="M36 8.875C21.027 8.875 8.875 21.027 8.875 36S21.027 63.125 36 63.125 63.125 50.973 63.125 36 50.973 8.875 36 8.875Z"/><path fill="#000" d="M53.347 33.277c-.569 0-.919-.143-1.411-.427-7.788-4.648-21.711-5.764-30.724-3.248-.393.109-.885.284-1.41.284-1.444 0-2.549-1.127-2.549-2.581 0-1.488.919-2.33 1.903-2.614 3.85-1.127 8.16-1.663 12.852-1.663 7.984 0 16.351 1.663 22.465 5.228.854.492 1.411 1.17 1.411 2.472a2.534 2.534 0 0 1-2.537 2.549Zm-3.39 8.334c-.57 0-.952-.252-1.346-.46-6.836-4.046-17.03-5.676-26.097-3.215-.525.142-.81.284-1.302.284a2.124 2.124 0 0 1-2.121-2.122c0-1.17.568-1.946 1.695-2.264 3.04-.853 6.147-1.487 10.697-1.487 7.098 0 13.956 1.76 19.36 4.977.885.525 1.235 1.203 1.235 2.154-.01 1.181-.93 2.133-2.122 2.133Zm-2.943 7.175c-.46 0-.744-.142-1.17-.394-6.825-4.112-14.766-4.287-22.608-2.68-.427.11-.984.285-1.302.285-1.06 0-1.728-.842-1.728-1.728 0-1.127.667-1.663 1.488-1.838 8.958-1.98 18.112-1.804 25.922 2.866.667.427 1.06.81 1.06 1.805 0 .995-.776 1.684-1.662 1.684Z"/></g></g><defs><clipPath id="a"><rect width="72" height="72" fill="#fff" rx="18"/></clipPath><clipPath id="b"><path fill="#fff" d="M8.875 8h54.25v56H8.875z"/></clipPath></defs></svg>`,
  
  roc: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 72 72"><rect width="72" height="72" fill="#1D1D1F" rx="18"/><path fill="#F7F7F7" fill-rule="evenodd" d="M25.74 52.74c5.04 0 8.195-3.812 8.195-9.867 0-6.079-3.156-9.89-8.196-9.89-5.04 0-8.194 3.811-8.194 9.889 0 6.035 3.154 9.868 8.194 9.868h.002Zm14.968-29.509a1.88 1.88 0 0 0 .254-.956v-.764l-2.202.149c-.873.06-1.406.294-1.647.705a1.22 1.22 0 0 0-.154.63c0 .846.7 1.312 1.674 1.312.932 0 1.698-.424 2.075-1.076Zm8.347 2.531c-2.626 0-4.236-1.78-4.236-4.702.002-2.857 1.61-4.637 4.236-4.615 2.266 0 3.621 1.313 3.854 3.049h-1.737c-.19-.867-.931-1.546-2.117-1.546-1.482 0-2.393 1.166-2.393 3.113 0 1.969.911 3.177 2.393 3.177 1.123 0 1.884-.529 2.118-1.504h1.736c-.233 1.78-1.63 3.028-3.854 3.028Zm-28.44-.17v-8.98l1.717-.02v1.44h.127c.38-1.016 1.29-1.61 2.496-1.61 1.23 0 2.077.636 2.479 1.61h.148c.445-.973 1.483-1.61 2.732-1.61 1.822 0 2.88 1.102 2.88 2.965v6.206h-1.778V19.83c0-1.25-.574-1.842-1.737-1.842-1.165 0-1.906.847-1.906 1.927v5.675h-1.737v-5.95c0-1.017-.677-1.652-1.737-1.652-1.08 0-1.905.931-1.905 2.096v5.506h-1.779Zm5.124 4.552c6.989 0 11.352 4.913 11.352 12.727 0 7.793-4.363 12.706-11.352 12.706-6.987.001-11.35-4.891-11.35-12.706 0-7.814 4.363-12.727 11.352-12.727h-.002Zm15.163-5.824c-.551.912-1.524 1.419-2.711 1.419-1.736 0-3.006-1.037-2.986-2.647 0-1.63 1.207-2.562 3.347-2.689l2.435-.148v-.784c0-.995-.635-1.546-1.842-1.546-.996 0-1.694.36-1.886 1.017h-1.714c.191-1.502 1.631-2.498 3.684-2.498 2.266 0 3.537 1.122 3.537 3.007v6.14h-1.715v-1.27h-.149Zm9.551 17.196c5.125 1.122 7.158 3.156 7.158 6.649 0 4.553-3.579 7.412-9.064 7.412-5.378 0-8.958-2.753-9.254-7.008h3.07c.36 2.645 2.816 4.255 6.353 4.255 3.325 0 5.76-1.758 5.76-4.172 0-2.055-1.377-3.43-4.785-4.172l-2.732-.593c-4.934-1.08-7.074-3.324-7.074-6.818 0-4.045 3.58-6.904 8.683-6.904 4.913 0 8.449 2.944 8.597 7.115h-3.07c-.276-2.711-2.393-4.361-5.611-4.361-3.072 0-5.443 1.503-5.443 4.001 0 1.927 1.313 3.262 4.68 4.003l2.732.593Z" clip-rule="evenodd"/></svg>`
};

// Style computed avec taille forcée - VERSION SIMPLIFIÉE
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

// Contenu SVG
const svgContent = computed(() => {
  const icon = appIcons[props.name];
  if (!icon) {
    console.warn(`AppIcon "${props.name}" not found`);
    return '';
  }
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

/* Suppression des classes de tailles obsolètes */
</style>