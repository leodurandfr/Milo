<template>
  <Teleport to="body" :disabled="!isMobile">
    <Transition name="audio-player" @after-leave="$emit('after-hide')">
      <!-- v-if, not v-show: a teleported v-show toggle (mobile) doesn't fire the
           transition classes, so the enter/leave would be instant. -->
      <div v-if="visible" class="audio-player"
        :class="[playerClasses, { 'audio-player-revealing': revealing, 'expand-open': expanded }]"
        @click="onBarClick" @touchstart="onTouchStart" @touchmove="onTouchMove" @touchend="onTouchEnd">
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
          <div class="player-artwork-frame"
            :class="{ 'has-badge': !!$slots['artwork-badge'], clickable: hasEntityLinks || expandable }"
            @click="onMiniArtworkClick">
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
              <div class="player-info-inner" :key="title" :class="{ 'has-entity-links': hasEntityLinks }"
                @click="onInfoClick">
                <slot name="info" :expanded="false"></slot>
              </div>
            </Transition>
          </div>

          <!-- Progress bar + controls are pinned together at the bottom, 8px apart —
               the info block above is what's centered in the remaining space, not this group. -->
          <div class="player-bottom">
            <slot name="progress"></slot>

            <div class="controls">
              <slot name="controls" :expanded="false">
                <!-- Default: Simple play/pause -->
                <div class="playback-controls">
                  <IconButton :icon="isPlaying ? 'pause' : 'play'" variant="ghost" size="medium" :loading="isLoading"
                    @click="$emit('toggle-play')" />
                </div>
              </slot>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>

  <Teleport to="body">
    <!-- JS hooks (`:css="false"`) own mount/unmount timing only; the open/close
         motion is a CSS transition on cardStyle/scrimStyle driven by a single
         offset, so the swipe-drag and the animation share one position value and
         never fight over the transform. -->
    <Transition :css="false" @enter="onExpandEnter" @leave="onExpandLeave">
      <div v-if="expanded" class="audio-player-expanded" :style="scrimStyle" @click.self="collapse">
        <!-- Dim layer: pointer-events none so taps fall through to the scrim's
             @click.self; its opacity fades with the sheet position. -->
        <div class="expanded-dim" :style="dimStyle"></div>
        <!-- Wrapper carries the positioning transform; the button keeps its own
             press-scale transform (which is !important and would otherwise clobber
             the translateX(-50%) centring, jumping the button sideways on tap). -->
        <div class="expanded-close" :style="closeStyle">
          <IconButton icon="close" variant="rounded" size="large"
            :aria-label="t('common.close')" @click="collapse" />
        </div>

        <div class="expanded-card" :class="playerClasses" :style="cardStyle"
          @touchstart="onExpandTouchStart" @touchmove="onExpandTouchMove" @touchend="onExpandTouchEnd">
          <div class="player-art-background">
            <img v-if="validArtwork" :src="validArtwork" alt="" class="background-image" />
            <div v-else-if="fallbackSvg" v-html="fallbackSvg" class="background-image" />
            <img v-else-if="placeholderArtwork" :src="placeholderArtwork" alt="" class="background-image" />
          </div>

          <div class="expanded-content">
            <div class="player-artwork-frame" :class="{ clickable: hasEntityLinks }" @click="onExpandedArtworkClick">
              <img v-if="validArtwork" :src="validArtwork" :alt="title" class="player-artwork"
                :class="{ loaded: artworkLoaded }" @load="handleArtworkLoad" @error="artworkError = true" />
              <div v-else-if="fallbackSvg" v-html="fallbackSvg" class="player-artwork" :aria-label="title" />
              <img v-else :src="placeholderArtwork" :alt="title" class="player-artwork placeholder" />
            </div>

            <div class="expanded-info" @click="onExpandedInfoClick">
              <slot name="info" :expanded="true"></slot>
            </div>

            <div class="expanded-bottom">
              <div class="expanded-progress">
                <slot name="progress"></slot>
              </div>

              <div class="expanded-controls">
                <slot name="controls" :expanded="true">
                  <div class="playback-controls">
                    <IconButton :icon="isPlaying ? 'pause' : 'play'" variant="ghost" size="medium"
                      :loading="isLoading" @click="$emit('toggle-play')" />
                  </div>
                </slot>
              </div>
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
import { useI18n } from '@/services/i18n'

