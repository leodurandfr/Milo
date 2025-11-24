<template>
  <Transition name="audio-player" appear @after-leave="$emit('after-hide')">
    <div
      ref="audioPlayerElement"
      v-show="visible"
      class="audio-player"
      :class="playerClasses"
      @click="handlePlayerClick"
      @touchstart="onSwipeStart"
      @touchmove="onSwipeMove"
      @touchend="onSwipeEnd"
    >
      <!-- Close button (expanded mode only) -->
      <button
        v-if="expandable && isVisuallyExpanded"
        class="close-button"
        @click.stop="handleClose"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M6 9L12 15L18 9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </button>

      <!-- Background image - heavily zoomed and blurred -->
      <div class="player-art-background">
        <img v-if="artwork" :src="artwork" alt="" class="background-image" />
      </div>

      <div class="player-content">
        <!-- Artwork -->
        <img v-if="artwork" :src="artwork" :alt="title" class="player-artwork" />
        <img v-else :src="placeholderArtwork" :alt="title" class="player-artwork placeholder" />

        <!-- Info section with slot for flexible content -->
        <div class="player-info">
          <slot name="info">
            <p v-if="subtitle" class="player-subtitle text-mono">{{ subtitle }}</p>
            <p class="player-title text-body-small">{{ title }}</p>
          </slot>
          <slot name="progress"></slot>

        </div>


        <!-- Controls section with slot for flexible controls -->
        <div class="controls">
          <slot name="controls">
            <!-- Default: Simple play/pause -->
            <div class="playback-controls">
              <IconButton :icon="isPlaying ? 'pause' : 'play'" variant="dark" size="large" :loading="isLoading"
                @click="$emit('toggle-play')" />
            </div>
          </slot>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import IconButton from '@/components/ui/IconButton.vue'
import episodePlaceholder from '@/assets/podcasts/podcast-placeholder.jpg'

