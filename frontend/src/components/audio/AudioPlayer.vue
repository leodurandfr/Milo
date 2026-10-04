<template>
  <Teleport to="body" :disabled="!isMobile">
    <Transition name="audio-player" @after-leave="$emit('after-hide')">
      <!-- v-if, not v-show: a teleported v-show toggle (mobile) doesn't fire the
           transition classes, so the enter/leave would be instant. -->
      <div v-if="visible" class="audio-player"
        :class="playerClasses"
        @click="onBarClick" @touchstart="onTouchStart" @touchmove="onTouchMove" @touchend="onTouchEnd">
        <!-- Background image - heavily zoomed and blurred -->
        <div class="player-art-background">
          <img v-if="validArtwork" :src="validArtwork" alt="" class="background-image" />
          <div v-else-if="stationAvatarSvg" v-html="stationAvatarSvg" class="background-image" />
          <img v-else-if="fallbackImage" :src="fallbackImage" alt="" class="background-image" />
        </div>

        <div class="player-content">
          <!-- Artwork: with none valid, the shared helper says what the slot shows —
             radio's font-aware inline avatar, or the bundled placeholder for the other
             two. The player never picks that itself; AudioPlayerFull asks the same
             helper, so the two cannot disagree on one silence.
             Frame hosts an optional #artwork-badge (mobile radio: station icon sitting
             behind the track artwork, which rides on top) — needs a real box since two of
             the three branches below are void <img> elements and can't host a child. -->
          <div class="player-artwork-frame"
            :class="{ 'has-badge': !!$slots['artwork-badge'], clickable: albumLink || isMobile }"
            @click="onArtworkClick">
            <img v-if="validArtwork" :src="validArtwork" :alt="title" class="player-artwork"
              :class="{ loaded: artworkLoaded }" @load="handleArtworkLoad" @error="artworkError = true" />
            <div v-else-if="stationAvatarSvg" v-html="stationAvatarSvg" class="player-artwork" :aria-label="title" />
            <img v-else-if="fallbackImage" :src="fallbackImage" :alt="title" class="player-artwork placeholder" />
            <slot name="artwork-badge"></slot>
          </div>

          <!-- The source bar, the lines, the bar and the transport: the body
               both players share, ranged left on this card. -->
          <PlayerBody ref="body" :source="source" surface="card" @secondary-click="$emit('secondary-click')">
            <!-- The phone's swipe over a queue: a 3-cell strip [prev｜current｜next]
                 driven by the carousel's own viewIndex into the queue, so the
                 text is rendered locally and never reindexes against the backend
                 skip echo mid-animation. -->
            <template v-if="carousel" #info>
              <div class="player-info-carousel">
                <div ref="trackEl" class="player-info-track" :style="trackStyle" @transitionend.self="onSettleEnd">
                  <div v-for="cell in cells" :key="cell.pos" class="player-info-cell">
                    <p class="carousel-title text-body">{{ cell.title }}</p>
                    <p v-if="cell.artist" class="carousel-subtitle text-body">{{ cell.artist }}</p>
                  </div>
                </div>
              </div>
            </template>
          </PlayerBody>

        </div>

        <!-- On the kiosk's card, over the cover: the way into the full player
             at the top-left corner, what the source adds that is not a command
             (the favorite, the star, the like) at the top-right — the full
             player's order, way between the views on the left and heart on the
             right. Outside .player-content, which scrolls on the card when its
             content overflows: the way into the player must not scroll away
             with it. Not on the phone's mini-bar, which is one target as a
             whole (a tap anywhere opens the full player, which carries the
             heart). -->
        <template v-if="!isMobile">
          <IconButton class="player-expand" icon="expand" variant="on-grey" size="small"
            :aria-label="t('common.expandPlayer')" @click.stop="$emit('expand')" />
          <div v-if="$slots['artwork-action']" class="player-artwork-action" @click.stop>
            <slot name="artwork-action"></slot>
          </div>
        </template>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, inject, nextTick, ref, watch } from 'vue'
