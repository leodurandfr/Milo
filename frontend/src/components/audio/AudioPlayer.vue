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
            :class="{ 'has-badge': !!$slots['artwork-badge'], clickable: hasEntityLinks || isMobile }"
            @click="onArtworkClick">
            <img v-if="validArtwork" :src="validArtwork" :alt="title" class="player-artwork"
              :class="{ loaded: artworkLoaded }" @load="handleArtworkLoad" @error="artworkError = true" />
            <div v-else-if="stationAvatarSvg" v-html="stationAvatarSvg" class="player-artwork" :aria-label="title" />
            <img v-else-if="fallbackImage" :src="fallbackImage" :alt="title" class="player-artwork placeholder" />
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
                <slot name="info"></slot>
              </div>
            </Transition>
          </div>

          <!-- Progress bar + controls are pinned together at the bottom, 8px apart —
               the info block above is what's centered in the remaining space, not this group. -->
          <div class="player-bottom">
            <slot name="progress"></slot>

            <div class="controls transport-scale--compact">
              <slot name="controls"></slot>
            </div>
          </div>

        </div>

        <!-- Opens the source's full player (AudioPlayerFull), which the source
             mounts in place of its navigation. Over the cover on the desktop
             card, at the end of the row on the phone's mini-bar — where a tap
             anywhere else on the bar does the same. Outside .player-content,
             which scrolls on the desktop card when its content overflows: the
             only way into the player must not scroll away with it. -->
        <IconButton class="player-expand" icon="caretUp" :variant="isMobile ? 'ghost' : 'on-grey'" size="small"
          :color="isMobile ? 'var(--color-text-contrast-50)' : null"
          :aria-label="t('common.expandPlayer')" @click.stop="$emit('expand')" />
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import IconButton from '@/components/ui/IconButton.vue'
import { useIsMobile } from '@/composables/useIsMobile'
import { useTimer } from '@/composables/useTimer'
import { generateStationAvatarSvg } from '@/utils/stationAvatar'
import { artworkFallback } from '@/utils/nowPlayingArtwork'
import { MIN_IMAGE_SIZE } from '@/constants/imageQuality'
import { TRACK_LAYOUT_SOURCES } from '@/constants/audioSources'
import { useI18n } from '@/services/i18n'

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
  },

  /**
   * Artwork/image URL for the current item
   */
  artwork: {
    type: String,
    default: null
  },

   /**
   * Station name for the generated inline SVG avatar. Radio only — the helper
   * decides that, this prop only supplies the text. Inline rendering (v-html)
   * inherits document @font-face; an <img> data URL would lose Space Mono Bold
   * and fall back to the system monospace.
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

// `expand` asks the source for its full player: the expand button anywhere, and
// a tap on the phone's mini-bar. The player draws neither view itself.
const emit = defineEmits(['after-hide', 'swipe-next', 'swipe-prev', 'artwork-click', 'secondary-click', 'expand'])

// Only a track player has album/artist pages to link to — radio/podcast render
// the same artwork frame and #player-info-secondary line but have nothing to
// navigate to, so they get neither the pointer cursor nor the click emit.
const trackLayout = computed(() => TRACK_LAYOUT_SOURCES.includes(props.source))
const hasEntityLinks = trackLayout

// On the phone the whole mini-bar is one target, the full player; the album and
// artist links are the full player's there. The transport stops its own taps.
function onBarClick() {
  if (isMobile.value) emit('expand')
}

// Delegated: .player-info-secondary is rendered by the slotted PlayerInfoText,
// not by this component, so it's caught by class rather than a direct handler.
function onInfoClick(e) {
  if (isMobile.value) return
  if (hasEntityLinks.value && e.target.closest('.player-info-secondary')) emit('secondary-click')
}

function onArtworkClick() {
  if (!isMobile.value && hasEntityLinks.value) emit('artwork-click')
}

// Artwork validation — falls back to inline SVG / placeholder on error or tiny image (e.g. 1x1 tracking pixel)
const artworkError = ref(false)
// Fade the real artwork in on load instead of popping over the neutral box —
// reset on every src change so a new track/station image fades rather than snaps.
const artworkLoaded = ref(false)
watch(() => props.artwork, () => { artworkError.value = false; artworkLoaded.value = false })
const validArtwork = computed(() => props.artwork && !artworkError.value ? props.artwork : null)
// What fills the slot with no usable artwork, straight from the shared helper.
const fallback = computed(() => artworkFallback(props.source))
const stationAvatarSvg = computed(() =>
  fallback.value.kind === 'avatar' && props.fallbackName
    ? generateStationAvatarSvg(props.fallbackName)
    : ''
)
const fallbackImage = computed(() => fallback.value.kind === 'image' ? fallback.value.src : '')

function handleArtworkLoad(e) {
  if (e.target.naturalWidth < MIN_IMAGE_SIZE || e.target.naturalHeight < MIN_IMAGE_SIZE) {
    artworkError.value = true
    return
  }
  artworkLoaded.value = true
}

const playerClasses = computed(() => ({
  [`source-${props.source}`]: true,
  'track-layout': trackLayout.value
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

/* Over the cover's top-right corner: the card's side padding plus the cover's
   own top inset (.player-content's), then the same again inside the cover. */
