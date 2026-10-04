<!-- AudioPlayerFull.vue - Full-screen player, for every source that has one.
     What it offers is read from the state, never from its caller: the buttons
     from `controls` (utils/playerControls), the play/pause glyph from the
     session's phase, the bar from the session's duration and position anchor.
     What is not a command — a favorite, a star, a like — is the source's to put
     in `#actions`; the album and artist links are emitted, never followed. -->
<template>
  <div class="connect-player">
    <!-- source-motion: what the source swap rises, leaving .connect-player (the
         clipping box and the panel's fill) welded to the screen edges. -->
    <div class="now-playing source-motion">
      <!-- Left side: Cover image with CSS staggering -->
      <div class="artwork-section stagger-1" :class="{ 'art-collapsed': hideContent }">
        <div class="artwork-container">
          <!-- Background blur -->
          <div class="artwork-blur"
            :style="{ backgroundImage: shownArtwork ? `url(${shownArtwork})` : 'none' }">
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
               is its identity. -->
          <div v-press="albumLink" class="artwork"
            :class="{ 'artwork-pending': artworkPending, 'is-link': albumLink }" @click="onArtworkClick">
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
      <div class="content-section stagger-2">
        <!-- Back to the navigation this player was expanded out of: drawn only
             when one is provided (BrowserSourceViews), so the sources whose only
             view this is draw no button at all. -->
        <div v-if="navigation" class="player-back">
          <IconButton icon="caretLeft" :variant="isMobile ? 'on-grey' : 'background-strong'" size="medium"
            :aria-label="t('common.back')" @click="navigation.back" />
        </div>

        <!-- Action buttons (used by CD for eject/tracklist) -->
        <slot name="action-buttons" />

        <!-- Content: player info or replacement (e.g., CD tracklist) -->
        <Transition name="player-swap" mode="out-in">
          <div v-if="!hideContent" key="player-info" class="player-info">
            <div class="track-info" :class="{ 'no-controls': !hasTransport }">
              <!-- What the title belongs to: the station a recognized song
                   plays on (its cover has replaced the station's), the show an
                   episode is part of. -->
              <div v-if="persistentMetadata.kicker" class="track-kicker">
                <LazyImage v-if="persistentMetadata.kickerIcon !== null" class="track-kicker-icon"
                  :src="persistentMetadata.kickerIcon" :fallback-name="persistentMetadata.kicker" alt="" />
                <span class="track-kicker-label text-mono-medium">{{ persistentMetadata.kicker }}</span>
              </div>
              <h1 class="track-title heading-1">{{ persistentMetadata.title || t('status.unknownTitle') }}</h1>
              <p v-if="secondaryLine" v-press="artistLink" class="track-artist heading-2"
                :class="{ 'is-link': artistLink }" @click="onSecondaryClick">{{ secondaryLine }}</p>
            </div>
            <div class="controls-section">
              <!-- The toggles the source lists, then what the source adds that
                   is not a command (a favorite, a like). The sources with
                   nothing to browse have neither, so the row is drawn only when
                   filled. -->
              <div v-if="optionControls.length || $slots.actions" class="options-row"
                :class="isMobile ? 'transport-scale--phone' : 'transport-scale'">
                <template v-for="control in optionControls" :key="control.id">
                  <div v-if="control.id === 'speed'" class="speed-selector">
                    <Dropdown :model-value="speedValue(control)" :options="speedOptions" size="small"
                      variant="background-neutral" :disabled="!control.enabled" @change="setSpeed" />
                  </div>
                  <IconButton v-else :icon="control.icon" variant="ghost" size="small"
                    class="option-button transport-secondary-round"
                    :color="control.active ? 'var(--color-text)' : 'var(--color-text-light)'"
                    :aria-label="optionLabel(control)" :aria-pressed="control.active"
                    :disabled="!control.enabled" @click="sendSourceCommand(control.command, control.params)" />
                </template>
                <slot name="actions" />
              </div>
              <!-- With a transport the row is always reserved, so the centered
                   track-info does not shift when the bar mounts on play; a
                   receiver without one only takes it while it has a bar. The
                   bar hides itself until the source has a duration and a
                   position. -->
              <div v-if="hasTransport || hasProgress" class="progress-wrapper">
                <ProgressBar :currentPosition="currentPosition" :duration="duration"
                  :progressPercentage="progressPercentage" :isReady="isPositionInitialized"
                  :interactive="canSeek" :loading="phase === 'loading'" animateIn @seek="seekTo" />
              </div>
              <div v-if="hasTransport" class="controls-wrapper">
                <div class="controls" :class="isMobile ? 'transport-scale--phone' : 'transport-scale'">
                  <IconButton v-for="control in transportControls" :key="control.id" :icon="control.icon"
                    variant="ghost" :size="control.id === 'main' ? 'medium' : 'small'"
                    :color="control.id === 'main' ? 'var(--color-text)' : 'var(--color-text-light)'"
                    class="control-button" :class="transportClass(control)"
                    :loading="control.id === 'main' && isBuffering" :disabled="!control.enabled"
                    @click="pressTransport(control)" />
                </div>
              </div>
              <div v-else class="source-bar">
                <AppIcon :name="source" :size="40" />
                <span class="source-bar-name heading-4">{{ sourceBarName }}</span>
              </div>
            </div>
          </div>
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
import { computed, inject, ref, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { usePodcastStore } from '@/stores/podcastStore';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { useIsMobile } from '@/composables/useIsMobile';
import { PLAYER_NAVIGATION } from '@/composables/usePlayerExpansion';
import { useI18n } from '@/services/i18n';
import { AUDIO_SOURCE_LABEL_KEYS } from '@/constants/audioSources';
import { formatDeviceNames } from '@/utils/deviceName';
import { getFaviconUrl } from '@/utils/faviconUrl';
import { generateStationAvatarSvg } from '@/utils/stationAvatar';
import { playerControls } from '@/utils/playerControls';

import { useArtworkTransition } from '@/composables/useArtworkTransition';
import { useDelayedFlag } from '@/composables/useDelayedFlag';
import { nowPlayingArtwork, nowPlayingArtworkPending, artworkFallback } from '@/utils/nowPlayingArtwork';
import { nowPlayingOf, nowPlayingSnapshot } from '@/utils/nowPlayingMetadata';

import ProgressBar from './ProgressBar.vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import IconButton from '@/components/ui/IconButton.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
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

// The album and artist behind the cover and the artist line, emitted only when
// the navigation around the player says there is one. The player does not know
// how to open either: its source does, in its own browser.
const emit = defineEmits(['artwork-click', 'secondary-click']);

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const podcastStore = usePodcastStore();
const { isMobile } = useIsMobile();
const {
  currentPosition, duration, progressPercentage, seekTo, skip, isPositionInitialized
} = useSourceProgress(props.source);

// This source's slice of the state: another source's session, controls and
// phase are not ours (the player is still on screen while it leaves).
const isSelected = computed(() => unifiedStore.systemState.source === props.source);
const session = computed(() => (isSelected.value ? unifiedStore.systemState.session : null));
const controls = computed(() => (isSelected.value ? unifiedStore.systemState.controls : []));
const details = computed(() => (isSelected.value ? unifiedStore.systemState.details : null));
const phase = computed(() => session.value?.phase ?? null);

// === CONTROLS ===
// Which buttons are drawn is read from a settled state: while switching away
// `controls` is empty, and the player leaving must not trade its transport for
// a source bar mid-fade. Whether each one is enabled, and a toggle's state, is
// the live list's — a button the source would refuse now shows disabled.
const settled = ref({ controls: [], details: null });
watch(
  () => {
    const { switching, service } = unifiedStore.systemState;
    return isSelected.value && !switching && service === 'running'
      ? { controls: controls.value, details: details.value }
      : null;
  },
  (state) => {
    if (state) settled.value = state;
  },
  { immediate: true }
);

const liveControls = computed(() => playerControls({ controls: controls.value, details: details.value, phase: phase.value }));
const shownControls = computed(() => {
  const live = new Map(liveControls.value.map(control => [control.id, control]));
  return playerControls({ ...settled.value, phase: phase.value })
    .map(control => live.get(control.id) ?? { ...control, enabled: false });
});
const transportControls = computed(() => shownControls.value.filter(control => control.row === 'transport'));
const optionControls = computed(() => shownControls.value.filter(control => control.row === 'options'));
// The receivers (AirPlay, Qobuz) list no main command and draw a source bar instead.
const hasTransport = computed(() => transportControls.value.length > 0);
const canSeek = computed(() => liveControls.value.some(control => control.id === 'seek'));
const hasProgress = computed(() => duration.value > 0 && isPositionInitialized.value);

const isBuffering = useDelayedFlag(() => phase.value === 'loading');

// sendCommand swallows + logs errors via the store. A command the source does
// not list now would be refused, so it is not sent.
function sendSourceCommand(command, data) {
  if (!controls.value.includes(command)) return;
  unifiedStore.sendCommand(props.source, command, data);
}

function pressTransport(control) {
  // A relative move goes through the playhead, which shows a burst's sum at once.
  if (control.command === 'skip') {
    if (controls.value.includes('skip')) skip(control.seconds);
    return;
  }
  sendSourceCommand(control.command);
}

// The flanking glyphs fill their box in both axes or not, which sets their
// rung (design-system.css § transport roles).
function transportClass(control) {
  if (control.id === 'main') return 'control-button--primary transport-primary';
  return control.command === 'skip' ? 'transport-secondary-round' : 'transport-secondary';
}

const REPEAT_LABEL_KEYS = {
  off: 'spotify.repeatOff',
  context: 'spotify.repeatContext',
  track: 'spotify.repeatTrack',
};

function optionLabel(control) {
  if (control.id === 'repeat') return t(REPEAT_LABEL_KEYS[control.mode]);
  return t('spotify.shuffle');
}

// The speeds are the backend's list, fetched whenever a source starts offering
// the control; the speed in force is the one its details publish.
const speedOptions = computed(() =>
  podcastStore.playbackSpeeds.map(speed => ({ label: `${speed}x`, value: String(speed) }))
);
watch(
  () => optionControls.value.some(control => control.id === 'speed'),
  (offered) => {
    if (offered) podcastStore.loadPlaybackSpeeds();
  },
  { immediate: true }
);

function speedValue(control) {
  return control.value === null ? null : String(control.value);
}

function setSpeed(value) {
  sendSourceCommand('set_speed', { speed: parseFloat(value) });
}

// === BACK AND LINKS ===
// The navigation this player was expanded out of, when there is one: the way
// back to it, and whether the cover and the artist line have a page to open
// there. Injected rather than passed, so the props stay the source's and
// hideContent's. Without it — the only view of a source with nothing to
// browse — the player has neither, and the cover and the line are inert.
const navigation = inject(PLAYER_NAVIGATION, null);
const albumLink = computed(() => !!navigation?.canOpenAlbum.value);
const artistLink = computed(() => !!navigation?.canOpenArtist.value);

function onArtworkClick() {
  if (albumLink.value) emit('artwork-click');
}

function onSecondaryClick() {
  if (artistLink.value) emit('secondary-click');
}

// === METADATA PERSISTENCE ===
// The last record worth naming, so the title and cover do not blank out while
// the player leaves (a source switch clears the record under it) — see the util.
// The lines around the title come from the details and are kept with it.
const lastValidMetadata = ref({
  title: '',
  artist: '',
  artwork: '',
  kicker: null,
  kickerIcon: null,
  avatarName: '',
  secondary: true
});

/**
 * What surrounds the title, by what the details say the record is.
 *
 * A radio's record names a recognized song or, without one, the station
 * itself; the station moves to the kicker only once the song's own cover has
 * replaced the station's, and a station has no artist line to fall back on.
 * An episode's `artist` is its show, which the kicker already says. Anything
 * else is a track, and a track always has an artist line.
 */
function linesOf(current) {
  const kind = current?.kind;
  if (kind === 'radio') {
    const station = current.station;
    const coverIsTrack = !!current.track?.artwork;
    return {
      kicker: coverIsTrack ? station.name : null,
      kickerIcon: coverIsTrack ? getFaviconUrl(station.favicon) : null,
      avatarName: station.name || '',
      secondary: false
    };
  }
  if (kind === 'podcast') {
    return { kicker: current.episode?.podcast?.name || null, kickerIcon: null, avatarName: '', secondary: false };
  }
  return { kicker: null, kickerIcon: null, avatarName: '', secondary: true };
}

watch(
  () => [nowPlayingOf(unifiedStore.systemState, props.source), details.value],
  ([record, current]) => {
    const snapshot = nowPlayingSnapshot(record);
    if (snapshot) lastValidMetadata.value = { ...snapshot, ...linesOf(current) };
  },
  { immediate: true }
);

const persistentMetadata = computed(() => lastValidMetadata.value);

// A track with no artist says so; a station or an episode has no such line.
const secondaryLine = computed(() => {
  const { artist, secondary } = persistentMetadata.value;
  if (secondary) return artist || t('status.unknownArtist');
  return artist && artist !== persistentMetadata.value.kicker ? artist : '';
});

// Who is sending, when the channel says so: AirPlay's and Bluetooth's sender.
// Nothing identifies the sender on the other receiver channels — the Qobuz
// proxy only knows the speaker — so the answer there is the source itself,
// read from the same key the status card and the dock use, never a label a
// backend hardcoded in one language.
const sourceBarName = computed(
  () => formatDeviceNames(session.value?.senders)
    || t(AUDIO_SOURCE_LABEL_KEYS[props.source])
);

// === ARTWORK TRANSITION ===
// Which cover this source shows is decided in one place — see the util.
const targetArtwork = computed(() => nowPlayingArtwork(persistentMetadata.value));
// What the slot shows with no cover at all — a bundled placeholder for the
// sources that ship one, this source's own glyph otherwise.
const fallback = computed(() => artworkFallback(props.source));
// The generated station avatar, and only where the helper says so (radio):
// inline markup rather than an image, so it renders in the app's own font.
const stationAvatarSvg = computed(() => {
  if (fallback.value.kind !== 'avatar') return '';
  const name = persistentMetadata.value.avatarName || persistentMetadata.value.title;
  return name ? generateStationAvatarSvg(name) : '';
});

// Holding the outgoing cover under a veil while the next one decodes.
const trackKey = computed(
  () => `${persistentMetadata.value.title}|${persistentMetadata.value.artist}`
);
// Live, not from the cached copy: a lifted flag must lift the veil at once.
const artworkAnnounced = computed(
  () => isSelected.value && nowPlayingArtworkPending(unifiedStore.systemState)
);
const { shownArtwork, preloadArtwork, artworkPending, settleFromLoad, settleFromError } =
  useArtworkTransition(targetArtwork, trackKey, artworkAnnounced);
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
  background: var(--color-background-neutral);
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
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  order: 2;
  z-index: 1;
}

/* Back to the navigation: the top of the column on the kiosk, where CD keeps
   its own buttons. */
.player-back {
  display: flex;
  flex-shrink: 0;
}

/* Player info (track-info + controls) */
.player-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  min-width: 0;
  min-height: 0;
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

/* Enter: no parent animation — children stagger themselves */

/* Stagger children on mount (initial load + re-enter after swap) */
.player-info > .track-info,
.player-info > .controls-section {
  opacity: 0;
  transform: translateY(var(--space-05));
  animation:
    stagger-transform var(--transition-spring) forwards,
    stagger-opacity 0.4s ease forwards;
}

.player-info > .track-info { animation-delay: 0ms; }
.player-info > .controls-section { animation-delay: 100ms; }

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
  background: var(--color-background-strong);
  color: var(--color-text-light);
}

/* Bundled placeholder. It is transparent by design — the same file sits on
   cards of two different colours elsewhere — so it needs the ground the glyph
   fallback gets, or the blurred backdrop shows through it. */
.artwork-placeholder {
  background: var(--color-background-strong);
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
  background: var(--color-background-contrast-32);
  /* The spinner's SVG paints with currentColor, and it sits on a darkened cover
     — not on the player background — so it takes the contrast token rather than
     inheriting the page text colour. Full contrast, not -50: the blades already
     animate down to 0.16 opacity, and halving that again loses them over a
     bright cover. */
  color: var(--color-text-contrast);
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

.track-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  text-align: center;
  gap: var(--space-03);
  min-width: 0;
  padding-top: var(--space-06);
}

.track-info.no-controls {
  padding-top: 0;
}

.controls-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
}