const props = defineProps({
  /**
   * Audio source type ('radio', 'podcast', 'bluetooth', etc.)
   */
  source: {
    type: String,
    required: true,
    validator: (value) => ['radio', 'podcast', 'bluetooth', 'roc'].includes(value)
  },

  /**
   * Visibility control (replaces v-if in parent)
   */
  visible: {
    type: Boolean,
    default: false
  },

  /**
   * Enable expandable mode (mobile + podcast only)
   */
  expandable: {
    type: Boolean,
    default: false
  },

  /**
   * Current expansion state (controlled by parent)
   */
  expanded: {
    type: Boolean,
    default: false
  },

  /**
   * Artwork/image URL for the current item
   */
  artwork: {
    type: String,
    default: null
  },

  /**
   * Placeholder artwork when no image is available
   */
  placeholderArtwork: {
    type: String,
    default: episodePlaceholder
  },

  /**
   * Main title (station name, episode name, etc.)
   */
  title: {
    type: String,
    default: 'No title'
  },

  /**
   * Subtitle (genre/bitrate, podcast name, etc.)
   */
  subtitle: {
    type: String,
    default: null
  },

  /**
   * Playback state
   */
  isPlaying: {
    type: Boolean,
    default: false
  },

  /**
   * Loading/buffering state
   */
  isLoading: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['toggle-play', 'after-hide', 'toggle-expanded'])

// Ref to audio player element for dimension calculations
const audioPlayerElement = ref(null)

// Local state for visual expansion (decoupled from props.expanded for animation control)
const isVisuallyExpanded = ref(false)
const isMorphing = ref(false)
const isContentFading = ref(false)
const isExpanding = ref(false)  // Track compact → expand transition
const isCollapsing = ref(false) // Track expand → compact transition

// Swipe to close
const swipeStartY = ref(0)
const swipeCurrentY = ref(0)
const isSwiping = ref(false)

// Watch props.expanded to trigger animations
watch(() => props.expanded, (newExpanded) => {
  if (newExpanded && !isVisuallyExpanded.value) {
    expandPlayer()
  } else if (!newExpanded && isVisuallyExpanded.value) {
    collapsePlayer()
  }
})

// Expansion animation (3 phases)
async function expandPlayer() {
  // Phase 1: Fade out compact content
  isContentFading.value = true
  await new Promise(resolve => setTimeout(resolve, 100))

  // Phase 2: Morphing animation
  if (audioPlayerElement.value) {
    // Capture current position and dimensions (absolute coordinates from viewport)
    const rect = audioPlayerElement.value.getBoundingClientRect()
    const computedStyle = window.getComputedStyle(audioPlayerElement.value)

    // Apply current state as inline styles (with !important to override CSS)
    // Use absolute coordinates, no transform needed since rect already gives us final position
    const el = audioPlayerElement.value
    el.style.setProperty('position', 'fixed', 'important')
    el.style.setProperty('top', `${rect.top}px`, 'important')
    el.style.setProperty('left', `${rect.left}px`, 'important')
    el.style.setProperty('width', `${rect.width}px`, 'important')
    el.style.setProperty('height', `${rect.height}px`, 'important')
    el.style.setProperty('transform', 'none', 'important')
    el.style.setProperty('border-radius', computedStyle.borderRadius, 'important')
    el.style.setProperty('padding', computedStyle.padding, 'important')

    await nextTick() // Force reflow with current position locked
  }

  // Trigger morphing - classes change but inline styles hold position
  isMorphing.value = true
  isExpanding.value = true  // Use fast transition
  isVisuallyExpanded.value = true
  await nextTick()

  // Remove inline styles - let CSS transition animate to expanded state
  if (audioPlayerElement.value) {
    const el = audioPlayerElement.value
    el.style.removeProperty('position')
    el.style.removeProperty('top')
    el.style.removeProperty('left')
    el.style.removeProperty('width')
    el.style.removeProperty('height')
    el.style.removeProperty('transform')
    el.style.removeProperty('border-radius')
    el.style.removeProperty('padding')
  }

  await new Promise(resolve => setTimeout(resolve, 200))  // Fast transition (--transition-fast)
  isMorphing.value = false
  isExpanding.value = false

  // Phase 3: Fade in expanded content
  await nextTick()
  isContentFading.value = false
}

// Collapse animation (reverse)
async function collapsePlayer() {
  // Phase 1: Fade out expanded content
  isContentFading.value = true
  await new Promise(resolve => setTimeout(resolve, 100))

  // Phase 2: Morphing collapse
  if (audioPlayerElement.value) {
    const el = audioPlayerElement.value

    // Step 1: Capture current expanded dimensions
    const expandedRect = el.getBoundingClientRect()
    const expandedStyle = window.getComputedStyle(el)

    // Step 2: Temporarily switch to compact to measure target dimensions
    const wasExpanded = isVisuallyExpanded.value
    const wasMorphing = isMorphing.value
    isVisuallyExpanded.value = false
    isMorphing.value = false
    await nextTick()

    // Measure compact dimensions
    const compactRect = el.getBoundingClientRect()
    const compactStyle = window.getComputedStyle(el)
    const targetHeight = compactRect.height
    const targetTop = compactRect.top
    const targetLeft = compactRect.left
    const targetWidth = compactRect.width
    const targetBorderRadius = compactStyle.borderRadius
    const targetPadding = compactStyle.padding

    // Step 3: Restore expanded state
    isVisuallyExpanded.value = wasExpanded
    isMorphing.value = wasMorphing
    await nextTick()

    // Step 4: Lock current expanded position with inline styles
    el.style.setProperty('position', 'fixed', 'important')
    el.style.setProperty('top', `${expandedRect.top}px`, 'important')
    el.style.setProperty('left', `${expandedRect.left}px`, 'important')
    el.style.setProperty('width', `${expandedRect.width}px`, 'important')
    el.style.setProperty('height', `${expandedRect.height}px`, 'important')
    el.style.setProperty('transform', 'none', 'important')
    el.style.setProperty('border-radius', expandedStyle.borderRadius, 'important')
    el.style.setProperty('padding', expandedStyle.padding, 'important')
    await nextTick()

    // Step 5: Trigger morphing and change class
    isMorphing.value = true
    isCollapsing.value = true  // Use spring transition
    isVisuallyExpanded.value = false
    await nextTick()

    // Step 6: Animate to explicit compact dimensions (NOT auto)
    el.style.setProperty('top', `${targetTop}px`, 'important')
    el.style.setProperty('left', `${targetLeft}px`, 'important')
    el.style.setProperty('width', `${targetWidth}px`, 'important')
    el.style.setProperty('height', `${targetHeight}px`, 'important')
    el.style.setProperty('border-radius', targetBorderRadius, 'important')
    el.style.setProperty('padding', targetPadding, 'important')
  }

  // Wait for morphing transition to complete (spring transition is 700ms)
  await new Promise(resolve => setTimeout(resolve, 700))

  // Step 7: Cleanup - remove all inline styles
  if (audioPlayerElement.value) {
    const el = audioPlayerElement.value
    el.style.removeProperty('position')
    el.style.removeProperty('top')
    el.style.removeProperty('left')
    el.style.removeProperty('width')
    el.style.removeProperty('height')
    el.style.removeProperty('transform')
    el.style.removeProperty('border-radius')
    el.style.removeProperty('padding')
  }

  isMorphing.value = false
  isCollapsing.value = false

  // Phase 3: Fade in compact content
  await nextTick()
  isContentFading.value = false
}

// Handle player click (expand)
function handlePlayerClick(event) {
  if (!props.expandable || props.expanded) return

  // Don't expand if clicking on a button or control
  if (event.target.closest('button, .progress-bar, .dropdown')) return

  emit('toggle-expanded')
}

// Handle close button
function handleClose() {
  if (!props.expandable || !props.expanded) return
  emit('toggle-expanded')
}

// Swipe handlers
function onSwipeStart(event) {
  if (!props.expandable || !props.expanded) return

  isSwiping.value = true
  swipeStartY.value = getEventY(event)
  swipeCurrentY.value = swipeStartY.value
}

function onSwipeMove(event) {
  if (!isSwiping.value) return

  swipeCurrentY.value = getEventY(event)
  const deltaY = swipeCurrentY.value - swipeStartY.value

  // Prevent default scroll if swiping down
  if (deltaY > 5) {
    event.preventDefault()
  }
}

function onSwipeEnd() {
  if (!isSwiping.value) return

  const deltaY = swipeCurrentY.value - swipeStartY.value

  // Close if swiped down > 50px
  if (deltaY > 50) {
    handleClose()
  }

  isSwiping.value = false
}

function getEventY(event) {
  if (event.type.includes('touch') || event.pointerType === 'touch') {
    return event.touches?.[0]?.clientY || event.changedTouches?.[0]?.clientY || event.clientY
  }
  return event.clientY
}

// Computed classes
const playerClasses = computed(() => ({
  [`source-${props.source}`]: true,
  'expandable': props.expandable,
  'expanded': isVisuallyExpanded.value,
  'morphing': isMorphing.value,
  'morphing-expand': isExpanding.value,
  'morphing-collapse': isCollapsing.value,
  'content-fading': isContentFading.value
}))
</script>

<style scoped>
/* Desktop: Vertical sidebar layout */
.audio-player {
  display: flex;
  margin: var(--space-07) 0 0 var(--space-06);
  max-height: 540px;
  flex-direction: column;
  gap: var(--space-04);
  padding: var(--space-04) var(--space-04) var(--space-05) var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  backdrop-filter: blur(16px);
  position: relative;
  overflow: hidden;
}

/* Glass stroke border effect (matching both radio and podcast players exactly) */
.audio-player::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  opacity: 0.8;
  background: var(--stroke-glass);
  border-radius: var(--radius-06);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: 1;
  pointer-events: none;
}

