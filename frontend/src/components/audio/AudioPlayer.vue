<template>
  <Teleport to="body" :disabled="!isMobile">
    <Transition name="audio-player" @after-leave="$emit('after-hide')">
      <!-- v-if, not v-show: a teleported v-show toggle (mobile) doesn't fire the
           transition classes, so the enter/leave would be instant. -->
      <div v-if="visible" class="audio-player" :class="[playerClasses, { 'audio-player-revealing': revealing }]"
        @touchstart="onTouchStart" @touchmove="onTouchMove" @touchend="onTouchEnd">
        <!-- Background image - heavily zoomed and blurred -->
        <div class="player-art-background">
          <img v-if="validArtwork" :src="validArtwork" alt="" class="background-image" />
          <div v-else-if="fallbackSvg" v-html="fallbackSvg" class="background-image" />
          <img v-else-if="placeholderArtwork" :src="placeholderArtwork" alt="" class="background-image" />
        </div>

        <div class="player-content">
          <!-- Artwork: falls back to inline-SVG avatar (font-aware) when no valid artwork,
             then to placeholderArtwork for sources that ship a static image (e.g. podcasts).
             Frame hosts an optional #artwork-badge (mobile radio: station icon sitting
             behind the track artwork, which rides on top) — needs a real box since two of
             the three branches below are void <img> elements and can't host a child. -->
          <div class="player-artwork-frame" :class="{ 'has-badge': !!$slots['artwork-badge'] }">
            <img v-if="validArtwork" :src="validArtwork" :alt="title" class="player-artwork"
              :class="{ loaded: artworkLoaded }" @load="handleArtworkLoad" @error="artworkError = true" />
            <div v-else-if="fallbackSvg" v-html="fallbackSvg" class="player-artwork" :aria-label="title" />
            <img v-else :src="placeholderArtwork" :alt="title" class="player-artwork placeholder" />
            <slot name="artwork-badge"></slot>
          </div>

          <div class="player-info" :class="{ 'player-info-carousel': carousel }">
            <!-- Mobile swipe (music library): a 3-cell strip [prev｜current｜next]
                 driven by the carousel's own viewIndex into the queue, so the
                 text is rendered locally and never reindexes against the backend
                 skip echo mid-animation. -->
            <div v-if="carousel" ref="trackEl" class="player-info-track" :style="trackStyle"
              @transitionend.self="onSettleEnd">
              <div v-for="cell in cells" :key="cell.pos" class="player-info-cell">
                <p class="player-title text-body">{{ cell.title }}</p>
                <p v-if="cell.artist" class="player-subtitle text-body">{{ cell.artist }}</p>
              </div>
            </div>
            <!-- Every non-swipe case (radio, podcast, desktop): instant swap, the
                 'track-none' transition has no CSS so it resolves immediately. -->
            <Transition v-else name="track-none" mode="out-in">
              <div class="player-info-inner" :key="title">
                <slot name="info"></slot>
              </div>
            </Transition>
          </div>

          <!-- Progress bar + controls are pinned together at the bottom, 8px apart —
               the info block above is what's centered in the remaining space, not this group. -->
          <div class="player-bottom">
            <slot name="progress"></slot>

            <div class="controls">
              <slot name="controls">
                <!-- Default: Simple play/pause -->
                <div class="playback-controls">
                  <IconButton :icon="isPlaying ? 'pause' : 'play'" variant="on-dark" size="medium" :loading="isLoading"
                    @click="$emit('toggle-play')" />
                </div>
              </slot>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import IconButton from '@/components/ui/IconButton.vue'
import episodePlaceholder from '@/assets/podcasts/podcast-placeholder.jpg'
import { useIsMobile } from '@/composables/useIsMobile'
import { useTimer } from '@/composables/useTimer'
import { useScreensaverRevealPulse } from '@/composables/useScreensaverReveal'
import { generateStationAvatarSvg } from '@/utils/stationAvatar'
import { MIN_IMAGE_SIZE } from '@/constants/imageQuality'

const { isMobile } = useIsMobile()
const timer = useTimer()

// Replay the slide-in entrance when the screensaver is dismissed (desktop only —
// the screensaver never shows on mobile). Duration covers the spring-slow enter.
const revealing = useScreensaverRevealPulse(1700)