/* Reserve the progress bar's row height even while it's hidden (idle CD) so the
   centered track-info doesn't shift when the bar mounts on play. Matches the
   bar's flex-row height, which the .time line-height (--line-height-mono-medium)
   dominates over the 8px track. */
.progress-wrapper {
  min-height: var(--line-height-mono-medium);
}

.track-title {
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-artist {
  color: var(--color-text-light);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The transport plate. space-evenly splits what the plate's padding leaves, so
   the side padding is the one knob that sets the whole rhythm — the reasoning
   and the measurements behind --space-06 are PlaybackControls.vue's, whose
   plate this one draws. */
.controls {
  background: var(--color-background);
  border-radius: var(--radius-06);
  display: flex;
  justify-content: space-evenly;
  align-items: center;
  padding: var(--space-01) var(--space-06);
}

/* The tap target, which is NOT the icon and does not follow it: 80/90px circles
   sized for a finger on the kiosk. IconButton sizes itself from its padding, so
   without these the buttons would collapse to the icon plus 8px. */
.controls .control-button {
  width: 80px;
  height: 80px;
  padding: 0;
  border-radius: 50%;
  color: var(--color-text-light);
}

.controls .control-button--primary {
  width: 90px;
  height: 90px;
  color: var(--color-text);
}

/* The ghost variant assumes a dark ground and dims its own color while
   loading; this row sits on --color-background, so the spinner keeps the icon's
   tone instead. */
.controls .control-button--primary.icon-button--loading {
  color: var(--color-text);
}

/* Toggles and the source's own actions, one centred row above the bar. */
.options-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-04);
  min-width: 0;
}

