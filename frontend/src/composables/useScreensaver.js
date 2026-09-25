// frontend/src/composables/useScreensaver.js
// Screensaver visibility, inactivity timer, activity listeners, and display-data
// computation for AudioScreensaver. Owns the full screensaver lifecycle so MainView
// only needs to render the component and wire the returned refs.
import { ref, computed, watch, onUnmounted } from 'vue';
import { useTimer } from '@/composables/useTimer';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useRadioStore } from '@/stores/radioStore';
import { usePodcastStore } from '@/stores/podcastStore';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useLyricsStore } from '@/stores/lyricsStore';
import { useI18n } from '@/services/i18n';
import { isKiosk } from '@/utils/kiosk';
import { formatDeviceNames } from '@/utils/deviceName';
import { getFaviconUrl } from '@/utils/faviconUrl';
import { nowPlayingArtwork, nowPlayingArtworkPending } from '@/utils/nowPlayingArtwork';
import { nowPlayingOf } from '@/utils/nowPlayingMetadata';
import { useRichDisplay } from '@/composables/useRichDisplay';
import { AUDIO_SOURCE_LABEL_KEYS } from '@/constants/audioSources';

/** Minimum ms between activity event processing. */
const ACTIVITY_THROTTLE_MS = 500;

// The receivers drawn with a bottom bar naming the other end. They draw no
// progress bar here, whatever their main view does: the bar row is theirs.
const RECEIVER_SOURCES = ['airplay', 'bluetooth', 'qobuz'];

// The sources whose screensaver can draw a progress bar — every rich view
// without a bottom bar and with a playhead to show (radio has none).
const PROGRESS_SOURCES = ['podcast', 'music_library', 'spotify', 'tidal', 'cd'];

/**
 * How long playback must stay paused before the overlay steps aside.
 *
 * A pause can also be the sliver between two tracks, so only duration tells
 * the two apart: a handover closes in well under a second, a pause lasts until
 * someone presses play. Three seconds sits above the gap and below what reads
 * as a screen that stopped answering.
 */
const PAUSE_DISMISS_MS = 3000;

/**
 * Manages the audio screensaver lifecycle: visibility, inactivity timer,
 * DOM activity listeners, and display data derived from the active source.
 *
 * @returns {{
 *   isScreensaverVisible: import('vue').Ref<boolean>,
 *   screensaverData: import('vue').ComputedRef<Object>,
 *   closeScreensaver: () => void
 * }}
 */