/* Background artwork - heavily blurred and saturated */
.player-art-background {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 100%;
  height: 100%;
  z-index: 0;
  pointer-events: none;
  overflow: hidden;
}

/* Overlay to darken the background image */
.player-art-background::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--color-background-contrast-32);
  z-index: 1;
  pointer-events: none;
}

.background-image {
  filter: blur(40px) saturate(1.6);
  transform: scale(1.5);
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* Player content (sits above background) */
.player-content {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  overflow-y: auto;
}

/* Artwork image */
.player-artwork {
  width: 100%;
  aspect-ratio: 1;
  border-radius: var(--radius-04);
  object-fit: cover;
  background: var(--color-background-neutral);
}

.player-artwork.placeholder {
  object-fit: cover;
}

/* Player info section */
.player-info {
  display: flex;
  height: 100%;
  flex-direction: column;
  gap: var(--space-02);
}

:deep(.player-title) {
  color: var(--color-text-contrast);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  margin: 0;
}

:deep(.player-subtitle) {
  color: var(--color-text-contrast-50);
  margin: 0;
  cursor: pointer;
}



/* Controls section */
.controls {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  position: relative;
}

:deep(.playback-controls) {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: var(--space-03);
}

/* Mobile: Horizontal bottom panel layout */
@media (max-aspect-ratio: 4/3) {
  .audio-player {
    position: fixed;
    bottom: var(--space-08);
    margin: 0;
    left: 50%;
    transform: translate(-50%, 0);
    width: calc(100% - var(--space-02) * 2);
    height: auto;
    max-height: none;
    flex-direction: row;
    align-items: center;
    padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
    border-radius: var(--radius-06);
  }

  .player-content {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    overflow-y: visible;
    gap: var(--space-02);
    width: 100%;
  }

  .player-artwork {
    width: 48px;
    height: 48px;
    min-width: 48px;
    border-radius: var(--radius-03);
  }

  .player-info {
    flex: 1;
    text-align: left;
    min-width: 0;
  }



  /* Apply same styles to slotted content (fixes scoped CSS limitation) */
  :deep(.player-title) {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    -webkit-line-clamp: unset;
    -webkit-box-orient: unset;
    display: block;
    color: var(--color-text-contrast);
    margin: 0;
  }

  :deep(.player-subtitle) {
    display: none;
  }

  /* Hide progress bar on mobile by default */
  .player-content :deep(.progress-bar) {
    display: none;
  }

  /* Show progress bar for podcasts on mobile */
  .audio-player.source-podcast .player-content :deep(.progress-bar) {
    display: flex;
  }

  .controls {
    gap: var(--space-02);
    justify-content: center;
    flex-shrink: 0;
  }

  /* Harmonize all IconButton sizes in controls on mobile (Radio + Podcasts) */
  .controls :deep(.icon-button) {
    width: 36px;
    height: 36px;
  }

  .controls :deep(.icon-button svg) {
    width: 28px;
    height: 28px;
  }
}

/* Vue Transition: Desktop - slide from right with fade */
@media (min-aspect-ratio: 4/3) {
  .audio-player-enter-active {
    transition:
      transform var(--transition-spring-slow),
      opacity 0.4s ease-out;
  }

  .audio-player-leave-active {
    transition:
      transform 0.6s cubic-bezier(0.5, 0, 0, 1),
      opacity 0.6s cubic-bezier(0.5, 0, 0, 1);
  }

  .audio-player-enter-from {
    opacity: 0;
    transform: translateX(100px);
  }

  .audio-player-leave-to {
    opacity: 0;
    transform: translateX(100px);
  }
}

/* Vue Transition: Mobile - slide up from bottom with fade */
@media (max-aspect-ratio: 4/3) {
  .audio-player-enter-active {
    transition:
      transform var(--transition-spring),
      opacity 0.4s ease-out;
  }

  .audio-player-leave-active {
    transition:
      transform 0.6s cubic-bezier(0.5, 0, 0, 1),
      opacity 0.6s cubic-bezier(0.5, 0, 0, 1);
  }

  /* Disable expanded styles during leave transition to prevent brutal height changes */
  .audio-player.expandable.expanded.audio-player-leave-active {
    position: fixed !important;
    bottom: var(--space-08) !important;
    left: 50% !important;
    width: calc(100% - var(--space-02) * 2) !important;
    height: auto !important;
    max-height: none !important;
    top: auto !important;
    right: auto !important;
    inset: auto !important;
    flex-direction: row !important;
    align-items: center !important;
    justify-content: flex-start !important;
    padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03) !important;
    border-radius: var(--radius-06) !important;
  }

  .audio-player-enter-from {
    opacity: 0;
    transform: translate(-50%, 120px);
  }

  .audio-player-enter-to,
  .audio-player-leave-from {
    opacity: 1;
    transform: translate(-50%, 0);
  }

  .audio-player-leave-to {
    opacity: 0;
    transform: translate(-50%, 120px);
  }

  /* ===== EXPANDABLE MODE (MOBILE + PODCAST ONLY) ===== */

  /* Close button */
  .audio-player.expandable .close-button {
    position: absolute;
    top: var(--space-04);
    right: var(--space-04);
    z-index: 10;
    background: none;
    border: none;
    color: var(--color-text-contrast);
    cursor: pointer;
    padding: var(--space-02);
    opacity: 0;
    transition: opacity 0.2s ease-out;
    pointer-events: none;
  }

  /* Show close button when expanded */
  .audio-player.expandable.expanded .close-button {
    opacity: 1;
    pointer-events: auto;
  }

  /* Expanded mode styles - near fullscreen vertical layout with 32px margin */
  .audio-player.expandable.expanded {
    position: fixed !important;
    inset: 32px !important;
    width: auto !important;
    height: auto !important;
    max-height: none !important;
    transform: none !important;
    margin: 0 !important;
    border-radius: var(--radius-06) !important;
    padding: var(--space-08) var(--space-05) var(--space-05) var(--space-05) !important;
    display: flex !important;
    flex-direction: column !important;
    justify-content: center !important;
  }

  /* Expanded player content - vertical layout */
  .audio-player.expandable.expanded .player-content {
    flex-direction: column !important;
    justify-content: center !important;
    align-items: center !important;
    gap: var(--space-05) !important;
    width: 100% !important;
  }

  /* Expanded artwork - much larger */
  .audio-player.expandable.expanded .player-artwork {
    width: 320px !important;
    height: 320px !important;
    max-width: 90vw !important;
    max-height: 90vw !important;
    border-radius: var(--radius-05) !important;
  }

  /* Expanded info section */
  .audio-player.expandable.expanded .player-info {
    width: 100% !important;
    text-align: center !important;
    gap: var(--space-03) !important;
  }

  /* Show subtitle in expanded mode */
  .audio-player.expandable.expanded :deep(.player-subtitle) {
    display: block !important;
  }

  /* Multi-line title in expanded mode */
  .audio-player.expandable.expanded :deep(.player-title) {
    white-space: normal !important;
    -webkit-line-clamp: 3 !important;
    -webkit-box-orient: vertical !important;
    display: -webkit-box !important;
  }

  /* Show progress bar in expanded mode */
  .audio-player.expandable.expanded .player-content :deep(.progress-bar) {
    display: flex !important;
  }

  /* Expanded controls */
  .audio-player.expandable.expanded .controls {
    width: 100% !important;
    justify-content: center !important;
    gap: var(--space-04) !important;
  }

  /* Hide speed selector and rewind15 in compact mobile mode */
  .audio-player.expandable:not(.expanded) :deep(.speed-selector) {
    display: none !important;
  }

  .audio-player.expandable:not(.expanded) :deep(.playback-controls > :first-child) {
    display: none !important;
  }

  /* Show all controls in expanded mode (speed selector, rewind15) */
  .audio-player.expandable.expanded :deep(.playback-controls > :first-child) {
    display: flex !important;
  }

  .audio-player.expandable.expanded :deep(.speed-selector) {
    display: flex !important;
  }

  /* Morphing transitions - different for expand vs collapse */
  .audio-player.morphing-expand {
    transition: all var(--transition-fast) !important;
  }

  .audio-player.morphing-collapse {
    transition: all var(--transition-spring) !important;
  }

  /* Content fade transitions */
  .audio-player.content-fading .player-content {
    opacity: 0 !important;
    transition: opacity 0.1s ease-out !important;
  }

  .audio-player:not(.content-fading) .player-content {
    opacity: 1 !important;
    transition: opacity 0.2s ease-in !important;
  }

  /* Make player clickable when expandable */
  .audio-player.expandable:not(.expanded) {
    cursor: pointer;
  }

  /* Prevent click on controls from expanding */
  .audio-player.expandable button,
  .audio-player.expandable .progress-bar,
  .audio-player.expandable .dropdown {
    pointer-events: auto;
  }
}
</style>