.speed-selector {
  flex-shrink: 0;
}

/* The station or the show above the title. */
.track-kicker {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-02);
  min-width: 0;
}

.track-kicker-icon {
  width: 24px;
  height: 24px;
  flex-shrink: 0;
  border-radius: var(--radius-01);
  overflow: hidden;
}

.track-kicker-label {
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* The album behind the cover, the artist behind the line. */
.artwork.is-link {
  pointer-events: auto;
  cursor: pointer;
}

.track-artist.is-link {
  cursor: pointer;
}

/* Source bar (AirPlay device info) */
.source-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-03);
  padding-bottom: var(--space-06);
}

.source-bar-name {
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
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

  /* Over the cover's top-left corner on the phone, as CD's buttons are. */
  .player-back {
    position: absolute;
    top: calc(max(var(--space-05), env(safe-area-inset-top, 0px)) + var(--space-04));
    left: calc(var(--space-05) + var(--space-04));
    z-index: 10;
  }

  .controls-section {
    margin-bottom: calc(env(safe-area-inset-bottom, 0px));
  }

  .content-section {
    z-index: auto;
  }

  .connect-player .content-section {
    transform: none;
    opacity: 1;
    animation: none;
  }

  /* Collapse album art when tracklist is open, keeping a strip for action buttons */
  .artwork-section {
    transition: margin-top 400ms var(--easeInOutCubic);
  }

  .artwork-section.art-collapsed {
    /* Buttons absolute top (from connect-player) minus artwork offset (from now-playing padding) */
    --btn-top: calc(max(var(--space-05), env(safe-area-inset-top, 0px)) + var(--space-04));
    --art-top: max(var(--space-05), env(safe-area-inset-top, 0px));
    --btn-height: 40px;
    --art-visible: calc(var(--btn-top) - var(--art-top) + var(--btn-height) + var(--space-04));
    margin-top: calc(-100vw + 2 * var(--space-05) + var(--art-visible));
  }

  .artwork {
    border-radius: var(--radius-07);
  }
  .artwork-blur {
    transform: scale(1) translateZ(0);
  }

  .track-info {
    padding: var(--space-06) 0 var(--space-03) 0;
  }

  .track-info.no-controls {
    padding: 0;
  }
}
</style>