import IconButton from '@/components/ui/IconButton.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { useTimer } from '@/composables/useTimer'
import { usePlayerState } from '@/composables/usePlayerState'
import { PLAYER_NAVIGATION } from '@/composables/usePlayerExpansion'
import { swipeable, swipeTarget } from '@/utils/playerControls'
import { MIN_IMAGE_SIZE } from '@/constants/imageQuality'
import { useI18n } from '@/services/i18n'
import PlayerBody from './PlayerBody.vue'

const { isMobile } = useIsMobile()
const { t } = useI18n()
const timer = useTimer()

const props = defineProps({
  /**
   * Audio source type ('radio', 'podcast', 'music_library', 'spotify')
   */
  source: {
    type: String,
    required: true,
    validator: (value) => ['radio', 'podcast', 'music_library', 'spotify'].includes(value)
  },

  /**
   * Visibility control (replaces v-if in parent)
   */
  visible: {
    type: Boolean,
    default: false
  }
})

// `expand` asks the source for its full player: the expand button anywhere, and
// a tap on the phone's mini-bar. The album and the artist are emitted where the
// navigation says there is one to open; the source opens them.
const emit = defineEmits(['after-hide', 'artwork-click', 'secondary-click', 'expand'])

// What this bar names and what its source offers: this bar's one reading of
// its state, which the body and the transport under it take too.
const { metadata, controls: sourceControls } = usePlayerState(props.source)
const { title, artwork, fallback, stationAvatarSvg } = metadata
const { controls, details } = sourceControls
const body = ref(null)

// The album behind the cover, where the navigation around the bar says there is
// one to open (PLAYER_NAVIGATION, which BrowserSourceViews provides).
const navigation = inject(PLAYER_NAVIGATION, null)
const albumLink = computed(() => !!navigation?.canOpenAlbum.value)

// On the phone the whole mini-bar is one target, the full player; the album and
// artist links are the full player's there. The transport stops its own taps.
function onBarClick() {
  if (!isMobile.value) return
  // A swipe is not a tap: a browser that still fires a click after a drag
  // (a short one, under its own slop) must not open the full player too.
  if (Date.now() - swipeEndedAt < SWIPE_CLICK_GUARD_MS) return
  emit('expand')
}

function onArtworkClick() {
  if (!isMobile.value && albumLink.value) emit('artwork-click')
}

// Artwork validation — falls back to inline SVG / placeholder on error or tiny image (e.g. 1x1 tracking pixel)
const artworkError = ref(false)
// Fade the real artwork in on load instead of popping over the neutral box —
// reset on every src change so a new track/station image fades rather than snaps.
const artworkLoaded = ref(false)
watch(artwork, () => { artworkError.value = false; artworkLoaded.value = false })
const validArtwork = computed(() => artwork.value && !artworkError.value ? artwork.value : null)
// What fills the slot with no usable artwork, straight from the shared helper.
const fallbackImage = computed(() => fallback.value.kind === 'image' ? fallback.value.src : '')

function handleArtworkLoad(e) {
  if (e.target.naturalWidth < MIN_IMAGE_SIZE || e.target.naturalHeight < MIN_IMAGE_SIZE) {
    artworkError.value = true
    return
  }
  artworkLoaded.value = true
}

// Mobile swipe gesture — only on the fixed docked player, and only where the
// source takes one now (utils/playerControls' swipeable: a source that pauses,
// with a step or a −15/+30 to send; never a live stream). A queue source keeps
// its steps listed while the track it stepped to loads, so the gesture — and
// the carousel under it — outlive a track change rather than unmount
// mid-slide. The animated 3-cell text carousel is the richer case and
// additionally needs a real queue to read neighbour titles from — without one
// there's nothing to slide text in from.
const swipeEnabled = computed(() => swipeable(controls.value))
const swipeActive = computed(() => isMobile.value && swipeEnabled.value)
// The queue and the current index within it (Music Library's details), in the
// Subsonic song shape (title/name + artist).
const tracks = computed(() => (Array.isArray(details.value?.queue) ? details.value.queue : []))
const currentIndex = computed(() => details.value?.queue_index ?? -1)