.player-expand {
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
   toggle, orthogonal to desktop-only/mobile-only above, kept apart from them
   because it names what the slot draws rather than where.
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
  gap: var(--space-01);
  width: 100%;
}

/* === Per-source transport layout ===
   The #controls slot has no default: all three browser sources fill it with
   their own row, so its layout has to live somewhere.
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

/* .vertical-layout is the desktop sidebar's row (the layout toggle above hides
   it in the mobile mini-bar). */
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

/* Podcast: the row is three columns — speed, transport, nothing — so the
   transport stays centred on the row whatever the speed chip measures. It was
   pinned with `position: absolute; left: 0` instead, which holds only while the
   chip is narrower than the gap left of the transport: giving it a rim widened
   it to 66px and it landed 18px on top of the -15s button's target. */
.source-podcast .controls {
  display: grid;
  /* minmax(0, 1fr), not 1fr: a plain fr track keeps a min-content floor, so a
     speed chip wider than its share grows the track and walks the transport
     off-centre — 18px, measured, with the chip at its first rim size. At zero
     the two side tracks are always equal, so the transport is centred whatever
     the chip measures, and a chip that outgrew its track would spill over it
     rather than move the row. */
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
}

/* Both items name their row, the transport's included: auto-placement fills
   row by row, so a chip asking for column 1 after the transport has taken
   column 2 lands on a second row instead of beside it. Measured, before this
   was explicit. */
.source-podcast :deep(.playback-controls) {
  grid-area: 1 / 2;
}

:deep(.speed-selector) {
  display: flex;
  align-items: center;
  grid-area: 1 / 1;
  justify-self: start;
}

:deep(.speed-selector .dropdown) {
  width: auto;
  flex: none;
}

/* Every rule below restyles Dropdown's internals through `:deep()`, so each one
   competes with a rule the primitive writes about itself — and a scoped rule
   carries its scope attribute in the same specificity class a `:deep()` prefix
   occupies, so two classes here tie with two classes there and the stylesheet
   emitted last takes it. Dropdown is emitted last, measured in the built CSS,
   and the ties cost two visible bugs: the label drew at `minimal`'s 50% white
   however plainly the rule here asked for full contrast, and
   `--minimal:focus { box-shadow: none }` erased the rim on click, so the pill
   lost its outline the first time the menu was opened and stayed bare until
   something else took the focus. Each rule therefore goes one class deeper than
   the Dropdown rule it must outrank; `.dropdown`, the primitive's own wrapper,
   is what buys the step, and the four keep their order among themselves by
   specificity rather than by position in this file. */

/* The speed reads as a control rather than as loose text: a pill rim around it.
   The `minimal` variant clears the trigger's own box-shadow, so the rim is put
   back the same way the base variant draws it, inset, which keeps the chip's
   box out of the row's arithmetic. The rim colour is a background token used as
   a stroke on purpose — the player has no border token for a dark ground. The
   padding is the chip's whole size: it is a label with a rim, not a button, and
   it sits next to a transport it must not compete with. */
:deep(.speed-selector .dropdown .dropdown-trigger) {
  padding: var(--space-01) 0;
  border-radius: var(--radius-full);
  box-shadow: inset 0 0 0 1px var(--color-background-neutral-50);
}

/* Full contrast, not the `minimal` variant's 50% white: the chip states the
   speed currently playing, which is a value and not a placeholder. */