const props = defineProps({
  /**
   * Audio source type ('radio', 'podcast', 'music_library')
   */
  source: {
    type: String,
    required: true,
    validator: (value) => ['radio', 'podcast', 'music_library'].includes(value)
  },

  /**
   * Visibility control (replaces v-if in parent)
   */
  visible: {
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
   * Placeholder artwork URL — used when no valid artwork and no fallbackName.
   * Sources with a deterministic name (radio stations) should pass fallbackName
   * instead so the avatar is generated inline with the correct font; this prop
   * stays for sources that ship a static placeholder asset (e.g. podcasts).
   */
  placeholderArtwork: {
    type: String,
    default: episodePlaceholder
  },

  /**
   * Name used to generate an inline SVG avatar when no valid artwork loads.
   * Inline rendering (v-html) inherits document @font-face — using an <img>
   * data URL would lose Space Mono Bold and fall back to the system monospace.
   */
  fallbackName: {
    type: String,
    default: null
  },

  /**
   * Main title (station name, episode name, etc.)
   */
  title: {
    type: String,
    default: 'No title'
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
  },

  /**
   * Enable the mobile horizontal-swipe gesture (next/prev). Off by default so
   * radio — which has no track-skip concept in the mini-player — never captures
   * swipes nor animates its station→metadata reveal as if it were one.
   */
  swipeEnabled: {
    type: Boolean,
    default: false
  },

  /**
   * The play queue and the current index within it — the swipe carousel reads
   * the adjacent entries' title/artist locally so its animation never waits for
   * (nor reindexes against) the backend skip echo. Each entry uses the Subsonic
   * song shape (title/name + artist). Only consulted when swipeEnabled.
   */
  tracks: {
    type: Array,
    default: () => []
  },
  currentIndex: {
    type: Number,
    default: -1
  }
})

const emit = defineEmits(['toggle-play', 'after-hide', 'swipe-next', 'swipe-prev'])

// Artwork validation — falls back to inline SVG / placeholder on error or tiny image (e.g. 1x1 tracking pixel)
const artworkError = ref(false)
// Fade the real artwork in on load instead of popping over the neutral box —
// reset on every src change so a new track/station image fades rather than snaps.
const artworkLoaded = ref(false)
watch(() => props.artwork, () => { artworkError.value = false; artworkLoaded.value = false })
const validArtwork = computed(() => props.artwork && !artworkError.value ? props.artwork : null)
const fallbackSvg = computed(() => props.fallbackName ? generateStationAvatarSvg(props.fallbackName) : '')

function handleArtworkLoad(e) {
  if (e.target.naturalWidth < MIN_IMAGE_SIZE || e.target.naturalHeight < MIN_IMAGE_SIZE) {
    artworkError.value = true
    return
  }
  artworkLoaded.value = true
}

const playerClasses = computed(() => ({
  [`source-${props.source}`]: true
}))

// Mobile swipe carousel (music library) — only on the fixed docked player.
const carousel = computed(() => isMobile.value && props.swipeEnabled)
const SWIPE_THRESHOLD_PX = 40
const SETTLE_MS = 300
let touchStartX = 0
let touchStartY = 0
let touchTracking = false

// The carousel owns its own index into the queue and reads its three cells from
// it, NOT from the live backend index — so the skip echo (which reindexes
// queueIndex almost instantly) can't swap cell contents out from under the
// settle animation. It re-syncs to the store only between swipes.
const viewIndex = ref(props.currentIndex)
watch(() => props.currentIndex, (ci) => { if (!committing) viewIndex.value = ci })

const CELL_OFFSETS = [-1, 0, 1]
const cells = computed(() => CELL_OFFSETS.map((offset) => {
  const song = props.tracks[viewIndex.value + offset]
  return {
    pos: offset,
    title: song ? (song.title || song.name || '') : '',
    artist: song ? (song.artist || '') : ''
  }
}))
const hasNextCell = computed(() => viewIndex.value >= 0 && viewIndex.value + 1 < props.tracks.length)
const hasPrevCell = computed(() => viewIndex.value > 0)

// Resting is 'center' (translateX(-100%), middle cell centred). A drag follows
// the finger; on release it settles to 'next' (-200%) / 'prev' (0%) or back.
// dragging and suppressTransition drop the CSS transition for instant moves.
const trackEl = ref(null)
const dragging = ref(false)
const dragX = ref(0)
const settle = ref('center')
const suppressTransition = ref(false)

const trackStyle = computed(() => {
  if (dragging.value) {
    return { transform: `translateX(calc(-100% + ${dragX.value}px))`, transition: 'none' }
  }
  const pos = settle.value === 'next' ? '-200%' : settle.value === 'prev' ? '0%' : '-100%'
  return suppressTransition.value
    ? { transform: `translateX(${pos})`, transition: 'none' }
    : { transform: `translateX(${pos})` }
})

let committing = false
let committedDir = 0
let rehomeHandle = null

// Settle finished: advance the local index onto the committed neighbour and snap
// the strip back to centre. The neighbour text is already centred, so the snap is
// invisible.
function rehome() {
  if (!committing) return
  if (rehomeHandle) { timer.clear(rehomeHandle); rehomeHandle = null }
  viewIndex.value += committedDir
  committing = false
  committedDir = 0
  suppressTransition.value = true
  settle.value = 'center'
  dragX.value = 0
  nextTick(() => {
    // Force a reflow so the snapped (transition:none) position commits before the
    // transition is re-enabled — else this microtask runs pre-paint, the browser
    // never sees the 'none' frame, and the snap animates instead.
    trackEl.value?.getBoundingClientRect()
    suppressTransition.value = false
  })
}

function onSettleEnd() {
  rehome()
}

function onTouchStart(e) {
  if (!carousel.value) return
  if (committing) rehome() // finish a pending swipe before starting a new one
  const touch = e.touches[0]
  touchStartX = touch.clientX
  touchStartY = touch.clientY
  touchTracking = true
}

function onTouchMove(e) {
  if (!touchTracking) return
  const touch = e.touches[0]
  const dx = touch.clientX - touchStartX
  const dy = touch.clientY - touchStartY
  if (!dragging.value) {
    // Capture only once the drag is confirmed horizontal — vertical scrolls and
    // taps pass through untouched.
    if (Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy)) {
      dragging.value = true
      settle.value = 'center'
    } else {
      return
    }
  }
  e.preventDefault()
  // Rubber-band toward a missing neighbour (queue end) so it snaps back.
  const towardMissing = dx < 0 ? !hasNextCell.value : !hasPrevCell.value
  dragX.value = towardMissing ? dx * 0.25 : dx
}

