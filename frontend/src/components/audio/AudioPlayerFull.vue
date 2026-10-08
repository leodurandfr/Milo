<!-- AudioPlayerFull.vue - Full-screen player, for every source that has one: a
     shell — the cover and its backdrop, the top row (PlayerTopRow) — around
     PlayerBody, the source bar, lines, bar and transport it shares with the
     playing bar, centred here. What it offers is read from the state, never
     from its caller. What is not a command is the source's to put in the top
     row: `#top-start` (CD's tracklist) and `#top-end` (CD's eject, Bluetooth's
     disconnect) — or at the end of the transport, `#transport-end` (radio's
     favorite). The cover — and on the phone a pull down — is the way back to
     the navigation this player was expanded out of; the album (the title) and
     artist links are emitted, never followed. -->
<template>
  <div class="connect-player" :class="{ 'connect-player--backdrop': isDark }" :style="sheetStyle"
    @touchstart="pull.onTouchStart" @touchmove="pull.onTouchMove" @touchend="pull.onTouchEnd"
    @touchcancel="pull.onTouchEnd" @click.capture="pull.onClickCapture">
    <!-- The dark theme's ground: the cover again, blurred edge to edge and
         dimmed under a veil. Drawn only in that theme, so the light one does
         not pay for a full-screen blur it would not show. -->
    <div v-if="isDark" class="player-backdrop" aria-hidden="true">
      <img v-if="shownArtwork" :src="shownArtwork" alt="" class="player-backdrop-image" />
      <div v-else-if="stationAvatarSvg" v-html="stationAvatarSvg" class="player-backdrop-image" />
    </div>

    <!-- source-motion: what the source swap rises, leaving .connect-player (the
         clipping box and the panel's fill) welded to the screen edges. -->
    <div class="now-playing source-motion">
      <!-- Left side: Cover image with CSS staggering -->
      <div class="artwork-section stagger-1" :class="{ 'art-collapsed': hideContent }">
        <div class="artwork-container">
          <!-- Background blur -->
          <div class="artwork-blur"
            :style="{ backgroundImage: haloUrl ? `url(${haloUrl})` : 'none' }">
          </div>

          <!-- Main cover art. The fallback is not decoration: Bluetooth can
               never carry a cover over the link (AVRCP puts images behind an
               OBEX channel BlueZ gives no client for), so when the lookup that
               replaces it finds nothing, this is what the slot shows instead of
               a blank square reading as a failed image. Which of the two it is
               comes from the shared helper, not from here — the playing bar
               resolves it the same way, and a fallback chosen per view is how
               two views came to disagree in the first place. A station with no
               logo is the one exception to a fallback: the generated avatar
               is its identity.
               Where there is a navigation behind the player, the cover is the
               way back to it. -->
          <div class="artwork"
            :class="{ 'artwork-pending': artworkPending, 'is-link': !!navigation }"
            :role="navigation ? 'button' : undefined" :tabindex="navigation ? 0 : undefined"
            :aria-label="navigation ? t('common.back') : undefined"
            @click="navigation?.back()" @keydown.enter.space.prevent="navigation?.back()">
            <img v-if="shownArtwork" :src="shownArtwork"
              alt="" />
            <div v-else-if="stationAvatarSvg" v-html="stationAvatarSvg" class="artwork-avatar" />
            <img v-else-if="fallback.kind === 'image'" :src="fallback.src"
              alt="" class="artwork-placeholder" />
            <div v-else class="artwork-fallback">
              <AppIcon :name="source" :size="112" />
            </div>

            <!-- Held over the outgoing cover until the incoming one is decoded,
                 so a track change never flashes the fallback glyph between two
                 covers. -->
            <Transition name="artwork-veil">
              <div v-if="artworkPending" class="artwork-veil">
                <LoadingSpinner :size="48" />
              </div>
            </Transition>
          </div>

          <!-- Decodes the incoming cover off-screen; @load is what promotes it —
               or rejects it, the size rule living in the composable rather than
               in this view. -->
          <img v-if="preloadArtwork" :src="preloadArtwork" alt="" class="artwork-preload"
            @load="settleFromLoad" @error="settleFromError" />
        </div>
      </div>

      <!-- Right side: Info and controls with CSS staggering. -->
      <div class="content-section stagger-2" :class="{ 'has-top-row': hasTopRow }">
        <!-- What the source puts in the top row, drawn only when it puts
             something there. Start: CD's tracklist. End: CD's eject,
             Bluetooth's disconnect. It takes no room: its buttons sit on the
             source bar's line, so the bar stays where it is without them. -->
        <PlayerTopRow v-if="hasTopRow" class="player-topbar">
          <template #start>
            <slot name="top-start" />
          </template>
          <template #end>
            <slot name="top-end" />
          </template>
        </PlayerTopRow>

        <!-- Content: player info or replacement (e.g., CD tracklist) -->
        <Transition name="player-swap" mode="out-in">
          <!-- The lines, the bar and the transport: the body both players
               share, centred here. -->
          <PlayerBody v-if="!hideContent" key="player-info" class="player-info" :source="source"
            surface="full" @title-click="emit('title-click')" @secondary-click="emit('secondary-click', $event)">
            <template v-if="$slots['transport-end']" #transport-end="slotProps">
              <slot name="transport-end" v-bind="slotProps" />
            </template>
          </PlayerBody>
          <div v-else key="content-replace" class="content-replace">
            <slot name="content-replace" />
          </div>
        </Transition>
      </div>
    </div>

    <!-- No error branch here on purpose: a failed service is refused a rich
         display by useRichDisplay before it looks at the source at all, so
         this player is never mounted with a message to show. The status card
         draws it. -->
  </div>
</template>

<script setup>
import { computed, inject, useSlots, watch } from 'vue';
import { useTheme } from '@/composables/useTheme';
import { useIsMobile } from '@/composables/useIsMobile';
import { usePullToDismiss } from '@/composables/usePullToDismiss';
import { PLAYER_NAVIGATION } from '@/composables/usePlayerExpansion';
import { useI18n } from '@/services/i18n';
import { useArtworkTransition } from '@/composables/useArtworkTransition';
import { usePlayerState } from '@/composables/usePlayerState';

import PlayerTopRow from './PlayerTopRow.vue';
import PlayerBody from './PlayerBody.vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';

const props = defineProps({
  source: {
    type: String,
    required: true
  },
  hideContent: {
    type: Boolean,
    default: false
  }
});

// The album and artist behind the title and the artist line, emitted only when
// the navigation around the player says there is one. The player does not know
// how to open either: its source does, in its own browser.
const emit = defineEmits(['title-click', 'secondary-click']);

const { t } = useI18n();
const { isDark } = useTheme();
const slots = useSlots();
const hasTopRow = computed(() => !!(slots['top-start'] || slots['top-end']));

// The cover this player draws: from this player's one reading of its state,
// which the body under it takes too (usePlayerState).
const {
  artwork, fallback, stationAvatarSvg, artworkAnnounced, trackKey
} = usePlayerState(props.source).metadata;

// === BACK ===
// The navigation this player was expanded out of, when there is one: the cover
// is the way back to it (the album and artist links are the body's). Injected
// rather than passed, so the props stay the source's and hideContent's. Without
// it — the only view of a source with nothing to browse — the cover is inert.
const navigation = inject(PLAYER_NAVIGATION, null);

// On the phone the player is a sheet over that navigation: pulled down, it
// follows the finger, its top corners rounding as it goes, and slides out or
// back. The navigation draws itself under it, behind a veil that clears as the
// pull goes on (BrowserSourceViews), from what is reported here.
const { isMobile } = useIsMobile();
const pull = usePullToDismiss({
  enabled: computed(() => !!navigation && isMobile.value),
  onDismiss: () => navigation.back()
});

// Fully rounded within the first pixels of the pull: the corners are what say
// the player has come loose from the screen.
const ROUNDED_AT_PX = 32;

const sheetStyle = computed(() => {
  if (!pull.active.value) return null;
  const timing = pull.timing.value;
  const radius = `calc(var(--radius-06) * ${Math.min(1, pull.offset.value / ROUNDED_AT_PX)})`;
  return {
    transform: `translateY(${pull.offset.value}px)`,
    borderTopLeftRadius: radius,
    borderTopRightRadius: radius,
    transition: timing ? `transform ${timing}, border-radius ${timing}` : 'none'
  };
});

watch(
  () => (pull.active.value ? { progress: pull.progress.value, timing: pull.timing.value } : null),
  (state) => navigation?.pull(state)
);

// === ARTWORK TRANSITION ===
// The halo behind the cover. In the dark theme a station's avatar gets one too,
// as its backdrop does: CSS needs a URL rather than markup, and under that much
// blur the avatar's font cannot be told from a fallback.
const haloUrl = computed(() => {
  if (shownArtwork.value) return shownArtwork.value;
  if (isDark.value && stationAvatarSvg.value) {
    return `data:image/svg+xml;utf8,${encodeURIComponent(stationAvatarSvg.value)}`;
  }
  return null;
});

// Holding the outgoing cover under a veil while the next one decodes.
const { shownArtwork, preloadArtwork, artworkPending, settleFromLoad, settleFromError } =
  useArtworkTransition(artwork, trackKey, artworkAnnounced);
</script>

<style scoped>
/* === SIMPLE AND NATURAL STAGGERING === */

/* Initial states: all elements are hidden */
.stagger-1,
.stagger-2 {
  opacity: 0;
  transform: translateY(var(--space-07));
}

/* Animation with two separate effects */
.connect-player .stagger-1,
.connect-player .stagger-2 {
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

/* Simple staggered delays */
.connect-player .stagger-1 { animation-delay: 0ms; }
.connect-player .stagger-2 { animation-delay: 0ms; }

/* Spring animation for transform */
@keyframes stagger-transform {
  to {
    transform: none;
  }
}

/* Ease animation for opacity */
@keyframes stagger-opacity {
  to {
    opacity: 1;
  }
}

/* === COMPONENT STYLES === */
/* The clipping box, and therefore where the panel's fill lives: it is welded to
   the slot's edges, so its overflow cut and the fill's edge both sit exactly on
   the screen edge, where neither can be seen. The fill used to sit on
   .now-playing, which carries the swap's rise — so a source change lifted it and
   opened a strip of page background along the bottom. See .source-motion in
   design-system.css. */
.connect-player {
  width: 100%;
  height: 100%;
  overflow: hidden;
  position: relative;
  background: var(--color-surface);
}

/* === DARK THEME BACKDROP ===
   Set from useTheme().isDark, never from a theme selector: the blurred cover
   fills the panel, under a veil that keeps the text legible over any cover.
   `isolation` makes the panel the stacking context the backdrop's negative
   z-index sinks into, under the content however it is layered. */
.connect-player--backdrop {
  background: var(--color-backdrop);
  isolation: isolate;
}

.player-backdrop {
  position: absolute;
  inset: 0;
  z-index: -1;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  pointer-events: none;
}

.player-backdrop-image {
  flex-shrink: 0;
  min-width: 150%;
  min-height: 150%;
  object-fit: cover;
  transform: scale(1.5) translateZ(0);
  filter: blur(var(--blur-05)) saturate(1.5);
  opacity: 0.16;
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
}

.player-backdrop-image :deep(svg) {
  display: block;
  width: 100%;
  height: 100%;
}

/* One veil rather than a brightness filter: no second filter pass over a
   full-screen blur. */
.player-backdrop::after {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--color-backdrop-veil);
}

/* The halo centred on the cover spreads over the whole panel here, faint,
   where the light theme keeps it to a glow at the cover's edge. */
.connect-player--backdrop .artwork-blur {
  top: 50%;
  left: 50%;
  right: auto;
  bottom: auto;
  width: 116vw;
  height: 116vw;
  transform: translate(-50%, -50%) translateZ(0);
  filter: blur(var(--blur-05)) saturate(1.5);
  opacity: 0.12;
}

.now-playing {
  display: flex;
  height: 100%;
  padding: var(--space-05);
  gap: var(--space-06);
}

/* Artwork */
.artwork-section {
  flex-shrink: 0;
  aspect-ratio: 1;
  order: 1;
  z-index: 2;
  pointer-events: none;
}

/* Content Section */
.content-section {
  /* The source bar's centre line: the body's top padding and half the bar's
     24px row (PlayerBody, SourceBar). */
  --source-line: calc(var(--space-06) + 12px);
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  order: 2;
  z-index: 1;
}

/* The top row takes no height: its buttons are centred on the source bar's
   line, over the body, so the bar sits where it does without them. */
.player-topbar {
  position: relative;
  z-index: 3;
  height: 0;
  top: var(--source-line);
}

/* What replaces the body (CD's tracklist) starts as far below the buttons'
   line as the line is from the top. */
.has-top-row .content-replace {
  padding-top: calc(2 * var(--source-line));
}

/* Content replacement (e.g., CD tracklist) */
.content-replace {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* === PLAYER SWAP TRANSITION === */
/* Leave: quick fade out */
.player-swap-leave-active {
  transition: opacity var(--transition-fast-leave);
}

.player-swap-leave-to {
  opacity: 0;
}

/* Enter: no parent animation — PlayerBody staggers its own parts. */

/* Container for the two stacked cover arts */
.artwork-container {
  position: relative;
  width: 100%;
  height: 100%;
}

/* Background cover art with blur */
.artwork-blur {
  position: absolute;
  top: -20px;
  left: -20px;
  right: -20px;
  bottom: -20px;
  z-index: 2;
  background-size: cover;
  background-position: center;
  filter: blur(var(--blur-04)) saturate(1.5);
  transform: scale(1.1) translateZ(0);
  opacity: .25;
  will-change: transform;
  -webkit-backface-visibility: hidden;
  backface-visibility: hidden;
  contain: strict;
}

/* Main cover art with border radius */
.artwork {
  position: relative;
  z-index: 3;
  width: 100%;
  height: 100%;
  border-radius: var(--radius-04);
  overflow: hidden;
  box-shadow: var(--shadow-artwork);
  pointer-events: none;
}

.artwork img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* Inline-SVG station avatar fills its wrapper like the real artwork. */
.artwork-avatar,
.artwork-avatar :deep(svg) {
  display: block;
  width: 100%;
  height: 100%;
}

.artwork-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-fill-faint);
  color: var(--color-text-tertiary);
}

