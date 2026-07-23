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
// Global counter to generate unique IDs for each instance
let instanceCounter = 0;
</script>

<script setup>
import { computed } from 'vue'
import { logger } from '@/services/logger'

const instanceId = ++instanceCounter;

import playIcon from '@/assets/icons/play.svg?raw'
import pauseIcon from '@/assets/icons/pause.svg?raw'
import nextIcon from '@/assets/icons/next.svg?raw'
import previousIcon from '@/assets/icons/previous.svg?raw'
import volumeIcon from '@/assets/icons/volume.svg?raw'
import plusIcon from '@/assets/icons/plus.svg?raw'
import minusIcon from '@/assets/icons/minus.svg?raw'
import threeDotsIcon from '@/assets/icons/three-dots.svg?raw'
import closeDotsIcon from '@/assets/icons/close-dots.svg?raw'
import arrowClockwiseIcon from '@/assets/icons/arrow-clockwise.svg?raw'
import arrowCounterClockwiseIcon from '@/assets/icons/arrow-counter-clockwise.svg?raw'
import settingsIcon from '@/assets/icons/settings.svg?raw'
import closeIcon from '@/assets/icons/close.svg?raw'
import caretLeftIcon from '@/assets/icons/caret-left.svg?raw'
import caretRightIcon from '@/assets/icons/caret-right.svg?raw'
import multiroomIcon from '@/assets/icons/multiroom.svg?raw'
import equalizerIcon from '@/assets/icons/equalizer.svg?raw'
import radioIcon from '@/assets/icons/radio.svg?raw'
import searchIcon from '@/assets/icons/search.svg?raw'
import stopIcon from '@/assets/icons/stop.svg?raw'
import heartIcon from '@/assets/icons/heart.svg?raw'
import heartOffIcon from '@/assets/icons/heart-off.svg?raw'
import caretDownIcon from '@/assets/icons/caret-down.svg?raw'
import caretUpIcon from '@/assets/icons/caret-up.svg?raw'
import powerIcon from '@/assets/icons/power.svg?raw'
import podcastIcon from '@/assets/icons/podcast.svg?raw'
import queueIcon from '@/assets/icons/queue.svg?raw'
import checkIcon from '@/assets/icons/check.svg?raw'
import rewind15Icon from '@/assets/icons/rewind-15.svg?raw'
import forward30Icon from '@/assets/icons/forward-30.svg?raw'
import linkIcon from '@/assets/icons/link.svg?raw'
import speakerSubIcon from '@/assets/icons/speaker-sub.svg?raw'
import speakerShelfIcon from '@/assets/icons/speaker-shelf.svg?raw'
import speakerColumnIcon from '@/assets/icons/speaker-column.svg?raw'
import speakerSatelliteIcon from '@/assets/icons/speaker-satellite.svg?raw'
import dragHandleIcon from '@/assets/icons/drag-handle.svg?raw'
import keyboardCapsLockIcon from '@/assets/icons/keyboard/caps-lock.svg?raw'
import keyboardCapsLockFilledIcon from '@/assets/icons/keyboard/caps-lock-filled.svg?raw'
import keyboardEnterIcon from '@/assets/icons/keyboard/enter.svg?raw'
import keyboardHideIcon from '@/assets/icons/keyboard/hide-keyboard.svg?raw'
import keyboardShiftIcon from '@/assets/icons/keyboard/shift.svg?raw'
import keyboardDeleteIcon from '@/assets/icons/keyboard/delete.svg?raw'
import networkIcon from '@/assets/icons/network.svg?raw'
import ejectIcon from '@/assets/icons/eject.svg?raw'
import trashIcon from '@/assets/icons/trash.svg?raw'
import bluetoothIcon from '@/assets/icons/bluetooth.svg?raw'
import infraredIcon from '@/assets/icons/infrared.svg?raw'
import shuffleIcon from '@/assets/icons/shuffle.svg?raw'
import lyricsIcon from '@/assets/icons/lyrics.svg?raw'
import swipeIndicatorIcon from '@/assets/icons/swipe-indicator.svg?raw'

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
  arrowClockwise: arrowClockwiseIcon,
  arrowCounterClockwise: arrowCounterClockwiseIcon,
  settings: settingsIcon,
  close: closeIcon,
  caretLeft: caretLeftIcon,
  caretRight: caretRightIcon,
  caretDown: caretDownIcon,
  caretUp: caretUpIcon,
  power: powerIcon,
  multiroom: multiroomIcon,
  equalizer: equalizerIcon,
  radio: radioIcon,
  search: searchIcon,
  stop: stopIcon,
  heart: heartIcon,
  heartOff: heartOffIcon,
  podcast: podcastIcon,
  queue: queueIcon,
  check: checkIcon,
  rewind15: rewind15Icon,
  forward30: forward30Icon,
  link: linkIcon,
  speakerSub: speakerSubIcon,
  speakerShelf: speakerShelfIcon,
  speakerColumn: speakerColumnIcon,
  speakerSatellite: speakerSatelliteIcon,
  dragHandle: dragHandleIcon,
  keyboardCapsLock: keyboardCapsLockIcon,
  keyboardCapsLockFilled: keyboardCapsLockFilledIcon,
  keyboardEnter: keyboardEnterIcon,
  keyboardHide: keyboardHideIcon,
  keyboardShift: keyboardShiftIcon,
  keyboardDelete: keyboardDeleteIcon,
  network: networkIcon,
  eject: ejectIcon,
  trash: trashIcon,
  bluetooth: bluetoothIcon,
  infrared: infraredIcon,
  shuffle: shuffleIcon,
  lyrics: lyricsIcon,
  swipeIndicator: swipeIndicatorIcon
}

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [String, Number], default: 24 },
  responsive: { type: Boolean, default: false },
  color: { type: String, default: null }
})

