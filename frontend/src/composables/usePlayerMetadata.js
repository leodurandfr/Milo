// frontend/src/composables/usePlayerMetadata.js
// What a source's player names: the title and the lines around it, the cover,
// and what the full player's source bar says — read from the state, and held
// while the player leaves. Read once per player through usePlayerState — by
// both players (AudioPlayer, the playing bar of the navigation;
// AudioPlayerFull, the full player), and taken from there by the body they
// share (PlayerBody) — so the bar and the full player cannot name one record
// differently.
import { computed, ref, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSpotifyStore } from '@/stores/spotifyStore';
import { useI18n } from '@/services/i18n';
import { AUDIO_SOURCE_LABEL_KEYS } from '@/constants/audioSources';
import { formatDeviceNames } from '@/utils/deviceName';
import { getFaviconUrl } from '@/utils/faviconUrl';
import { episodeHasOwnImage } from '@/utils/podcastArtwork';
import { generateStationAvatarSvg } from '@/utils/stationAvatar';
import { nowPlayingArtwork, nowPlayingArtworkPending, artworkFallback } from '@/utils/nowPlayingArtwork';
import { nowPlayingOf, nowPlayingSnapshot } from '@/utils/nowPlayingMetadata';

/**
 * What surrounds the title, by what the details say the record is.
 *
 * `barLabel` names what the music comes from when the details know something
 * more specific than the source, with `barImage` as its picture: the station a
 * detected song plays on (its logo through the favicon proxy, '' when it has
 * none — the label's generated avatar), or the show an episode belongs to when
 * the cover is the episode's own picture rather than the show's
 * (utils/podcastArtwork: a show's cover already says which show it is), with
 * the show's image when it has one. `barAccount` is the Spotify account, named
 * from the profiles. A radio's record names a detected song or, without one,
 * the station itself, which has no artist line to fall back on. An episode's
 * `artist` is its show: drawn under the title only when the bar does not
 * already say it. Anything else is a track, and a track always has an artist
 * line.
 *
 * `barOnCard` says whether the playing bar's card draws the source bar too.
 * Only for a station playing a detected song with its own cover: the card's
 * cover is then the song's, and nothing else on the card says which station it
 * comes from. Every other card names its source already — the station's own
 * logo, the show in the secondary line, the browser around it — and the full
 * player draws the source bar always.
 *
 * @param {object|null} current - the source's `details`
 */
export function linesOf(current) {
  const kind = current?.kind;
  const none = { barLabel: null, barImage: null, barAccount: null, barRemote: null, avatarName: '', barOnCard: false };
  if (kind === 'radio') {
    const station = current.station;
    const track = current.track;
    // A song counts once it has a title, an artist and a cover of its own:
    // without that cover the cover slot holds the station's logo already, and
    // the bar would draw it a second time.
    const detected = !!track?.title && !!track?.artist && !!track?.artwork && !!station?.name;
    return {
      ...none,
      barLabel: detected ? station.name : null,
      barImage: detected ? getFaviconUrl(station.favicon) : null,
      avatarName: station?.name || '',
      barOnCard: detected,
      secondary: false
    };
  }
  if (kind === 'podcast') {
    const show = current.episode?.podcast;
    const own = episodeHasOwnImage(current.episode);
    return {
      ...none,
      barLabel: own ? show?.name || null : null,
      barImage: own ? show?.image_url || null : null,
      secondary: false
    };
  }
  if (kind === 'spotify') {
    // What another device plays says where it plays, on the card too: nothing
    // else on it tells that the music is not here.
    const remote = current.remote;
    return {
      ...none,
      barAccount: current.account ?? null,
      barRemote: remote ? { device: remote.device_name, paused: remote.paused } : null,
      barOnCard: !!remote,
      secondary: true
    };
  }
  return { ...none, secondary: true };
}