const playerClasses = computed(() => ({
  [`source-${props.source}`]: true,
  swipeable: swipeEnabled.value
}))
const carousel = computed(() => swipeActive.value && tracks.value.length > 0)
const SWIPE_THRESHOLD_PX = 40
// How long after a drag a click is taken for the drag's own, and ignored.
const SWIPE_CLICK_GUARD_MS = 400
let swipeEndedAt = 0
const SETTLE_MS = 300
let touchStartX = 0
let touchStartY = 0
let touchTracking = false

// The carousel owns its own index into the queue and reads its three cells from
// it, NOT from the live backend index — so the skip echo (which reindexes
// queueIndex almost instantly) can't swap cell contents out from under the
// settle animation. It re-syncs to the store only between swipes.
const viewIndex = ref(currentIndex.value)
watch(currentIndex, (ci) => { if (!committing) viewIndex.value = ci })

// The entries a swipe lands on either side of the one shown (utils/
// playerControls' swipeTarget: past the last entry of a repeating queue, the
// first again), -1 where there is none.
const swipeTargets = computed(() => {
  const state = { controls: controls.value, details: details.value }
  const length = tracks.value.length
  return {
    prev: swipeTarget(state, 'prev', viewIndex.value, length),
    next: swipeTarget(state, 'next', viewIndex.value, length)
  }
})

const CELLS = [[-1, 'prev'], [0, null], [1, 'next']]
const cells = computed(() => CELLS.map(([offset, direction]) => {
  const song = tracks.value[direction ? swipeTargets.value[direction] : viewIndex.value]
  return {
    pos: offset,
    title: song ? (song.title || song.name || '') : '',
    artist: song ? (song.artist || '') : ''
  }
}))
const hasNextCell = computed(() => swipeTargets.value.next >= 0)
const hasPrevCell = computed(() => swipeTargets.value.prev >= 0)

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
let committedTarget = -1
let rehomeHandle = null

// Settle finished: advance the local index onto the committed neighbour and snap
// the strip back to centre. The neighbour text is already centred, so the snap is
// invisible.
function rehome() {
  if (!committing) return
  if (rehomeHandle) { timer.clear(rehomeHandle); rehomeHandle = null }
  viewIndex.value = committedTarget
  committing = false
  committedTarget = -1
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
  swipeEndedAt = Date.now()
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
      committedTarget = goingNext ? swipeTargets.value.next : swipeTargets.value.prev
      settle.value = goingNext ? 'next' : 'prev'
      committing = true
      if (rehomeHandle) timer.clear(rehomeHandle)
      rehomeHandle = timer.setTimeout(rehome, SETTLE_MS + 120) // fallback if transitionend is missed
    }
    // The body sends it: a step, or a −15/+30 through its own playhead. A step
    // back counts from the entry the carousel shows, not the one the backend
    // last echoed, so a second quick swipe aims one entry further back.
    body.value?.swipe(goingNext ? 'next' : 'prev', viewIndex.value)
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
  background: var(--color-veil-on-image);
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

/* The desktop sidebar's square cover; the mobile media query below shrinks it
   to the mini-bar's 48px thumbnail. */
.player-artwork-frame {
  position: relative;
  align-self: center;
  width: 100%;
  aspect-ratio: 1;
  /* In the parent flex column, flex-shrink: 1 (default) lets aspect-ratio be
     overridden when vertical space is tight; pinning it preserves the 1:1
     box for both <img> (which has intrinsic size) and the <div v-html=svg>
     wrapper (whose content is the SVG sized below). flex: none (not just
     flex-shrink: 0) also pins flex-grow/flex-basis so the sidebar's column
     layout can't compress this height (derived from width via aspect-ratio)
     either. */
  flex: none;
}

.player-artwork-frame.clickable {
  cursor: pointer;
}

/* Over the cover's top-left corner: the card's side padding plus the cover's
   own top inset (.player-content's), then the same again inside the cover. */