function onTouchEnd(e) {
  if (!touchTracking) return
  touchTracking = false
  const wasDragging = dragging.value
  dragging.value = false
  if (!wasDragging) return
  const touch = e.changedTouches[0]
  const dx = touch.clientX - touchStartX
  const dy = touch.clientY - touchStartY
  const goingNext = dx < 0
  const passed = Math.abs(dx) > SWIPE_THRESHOLD_PX && Math.abs(dx) > Math.abs(dy) * 1.5
  const hasNeighbour = goingNext ? hasNextCell.value : hasPrevCell.value
  if (passed && hasNeighbour) {
    // Finger left → next, finger right → prev.
    committedDir = goingNext ? 1 : -1
    settle.value = goingNext ? 'next' : 'prev'
    committing = true
    if (rehomeHandle) timer.clear(rehomeHandle)
    rehomeHandle = timer.setTimeout(rehome, SETTLE_MS + 120) // fallback if transitionend is missed
    emit(goingNext ? 'swipe-next' : 'swipe-prev')
  } else {
    settle.value = 'center'
    dragX.value = 0
  }
}
</script>

<style scoped>
/* Desktop: Vertical sidebar layout */
.audio-player {
  display: flex;
  width: 100%;
  margin: 0;
  height: 100%;
  max-height: 720px;
  flex-direction: column;
  gap: var(--space-04);
  padding: 0 var(--space-02);
  background: var(--color-background-medium-32);
  border-radius: var(--radius-06);
  backdrop-filter: blur(var(--blur-02));
  -webkit-backdrop-filter: blur(var(--blur-02));
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
  position: relative;
  overflow: hidden;
  z-index: 50;
}

/* Glass stroke border effect (matching both radio and podcast players exactly) */
.audio-player::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 1px;
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
  filter: blur(var(--blur-04)) saturate(1.6);
  transform: scale(1.5) translateZ(0);
  width: 100%;
  height: 100%;
  object-fit: cover;
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
}

/* Player content (sits above background) */
.player-content {
  height: 100%;
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  padding: var(--space-02) 0 var(--space-05) 0;
  gap: var(--space-04);
  overflow-y: auto;
}

.player-artwork-frame {
  position: relative;
  width: 100%;
  aspect-ratio: 1;
  /* In the parent flex column, flex-shrink: 1 (default) lets aspect-ratio be
     overridden when vertical space is tight; pinning it preserves the 1:1
     box for both <img> (which has intrinsic size) and the <div v-html=svg>
     wrapper (whose content is the SVG sized below). */
  flex-shrink: 0;
}

.player-artwork {
  width: 100%;
  height: 100%;
  border-radius: var(--radius-04);
  object-fit: cover;
  background: var(--color-background-neutral);
  /* Clip the inline-SVG fallback to the rounded corners. (For <img>, content
     is clipped natively by border-radius — this matters only for the <div>
     wrapper case.) */
  overflow: hidden;
  display: block;
}

