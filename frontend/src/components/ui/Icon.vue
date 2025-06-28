<template>
  <div 
    class="icon" 
    :style="iconStyle"
    v-html="svgContent"
  />
</template>

<script>
// Import de tous vos SVG
import playIcon from '@/assets/icons/play.svg?raw';
import pauseIcon from '@/assets/icons/pause.svg?raw';
import nextIcon from '@/assets/icons/next.svg?raw';
import previousIcon from '@/assets/icons/previous.svg?raw';
import volumeIcon from '@/assets/icons/volume.svg?raw';
import plusIcon from '@/assets/icons/plus.svg?raw';
import minusIcon from '@/assets/icons/minus.svg?raw';
import threeDotsIcon from '@/assets/icons/three-dots.svg?raw';
import closeDotsIcon from '@/assets/icons/close-dots.svg?raw';
// Nouvelles icônes à ajouter
import resetIcon from '@/assets/icons/reset.svg?raw';
import settingsIcon from '@/assets/icons/settings.svg?raw';

const icons = {
  play: playIcon,
  pause: pauseIcon,
  next: nextIcon,
  previous: previousIcon,
  volume: volumeIcon,
  plus: plusIcon,
  minus: minusIcon,
  threeDots: threeDotsIcon,
  closeDots: closeDotsIcon,
  reset: resetIcon,
  settings: settingsIcon,
};

export default {
  name: 'Icon',
  props: {
    name: { type: String, required: true },
    size: { type: [String, Number], default: 24 }
  },
  computed: {
    iconStyle() {
      const scale = this.size / 24;
      return {
        width: `${this.size}px`,
        height: `${this.size}px`,
        '--icon-scale': scale
      };
    },
    svgContent() {
      const icon = icons[this.name];
      if (!icon) {
        console.warn(`Icon "${this.name}" not found`);
        return '';
      }
      
      // Nettoyer les attributs fill pour permettre currentColor
      return icon
        .replace(/fill="#[^"]*"/g, 'fill="currentColor"')
        .replace(/fill='#[^']*'/g, 'fill="currentColor"');
    }
  }
};
</script>

<style scoped>
.icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.icon :deep(svg) {
  fill: currentColor;
  transform: scale(var(--icon-scale, 1));
}
</style>