.player-expand {
  position: absolute;
  top: calc(var(--space-02) + var(--space-02));
  left: calc(var(--space-02) + var(--space-02));
  z-index: 3;
}

/* Its mirror over the top-right corner. */
.player-artwork-action {
  position: absolute;
  top: calc(var(--space-02) + var(--space-02));
  right: calc(var(--space-02) + var(--space-02));
  z-index: 3;
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

/* The swipe carousel's lines (the phone's mini-bar over a queue), one line
   each, cut by the carousel's own edge mask. */
.carousel-title,
.carousel-subtitle {
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: clip;
}

.carousel-title {
  color: var(--color-text-contrast);
}

.carousel-subtitle {
  color: var(--color-text-contrast-50);
}

/* Mobile: Horizontal bottom panel layout */
@media (max-aspect-ratio: 4/3) {
  .audio-player {
    position: fixed;
    /* bottom: calc(max(var(--space-06), env(safe-area-inset-bottom, 0px)) + var(--space-05)); */
    /* bottom: env(safe-area-inset-bottom, 0px); */
    bottom: calc(env(safe-area-inset-bottom, 0px) + var(--space-06));

    margin: 0;

    /* Anchored by both insets rather than centered with translate(-50%), and the
       absence of a transform is the point. Measured on an iPhone at DPR 3,
       music library playing, geometry held identical across all three: with
       translate(-50%, 0) the pause glyph shimmers by a device pixel on every
       progress tick; with translate(0, 0) it still shimmers; with no transform
       at all it stops. So a transform on this element is what triggers it,
       whatever its value. Why is not established — the card is composited
       regardless (the backdrop-filter above does that on its own), so layer
       promotion is not the explanation, and two theories died on measurement:
       a half-device-pixel origin (refuted — the viewport is 390 CSS px, an
       even number) and a glyph-local raster (refuted — translateZ(0) on the
       svg changed nothing). What is reproducible is the three-way result
       above. Only the pause glyph gives it away: two hard vertical edges,
       where the artwork and the text are too soft to show a third of a CSS
       pixel, and layout geometry stays stable to 0.00 device pixels the whole
       time, which is why it reads as a glyph bug rather than a whole-card one.
       The second condition is the progress fill translating inside the blurred
       card at 10 Hz; at rest nothing repaints and nothing shimmers. Keep this
       element transform-free at rest — the entrance below animates translateY
       alone for the same reason. */
    left: var(--space-02);
    right: var(--space-02);
    width: auto;
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

  .audio-player.swipeable {
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

  /* Single 48px row layout, shared by the four sources — artwork | the body
     (title+subtitle, then the main button) | expand.
     Width is animated so the radio station→track reveal (frame 48→72) shifts the
     title/subtitle text rightward in sync with the track image sliding in. */
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

  /* Swipe carousel: the clipped viewport the body's info block hosts; the strip
     holds the three text cells side by side and slides horizontally. Only in
     the DOM on the phone over a queue (v-if="carousel"). */
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
    padding-left: var(--space-02);
  }

  .player-content {
    min-width: 0;
  }

  /* The mini-bar keeps the main button alone; everything else on the
     transport is PlayerTransport's `player-extra`, and the swipe gesture
     covers prev/next (or −15 / +30) here instead. */
  .audio-player :deep(.player-extra) {
    display: none;
  }

  /* Radio, track detected: two 48px thumbnails overlapping by half. The station
     icon sits behind, pinned left; the track artwork rides on top, offset right.
     Frame widens to 72px (48 + 24 overlap) so the flex layout reserves the pair's
     full width and the title/subtitle clears it instead of overlapping.
     #artwork-badge only ever renders in the mini-bar, alongside the 48px sizing
     above. */
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
    left: var(--space-02);
    right: var(--space-02);
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
    transform: translateY(120px);
  }

  .audio-player-enter-to,
  .audio-player-leave-from {
    opacity: 1;
    transform: none;
  }

  .audio-player-leave-to {
    opacity: 0;
    transform: translateY(120px);
  }
}
</style>