const { isMobile } = useIsMobile()
const { t } = useI18n()
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

const emit = defineEmits(['toggle-play', 'after-hide', 'swipe-next', 'swipe-prev', 'artwork-click', 'secondary-click'])

// Only music_library has album/artist pages to link to — radio/podcast render
// the same artwork frame and #player-info-secondary line but have nothing to
// navigate to, so they get neither the pointer cursor nor the click emit.
const hasEntityLinks = computed(() => props.source === 'music_library')

// Delegated: .player-info-secondary is rendered by the slotted PlayerInfoText,
// not by this component, so it's caught by class rather than a direct handler.
function onInfoClick(e) {
  if (expandable.value) return
  if (hasEntityLinks.value && e.target.closest('.player-info-secondary')) emit('secondary-click')
}

const EXPANDABLE_SOURCES = ['radio', 'podcast', 'music_library']
const expanded = ref(false)
const expandable = computed(() => isMobile.value && EXPANDABLE_SOURCES.includes(props.source))

// One source of truth for the sheet's vertical position, in px (0 = fully open,
// growing downward → toward closed). The open/close animation AND the swipe drag
// both drive this single value, so there's no inline-style ↔ Vue-transition
// handoff (which is what used to jump). `placing` is the pre-open frame where the
// card is parked off-screen with the transition suppressed, before it animates up.
const offsetY = ref(0)
const expandDragging = ref(false)
const placing = ref(false)

// Timing for the non-drag moves. CSS var()s resolve fine inside an inline
// transition string, so the tokens stay the single source of truth.
const OPEN_TIMING = '0.62s cubic-bezier(0.16, 1, 0.3, 1)' // dynamic easeOutExpo
const OPEN_MS = 620
// easeOut (fast start), NOT easeIn: on a swipe-release the card must keep the
// finger's downward momentum. easeIn barely moves for the first ~100ms, which
// reads as a "stall"/lag right after you let go.
const CLOSE_TIMING = '0.4s var(--easeOutCubic)'
const CLOSE_MS = 400
const SNAP_TIMING = 'var(--transition-medium)'
const moveTiming = ref(OPEN_TIMING)

function closedOffset() {
  return typeof window !== 'undefined' ? window.innerHeight : 1000
}

// Scrim blur is derived from the SAME offset, so it always tracks the card's
// position — eased over moveTiming during open/close, following the finger during
// a drag. Full (--blur-03 = 24px) at rest, 0 once off-screen.
const SCRIM_BLUR_MAX_PX = 24
const SCRIM_BLUR_FALLOFF_PX = 300

const cardStyle = computed(() => ({
  transform: `translateY(${offsetY.value}px)`,
  transition: (expandDragging.value || placing.value) ? 'none' : `transform ${moveTiming.value}`
}))

const scrimStyle = computed(() => {
  // Blur is derived from the sheet position so it tracks the card everywhere:
  // per-frame during a drag (transition none → follows the finger), and eased on
  // open/close (transition set). Mobile-only view, so iOS handles the per-frame
  // backdrop-filter fine.
  const blur = SCRIM_BLUR_MAX_PX * Math.max(0, 1 - offsetY.value / SCRIM_BLUR_FALLOFF_PX)
  const b = `blur(${blur.toFixed(1)}px)`
  return {
    backdropFilter: b,
    WebkitBackdropFilter: b,
    transition: (expandDragging.value || placing.value)
      ? 'none'
      : `backdrop-filter ${moveTiming.value}, -webkit-backdrop-filter ${moveTiming.value}`
  }
})

// The top close button rises with the drag (mirror of the card sinking), capped
// at the distance that tucks it above the top edge. Same position source as the
// card, so it enters from the top on open and leaves upward on close.
const CLOSE_UP_MAX_PX = 120
function closeUpFor(y) {
  return Math.min(y, CLOSE_UP_MAX_PX)
}
const closeStyle = computed(() => ({
  transform: `translateX(-50%) translateY(${-closeUpFor(offsetY.value)}px)`,
  transition: (expandDragging.value || placing.value) ? 'none' : `transform ${moveTiming.value}`
}))