/* Fade the real artwork <img> in on load (the SVG-avatar div and the static
   placeholder img are excluded — they have no load event and must stay
   visible). */
img.player-artwork:not(.placeholder) {
  opacity: 0;
  transition: opacity 0.25s ease-out;
}

img.player-artwork.loaded {
  opacity: 1;
}

/* Inline-SVG fallback: let the SVG sit in normal flow with width:100% and
   height derived from its 1024×1024 viewBox (height: auto). This gives the
   wrapper a real, square content height — no circular dependency with the
   wrapper's aspect-ratio (which would otherwise fall back to the SVG default
   300×150 / ~1.94:1 box). */
.player-artwork :deep(svg) {
  display: block;
  width: 100%;
  height: auto;
}

.player-artwork.placeholder {
  object-fit: cover;
}

.player-info {
  display: flex;
  justify-content: center;
  height: 100%;
  flex-direction: column;
  gap: var(--space-04);
  padding: 0 var(--space-04);
}

.player-info-inner {
  display: flex;
  flex-direction: column;
  /* Explicit `inherit` (gap isn't inherited by default) mirrors whatever
     .player-info currently has — var(--space-04) desktop, var(--space-01) mobile —
     without duplicating the token here. */
  gap: inherit;
  width: 100%;
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
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Desktop/mobile split for slotted #info content — each source renders both
   variants and lets this toggle pick one, instead of duplicating layout CSS
   per source file. */
:deep(.mobile-only) {
  display: none;
}

.player-bottom {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  padding: 0 var(--space-04);
}

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
  gap: var(--space-02);
  width: 100%;
}