:deep(.speed-selector .dropdown .dropdown-trigger .dropdown-label) {
  color: var(--color-text-contrast);
}

/* Open: the chip fills, so it reads as the thing the menu belongs to. The rim
   goes with the fill — it is invisible on it — and the label has to flip with
   it, `minimal` drawing it at 50% white, which would vanish. */
:deep(.speed-selector .dropdown .dropdown-trigger.is-open) {
  background: var(--color-background-neutral);
  box-shadow: none;
}

:deep(.speed-selector .dropdown .dropdown-trigger.is-open .dropdown-label) {
  color: var(--color-text);
}


/* Music library: one transport row (shuffle … prev·play·next … like), with the
   trio centred inside it. */
:deep(.track-controls) {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  width: 100%;
}

:deep(.track-transport-main) {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-01);
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

  .audio-player.track-layout {
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
    padding-left: var(--space-02);
  }

  .player-bottom {
    flex-shrink: 0;
    padding: 0;
    gap: 0;
  }

  /* The row's last item, after the transport (.player-content shrinks to
     leave it room). */
  .player-expand {
    position: static;
    flex-shrink: 0;
  }

  .player-content {
    min-width: 0;
  }

  /* Docked bar: every text line is exactly one line, cut by a right-edge fade
     rather than an ellipsis. Applies to the slotted title/subtitle pair
     (fixes scoped CSS limitation) and to the carousel's own cells alike.
     `white-space: nowrap` is the only clamp available here: .horizontal-layout's
     `display: block !important` below overrides the -webkit-box the base rules
     declare, and -webkit-line-clamp does nothing outside -webkit-box — which is
     how the subtitle silently grew a second line, taking the bar's height with
     it (the row is `height: auto` + `flex-wrap: wrap`). */
  .audio-player :deep(.player-title),
  .audio-player :deep(.player-subtitle) {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: clip;
    -webkit-line-clamp: unset;
    -webkit-box-orient: unset;
    display: block;
  }

  /* The fade is per line here. The swipe carousel's cells must NOT get one:
     .player-info-carousel above already masks the same edge for the whole
     strip, and a second mask on the text would compound it into a harder cut. */
  .player-info-inner :deep(.player-title),
  .player-info-inner :deep(.player-subtitle) {
    -webkit-mask-image: linear-gradient(to right, #000 calc(100% - var(--space-05)), transparent 100%);
    mask-image: linear-gradient(to right, #000 calc(100% - var(--space-05)), transparent 100%);
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
  .audio-player.track-layout .player-content :deep(.progress-bar) {
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
  .audio-player.track-layout .player-content :deep(.progress-bar) .time {
    display: none;
  }

  .audio-player.source-podcast .player-content :deep(.progress-container),
  .audio-player.track-layout .player-content :deep(.progress-container) {
    height: 100%;
    border-radius: 0;
  }

  .audio-player.source-podcast .player-content :deep(.progress),
  .audio-player.track-layout .player-content :deep(.progress) {
    border-radius: 0;
  }

  .controls {
    gap: var(--space-02);
    justify-content: center;
  }

  /* Compact mini-bar: the primary control is too large next to the 48px artwork
     thumbnail in this tight single row. The one place a tier token is bent
     rather than picked — this row is sized against the thumbnail beside it, not
     against the transport scale. The desktop sidebar keeps its tier.

     It has to hang off .controls, not .playback-controls. Scoped CSS stamps this
     file's id on the last compound of a selector, and .playback-controls is slot
     content — it carries the *source's* id, never this component's, so a rule
     ending on it silently matches nothing. The predecessor of this rule did
     exactly that for the whole life of the mini-bar: it read `20px`, never
     applied, and the row quietly took SvgIcon's native mobile medium of 24px
     instead. 28 is a deliberate step up from that accidental 24.
     .controls is this component's own element, so it carries the id. */
  .audio-player .controls {
    --transport-primary: 28px;
  }

  /* Compact mobile player keeps only play/pause; shuffle/prev/next/like are
     desktop-only — the swipe gesture covers prev/next on mobile instead. */
  .audio-player.track-layout :deep(.track-transport-extra) {
    display: none;
  }

  /* Radio's docked mini-bar renders only the compact ghost icon button
     (.horizontal-layout) — push it to the row's edge. */
  :deep(.radio-controls) {
    justify-content: flex-end;
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
