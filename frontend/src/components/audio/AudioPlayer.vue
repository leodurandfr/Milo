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

          <div class="player-info">
            <!-- Track text crossfade+slides only on a swipe (next/prev); every other
                 change — radio's station→metadata reveal, autoplay, source switch —
                 swaps instantly (transition name resolves to the no-op 'track-none').
                 Direction follows the finger, set in onTouchEnd. -->
            <Transition :name="trackTransitionName" mode="out-in" @after-enter="onTrackEnter">
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
import { computed, ref, watch } from 'vue'
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

// Mobile-only horizontal swipe on the fixed docked player. touchmove is left
// non-passive so a confirmed horizontal drag can preventDefault without fighting
// page scroll; a plain tap never crosses the distance threshold.
const SWIPE_THRESHOLD_PX = 40
let touchStartX = 0
let touchStartY = 0
let touchTracking = false

// Track-text transition is armed only for the brief window around a swipe, then
// resolves back to the no-op 'track-none'. swipeDir drives the slide direction so
// the text follows the finger. Cleared on the transition's after-enter, with a
// fallback timer for swipes that produce no track change (e.g. end of queue).
const swiping = ref(false)
const swipeDir = ref('left')
let swipeResetHandle = null
const trackTransitionName = computed(() => {
  if (!swiping.value) return 'track-none'
  return swipeDir.value === 'right' ? 'track-right' : 'track-left'
})

function onTrackEnter() {
  swiping.value = false
  if (swipeResetHandle) timer.clear(swipeResetHandle)
}

function onTouchStart(e) {
  if (!isMobile.value || !props.swipeEnabled) return
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
  if (Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy)) {
    e.preventDefault()
  }
}

function onTouchEnd(e) {
  if (!touchTracking) return
  touchTracking = false
  const touch = e.changedTouches[0]
  const dx = touch.clientX - touchStartX
  const dy = touch.clientY - touchStartY
  if (Math.abs(dx) > SWIPE_THRESHOLD_PX && Math.abs(dx) > Math.abs(dy) * 1.5) {
    // Finger right → next, finger left → prev; the slide follows the finger.
    swipeDir.value = dx > 0 ? 'right' : 'left'
    swiping.value = true
    if (swipeResetHandle) timer.clear(swipeResetHandle)
    swipeResetHandle = timer.setTimeout(() => { swiping.value = false }, 1200)
    emit(dx > 0 ? 'swipe-next' : 'swipe-prev')
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

/* Direction-aware track-text slide (swipe only — see trackTransitionName).
   The content follows the finger: swipe-right slides the row rightward (old
   exits right, new enters from the left), swipe-left the mirror. 'track-none'
   has no rules, so every non-swipe change swaps instantly. */
.track-right-enter-active,
.track-right-leave-active,
.track-left-enter-active,
.track-left-leave-active {
  transition: transform 0.2s ease-out, opacity 0.2s ease-out;
}

.track-right-enter-from {
  opacity: 0;
  transform: translateX(-16px);
}

.track-right-leave-to {
  opacity: 0;
  transform: translateX(16px);
}

.track-left-enter-from {
  opacity: 0;
  transform: translateX(16px);
}

.track-left-leave-to {
  opacity: 0;
  transform: translateX(-16px);
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
    bottom: calc(env(safe-area-inset-bottom, 0px) + var(--space-08));

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
    transition: width 0.3s ease-out;
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

  /* Track artwork: on top of the station. It reveals from the station's exact
     position — starts fully overlapping it (translateX(-24px)) and transparent,
     then slides to its offset-right resting spot and fades in as the image
     loads (.loaded), a coordinated reveal synced with the frame widening and the
     text shifting right. left:24px (not right:0) keeps its anchor stable while
     the frame animates its width. */
  .player-artwork-frame.has-badge .player-artwork {
    position: absolute;
    top: 0;
    left: 24px;
    width: 48px;
    height: 48px;
    z-index: 1;
    opacity: 0;
    transform: translateX(-24px);
    transition: transform 0.3s ease-out, opacity 0.3s ease-out;
  }

  .player-artwork-frame.has-badge .player-artwork.loaded {
    opacity: 1;
    transform: translateX(0);
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

/* Vue Transition: Mobile */
@media (max-aspect-ratio: 4/3) {

  .audio-player-enter-active,
  .audio-player-leave-active {
    position: fixed;
    bottom: calc(env(safe-area-inset-bottom, 0px) + var(--space-08));
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