/* Mobile: Horizontal bottom panel layout */
@media (max-aspect-ratio: 4/3) {
  .audio-player {
    position: fixed;
    /* bottom: calc(max(var(--space-06), env(safe-area-inset-bottom, 0px)) + var(--space-05)); */
    /* bottom: env(safe-area-inset-bottom, 0px); */
    bottom: calc(env(safe-area-inset-bottom, 0px) + var(--space-06));

    margin: 0;
    left: 50%;
    transform: translate(-50%, 0);
    width: calc(100% - var(--space-02) * 2);
    height: auto;
    max-height: none;
    flex-direction: row;
    align-items: center;
    padding: var(--space-02) var(--space-03) var(--space-02) var(--space-02);
    border-radius: var(--radius-05);
    box-shadow: 0px 4px 16px rgba(0, 0, 0, 0.24);
  }

  .audio-player::before {
    border-radius: var(--radius-05);
  }

  .player-content {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    overflow-y: visible;
    padding: 0;
    gap: var(--space-03);
    width: 100%;
    position: static;
  }

  /* Single 48px row layout, shared by all three sources (radio, podcast,
     music library) — artwork | title+subtitle | one play/pause(-ish) button.
     Width is animated so the radio station→track reveal (frame 48→72) shifts the
     title/subtitle text rightward in sync with the track image sliding in. */
  .player-artwork-frame {
    width: 48px;
    height: 48px;
    min-width: 48px;
    transition: width var(--transition-medium);
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
    padding: 0;
    min-width: 0;
    gap: var(--space-01);
  }

  /* Swipe carousel: .player-info becomes the clipped viewport; the strip holds
     the three text cells side by side and slides horizontally. Only present in
     the DOM on mobile music-library (v-if="carousel"). */
  .player-info-carousel {
    overflow: hidden;
    position: relative;
  }

  .player-info-track {
    display: flex;
    width: 100%;
    will-change: transform;
    /* Settle only on release; the finger-follow and the re-home snap pass an
       inline `transition: none` to override this. Duration mirrors SETTLE_MS. */
    transition: transform 0.3s cubic-bezier(0.32, 0.72, 0, 1);
  }

  .player-info-cell {
    display: flex;
    flex-direction: column;
    justify-content: center;
    flex: 0 0 100%;
    min-width: 0;
    gap: var(--space-01);
  }

  .player-bottom {
    flex-shrink: 0;
    padding: 0;
    gap: 0;

  }

  /* Apply same styles to slotted content (fixes scoped CSS limitation) */
  :deep(.player-title) {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    -webkit-line-clamp: unset;
    -webkit-box-orient: unset;
    display: block;
  }

  :deep(.desktop-only) {
    display: none !important;
  }

  :deep(.mobile-only) {
    display: block !important;
  }

  /* Hide progress bar on mobile by default (radio has none) */
  .player-content :deep(.progress-bar) {
    display: none;
  }

  /* Podcast/music library mobile: progress becomes a thin full-width strip
     pinned to the very bottom of the card. It's positioned relative to
     .audio-player itself (the nearest positioned ancestor) so it spans the
     whole card and gets clipped by the card's own border-radius/overflow —
     no manual inset needed for the rounded corners. */
  .audio-player.source-podcast .player-content :deep(.progress-bar),
  .audio-player.source-music_library .player-content :deep(.progress-bar) {
    display: flex;
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    height: 2px;
    padding: 0;
    gap: 0;
  }

  .audio-player.source-podcast .player-content :deep(.progress-bar) .time,
  .audio-player.source-music_library .player-content :deep(.progress-bar) .time {
    display: none;
  }

  .audio-player.source-podcast .player-content :deep(.progress-container),
  .audio-player.source-music_library .player-content :deep(.progress-container) {
    height: 100%;
    border-radius: 0;
  }

  .audio-player.source-podcast .player-content :deep(.progress),
  .audio-player.source-music_library .player-content :deep(.progress) {
    border-radius: 0;
  }

  .controls {
    gap: var(--space-02);
    justify-content: center;
  }

  /* Compact mobile player keeps only play/pause; shuffle/prev/next/like are
     desktop-only — the swipe gesture covers prev/next on mobile instead. */
  .audio-player.source-music_library :deep(.ml-transport-extra) {
    display: none;
  }

  /* Radio, track detected: two 48px thumbnails overlapping by half. The station
     icon sits behind, pinned left; the track artwork rides on top, offset right.
     Frame widens to 72px (48 + 24 overlap) so the flex layout reserves the pair's
     full width and the title/subtitle clears it instead of overlapping. */
  .player-artwork-frame.has-badge {
    width: 72px;
  }

  /* Station icon: behind, pinned left. Extra .player-artwork-frame ancestor
     (rather than a bare :deep()) so this reliably outranks LazyImage's own
     scoped `.lazy-image { position: relative }`. */
  .player-artwork-frame.has-badge :deep(.player-artwork-badge) {
    position: absolute !important;
    top: 0;
    left: 0;
    width: 48px;
    height: 48px;
    border-radius: var(--radius-03);
    z-index: 0;
  }

  /* Track artwork: on top of the station, offset right so the pair overlaps by
     half. left:24px (not right:0) keeps its anchor stable while the frame
     animates its width. The reveal slide is driven by the animation below, not
     by .loaded: it must play once when the two-thumbnail state first appears and
     stay decoupled from the image's network load — a cached bitmap still slides
     in from the station's position (translateX(-24px)→0) instead of popping at
     rest. Opacity stays tied to .loaded (base img.player-artwork rule) so the
     bitmap fades in as it decodes. */
  .player-artwork-frame.has-badge .player-artwork {
    position: absolute;
    top: 0;
    left: 24px;
    width: 48px;
    height: 48px;
    z-index: 1;
    /* Runs on class-apply (state appears), not on src change — a song change
       keeps the element and the .has-badge class, so it doesn't re-slide; the
       new cover just fades in place. Synced with the frame widening 48→72 and
       the text shifting right (same 300ms easeOutCubic). */
    animation: musicImgReveal 0.3s var(--easeOutCubic);
  }
}

/* Vue Transition: Desktop - slide from right with fade */
@media (min-aspect-ratio: 4/3) {

  .audio-player-enter-active,
  .audio-player-leave-active {
    /* Pin to the rendered width (wrapper minus its left padding) so the player
       doesn't reflow to 100% while .player-wrapper collapses during the slide.
       Inherits the layout's single source of truth — no JS prop needed. */
    width: calc(var(--audio-player-wrapper-width) - var(--space-06));
  }

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

  /* Screensaver reveal: replay the slide-in (same as the enter transition above)
     when the screensaver is dismissed, without toggling the v-if/Transition. */
  .audio-player.audio-player-revealing {
    animation: audioPlayerReveal var(--transition-spring-slow) forwards;
  }
}

@keyframes audioPlayerReveal {
  from {
    opacity: 0;
    transform: translateX(100px);
  }

  to {
    opacity: 1;
    transform: translateX(0);
  }
}

/* Mobile radio: the track thumbnail slides out from the station's position
   (fully overlapping) to its half-overlap resting spot. Transform only —
   opacity is handled separately by the .loaded fade. */
@keyframes musicImgReveal {
  from {
    transform: translateX(-24px);
  }

  to {
    transform: translateX(0);
  }
}

/* Vue Transition: Mobile */
@media (max-aspect-ratio: 4/3) {

  .audio-player-enter-active,
  .audio-player-leave-active {
    position: fixed;
    bottom: calc(env(safe-area-inset-bottom, 0px) + var(--space-06));
    left: 50%;
  }

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
}
</style>