export function usePlayerMetadata(source) {
  const unifiedStore = useUnifiedAudioStore();
  const spotifyStore = useSpotifyStore();
  const { t } = useI18n();

  const isSelected = computed(() => unifiedStore.systemState.source === source);
  const session = computed(() => (isSelected.value ? unifiedStore.systemState.session : null));
  const details = computed(() => (isSelected.value ? unifiedStore.systemState.details : null));

  // The last record worth naming, so the title and cover do not blank out while
  // the player leaves (a source switch clears the record under it) — see the
  // util. The lines around the title come from the details and are kept with it.
  const metadata = ref({
    title: '',
    artist: '',
    artwork: '',
    ...linesOf(null)
  });

  watch(
    () => [nowPlayingOf(unifiedStore.systemState, source), details.value],
    ([record, current]) => {
      const snapshot = nowPlayingSnapshot(record);
      if (snapshot) metadata.value = { ...snapshot, ...linesOf(current) };
    },
    { immediate: true }
  );

  const title = computed(() => metadata.value.title || t('status.unknownTitle'));

  // A track with no artist says so; a station or an episode has no such line.
  const secondaryLine = computed(() => {
    const { artist, secondary, barLabel } = metadata.value;
    if (secondary) return artist || t('status.unknownArtist');
    return artist && artist !== barLabel ? artist : '';
  });

  // The name of the Spotify account playing, from the profiles the Spotify
  // store holds (SpotifySource loads them).
  const accountName = computed(() => {
    const username = metadata.value.barAccount;
    if (!username) return null;
    return spotifyStore.profiles.find(profile => profile.username === username)?.name || null;
  });

  // The source bar's label. Who is sending, when the channel says so (AirPlay's,
  // Bluetooth's and the Mac's senders); else what the details name (the
  // station, the show, the account); else the source itself, read from the same
  // key the status card and the dock use, never a label a backend hardcoded in
  // one language. Nothing identifies the sender on the other receiver channels —
  // the Qobuz proxy only knows the speaker.
  const senderNames = computed(() => formatDeviceNames(session.value?.senders));
  const remoteLabel = computed(() => {
    const remote = metadata.value.barRemote;
    if (!remote) return null;
    return t(remote.paused ? 'spotify.pausedOn' : 'spotify.playingOn', { device: remote.device });
  });
  const sourceLabel = computed(
    () => senderNames.value
      || metadata.value.barLabel
      || remoteLabel.value
      || (accountName.value && t('spotify.accountSpotify', { name: accountName.value }))
      || t(AUDIO_SOURCE_LABEL_KEYS[source])
  );
  // The icon's replacement: a named station's logo, a show's image.
  const sourceImage = computed(() => (senderNames.value ? null : metadata.value.barImage));
  // Whether the playing bar's card draws it as well — see linesOf.
  const sourceOnCard = computed(() => metadata.value.barOnCard);

  // Which cover this source shows is decided in one place — see the util.
  const artwork = computed(() => nowPlayingArtwork(metadata.value));
  // What the slot shows with no cover at all.
  const fallback = computed(() => artworkFallback(source));
  // The generated station avatar, and only where the helper says so (radio):
  // inline markup rather than an image, so it renders in the app's own font.
  const stationAvatarSvg = computed(() => {
    if (fallback.value.kind !== 'avatar') return '';
    const name = metadata.value.avatarName || metadata.value.title;
    return name ? generateStationAvatarSvg(name) : '';
  });
  // Live, not from the held copy: a lifted flag must lift the veil at once.
  const artworkAnnounced = computed(
    () => isSelected.value && nowPlayingArtworkPending(unifiedStore.systemState)
  );
  const trackKey = computed(() => `${metadata.value.title}|${metadata.value.artist}`);

  return {
    metadata, title, secondaryLine, sourceLabel, sourceImage, sourceOnCard,
    artwork, fallback, stationAvatarSvg, artworkAnnounced, trackKey
  };
}