const colorStyle = computed(() => {
  return props.color ? { color: props.color } : {}
})

const sizeClass = computed(() => {
  // If size is a string (small/medium/large), use CSS class for responsive sizing
  if (typeof props.size === 'string') {
    return `icon--size-${props.size}`
  }
  return null
})

const isResponsiveSize = computed(() => {
  return typeof props.size === 'string'
})

// Make SVG IDs unique to avoid url(#id) conflicts between multiple instances
const prepareSvg = (svgString, prefix) => {
  let result = svgString;

  // Find all IDs in the SVG
  const idPattern = /id="([^"]+)"/g;
  const ids = new Set();
  let match;

  while ((match = idPattern.exec(result)) !== null) {
    ids.add(match[1]);
  }

  // Replace each ID and its url() references with unique versions
  ids.forEach(id => {
    const newId = `${prefix}-${id}`;
    result = result.replace(new RegExp(`id="${id}"`, 'g'), `id="${newId}"`);
    result = result.replace(new RegExp(`url\\(#${id}\\)`, 'g'), `url(#${newId})`);
    result = result.replace(new RegExp(`clip-path="url\\(#${id}\\)"`, 'g'), `clip-path="url(#${newId})"`);
    result = result.replace(new RegExp(`filter="url\\(#${id}\\)"`, 'g'), `filter="url(#${newId})"`);
  });

  return result;
};

const svgContent = computed(() => {
  const icon = icons[props.name]
  if (!icon) {
    logger.warn('component', `Icon "${props.name}" not found`)
    return ''
  }

  let cleanedIcon = icon
    .replace(/fill="#[^"]*"/g, 'fill="currentColor"')
    .replace(/fill='#[^']*'/g, 'fill="currentColor"')

  // Make IDs unique to avoid url(#id) conflicts during opacity transitions
  cleanedIcon = prepareSvg(cleanedIcon, `${props.name}-${instanceId}`);

  if (props.responsive || isResponsiveSize.value) {
    // For responsive sizing, let CSS handle dimensions
    cleanedIcon = cleanedIcon.replace('<svg', '<svg class="svg-responsive"')
  } else {
    // For fixed pixel sizing, set dimensions directly. Lookbehind excludes
    // compound attributes like stroke-width="…" — a plain /width="[^"]*"/g
    // also matches inside "stroke-width=", corrupting the stroke thickness.
    cleanedIcon = cleanedIcon
      .replace(/(?<!-)\bwidth="[^"]*"/g, `width="${props.size}"`)
      .replace(/(?<!-)\bheight="[^"]*"/g, `height="${props.size}"`)
      .replace('<svg', `<svg width="${props.size}" height="${props.size}"`)
  }

  return cleanedIcon
})
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

@media (max-aspect-ratio: 4/3) {
  .icon--size-small :deep(.svg-responsive) {
    width: 20px;
    height: 20px;
  }

  .icon--size-medium :deep(.svg-responsive) {
    width: 24px;
    height: 24px;
  }

  .icon--size-large :deep(.svg-responsive) {
    width: 28px;
    height: 28px;
  }
}
</style>