/* Bundled placeholder. It is transparent by design — the same file sits on
   cards of two different colours elsewhere — so it needs the ground the glyph
   fallback gets, or the blurred backdrop shows through it. */
.artwork-placeholder {
  background: var(--color-fill-faint);
}

/* Held cover while the next one decodes. The scale is not decoration: a blur
   samples past the element's edge, and without it the rounded corners show a
   translucent halo against the player background. */
.artwork > img,
.artwork > .artwork-avatar,
.artwork > .artwork-fallback {
  transition:
    filter var(--transition-medium),
    transform var(--transition-medium);
}

.artwork-pending > img,
.artwork-pending > .artwork-avatar,
.artwork-pending > .artwork-fallback {
  filter: blur(var(--blur-02));
  transform: scale(1.06);
}

.artwork-veil {
  position: absolute;
  inset: 0;
  z-index: 4;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-image-veil);
  /* The spinner's SVG paints with currentColor, and it sits on a darkened cover
     — not on the player background — so it takes the contrast token rather than
     inheriting the page text colour. Full contrast, not -50: the blades already
     animate down to 0.16 opacity, and halving that again loses them over a
     bright cover. */
  color: var(--color-text-on-contrast);
}

.artwork-veil-enter-active,
.artwork-veil-leave-active {
  transition: opacity var(--transition-medium);
}

