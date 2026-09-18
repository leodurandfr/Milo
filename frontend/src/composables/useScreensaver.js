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
import { nowPlayingArtwork } from '@/utils/nowPlayingArtwork';
import { useRichDisplay } from '@/composables/useRichDisplay';
import { AUDIO_SOURCE_LABEL_KEYS } from '@/constants/audioSources';

/** Minimum ms between activity event processing. */
const ACTIVITY_THROTTLE_MS = 500;

// Media sources: the countdown only runs while audio is actually playing — an
// idle unit showing a paused track has nothing to fade into. The two receivers
// below are armed by a connected sender instead, because neither is guaranteed
// to report a play state at all. Bluetooth may nonetheless *have* one (see
// isPlaybackStopped); Mac never does.
const PLAYBACK_GATED_SOURCES = ['radio', 'podcast', 'airplay', 'dlna', 'qobuz', 'music_library', 'spotify', 'cd', 'tidal'];
const PASSIVE_SOURCES = ['bluetooth', 'mac'];

/**
 * How long playback must stay stopped before the overlay steps aside.
 *
 * A pause and the gap between two tracks are one and the same thing on the wire
 * — `is_playing: false` — so only duration tells them apart: a handover closes
 * in well under a second (Spotify's `not_playing` followed by the next track's
 * `metadata`), a pause lasts until someone presses play. Three seconds sits
 * above the gap and below what reads as a screen that stopped answering.
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

  const {
    currentPosition: podcastPosition,
    duration: podcastDuration,
    progressPercentage: podcastProgressPercentage,
    isPositionInitialized: podcastProgressReady,
  } = useSourceProgress('podcast');

  const {
    currentPosition: libraryPosition,
    duration: libraryDuration,
    progressPercentage: libraryProgressPercentage,
    isPositionInitialized: libraryProgressReady,
  } = useSourceProgress('music_library');

  const {
    currentPosition: spotifyPosition,
    duration: spotifyDuration,
    progressPercentage: spotifyProgressPercentage,
    isPositionInitialized: spotifyProgressReady,
  } = useSourceProgress('spotify');

  const {
    currentPosition: cdPosition,
    duration: cdDuration,
    progressPercentage: cdProgressPercentage,
    isPositionInitialized: cdProgressReady,
  } = useSourceProgress('cd');

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
   * the air. Nothing about the playback itself belongs here — playback is asked
   * separately, by canArmScreensaver and by isPlaybackStopped, because a single
   * expression answering all three is the bug this split fixes.
   *
   * `is_playing` dips to false when a track ends *on its own*, so a screensaver
   * keyed on it closed itself between two tracks — no touch, no user, just the
   * gap. The sources carry that dip each from its own channel: Spotify's
   * `not_playing` event (published straight from the event, without re-reading
   * /status — which is why polling /status at 10 Hz across two boundaries never
   * sees it), Tidal's BUFFERING/IDLE player states and DLNA's STOPPED transport
   * state. A skip commanded from the sender never produced it, which is what
   * made it look intermittent. AirPlay is absent from that list on purpose: its
   * `pfls` flush was once assumed to carry the same dip, but shairport-sync
   * sends neither `pfls` nor `pend` from a macOS sender (measured 2026-08-07,
   * sources/airplay/source.py), which is also why an AirPlay pause never
   * dismisses anything here.
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
    return unifiedStore.systemState.source_state === 'active';
  });

  /**
   * Whether `is_playing` means anything for the source on the air.
   *
   * Both halves below ask it, and they must get the same answer: a source whose
   * pause dismisses the overlay is a source whose pause must also keep it from
   * appearing, or a paused sender would draw one every idle stretch just to lose
   * it three seconds later.
   *
   * Bluetooth is the one source that can go either way, because it has two feeds
   * and only one of them is guaranteed: BlueALSA says a sender is connected,
   * AVRCP says what is playing — and an AVRCP player is optional, may appear
   * seconds after the link, and can go away with the app that published it.
   * `is_playing` alone cannot tell "no player" from "paused", since
   * PlaybackMetadata always serializes it: a sender with no player publishes
   * `is_playing: false` for the whole session, and reading that as a pause would
   * take its screensaver away three seconds in and never give it back. So the
   * source says it outright — `has_avrcp`, see sources/bluetooth/source.py.
   *
   * Not inferred from the track text either, and that is measured rather than
   * cautious: a Mac mini registers a player whose play/pause is accurate while
   * serving no title and no artist (2026-09-18, on the unit). Every guess from
   * the track would have read that sender as having no transport at all — and
   * pausing it is exactly the case this rule exists for.
   */
  const reportsPlayState = computed(() => {
    const source = unifiedStore.systemState.active_source;
    if (PLAYBACK_GATED_SOURCES.includes(source)) return true;
    return source === 'bluetooth' && unifiedStore.systemState.metadata?.has_avrcp === true;
  });

  /**
   * Whether playback has stopped, as opposed to handing over to the next track.
   *
   * `is_buffering` separates the two while the gap is still open: a source
   * loading what comes next is not paused. It is a bonus, not the mechanism —
   * only Spotify, Tidal, Music Library and CD ever set it, and Spotify clears it
   * for the sliver between `not_playing` and the next track's `metadata`. AirPlay
   * and DLNA never set it at all, so their handovers are held by the wall clock
   * alone: a DLNA controller taking more than PAUSE_DISMISS_MS between STOPPED
   * and the next Play would read as a pause here. Measured against nothing —
   * accepted as the price of one rule per source rather than four.
   */
  const isPlaybackStopped = computed(() => {
    if (!reportsPlayState.value) return false;
    const metadata = unifiedStore.systemState.metadata || {};
    if (metadata.is_buffering === true) return false;
    return metadata.is_playing !== true;
  });

  /** Whether the inactivity countdown may run: the above, plus live playback. */
  const canArmScreensaver = computed(() => {
    if (!screensaverStillApplies.value) return false;
    const source = unifiedStore.systemState.active_source;
    if (!PLAYBACK_GATED_SOURCES.includes(source) && !PASSIVE_SOURCES.includes(source)) {
      return false;
    }
    // A receiver that reports nothing is armed by the link alone.
    if (!reportsPlayState.value) return true;
    return unifiedStore.systemState.metadata?.is_playing === true;
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
  // it twice is exactly how DLNA came to draw a full media card — with a
  // generated text avatar standing in for the cover it did not have — over a
  // status card that had already refused it for want of one. AirPlay and
  // Bluetooth used to restate the rule here verbatim; they now read it from
  // the same place as every other source, and the copies are gone.
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
      const episode = podcastStore.displayEpisode;
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
      const track = musicLibraryStore.displayTrack;
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

    const metadata = unifiedStore.systemState.metadata || {};

    // Spotify, Tidal and CD: active players with rich metadata, rendered exactly
    // like music_library (cover + title/artist + progress bar, no bottom bar),
    // read straight from the shared metadata mirror. Every cover below comes
    // from nowPlayingArtwork, which is also what AudioPlayerFull paints — the
    // screensaver crossfades into that view, so anything else reads as a glitch.
    if (source === 'spotify' || source === 'tidal' || source === 'cd') {
      return {
        mode: 'media',
        sourceType: source,
        artwork: nowPlayingArtwork(metadata),
        title: metadata.title || '',
        subtitle: metadata.artist || null,
      };
    }

    // The four receivers, all drawn the same way — cover, title/artist, and a
    // bottom bar naming the other end. No progress bar: none of them shows one
    // in its main view either. They differ only in what fills that bar, and the
    // split below is the difference itself rather than a ternary hiding it.
    const receiver = (stationName) => ({
      mode: 'media',
      sourceType: source,
      artwork: nowPlayingArtwork(metadata),
      title: metadata.title || '',
      subtitle: metadata.artist || null,
      stationIcon: source,
      stationName,
    });

    // Named by the sender. With no name on the record the bar hides itself
    // (showBottomBar reads the name, not the icon) — a phone or a Mac always
    // publishes one, so an empty slot here means the session is not really up.
    if (source === 'bluetooth') return receiver(formatDeviceNames(metadata.device_name));
    if (source === 'airplay') return receiver(metadata.client_name || null);

    // Named by the service. DLNA names the media server once resolved and Qobuz
    // names nobody at all, so with no name on the record both read the source's
    // own label rather than leaving a bar the user cannot interpret.
    if (source === 'qobuz' || source === 'dlna') {
      return receiver(metadata.client_name || t(AUDIO_SOURCE_LABEL_KEYS[source]));
    }

    // Unreachable: a source with no branch here has no rich view either, so
    // richSource sent it to simpleData. Carries no `artwork` key on purpose —
    // the prop defaults to null, and a literal here would be the one thing the
    // parity guard cannot tell apart from a source that forgot to derive its
    // cover.
    return {
      mode: 'media',
      sourceType: null,
      title: '',
      subtitle: null,
      stationFavicon: null,
      stationName: null,
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
    const metadata = unifiedStore.systemState.metadata || {};

    if (source === 'mac') {
      return {
        mode: 'simple',
        sourceType: source,
        title: t('status.audioReceivedFrom'),
        subtitle: formatDeviceNames(metadata.client_names),
      };
    }

    if (source === 'bluetooth') {
      return {
        mode: 'simple',
        sourceType: source,
        title: t('status.connectedTo'),
        subtitle: formatDeviceNames(metadata.device_name),
      };
    }

    if (source === 'airplay' || source === 'dlna' || source === 'qobuz') {
      return {
        mode: 'simple',
        sourceType: source,
        title: t('status.connectedTo'),
        subtitle: metadata.client_name || null,
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
    const source = unifiedStore.systemState.active_source;
    return richSource.value === null ? simpleData(source) : mediaData(source);
  });

  const screensaverProgress = computed(() => {
    const source = unifiedStore.systemState.active_source;

    if (source === 'podcast') {
      return {
        currentPosition: podcastPosition.value,
        duration: podcastDuration.value,
        progressPercentage: podcastProgressPercentage.value,
        isReady: podcastProgressReady.value,
      };
    }

    if (source === 'music_library') {
      return {
        currentPosition: libraryPosition.value,
        duration: libraryDuration.value,
        progressPercentage: libraryProgressPercentage.value,
        isReady: libraryProgressReady.value,
      };
    }

    if (source === 'spotify') {
      return {
        currentPosition: spotifyPosition.value,
        duration: spotifyDuration.value,
        progressPercentage: spotifyProgressPercentage.value,
        isReady: spotifyProgressReady.value,
      };
    }

    if (source === 'cd') {
      return {
        currentPosition: cdPosition.value,
        duration: cdDuration.value,
        progressPercentage: cdProgressPercentage.value,
        isReady: cdProgressReady.value,
      };
    }

    return null;
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