// The dim backdrop fades out with the sheet position (same falloff as the blur),
// on a dedicated layer so only the darkening fades — not the blur or the content.
const dimStyle = computed(() => ({
  opacity: Math.max(0, 1 - offsetY.value / SCRIM_BLUR_FALLOFF_PX).toFixed(3),
  transition: (expandDragging.value || placing.value) ? 'none' : `opacity ${moveTiming.value}`
}))

function collapse() {
  expanded.value = false // fires the <Transition> leave hook, which animates offsetY out
}

function onBarClick() {
  if (!expandable.value || expanded.value) return
  // Park the card off-screen before it mounts so the enter hook animates it up
  // from the very bottom (no first-paint flash at the rest position).
  offsetY.value = closedOffset()
  placing.value = true
  moveTiming.value = OPEN_TIMING
  expanded.value = true
}

// Transition JS hooks own only mount/unmount timing; the motion itself is the CSS
// transition on cardStyle/scrimStyle, driven by offsetY.
function onExpandEnter(el, done) {
  moveTiming.value = OPEN_TIMING
  nextTick(() => {
    el.getBoundingClientRect() // commit the parked off-screen frame before animating
    placing.value = false
    offsetY.value = 0
    timer.setTimeout(done, OPEN_MS)
  })
}

function onExpandLeave(el, done) {
  // A leaving element is detached from reactive :style updates, so a ref change
  // here would NOT reach the card — it would freeze at the release point and then
  // vanish. Drive the close on the DOM nodes directly instead. `el` is the scrim;
  // the card is its child. The card starts from whatever transform it currently
  // has (rest 0 OR a mid-drag offset), so the motion continues seamlessly.
  const card = el.querySelector('.expanded-card')
  const closeBtn = el.querySelector('.expanded-close')
  const dim = el.querySelector('.expanded-dim')
  const target = closedOffset()
  el.style.transition = `backdrop-filter ${CLOSE_TIMING}, -webkit-backdrop-filter ${CLOSE_TIMING}`
  if (card) card.style.transition = `transform ${CLOSE_TIMING}`
  if (closeBtn) closeBtn.style.transition = `transform ${CLOSE_TIMING}`
  if (dim) dim.style.transition = `opacity ${CLOSE_TIMING}`
  el.getBoundingClientRect() // commit the current transform/blur/opacity as the transitions' start
  el.style.backdropFilter = 'blur(0px)'
  el.style.webkitBackdropFilter = 'blur(0px)'
  if (card) card.style.transform = `translateY(${target}px)`
  if (closeBtn) closeBtn.style.transform = `translateX(-50%) translateY(${-CLOSE_UP_MAX_PX}px)`
  if (dim) dim.style.opacity = '0'
  timer.setTimeout(done, CLOSE_MS)
}

function onMiniArtworkClick() {
  if (!expandable.value && hasEntityLinks.value) emit('artwork-click')
}

function onExpandedArtworkClick() {
  if (hasEntityLinks.value) { emit('artwork-click'); collapse() }
}
function onExpandedInfoClick(e) {
  if (hasEntityLinks.value && e.target.closest('.player-info-secondary')) { emit('secondary-click'); collapse() }
}

watch(() => props.visible, (v) => { if (!v) expanded.value = false })
watch(isMobile, (m) => { if (!m) expanded.value = false })

// Swipe-down-to-close: the card follows a downward drag (offsetY = drag distance)
// and dismisses past the threshold; horizontal/upward drags and taps pass through.
const EXPAND_CLOSE_THRESHOLD_PX = 100
let expandStartX = 0
let expandStartY = 0
let expandTracking = false

function onExpandTouchStart(e) {
  const touch = e.touches[0]
  expandStartX = touch.clientX
  expandStartY = touch.clientY
  expandTracking = true
  expandDragging.value = false
}

function onExpandTouchMove(e) {
  if (!expandTracking) return
  const touch = e.touches[0]
  const dx = touch.clientX - expandStartX
  const dy = touch.clientY - expandStartY
  if (!expandDragging.value) {
    if (dy > 10 && dy > Math.abs(dx)) {
      expandDragging.value = true
    } else {
      return
    }
  }
  // Own the gesture so the page behind doesn't scroll / native overscroll-bounce
  // underneath the sheet (a common source of drag jitter).
  if (e.cancelable) e.preventDefault()
  offsetY.value = Math.max(0, dy)
}