export function useScreensaver() {
  const unifiedStore = useUnifiedAudioStore();
  const radioStore = useRadioStore();
  const podcastStore = usePodcastStore();
  const musicLibraryStore = useMusicLibraryStore();
  const settingsStore = useSettingsStore();
  const lyricsStore = useLyricsStore();
  const { t } = useI18n();
  const timer = useTimer();

  // One playhead per source that can draw a bar; only the selected one moves.
  const progressBySource = Object.fromEntries(
    PROGRESS_SOURCES.map((source) => [source, useSourceProgress(source)])
  );

  // --- Reactive state ---
  const isScreensaverVisible = ref(false);
  // Bumped on each close (visible → hidden) so a revealed source view can replay
  // its entrance animation — consumed via useScreensaverReveal.
  const screensaverRevealNonce = ref(0);
  let inactivityTimer = null;
  let pauseDismissTimer = null;
  let lastActivityTime = 0;

  // --- Derived settings ---

  const screensaverDelay = computed(() =>
    (settingsStore.screenScreensaver.screensaver_delay_seconds ?? 15) * 1000
  );

  /**
   * What keeps the screensaver up is having something to show: a source still on
   * the air, i.e. a session. Nothing about the playback itself belongs here —
   * playback is asked separately, by canArmScreensaver and by isPlaybackStopped,
   * because a single expression answering all three is the bug this split fixes.
   */
  const screensaverStillApplies = computed(() => {
    // Pi-screen-only: a remote Mac/iPhone viewing the UI never shows it (matches
    // ui_scale + color filter). Also removes the need for the portrait CSS hide
    // hack it once used.
    if (!isKiosk()) return false;
    if (!settingsStore.screenScreensaver.screensaver_enabled) return false;
    // Lyrics is itself a full-screen reading view that scrolls on its own: covering
    // it after a delay would hide the thing being read, without any user inactivity.
    if (lyricsStore.isOpen) return false;
    return unifiedStore.systemState.session !== null;
  });

  const phase = computed(() => unifiedStore.systemState.session?.phase ?? null);

  /**
   * Whether playback has stopped, as opposed to handing over to the next track.
   *
   * Only `paused` says so. A session `loading` its next track is not paused, and
   * a `connected` one (Mac, a Bluetooth sender Milō cannot read, AirPlay's
   * system audio) has no play state at all: reading that as a pause would take
   * its screensaver away three seconds in and never give it back.
   */
  const isPlaybackStopped = computed(() => phase.value === 'paused');

  /**
   * Whether the inactivity countdown may run: the above, plus live playback —
   * an idle unit showing a paused track has nothing to fade into. A connected
   * sender reports no play state, so the link alone arms it; the two halves
   * agree, so a paused sender is never handed an overlay it would lose 3 s later.
   */
  const canArmScreensaver = computed(() => {
    if (!screensaverStillApplies.value) return false;
    return phase.value === 'playing' || phase.value === 'connected';
  });

  // --- Timer management ---

  function clearInactivityTimer() {
    if (inactivityTimer) {
      timer.clear(inactivityTimer);
      inactivityTimer = null;
    }
  }

  function clearPauseDismissTimer() {
    if (pauseDismissTimer) {
      timer.clear(pauseDismissTimer);
      pauseDismissTimer = null;
    }
  }

  function resetInactivityTimer() {
    clearInactivityTimer();
    if (!canArmScreensaver.value || isScreensaverVisible.value) return;

    inactivityTimer = timer.setTimeout(() => {
      isScreensaverVisible.value = true;
    }, screensaverDelay.value);
  }

  // --- Activity handling ---

  function handleUserActivity() {
    const now = Date.now();
    if (now - lastActivityTime < ACTIVITY_THROTTLE_MS) return;
    lastActivityTime = now;

    if (!isScreensaverVisible.value) {
      resetInactivityTimer();
    }
  }

  // --- DOM listener management ---

  function addActivityListeners() {
    document.addEventListener('pointerdown', handleUserActivity, { passive: true });
    document.addEventListener('wheel', handleUserActivity, { passive: true });
    document.addEventListener('touchstart', handleUserActivity, { passive: true });
  }

  function removeActivityListeners() {
    document.removeEventListener('pointerdown', handleUserActivity);
    document.removeEventListener('wheel', handleUserActivity);
    document.removeEventListener('touchstart', handleUserActivity);
  }

  // --- Public action ---

  function closeScreensaver() {
    clearPauseDismissTimer();
    isScreensaverVisible.value = false;
    resetInactivityTimer();
  }

  // --- Screensaver display data ---

  // Which layout the screensaver draws is not its own decision: it mirrors
  // useRichDisplay, the rule that already picks between a rich player and the
  // AudioSourceStatus card for the view sitting behind this overlay. Deciding
  // it twice would let a receiver draw a full media card over a status card
  // that had already refused it for want of a cover. AirPlay and Bluetooth
  // used to restate the rule here verbatim; they now read it from the same
  // place as every other source, and the copies are gone.
  const { richSource } = useRichDisplay();

  /**
   * The full-screen restatement of a rich player: cover, title, subtitle, and
   * for the receivers a bottom bar naming the other end.
   *
   * No branch resolves its own no-cover fallback — `artwork` may be empty and
   * AudioScreensaver asks the shared helper what fills the slot, so the player
   * behind it cannot be showing something else.
   */
  function mediaData(source) {
    if (source === 'radio') {
      const station = radioStore.currentStation;
      const track = radioStore.trackInfo;

      // Favicon URL only — AudioScreensaver renders the inline SVG avatar from
      // `stationName` so the font cascades correctly. Radio is the one source
      // the helper answers 'avatar' for.
      const stationArt = getFaviconUrl(station?.favicon);

      if (track) {
        return {
          mode: 'media',
          sourceType: source,
          artwork: track.artwork || stationArt,
          title: track.title,
          subtitle: track.artist || null,
          stationFavicon: stationArt,
          stationName: station?.name || null,
        };
      }

      const genre = station?.genre
        ? station.genre.charAt(0).toUpperCase() + station.genre.slice(1)
        : null;
      const bitrate = station?.bitrate > 0 ? `${station.bitrate} kbps` : null;
      const metaParts = [genre, bitrate].filter(Boolean);

      return {
        mode: 'media',
        sourceType: source,
        artwork: stationArt,
        title: station?.name || t('radio.unknownStation'),
        subtitle: metaParts.length > 0 ? metaParts.join(' \u2022 ') : t('radio.live'),
        stationFavicon: null,
        stationName: null,
        useMonoSubtitle: true,
      };
    }

    if (source === 'podcast') {
      const episode = podcastStore.currentEpisode;
      return {
        mode: 'media',
        sourceType: source,
        artwork: episode?.image_url || null,
        title: episode?.name || t('podcasts.noEpisode'),
        subtitle: episode?.podcast?.name || null,
        stationFavicon: null,
        stationName: null,
      };
    }

    if (source === 'music_library') {
      const track = musicLibraryStore.nowPlaying;
      return {
        mode: 'media',
        sourceType: source,
        artwork: track?.albumArtUrl || null,
        title: track?.title || '',
        subtitle: track?.artist || null,
        stationFavicon: null,
        stationName: null,
      };
    }

    const state = unifiedStore.systemState;
    const nowPlaying = nowPlayingOf(state, source);

    // Spotify, Tidal and CD (Mac, always `connected`, never reaches a rich
    // view): active players rendered exactly like music_library (cover +
    // title/artist + progress bar, no bottom bar), read from the now-playing
    // record. Every cover below comes from
    // nowPlayingArtwork, which is also what AudioPlayerFull paints — the
    // screensaver crossfades into that view, so anything else reads as a glitch.
    if (!RECEIVER_SOURCES.includes(source)) {
      return {
        mode: 'media',
        sourceType: source,
        artwork: nowPlayingArtwork(nowPlaying),
        artworkAnnounced: nowPlayingArtworkPending(state),
        title: nowPlaying?.title || '',
        subtitle: nowPlaying?.artist || null,
      };
    }

    // The three receivers: cover, title/artist, and a bottom bar naming the
    // other end — the sender when the channel names one (AirPlay, Bluetooth).
    // Qobuz names nobody at all, so the bar reads the source's own label rather
    // than being left for the user to interpret. With no name at all the bar
    // hides itself (showBottomBar reads the name, not the icon).
    return {
      mode: 'media',
      sourceType: source,
      artwork: nowPlayingArtwork(nowPlaying),
      artworkAnnounced: nowPlayingArtworkPending(state),
      title: nowPlaying?.title || '',
      subtitle: nowPlaying?.artist || null,
      stationIcon: source,
      stationName: source === 'qobuz'
        ? t(AUDIO_SOURCE_LABEL_KEYS[source])
        : formatDeviceNames(nowPlaying?.senders) || null,
    };
  }

  /**
   * The full-screen restatement of the AudioSourceStatus card: the source
   * glyph, a status line, and whoever is on the other end.
   *
   * Only the receivers genuinely reach it — every other source earns a rich
   * view whenever it is active — but the default keeps a transition or an
   * unavailable source from rendering a blank overlay.
   */
  function simpleData(source) {
    const senders = formatDeviceNames(unifiedStore.systemState.session?.senders);

    if (source === 'mac') {
      return {
        mode: 'simple',
        sourceType: source,
        title: t('status.audioReceivedFrom'),
        subtitle: senders,
      };
    }

    if (RECEIVER_SOURCES.includes(source)) {
      return {
        mode: 'simple',
        sourceType: source,
        title: t('status.connectedTo'),
        subtitle: senders || null,
      };
    }

    const labelKey = AUDIO_SOURCE_LABEL_KEYS[source];
    return {
      mode: 'simple',
      sourceType: labelKey ? source : null,
      title: labelKey ? t(labelKey) : '',
      subtitle: null,
    };
  }

  const screensaverData = computed(() => {
    const source = unifiedStore.systemState.source;
    return richSource.value === null ? simpleData(source) : mediaData(source);
  });

  // The bar the revealed player draws, restated: shown once the source has a
  // duration and a position, exactly as ProgressBar gates itself there.
  const screensaverProgress = computed(() => {
    if (richSource.value === null) return null;
    const progress = progressBySource[unifiedStore.systemState.source];
    if (!progress || !progress.duration.value || !progress.isPositionInitialized.value) return null;
    return {
      currentPosition: progress.currentPosition.value,
      duration: progress.duration.value,
      progressPercentage: progress.progressPercentage.value,
      isReady: progress.isPositionInitialized.value,
    };
  });

  // --- Watchers ---

  watch(isScreensaverVisible, (visible, wasVisible) => {
    if (wasVisible && !visible) screensaverRevealNonce.value += 1;
  });

  watch(canArmScreensaver, (canArm) => {
    if (canArm) {
      addActivityListeners();
      resetInactivityTimer();
    } else {
      // Only the countdown stops here. What becomes of an overlay already up is
      // the next watcher's question, and it waits before answering it.
      removeActivityListeners();
      clearInactivityTimer();
    }
  }, { immediate: true });

  // First automatic dismissal: the source being drawn is no longer on the air,
  // so the overlay would be showing a track nothing is playing.
  watch(screensaverStillApplies, (stillApplies) => {
    if (!stillApplies) isScreensaverVisible.value = false;
  });

  // Second: playback stopped and stayed stopped, which no track handover does.
  // Pausing from a remote, a phone or the sender's own app is the one way to
  // reach the unit's screen without touching it, and the UI it hides is what
  // the hand reaching for the pause button was looking for.
  //
  // Visibility is watched alongside, not assumed: an overlay can go up over a
  // source that stopped before it appeared — a Bluetooth sender whose AVRCP
  // player shows up mid-session changes the answer without anything on the wire
  // moving — and an edge on `stopped` alone would never come.
  watch([isPlaybackStopped, isScreensaverVisible], ([stopped, visible]) => {
    clearPauseDismissTimer();
    if (!stopped || !visible) return;

    pauseDismissTimer = timer.setTimeout(() => {
      isScreensaverVisible.value = false;
    }, PAUSE_DISMISS_MS);
  });

  watch(
    () => settingsStore.screenScreensaver.screensaver_delay_seconds,
    () => {
      if (canArmScreensaver.value && !isScreensaverVisible.value) {
        resetInactivityTimer();
      }
    }
  );

  // --- Cleanup ---

  onUnmounted(() => {
    removeActivityListeners();
    // inactivityTimer is auto-cleared by useTimer.
  });

  return {
    isScreensaverVisible,
    screensaverRevealNonce,
    screensaverData,
    screensaverProgress,
    closeScreensaver,
  };
}