.artwork-veil-enter-from,
.artwork-veil-leave-to {
  opacity: 0;
}

/* Decodes the incoming cover out of sight. Deliberately not `display: none`,
   which lets a browser skip the fetch — and the load event it fires is the
   whole mechanism. */
.artwork-preload {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

/* The way back to the navigation. */
.artwork.is-link {
  pointer-events: auto;
  cursor: pointer;
}

@media (max-aspect-ratio: 4/3) {
  .now-playing {
    padding-left: var(--space-05);
    padding-right: var(--space-05);
    padding-top: max(var(--space-05), env(safe-area-inset-top, 0px));
    padding-bottom: max(var(--space-06), env(safe-area-inset-bottom, 0px));

    flex-direction: column;
    gap: 0;
  }

  .content-replace {
    margin-bottom: calc(-1 * max(var(--space-06), env(safe-area-inset-bottom, 0px)));
  }

  /* The column dissolves into the page's one column, so the top row can sit
     right under the cover — beside it, as on the kiosk where it heads the
     column next to the cover — rather than over it: the source bar is text,
     which a cover would not leave legible. */
  .content-section {
    display: contents;
  }

  .player-topbar {
    order: 2;
  }

  .player-info,
  .content-replace {
    order: 3;
  }

  /* The tracklist takes the cover's place: the cover rises out by its own
     height (the page width less the padding) and fades as it goes, bringing the
     top row under it up to the top of the screen. */
  .artwork-section {
    transition: margin-top 400ms var(--easeInOutCubic);
  }

  .artwork-section.art-collapsed {
    margin-top: calc(-100vw + 2 * var(--space-05));
  }

  /* The fade is the container's, not the section's: the section's entrance
     animation holds its opacity at 1 (fill-mode forwards), which would win over
     any opacity set here. */
  .artwork-container {
    transition: opacity 300ms var(--easeInOutCubic);
  }

  .artwork-section.art-collapsed .artwork-container {
    opacity: 0;
  }

  .artwork-blur {
    transform: scale(1) translateZ(0);
  }
}
</style>
