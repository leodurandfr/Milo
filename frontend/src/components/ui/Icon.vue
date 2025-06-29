<!-- frontend/src/components/ui/Icon.vue -->
<template>
  <div 
    class="icon" 
    :class="{ 'icon--responsive': responsive }"
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
import resetIcon from '@/assets/icons/reset.svg?raw';
import settingsIcon from '@/assets/icons/settings.svg?raw';
import closeIcon from '@/assets/icons/close.svg?raw';
import caretLeftIcon from '@/assets/icons/caret-left.svg?raw';


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
  close: closeIcon,
  caretLeft : caretLeftIcon,
};

export default {
  name: 'Icon',
  props: {
    name: { type: String, required: true },
    size: { type: [String, Number], default: 24 },
    responsive: { type: Boolean, default: false }
  },
  computed: {
    svgContent() {
      const icon = icons[this.name];
      if (!icon) {
        console.warn(`Icon "${this.name}" not found`);
        return '';
      }
      
      let cleanedIcon = icon
        .replace(/fill="#[^"]*"/g, 'fill="currentColor"')
        .replace(/fill='#[^']*'/g, 'fill="currentColor"');
      
      if (this.responsive) {
        // Injection des classes CSS pour le responsive
        cleanedIcon = cleanedIcon.replace('<svg', '<svg class="svg-responsive"');
      } else {
        // Injection de la taille directement dans le SVG
        cleanedIcon = cleanedIcon
          .replace(/width="[^"]*"/g, `width="${this.size}"`)
          .replace(/height="[^"]*"/g, `height="${this.size}"`)
          .replace('<svg', `<svg width="${this.size}" height="${this.size}"`);
      }
      
      return cleanedIcon;
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
  display: block;
}

/* Mode responsive : application directe sur le SVG */
.icon :deep(.svg-responsive) {
  width: 28px;
  height: 28px;
}

@media (max-aspect-ratio: 4/3) {
  .icon :deep(.svg-responsive) {
    width: 24px;
    height: 24px;
  }
}
</style>