function onExpandTouchEnd() {
  if (!expandTracking) return
  expandTracking = false
  const wasDragging = expandDragging.value
  expandDragging.value = false
  if (!wasDragging) return
  if (offsetY.value > EXPAND_CLOSE_THRESHOLD_PX) {
    collapse() // leave hook animates offsetY from here → off-screen, no jump
  } else {
    moveTiming.value = SNAP_TIMING
    offsetY.value = 0 // snap back up
  }
}

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

// Mobile swipe gesture — only on the fixed docked player. swipeEnabled alone
// covers seek-style sources (podcast: swipe always fires, no neighbour concept,
// title stays the plain slotted text). The animated 3-cell text carousel is the
// richer case (music library) and additionally needs a real queue to read
// neighbour titles from — without one there's nothing to slide text in from.
const swipeActive = computed(() => isMobile.value && props.swipeEnabled)
const carousel = computed(() => swipeActive.value && props.tracks.length > 0)
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
  if (!swipeActive.value) return
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
  if (e.cancelable) e.preventDefault()
  // Rubber-band toward a missing neighbour (queue end) so it snaps back. Only
  // meaningful for the queue-backed carousel — a plain seek swipe (podcast) has
  // no neighbour concept and always follows the finger at full strength.
  const towardMissing = carousel.value && (dx < 0 ? !hasNextCell.value : !hasPrevCell.value)
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
  // No carousel (podcast: plain seek swipe) → no neighbour to check, always fires.
  const hasNeighbour = !carousel.value || (goingNext ? hasNextCell.value : hasPrevCell.value)
  if (passed && hasNeighbour) {
    // Finger left → next, finger right → prev.
    if (carousel.value) {
      committedDir = goingNext ? 1 : -1
      settle.value = goingNext ? 'next' : 'prev'
      committing = true
      if (rehomeHandle) timer.clear(rehomeHandle)
      rehomeHandle = timer.setTimeout(rehome, SETTLE_MS + 120) // fallback if transitionend is missed
    }
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

/* While the expanded sheet is open, hide the docked mini-bar so its title doesn't
   show through the scrim's blur as a ghost second title. */
.audio-player.expand-open {
  visibility: hidden;
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

/* Shared by the docked frame (desktop sidebar + mobile mini-bar, sized via the
   mobile media query below) and the expanded sheet's artwork (which keeps this
   base square size — only the docked mobile override shrinks it to 48px). */
.player-artwork-frame {
  position: relative;
  align-self: center;
  width: 100%;
  aspect-ratio: 1;
  /* In the parent flex column, flex-shrink: 1 (default) lets aspect-ratio be
     overridden when vertical space is tight; pinning it preserves the 1:1
     box for both <img> (which has intrinsic size) and the <div v-html=svg>
     wrapper (whose content is the SVG sized below). flex: none (not just
     flex-shrink: 0) also pins flex-grow/flex-basis so the expanded sheet's
     column layout can't compress this height (derived from width via
     aspect-ratio) either. */
  flex: none;
}

.player-artwork-frame.clickable {
  cursor: pointer;
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

.player-info-inner.has-entity-links :deep(.player-info-secondary) {
  cursor: pointer;
}

/* Desktop/mobile split for slotted #controls content that isn't available in
   the compact mini-bar (podcast's seek buttons, speed selector) — each source
   renders both variants and lets this toggle pick one, instead of duplicating
   layout CSS per source file. */
:deep(.mobile-only) {
  display: none;
}

/* Vertical (column: kicker/title/secondary via PlayerInfoText) vs horizontal
   (compact single-line title/subtitle pair) — the #info slot's own layout
   toggle, orthogonal to desktop-only/mobile-only above: "vertical" also
   renders on the mobile expanded sheet for sources that reuse it there
   (podcast, music-library), so naming it "desktop-only" would be wrong.
   !important: an element carrying .horizontal-layout can also carry another
   utility class with its own `display` (e.g. radio's .playback-controls,
   `display: flex`) — same specificity, and without !important here the later
   rule in the cascade would win regardless of aspect ratio. */
:deep(.horizontal-layout) {
  display: none !important;
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

/* === Per-source transport layout ===
   The #controls slot has a default (a lone play/pause) and all three browser
   sources replace it with their own row, so its layout has to live somewhere.
   It lives here, beside the sizing rules that already key off these same class
   names, rather than in each source's scoped CSS: scoped CSS reaches only the
   markup that file authors, and the same rows are re-authored by the gallery's
   SourceStage — which left the transports rendering unstyled there while
   looking plausible. One home per class, and the class is already the contract. */

/* Radio: a text Button plus a favourite, not a ghost icon row. */
:deep(.radio-controls) {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: var(--space-02);
  z-index: 1;
  width: 100%;
}

/* .vertical-layout renders identically wherever it's shown — desktop sidebar
   and the mobile expanded sheet alike (the layout toggle above hides it in the
   mobile docked mini-bar only) — so its width/justify rules must NOT be
   aspect-ratio-gated, or the sheet loses parity with the desktop sidebar. */
:deep(.radio-controls-main) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-02);
  width: 100%;
}

:deep(.radio-controls-main .btn) {
  width: 100%;
}

/* Podcast: the speed dropdown is pulled out of the centred transport row and
   pinned to the row's left edge (.controls is the positioned ancestor). */
:deep(.speed-selector) {
  display: flex;
  align-items: center;
  position: absolute;
  left: 0;
}

:deep(.speed-selector .dropdown) {
  width: auto;
  flex: none;
}

:deep(.speed-selector .dropdown-menu) {
  min-width: 100px;
}

/* Music library: one transport row (shuffle … prev·play·next … like), with the
   trio centred inside it. */
:deep(.ml-controls) {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  width: 100%;
}

:deep(.ml-transport-main) {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-02);
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
    box-shadow: var(--shadow-raised-03);
  }

  .audio-player::before {
    border-radius: var(--radius-05);
  }

  .audio-player.source-music_library {
    touch-action: pan-y;
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
     title/subtitle text rightward in sync with the track image sliding in.
     Scoped to .audio-player (the docked bar) — the expanded sheet shares the
     same .player-artwork-frame/.player-artwork classes but must stay full-size
     even though it's also viewed under this same mobile aspect-ratio query. */
  .audio-player .player-artwork-frame {
    width: 48px;
    height: 48px;
    min-width: 48px;
    transition: width var(--transition-medium);
  }

  .audio-player .player-artwork {
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
    margin-left: calc(-1 * var(--space-03));
    margin-right: calc(-1 * (var(--space-03) - var(--space-01)));
    -webkit-mask-image: linear-gradient(to right, transparent 0, #000 var(--space-02), #000 calc(100% - var(--space-05)), transparent 100%);
    mask-image: linear-gradient(to right, transparent 0, #000 var(--space-02), #000 calc(100% - var(--space-05)), transparent 100%);
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
    padding-left: var(--space-02);
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

  .audio-player :deep(.desktop-only) {
    display: none !important;
  }

  .audio-player :deep(.mobile-only) {
    display: block !important;
  }

  .audio-player :deep(.vertical-layout) {
    display: none !important;
  }

  .audio-player :deep(.horizontal-layout) {
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

  /* Compact mini-bar: the medium-tier play/pause/stop toggle is too large next
     to the 48px artwork thumbnail in this tight single row — drop to
     SvgIcon's own native mobile-small size (20px) instead of inventing a new
     arbitrary value. Scoped to .audio-player (the docked bar only) so the
     desktop sidebar and the expanded sheet — both reached via the same
     .icon-button--ghost.icon-button--medium selector — keep their own sizing
     untouched. */
  .audio-player .playback-controls :deep(.icon-button--ghost.icon-button--medium .svg-responsive) {
    width: 20px;
    height: 20px;
  }

  .audio-player .playback-controls :deep(.icon-button--ghost.icon-button--medium.icon-button--loading .loading-spinner--medium) {
    --spinner-size: 20px;
  }

  /* Compact mobile player keeps only play/pause; shuffle/prev/next/like are
     desktop-only — the swipe gesture covers prev/next on mobile instead. */
  .audio-player.source-music_library :deep(.ml-transport-extra) {
    display: none;
  }

  /* Radio's docked mini-bar renders only the compact ghost icon button
     (.horizontal-layout) — push it to the row's edge. */
  :deep(.radio-controls) {
    justify-content: flex-end;
  }

  /* Nothing to pin against in the single mini-bar row: the speed selector goes
     back in flow. (Only reached in the expanded sheet — it is .desktop-only in
     the docked bar.) */
  :deep(.speed-selector) {
    position: static;
  }

  /* Radio, track detected: two 48px thumbnails overlapping by half. The station
     icon sits behind, pinned left; the track artwork rides on top, offset right.
     Frame widens to 72px (48 + 24 overlap) so the flex layout reserves the pair's
     full width and the title/subtitle clears it instead of overlapping.
     #artwork-badge only ever renders in the docked bar, so these are scoped to
     .audio-player alongside the 48px sizing above (never reached in the sheet). */
  .audio-player .player-artwork-frame.has-badge {
    width: 72px;
  }

  /* Station icon: behind, pinned left. Extra .player-artwork-frame ancestor
     (rather than a bare :deep()) so this reliably outranks LazyImage's own
     scoped `.lazy-image { position: relative }`. */
  .audio-player .player-artwork-frame.has-badge :deep(.player-artwork-badge) {
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
  .audio-player .player-artwork-frame.has-badge .player-artwork {
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

/* Scrim: full-screen blurred backdrop, exactly like Modal.vue. Tapping it closes. */
.audio-player-expanded {
  position: fixed;
  inset: 0;
  z-index: 4500;
  display: flex;
  flex-direction: column;
  /* Modal-sized inset around the card: 8px side gutters, 32px bottom margin (or
     the safe area if larger), and top clearance for the close button. The card
     fills what remains. */
  padding:
    calc(max(var(--space-04), env(safe-area-inset-top, 0px)) + 64px)
    var(--space-02)
    max(32px, env(safe-area-inset-bottom, 0px));
  /* Backdrop blur is driven inline (scrimStyle) from the sheet offset, so it
     tracks the card through open, close and drag; this base value is just the
     resting fallback. The dim darkening lives on .expanded-dim so it can fade
     independently of the blur. */
  backdrop-filter: blur(var(--blur-03));
  -webkit-backdrop-filter: blur(var(--blur-03));
}

/* Dim darkening layer — its opacity is driven inline (dimStyle) so it fades with
   the sheet position, over the blurred backdrop and under the card/close button. */
.expanded-dim {
  position: absolute;
  inset: 0;
  background: var(--color-background-medium-32);
  pointer-events: none;
}

/* Top-centred rounded close button, mirroring Modal.vue's affordance. */
.expanded-close {
  position: absolute;
  top: max(var(--space-04), env(safe-area-inset-top, 0px));
  left: 50%;
  transform: translateX(-50%);
  z-index: 3;
}

/* The player itself: a rounded panel floating on the scrim. Slides up from the
   bottom on open; the swipe-down drag + snap-back also move this. */
.expanded-card {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-06);
  overflow: hidden;
  background: var(--color-background-neutral);
  will-change: transform;
  /* Transform is driven inline (cardStyle) from the sheet offset — no base
     transition here, or it would fight the inline one during a drag. */
}

.expanded-card .expanded-content {
  position: relative;
  z-index: 2;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
  padding: var(--space-02);
  /* Escape valve for when artwork + info + controls don't all fit (e.g. a tall
     info block): the column scrolls instead of the artwork getting compressed. */
  overflow-y: auto;
}

/* Fills the space between artwork and controls when there's slack (grows,
   content centred vertically) but — unlike .expanded-content/.expanded-card —
   deliberately has NO min-height override, so its floor stays its own content
   size (mirrors desktop .player-info): once content + artwork + controls don't
   fit, this can't be squeezed smaller — .expanded-content overflows and
   scrolls instead. */
.expanded-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  text-align: center;
}

.expanded-info :deep(.player-info-text) {
  align-items: center;
}

/* Radio and music-library keep --space-03 (12px) between title/artist in the
   expanded sheet — wider than PlayerInfoText's own default --space-02 (8px). */
.expanded-card.source-radio .expanded-info :deep(.player-info-text),
.expanded-card.source-music_library .expanded-info :deep(.player-info-text) {
  gap: var(--space-03);
}

/* Music-library only: 32px breathing room from artwork/controls on mobile
   (the only context this renders in) — space-06 would shrink to 24px there. */
.expanded-card.source-music_library .expanded-info :deep(.player-info-text) {
  padding: var(--space-07) var(--space-06);
}

.expanded-bottom {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
  padding-bottom: var(--space-06);
}

/* Radio only: its controls (Button + heart) carry no side padding of their own
   (unlike podcast/music-library's .playback-controls, which pads itself) — give
   them the same 24px gutter as the section's own vertical spacing above. Gap is
   zeroed instead of inherited: the base gap exists to separate the progress bar
   from the controls, and radio has no #progress slot content — an empty
   .expanded-progress would otherwise still claim that gap as blank space. */
.expanded-card.source-radio .expanded-bottom {
  gap: 0;
  padding-left: var(--space-06);
  padding-right: var(--space-06);
}

.expanded-progress {
  flex-shrink: 0;
}

.expanded-progress :deep(.progress-bar) {
  padding: 0 var(--space-04);
}

.expanded-controls {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-04);
}

.expanded-card.source-music_library :deep(.ml-controls .playback-controls) {
  width: 100%;
  justify-content: space-around;
  padding: 0 var(--space-02);
}

/* Transport hierarchy (IconButton's ghost variant has no size bump of its own
   — it's sized like every other variant — so the tiers below are local to
   this player): shuffle + like sit as direct children of the row (the trio
   is nested in .ml-transport-main) and stay at their native small size,
   everywhere. */
.audio-player.source-music_library :deep(.ml-controls .playback-controls > .icon-button .svg-responsive),
.expanded-card.source-music_library :deep(.ml-controls .playback-controls > .icon-button .svg-responsive) {
  width: 24px;
  height: 24px;
}

/* prev/next and play/pause read bigger in the expanded sheet (any aspect
   ratio) AND in the desktop docked sidebar (the full transport row, not the
   mobile single-row mini-bar — that one is handled by the mobile-scoped
   .audio-player .playback-controls rule further up, which stays a tight
   single button). The two states must match exactly, so both selector groups
   below carry identical pixel values. */
.expanded-card.source-music_library :deep(.ml-transport-main .icon-button--small .svg-responsive) {
  width: 34px;
  height: 34px;
}

.expanded-card :deep(.ml-transport-main .icon-button--medium .svg-responsive) {
  width: 44px;
  height: 44px;
}

.expanded-card :deep(.ml-transport-main .icon-button--medium.icon-button--loading .loading-spinner--medium) {
  --spinner-size: 44px;
}

.expanded-card :deep(.ml-transport-main .icon-button--medium.icon-button--loading .loading-spinner--medium .loading-spinner-content) {
  transform: scale(0.85);
}

@media (min-aspect-ratio: 4/3) {
  .audio-player.source-music_library :deep(.ml-transport-main .icon-button--small .svg-responsive) {
    width: 34px;
    height: 34px;
  }

  .audio-player :deep(.ml-transport-main .icon-button--medium .svg-responsive) {
    width: 44px;
    height: 44px;
  }

  .audio-player :deep(.ml-transport-main .icon-button--medium.icon-button--loading .loading-spinner--medium) {
    --spinner-size: 44px;
  }

  .audio-player :deep(.ml-transport-main .icon-button--medium.icon-button--loading .loading-spinner--medium .loading-spinner-content) {
    transform: scale(0.85);
  }

  /* Podcast, desktop sidebar only: play/pause joins the 44px primary tier that
     music library and the lyrics bar already use. The -15s/+30s pair is left on
     IconButton's native 24px small deliberately — it is utility, the peer of
     ML's shuffle/like rather than of its prev/next, so it takes their tier. */
  .audio-player.source-podcast :deep(.playback-controls .icon-button--medium .svg-responsive) {
    width: 44px;
    height: 44px;
  }

  .audio-player.source-podcast :deep(.playback-controls .icon-button--medium.icon-button--loading .loading-spinner--medium) {
    --spinner-size: 44px;
  }

  .audio-player.source-podcast :deep(.playback-controls .icon-button--medium.icon-button--loading .loading-spinner--medium .loading-spinner-content) {
    transform: scale(0.85);
  }
}
</style>
