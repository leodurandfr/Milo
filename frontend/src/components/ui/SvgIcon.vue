<!-- frontend/src/components/ui/Icon.vue -->
<template>
  <div
    class="icon"
    :class="[
      { 'icon--responsive': responsive },
      sizeClass
    ]"
    :style="colorStyle"
    v-html="svgContent"
  />
</template>

<script>
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
import caretRightIcon from '@/assets/icons/caret-right.svg?raw';
import multiroomIcon from '@/assets/icons/multiroom.svg?raw';
import equalizerIcon from '@/assets/icons/equalizer.svg?raw';
import radioIcon from '@/assets/icons/radio.svg?raw';
import searchIcon from '@/assets/icons/search.svg?raw';
import stopIcon from '@/assets/icons/stop.svg?raw';
import heartIcon from '@/assets/icons/heart.svg?raw';
import heartOffIcon from '@/assets/icons/heart-off.svg?raw';
import caretDownIcon from '@/assets/icons/caret-down.svg?raw';
import podcastIcon from '@/assets/icons/podcast.svg?raw';
import listIcon from '@/assets/icons/list.svg?raw';
import checkIcon from '@/assets/icons/check.svg?raw';
import rewind15Icon from '@/assets/icons/rewind-15.svg?raw';
import forward30Icon from '@/assets/icons/forward-30.svg?raw';


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
  caretRight : caretRightIcon,
  caretDown : caretDownIcon,
  multiroom : multiroomIcon,
  equalizer : equalizerIcon,
  radio : radioIcon,
  search : searchIcon,
  stop : stopIcon,
  heart : heartIcon,
  heartOff : heartOffIcon,
  podcast : podcastIcon,
  list : listIcon,
  check : checkIcon,
  rewind15 : rewind15Icon,
  forward30 : forward30Icon,


};

export default {
  name: 'Icon',
  props: {
    name: { type: String, required: true },
    size: { type: [String, Number], default: 24 },
    responsive: { type: Boolean, default: false },
    color: { type: String, default: null }
  },
  computed: {
    colorStyle() {
      return this.color ? { color: this.color } : {};
    },
    sizeClass() {
      // If size is a string (small/medium/large), use CSS class for responsive sizing
      if (typeof this.size === 'string') {
        return `icon--size-${this.size}`;
      }
      return null;
    },
    isResponsiveSize() {
      return typeof this.size === 'string';
    },
    svgContent() {
      const icon = icons[this.name];
      if (!icon) {
        console.warn(`Icon "${this.name}" not found`);
        return '';
      }

      let cleanedIcon = icon
        .replace(/fill="#[^"]*"/g, 'fill="currentColor"')
        .replace(/fill='#[^']*'/g, 'fill="currentColor"');

      if (this.responsive || this.isResponsiveSize) {
        // For responsive sizing, let CSS handle dimensions
        cleanedIcon = cleanedIcon.replace('<svg', '<svg class="svg-responsive"');
      } else {
        // For fixed pixel sizing, set dimensions directly
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

/* Default responsive behavior (legacy support) */
.icon :deep(.svg-responsive) {
  width: 32px;
  height: 32px;
}

@media (max-aspect-ratio: 4/3) {
  .icon :deep(.svg-responsive) {
    width: 24px;
    height: 24px;
  }
}

/* Size variants with responsive sizing */
.icon--size-small :deep(.svg-responsive) {
  width: 28px;
  height: 28px;
}

.icon--size-medium :deep(.svg-responsive) {
  width: 32px;
  height: 32px;
}

.icon--size-large :deep(.svg-responsive) {
  width: 40px;
  height: 40px;
}

@media (max-aspect-ratio: 4/3) {
  .icon--size-small :deep(.svg-responsive) {
    width: 24px;
    height: 24px;
  }

  .icon--size-medium :deep(.svg-responsive) {
    width: 28px;
    height: 28px;
  }

  .icon--size-large :deep(.svg-responsive) {
    width: 32px;
    height: 32px;
  }
}
</style>