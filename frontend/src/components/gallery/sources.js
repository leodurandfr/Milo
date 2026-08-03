// frontend/src/components/gallery/sources.js
/**
 * The ten audio sources, as pages of the gallery — the second axis.
 *
 * The catalogue next door answers "what does this component do"; this file
 * answers "what does a source look like, in every state it can reach". The two
 * are not the same question: AudioPlayerFull serves five sources and none of
 * them shows the same thing, while CD alone reaches eight states across three
 * different components. A reader after either one had to assemble it from four
 * component pages and useRichDisplay's source code.
 *
 * ## A scenario is a stimulus, not a state someone named
 *
 * Every scenario below is a list of **WebSocket events in the backend's own
 * wire shape** — the envelope `ws_events.py::WsEvent.to_envelope` builds, around
 * the `full_state` `state.py::get_current_state()` injects. `SourceStage` hands
 * each one to `unifiedAudioStore.updateState`, which is the exact handler
 * `App.vue` registers for these pairs, so the record goes through the same Zod
 * validation a real broadcast does. Then *the app's own rules* decide what
 * appears: `useRichDisplay()` picks the player or the status card,
 * `rawSourceState` derives CD's three pseudo-states, `currentDeviceName` maps
 * the per-source identity field.
 *
 * This is a stricter rule than it looks, and it is the second attempt. The first
 * wrote a `systemState` snapshot straight into the store and gave each one a
 * hand-written name — and hand-written names drift into *fiction*: "small cover"
 * and "sender stopped" were two AirPlay scenarios rendering the same screen,
 * pixel for pixel, because the status card never reads a cover width. Naming a
 * scenario after the screen it produces means knowing that screen, which is the
 * app's job, not this file's.
 *
 * So a scenario is named after **what it sends**, never after what comes back.
 * `scenarioId()` derives the id from the final event's `source_state` plus the
 * metadata fields the app's deciders branch on: `active is_playing
 * album_art_width=128` names a stimulus, and every token in it is a real field
 * on the wire. Two scenarios that produce the same screen therefore keep two
 * names — and the collision becomes a finding about the app, which is what the
 * page is for, instead of a naming problem to sweep up.
 *
 * ## Nothing here reaches the appliance
 *
 * The events are built in this file and dispatched locally: no socket is opened,
 * and no catalogued component subscribes to one (the guardrail pins both).
 * Reads are served by `canvasHttp` from the fixtures below, writes are blocked,
 * and `CanvasApp` replaces `sendCommand`. The page cannot see, and cannot
 * change, what is playing in the next room. The backend models are read *by the
 * guardrail only*, from the `.py` files at test time — never bundled.
 *
 * ## The three with a browser
 *
 * Radio, Podcasts and Music Library dispatch to their own `*Source.vue`, which
 * own feature stores and fetch on mount (`/api/radio/countries`,
 * `loadLikedSongs()`), so mounting *those* would read the real catalogue. Only
 * that wrapper is reassembled: `via: 'browser'` gives the real
 * AudioSourceLayout the header its source passes, and mounts the source's real
 * browsing view inside it. Everything below the wrapper is the app's.
 *
 * Their audio state never varies — `active`, and `hasRichDisplay` returns true
 * for them unconditionally — so the event alone cannot tell two of their
 * scenarios apart. What does is the *catalogue* condition, and that arrives over
 * HTTP rather than the socket. Those scenarios therefore carry a `condition`,
 * spelled with the real field names of the fixture that produces it
 * (`stations=0`, `scanning`), each token checked by the guardrail against the
 * scenario's own browser block. Two axes, two vocabularies, both borrowed.
 *
 * What a scenario supplies to a browser is what the backend would, in three
 * shapes, because the stores leave three different ways in:
 *
 *   api    GET responses, keyed by URL prefix. The store parses them by its own
 *          code path, so the fixture is checked against the shape it expects.
 *   seed   store fields written directly, for state with no load path worth
 *          running — and for the `*Loaded` flags, which is what stops a view
 *          fetching on mount and shimmering for ever.
 *   prime  a store action to call once the two above are in place, for state
 *          whose field is a computed and so cannot be seeded at all (radio's
 *          favourites are exposed as a sorted computed).
 *
 * The three are not interchangeable and the guardrail says so: a seed key is
 * checked against what the store actually exports *and* whether it can be
 * written, which is the check that caught `favoriteStations` being derived.
 *
 * Plain data, no `.vue` import: the guardrail reads this file to check the ten
 * against ALL_AUDIO_SOURCES, every event against the backend's own models, and
 * every fabricated metadata key against the files that read it.
 * `SourceStage.vue` is what turns a scenario into a mounted component.
 */
import albumPlaceholder from '@/assets/images/album-placeholder.svg';
import stationImageTurntable from './samples/station-image-turntable.webp';
import stationImageCapsule from './samples/station-image-capsule.webp';

/** Prefix that tells a source page apart from a catalogue entry in `?c=`. */
export const SOURCE_PAGE_PREFIX = 'source:';

/** A cover big enough to clear the untrusted-sender gate, and one that is not. */
const TRUSTED_COVER_PX = 600;
const FAVICON_COVER_PX = 128;

/**
 * The files that read what these events carry. Checked key by key by the
 * guardrail, which is what stops a fixture outliving the field it fabricates.
 */
export const METADATA_READERS = [
  'App.vue',
  'components/audio/AudioSourceView.vue',
  'components/audio/AudioPlayerFull.vue',
  'composables/useRichDisplay.js',
  'composables/useSourceProgress.js',
  'utils/playbackBuffering.js',
  'stores/cdStore.js'
];

/**
 * The three files that turn a record into a screen — the app's deciders. Every
 * field in BEHAVIOURAL_FIELDS must be read by one of them, or it is not
 * behavioural and has no business in a scenario's name.
 */
export const DECIDERS = [
  'composables/useRichDisplay.js',
  'components/audio/AudioSourceView.vue',
  'utils/playbackBuffering.js'
];

/**
 * The metadata fields the deciders branch on, in the order a name spells them.
 *
 * Not a taxonomy of our own: each appears in a condition in DECIDERS, and the
 * guardrail fails on any that does not. Everything else a source emits —
 * `album_art_url`, `position`, `disc_album` — is *content*: it changes what the
 * screen says, never which screen you get, so it stays out of the name.
 */
export const BEHAVIOURAL_FIELDS = [
  'title',
  'artist',
  'album_art_width',
  'device_name',
  'client_name',
  'client_names',
  'is_playing',
  'is_buffering',
  'drive_connected',
  'disc_present',
  'cache_ready',
  'ejecting',
  'account_authenticated'
];

/**
 * How a field renders inside a name. Presence is enough for a string — the value
 * is content, and a track title in a tab would be noise; a number and an
 * explicit `false` are themselves the discriminating part, so they are printed;
 * an array prints its length, which is what separates Mac's one sender from its
 * two.
 */
function spell(key, value) {
  if (value === true) return key;
  if (Array.isArray(value)) return `${key}=${value.length}`;
  if (typeof value === 'string') return key;
  return `${key}=${value}`;
}

/**
 * A scenario's name, derived from what it sends — never written by hand.
 *
 * The final event is what the screen settles on, so the id reads its
 * `full_state`: the source state (the backend enum value, verbatim) followed by
 * the behavioural metadata it carries, then the catalogue condition for the
 * three sources that have one.
 */
export function scenarioId(events, browser) {
  const settled = events[events.length - 1].data.full_state;
  const metadata = settled.metadata || {};
  const facts = BEHAVIOURAL_FIELDS
    .filter(key => metadata[key] !== undefined)
    .map(key => spell(key, metadata[key]));

  return [settled.source_state, ...facts, ...(browser?.condition ?? [])].join(' ');
}

/**
 * The wire envelope, as `WsEvent.to_envelope` builds it. `timestamp` is the one
 * field nothing on this side reads, so it is pinned rather than faked — which
 * also keeps a scenario byte-identical across runs.
 */
function envelope(category, type, origin, data) {
  return { category, type, origin, data, timestamp: 0 };
}

/**
 * `full_state`, as `AudioStateMachine.get_current_state()` assembles it: the
 * dataclass's own `to_dict()` plus the two global flags it pulls from the
 * routing and CamillaDSP services. Both are carried because the real payload
 * always carries them, and `unifiedAudioStore` mirrors both.
 */
function fullState(source, sourceState, metadata, { transitioning = false, error = null } = {}) {
  return {
    active_source: source,
    source_state: sourceState,
    transitioning,
    metadata,
    error,
    multiroom_enabled: false,
    equalizer_effects_enabled: true
  };
}

/**
 * `source/state_changed` — the event every source lifecycle change rides on.
 * `data` carries the model's own three fields plus the injected snapshot: the
 * store reads the snapshot and nothing else, App.vue reads `new_state` and
 * `metadata.error` for the notification banner.
 */
function stateChanged(source, newState, metadata = {}, options = {}) {
  return envelope('source', 'state_changed', source, {
    source,
    new_state: newState,
    metadata,
    full_state: fullState(source, newState, metadata, options)
  });
}

/**
 * `system/transition_start` — what the state machine emits before a switch, and
 * the only honest way to reach `transitioning`. It declares no fields of its
 * own: the whole payload is the injected snapshot.
 */
function transitionStart(source) {
  return envelope('system', 'transition_start', 'system', {
    full_state: fullState(source, 'starting', {}, { transitioning: true })
  });
}

/** Assembles a scenario and derives its name. The only way one is built. */
function scenario(events, label, note, browser) {
  return {
    id: scenarioId(events, browser),
    label,
    note,
    events,
    ...(browser ? { browser } : {})
  };
}

/** `transitioning` is what every source's first state actually looks like. */
function starting(source) {
  return scenario(
    [transitionStart(source)],
    'Starting',
    'transitioning — the status card takes over whatever the source is, and the spinner replaces its icon. Held for 500 ms minimum so a fast backend never flashes it.'
  );
}

/** Nothing is playing yet: the source is up and idle. */
function waiting(source, label, note, metadata = {}) {
  return scenario([stateChanged(source, 'waiting', metadata)], label, note);
}

function active(source, label, note, metadata) {
  return scenario([stateChanged(source, 'active', metadata)], label, note);
}

/**
 * `SourceState.ERROR`, which every source can reach — `BaseAudioSource` sets it
 * on a failed start and the state machine broadcasts it. Carried twice because
 * the app reads it in two places: `full_state.error` for the store,
 * `metadata.error` for App.vue's notification banner.
 */
function errored(source, note, message) {
  return scenario(
    [stateChanged(source, 'error', { error: message }, { error: message })],
    'Error',
    note
  );
}

/** A browser source's scenario: an active record plus the browser's setup. */
function browsing(source, label, note, browser) {
  return scenario([stateChanged(source, 'active', {})], label, note, browser);
}

/** Shared by the CD scenarios that have a disc: identity + its tracklist. */
const CD_DISC = {
  disc_present: true,
  cache_ready: true,
  disc_id: 'yvYlA5_2ZK6mQvZ1kZ0rXqLg7dM-',
  disc_album: 'Felt',
  disc_artist: 'Nils Frahm',
  disc_year: '2011',
  disc_cover_url: albumPlaceholder,
  track_count: 4,
  tracks: [
    { number: 1, title: 'Keep', duration: 312000 },
    { number: 2, title: 'Snippet', duration: 96000 },
    { number: 3, title: 'Kind', duration: 268000 },
    { number: 4, title: 'Unter', duration: 401000 }
  ]
};

/**
 * The three browsers' headers, copied from their own call sites as *i18n keys*
 * rather than as text: the header is half of what "the real rendering" means,
 * and a hard-coded English string on a unit running in French would be a
 * different screen from the one being documented.
 */
const RADIO_HEADER = { titleKey: 'audioSources.radioSource.favoritesTitle', actions: ['search'] };
const PODCAST_HEADER = { titleKey: 'podcasts.podcasts', actions: ['heartOff', 'search', 'queue'] };
const ML_HEADER = { titleKey: 'audioSources.musicLibrary', actions: ['queue', 'search'] };

/**
 * Radio stations as the favourites grid receives them — two carrying an image,
 * four with an empty `favicon`, which is the split the page exists to show.
 *
 * StationCard has two branches and they look nothing alike. A station with an
 * image renders it; a station without one gets `generateStationAvatarSvg`, a
 * deterministic coloured monogram of its name. Most of a directory lands on the
 * second, because a directory entry only carries a usable logo some of the
 * time, so a grid that showed one branch would misreport what a real favourites
 * screen looks like.
 *
 * The images are the *custom* branch, and that is the only one this page can
 * draw. `getFaviconUrl` passes a same-origin path straight through — a custom
 * station's uploaded `/api/radio/images/…` in the app, a bundled sample here —
 * while any external logo becomes `/api/radio/favicon?url=…`, a backend fetch
 * to the station's own host. That third case stays out: one outbound fetch per
 * card, on a page whose whole point is to render the same way every time and
 * without touching the unit.
 *
 * The two samples are neutral illustrations rather than either broadcaster's
 * mark, which is also what a custom image *is* on a real unit: whatever the
 * listener uploaded for that station, in place of the logo.
 */
const RADIO_FAVOURITES = [
  { id: 'st-nova', name: 'Radio Nova', favicon: stationImageTurntable, countrycode: 'FR', genre: 'eclectic' },
  { id: 'st-fip', name: 'FIP', favicon: '', countrycode: 'FR', genre: 'eclectic' },
  { id: 'st-inter', name: 'France Inter', favicon: '', countrycode: 'FR', genre: 'talk' },
  { id: 'st-musique', name: 'France Musique', favicon: stationImageCapsule, countrycode: 'FR', genre: 'classical' },
  { id: 'st-tsf', name: 'TSF Jazz', favicon: '', countrycode: 'FR', genre: 'jazz' },
  { id: 'st-nts', name: 'NTS Radio 1', favicon: '', countrycode: 'GB', genre: 'electronic' }
];

/** The station behind every player scenario, so the grid and the pane agree. */
const RADIO_STATION_WITH_IMAGE = RADIO_FAVOURITES[0];
const RADIO_STATION_NO_IMAGE = RADIO_FAVOURITES[1];

/** One USB key: the single-storage case, where the picker is not drawn at all. */
const ML_STORAGE_USB = [
  { id: 'usb-1', name: 'SanDisk 128G', kind: 'usb', library_id: 1, mounted: true }
];

/** A key plus two network shares — the only case that draws the storage picker. */
const ML_STORAGE_MIXED = [
  ...ML_STORAGE_USB,
  { id: 'share-nas', name: 'NAS — Musique', kind: 'share', library_id: 2, mounted: true },
  { id: 'share-studio', name: 'Studio SMB', kind: 'share', library_id: 3, mounted: true }
];

/**
 * The other three tabs, so switching one does not land on a skeleton that never
 * resolves: every catalog read is scoped to the selected library and the store
 * drops its caches when that changes, so seeding the `*Loaded` flags is not
 * enough — the fixtures have to answer. Artists arrive pre-bucketed by initial,
 * which is the shape `displayedArtistIndex` renders.
 */
const ML_ARTIST_INDEX = [
  { name: 'A', artist: [{ id: 'ar-1', name: 'Alain Bashung', albumCount: 2 }] },
  { name: 'M', artist: [{ id: 'ar-2', name: 'Miles Davis', albumCount: 1 }] },
  { name: 'N', artist: [{ id: 'ar-3', name: 'Nils Frahm', albumCount: 3 }] }
];

const ML_GENRES = [
  { value: 'Ambient', songCount: 64 },
  { value: 'Chanson française', songCount: 38 },
  { value: 'Jazz', songCount: 112 },
  { value: 'Modern Classical', songCount: 47 }
];

const ML_PLAYLISTS = [
  { id: 'pl-1', name: 'Travail', songCount: 82 },
  { id: 'pl-2', name: 'Dimanche matin', songCount: 34 }
];

/**
 * The speed list the backend owns (GET /api/podcast/playback-speeds). Served
 * rather than hardcoded into the stage: the store fetches it at mount and the
 * dropdown maps whatever comes back, so the gallery cannot drift into offering
 * a set the appliance does not.
 */
const PODCAST_SPEEDS = [0.8, 1.0, 1.2, 1.5, 1.8, 2.0];

/** The shows the unit is subscribed to, as the home's first block lists them. */
const PODCAST_SUBSCRIPTIONS = [
  { uuid: 'sub-1', name: 'Le Code a changé', publisher: 'France Inter', is_subscribed: true },
  { uuid: 'sub-2', name: 'Affaires sensibles', publisher: 'France Inter', is_subscribed: true }
];

/**
 * A Podcast Index top-charts page. `image_url` is left off so LazyImage takes
 * its bundled-placeholder branch — the artwork is a CDN fetch the gallery has
 * no business making, and the placeholder is what a slow one shows anyway.
 */
const PODCAST_CHARTS = [
  { uuid: 'pi-1', itunes_id: 1, name: 'Le Code a changé', publisher: 'France Inter' },
  { uuid: 'pi-2', itunes_id: 2, name: 'Affaires sensibles', publisher: 'France Inter' },
  { uuid: 'pi-3', itunes_id: 3, name: 'Les Pieds sur terre', publisher: 'France Culture' },
  { uuid: 'pi-4', itunes_id: 4, name: 'Song Exploder', publisher: 'Hrishikesh Hirway' },
  { uuid: 'pi-5', itunes_id: 5, name: 'Transfert', publisher: 'Slate.fr' },
  { uuid: 'pi-6', itunes_id: 6, name: 'Vlan!', publisher: 'Grégory Pouy' }
];

const ML_ALBUMS = [
  { id: 'al-1', name: 'Felt', artist: 'Nils Frahm', year: 2011 },
  { id: 'al-2', name: 'Spaces', artist: 'Nils Frahm', year: 2013 },
  { id: 'al-3', name: 'All Melody', artist: 'Nils Frahm', year: 2018 },
  { id: 'al-4', name: 'Bleu Pétrole', artist: 'Alain Bashung', year: 2008 },
  { id: 'al-5', name: 'Fantaisie Militaire', artist: 'Alain Bashung', year: 1998 },
  { id: 'al-6', name: 'Kind of Blue', artist: 'Miles Davis', year: 1959 }
];

/**
 * The Music Library setup, split the way the store forces it to be.
 *
 * `storages` and `scanning` arrive over HTTP rather than as a seed, and not by
 * preference: `storagesLoaded` and `scanning` are private to the store, so the
 * mount-time `loadStorages()` cannot be short-circuited and `scanning` cannot
 * be written from outside. `applyStorages` sets all three, so serving the
 * response is the only way in — and the real parse runs, which is the better
 * half of the bargain.
 *
 * Everything else is a seed, and the `*Loaded` flags are the point rather than
 * an afterthought: each `loadTab` is guarded on them, so setting them is what
 * stops the view fetching. A tab left unloaded with nothing to load shows its
 * skeleton for ever.
 */
function mlSetup({ storages, albums = [], scanning = false, activeLibraryId = null }) {
  return {
    api: {
      '/api/music-library/storages': { storages, scanning },
      '/api/music-library/albums': { albums },
      '/api/music-library/artists': { index: albums.length ? ML_ARTIST_INDEX : [] },
      '/api/music-library/genres': { genres: albums.length ? ML_GENRES : [] },
      '/api/music-library/playlists': { playlists: albums.length ? ML_PLAYLISTS : [] }
    },
    seed: {
      musicLibrary: {
        activeLibraryId,
        likedSongIds: new Set(['s-1', 's-2', 's-3'])
      }
    },
    // Every loader here is guarded on a "already loaded" flag, and the flags
    // survive a scenario change — so without forcing the reads, picking the
    // scanning scenario after the one-key one would show the previous
    // scenario's albums and its storage picker. `storagesLoaded` is private to
    // the store, which is why this is a prime rather than four more seeds.
    prime: [
      ['musicLibrary', 'loadStorages', { force: true }],
      ['musicLibrary', 'loadAlbums', { reset: true }],
      ['musicLibrary', 'loadArtists', { force: true }],
      ['musicLibrary', 'loadGenres', { force: true }],
      ['musicLibrary', 'loadPlaylists', { force: true }]
    ]
  };
}

export const SOURCE_PAGES = [
  {
    id: `${SOURCE_PAGE_PREFIX}spotify`,
    source: 'spotify',
    title: 'Spotify',
    family: 'C — active player',
    uses: 'AudioSourceStatus · AudioPlayerFull (showControls)',
    via: 'dispatcher',
    summary:
      'The only Connect source Milō drives back: AudioPlayerFull with the full transport. Its rich display is gated on title + artist alone — Spotify is a trusted metadata provider, so no cover-quality check. There is no "active, no metadata" scenario here any more: the source no longer publishes ACTIVE unless go-librespot answered with a track, so the gap between the session opening and the first track event now reads as waiting instead of as a card with nothing to draw.',
    scenarios: [
      starting('spotify'),
      waiting('spotify', 'Waiting', 'Connected to go-librespot, no phone has picked the speaker yet.'),
      active('spotify', 'Playing', 'Rich display earned: AudioPlayerFull, progress bar and transport. The buttons report to the event log instead of reaching the unit.', {
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: albumPlaceholder,
        is_playing: true,
        position: 192000,
        duration: 511000
      }),
      active('spotify', 'Paused', 'Same record, is_playing false — the glyph flips and useSourceProgress stops ticking.', {
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: albumPlaceholder,
        is_playing: false,
        position: 192000,
        duration: 511000
      }),
      active('spotify', 'Buffering', 'is_buffering swaps the play/pause glyph for a spinner (isSourceBuffering). The bar keeps its last position.', {
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: albumPlaceholder,
        is_playing: true,
        is_buffering: true,
        position: 0,
        duration: 511000
      }),
      errored(
        'spotify',
        'The state every source can reach and none of them draws: AudioSourceStatus has no error branch, so the card falls through to the bare "waiting" line while App.vue raises the notification banner over it. Worth looking at rather than accepting.',
        'go-librespot exited'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}qobuz`,
    source: 'qobuz',
    title: 'Qobuz',
    family: 'B — passive player',
    uses: 'AudioSourceStatus · AudioPlayerFull (showControls false, showProgress)',
    via: 'dispatcher',
    summary:
      'Receiver-driven, so AudioPlayerFull draws a read-only bar and a source bar instead of a transport. The only source with a login state: account_authenticated false swaps the idle line and arms the connect CTA, and only an explicit false does — an absent field reads as connected so the card never flashes the CTA before the proxy has answered.',
    scenarios: [
      starting('qobuz'),
      waiting('qobuz', 'Waiting', 'Account connected, waiting for the app to pick the speaker.'),
      waiting(
        'qobuz',
        'Waiting, no account',
        'account_authenticated false — the only path to the second CTA in AudioSourceStatus. Tapping it calls inject("openSettings"), which is absent here, so it no-ops.',
        { account_authenticated: false }
      ),
      active(
        'qobuz',
        'Active, before now_playing',
        'Reachable, but only as an escape hatch: the source holds an active status carrying no track for a few poll ticks, then commits anyway so a proxy that never delivers one cannot wedge it in waiting. The proxy exposes no controller identity, so currentDeviceName is empty and the generic active branch prints "Qobuz / playing" rather than falling back to "waiting".',
        { is_playing: true }
      ),
      active('qobuz', 'Playing', 'Trusted CDN cover, so no album_art_width gate — title + artist is enough. Read-only bar above the source bar.', {
        title: 'Ambre',
        artist: 'Nils Frahm',
        album_art_url: albumPlaceholder,
        is_playing: true,
        client_name: 'Milō',
        position: 64000,
        duration: 264000
      }),
      errored(
        'qobuz',
        'Same fall-through as every other source: the card prints the bare "waiting" line. Qobuz reaches it when the proxy sidecar dies, which is also the moment its account state stops being knowable.',
        'qobuz-proxy unreachable'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}airplay`,
    source: 'airplay',
    title: 'AirPlay',
    family: 'B — passive player',
    uses: 'AudioSourceStatus · AudioPlayerFull (showControls false, showProgress)',
    via: 'dispatcher',
    summary:
      'The untrusted-sender gate lives here: title, artist, audio actually flowing AND a cover above UNTRUSTED_SENDER_MIN_ARTWORK_PX (300). Two scenarios below fail it for different reasons and land on the same screen — the card names the sender and nothing else, because it reads neither a cover width nor a playing flag. That collision is the page saying the gate has one outcome, not three.',
    scenarios: [
      starting('airplay'),
      waiting('airplay', 'Waiting', 'shairport-sync advertising, nobody streaming.'),
      active(
        'airplay',
        'Active, favicon cover',
        `album_art_width ${FAVICON_COVER_PX} is below the 300 px floor, so the rich display is declined and the card names the sender instead. Change the constant and this scenario changes with it.`,
        {
          title: 'Ainsi parlait Zarathoustra',
          artist: 'Alain Bashung',
          album_art_url: albumPlaceholder,
          is_playing: true,
          client_name: 'Leo’s iPhone',
          album_art_width: FAVICON_COVER_PX
        }
      ),
      active(
        'airplay',
        'Active, sender stopped',
        'The route stays connected and the backend keeps the stale cover, but is_playing flips false — the gate drops to the card rather than freezing a cover over audio that no longer plays. Same screen as the scenario above: the card reads neither field, only the sender name.',
        {
          title: 'Ainsi parlait Zarathoustra',
          artist: 'Alain Bashung',
          album_art_url: albumPlaceholder,
          is_playing: false,
          client_name: 'Leo’s iPhone',
          album_art_width: TRUSTED_COVER_PX
        }
      ),
      active('airplay', 'Playing', 'All four conditions met. The source bar carries the sender name; position is corrected only every 30 s, so useSourceProgress interpolates between broadcasts.', {
        title: 'Ainsi parlait Zarathoustra',
        artist: 'Alain Bashung',
        album_art_url: albumPlaceholder,
        is_playing: true,
        client_name: 'Leo’s iPhone',
        album_art_width: TRUSTED_COVER_PX,
        position: 41000,
        duration: 297000
      }),
      errored(
        'airplay',
        'shairport-sync failing to start is the common case — the port is taken, or the ALSA device is busy. The sender name goes with it, so the source that most depends on naming its sender ends up on the anonymous line.',
        'shairport-sync failed to start'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}dlna`,
    source: 'dlna',
    title: 'DLNA',
    family: 'B — passive player',
    uses: 'AudioSourceStatus · AudioPlayerFull (showControls false, showProgress)',
    via: 'dispatcher',
    summary:
      'Same untrusted-sender gate as AirPlay, and the same player — the difference is identity: UPnP exposes no "who is casting", so currentDeviceName hard-returns empty and the card needs its own active branch to avoid falling back to "waiting". client_name here is the static "DLNA" label the player’s source bar reads, not a controller name.',
    scenarios: [
      starting('dlna'),
      waiting('dlna', 'Waiting', 'The UPnP renderer is advertised, no controller has pushed anything.'),
      active(
        'dlna',
        'Active, no cover',
        'A controller pushing a bare title: no album_art_width, so the gate declines. The dedicated DLNA active branch prints "DLNA / playing" — without it this would read as "waiting" while audio was flowing.',
        { title: 'Untitled', is_playing: true, client_name: 'DLNA' }
      ),
      active('dlna', 'Playing', 'A full-fat controller: cover above the floor, audio flowing. Read-only bar plus the static DLNA source bar.', {
        title: 'Hammers',
        artist: 'Nils Frahm',
        album_art_url: albumPlaceholder,
        is_playing: true,
        client_name: 'DLNA',
        album_art_width: TRUSTED_COVER_PX,
        position: 88000,
        duration: 331000
      }),
      errored(
        'dlna',
        'The renderer failing to advertise. DLNA has no device name to lose, so what is left is the bare waiting line — the same screen its idle state shows, for the opposite reason.',
        'UPnP renderer failed to advertise'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}cd`,
    source: 'cd',
    title: 'CD',
    family: 'C — active player',
    uses: 'AudioSourceStatus · AudioPlayerFull (+ both slots)',
    via: 'dispatcher',
    summary:
      'The widest state matrix of the ten, and the one source whose rich-display rule ignores source_state entirely: a disc that is loaded and ready shows the player whether it is playing or idle. Three of the screens below exist nowhere in the backend enum — AudioSourceView derives no_drive, loading_disc and ejecting from metadata — which is exactly why those scenarios are named by the fields they set rather than by a state.',
    scenarios: [
      starting('cd'),
      waiting(
        'cd',
        'No drive',
        'drive_connected false — the source is active but the hardware is missing. rawSourceState derives the pseudo-state "no_drive" from this one field.',
        { drive_connected: false }
      ),
      waiting(
        'cd',
        'Drive empty',
        'Drive present, no disc: the plain idle line, and the only CD scenario that reaches the generic waiting branch.',
        { drive_connected: true }
      ),
      waiting(
        'cd',
        'Reading the disc',
        'disc_present with no cache_ready/disc_id yet — the MusicBrainz lookup is in flight. Pseudo-state "loading_disc", spinner in place of the icon. A fallback DiscInfo always sets disc_id, so this window cannot hang.',
        { drive_connected: true, disc_present: true }
      ),
      waiting(
        'cd',
        'Disc ready, not playing',
        'source_state is still "waiting" and the player shows anyway — the CD branch of hasRichDisplay never looks at the state. The backend projects the idle view here: track 1’s title and the disc artist, with position and duration zeroed so the bar stays hidden until a session is live.',
        {
          ...CD_DISC,
          drive_connected: true,
          is_playing: false,
          title: 'Keep',
          artist: 'Nils Frahm',
          album_art_url: albumPlaceholder,
          current_track: 1,
          position: 0,
          duration: 0
        }
      ),
      active('cd', 'Playing', 'AudioPlayerFull with the full transport. hasNext is false on the last track, mirroring the backend’s "next" no-op.', {
        ...CD_DISC,
        drive_connected: true,
        title: 'Kind',
        artist: 'Nils Frahm',
        album_art_url: albumPlaceholder,
        is_playing: true,
        current_track: 3,
        position: 74000,
        duration: 268000
      }),
      waiting(
        'cd',
        'Ejecting',
        'ejecting wins over a ready disc in hasRichDisplay, so the player gives way to the card mid-eject rather than lingering over a disc that is leaving.',
        { ...CD_DISC, drive_connected: true, ejecting: true }
      ),
      errored(
        'cd',
        'A read failure mid-disc. The metadata goes with it, so the player gives way to the card — and the card, having no error branch, prints the bare waiting line over a disc that is still in the drive.',
        'cdparanoia read failed'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}bluetooth`,
    source: 'bluetooth',
    title: 'Bluetooth',
    family: 'A — mute receiver',
    uses: 'AudioSourceStatus only',
    via: 'dispatcher',
    summary:
      'A mute receiver: no rich metadata, so hasRichDisplay returns false for every record and the status card is the whole UI. It owns one of the card’s two CTAs — disconnect, armed only while active — and it is one of the three sources that put a real name on the second line, from metadata.device_name.',
    scenarios: [
      starting('bluetooth'),
      waiting('bluetooth', 'Waiting', 'Discoverable, nothing paired-and-connected. No CTA in this state.'),
      active('bluetooth', 'Connected', 'device_name fills the second line and the disconnect CTA appears. It routes through sendCommand, so here it reports to the event log.', {
        device_name: 'Leo’s iPhone'
      }),
      errored(
        'bluetooth',
        'bluealsa dying takes the device name with it, so the CTA disappears with the line it belonged to — the card cannot offer a disconnect from a source it can no longer address.',
        'bluealsa is not running'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}mac`,
    source: 'mac',
    title: 'Mac (ROC)',
    family: 'A — mute receiver',
    uses: 'AudioSourceStatus only',
    via: 'dispatcher',
    summary:
      'The other mute receiver, and the only source whose device name is an array: several Macs can stream over ROC at once, and formatDeviceNames joins them across two lines. Its "disconnect" is a no-op in the store, so the card shows no CTA at all — the sender stops from its own side.',
    scenarios: [
      starting('mac'),
      waiting('mac', 'Waiting', 'roc-recv is listening; no Mac is sending.'),
      active('mac', 'One Mac streaming', 'client_names is an array even with a single entry, which is why the name spells its length rather than its presence.', {
        client_names: ['Leo’s MacBook']
      }),
      active('mac', 'Two Macs streaming', 'The case the array exists for — formatDeviceNames breaks the second line, which is why status-line-2 carries white-space: pre-line.', {
        client_names: ['Leo’s MacBook', 'Studio iMac']
      }),
      errored(
        'mac',
        'roc-recv failing to bind its port. The sender list empties, so the two-line "audio received from" screen collapses to the bare waiting line.',
        'roc-recv failed to bind'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}radio`,
    source: 'radio',
    title: 'Radio',
    family: 'C — active player, with a browser',
    uses: 'AudioSourceStatus · AudioSourceLayout + AudioPlayer',
    via: 'browser',
    summary:
      'hasRichDisplay returns true unconditionally for the three browser sources — their own layout handles empty and loading — so the status card is only ever reached while transitioning, and an errored radio would still draw the full browser as if nothing had happened. The player is the one with no progress bar at all: a live stream has no duration, which is also why its command is resume_playback (re-tune) rather than resume.',
    scenarios: [
      starting('radio'),
      browsing('radio', 'Favourites loading', 'favoritesInitialized false — the grid is sixteen SkeletonStationCards. It is the state a cold boot opens on, and the only one where the count on screen is a guess rather than the truth.', {
        condition: ['favoritesInitialized=false'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        props: { isLoading: true },
        seed: { radio: { favoritesInitialized: false } },
        player: null
      }),
      browsing('radio', 'No favourites yet', 'Initialised and empty, which is a different thing from loading and is why favoritesInitialized exists: MessageContent says there is nothing rather than shimmering for ever at a unit that simply has no favourites.', {
        condition: ['stations=0'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        api: { '/api/radio/stations': { stations: [] } },
        prime: [['radio', 'loadStations', true]],
        player: null
      }),
      browsing('radio', 'Favourites, mixed artwork', 'The real favourites grid, six real StationCards in the image variant, split across the card\'s two branches: two stations carry an image, four fall back to the generated monogram. The third case — an external logo fetched through /api/radio/favicon — stays out, see RADIO_FAVOURITES.', {
        condition: ['stations=6'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: null
      }),
      browsing('radio', 'Tuning a station', 'bufferingStationId marks one card while the stream opens; the rest of the grid stays live, so a second tap goes somewhere rather than being swallowed by a full-screen loader.', {
        condition: ['bufferingStationId'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        props: { bufferingStationId: 'st-nova' },
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: null
      }),
      browsing('radio', 'Playing a station', 'The pane animates in and the content gives up 340 px; the playing card is marked in the grid. No track recognised yet, so the station is the whole info block and its own image is the artwork. No #progress slot either — a live stream has no duration, so the bar would have nothing to show.', {
        condition: ['currentStation', 'artwork'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        props: { isPlaying: true, currentStation: RADIO_STATION_WITH_IMAGE },
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: {
          // Both halves of the same station: the grid card and the pane resolve
          // their image from one favicon, exactly as RadioSource does through
          // getFaviconUrl(displayStation.favicon).
          station: { name: RADIO_STATION_WITH_IMAGE.name, artwork: RADIO_STATION_WITH_IMAGE.favicon },
          track: null,
          isPlaying: true,
          controls: { favorite: true }
        }
      }),
      browsing('radio', 'Playing, no station image', 'The same pane for a station that carries no image: AudioPlayer takes fallbackName and renders the generated monogram, in the artwork frame and blurred behind it both. Most of a directory looks like this, which is why the avatar is a branch rather than a placeholder.', {
        condition: ['currentStation'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        props: { isPlaying: true, currentStation: RADIO_STATION_NO_IMAGE },
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: {
          station: { name: RADIO_STATION_NO_IMAGE.name, artwork: RADIO_STATION_NO_IMAGE.favicon },
          track: null,
          isPlaying: true,
          controls: { favorite: false }
        }
      }),
      browsing('radio', 'Track detected', 'Shazam matched the stream: the station drops to the kicker and the track takes the title. The station image is what the kicker icon shows — and on the Phone viewport the same image slides in behind the cover, so switch the viewport to see the pair overlap.', {
        condition: ['currentStation', 'artwork', 'track'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        props: { isPlaying: true, currentStation: RADIO_STATION_WITH_IMAGE },
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: {
          station: { name: RADIO_STATION_WITH_IMAGE.name, artwork: RADIO_STATION_WITH_IMAGE.favicon },
          // The kicker (and the mobile badge) are gated on the *track* having
          // artwork, not on the station having any — so this is the only shape
          // that reaches either.
          track: { title: 'Ainsi parlait Zarathoustra', artist: 'Alain Bashung', artwork: albumPlaceholder },
          isPlaying: true,
          controls: { favorite: false }
        }
      })
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}podcast`,
    source: 'podcast',
    title: 'Podcasts',
    family: 'C — active player, with a browser',
    uses: 'AudioSourceStatus · AudioSourceLayout + AudioPlayer',
    via: 'browser',
    summary:
      'The same two parts as Radio, with a progress bar and a swipe gesture — but swipeEnabled without a tracks queue, so the swipe seeks (±15/30 s) instead of skipping and no text carousel is built. Its header is the one that changes shape as you descend: title, subtitle and the back affordance are all driven by the current view.',
    scenarios: [
      starting('podcast'),
      browsing('podcast', 'Browsing the charts', 'The home view, three header actions and no back. Unlike the other two browsers this one fetches from the component rather than a store, so its charts are served as an HTTP fixture — the real loadData() runs.', {
        condition: ['results'],
        layout: PODCAST_HEADER,
        view: 'podcast-home',
        api: {
          '/api/podcast/discover/top-charts': { results: PODCAST_CHARTS },
          '/api/podcast/subscriptions': { subscriptions: PODCAST_SUBSCRIPTIONS },
          '/api/podcast/playback-speeds': { speeds: PODCAST_SPEEDS }
        },
        prime: [['podcast', 'loadPlaybackSpeeds']],
        player: null
      }),
      browsing('podcast', 'Charts unreachable', 'The Podcast Index is the one source that depends on the internet rather than the LAN, so the backend answers `network_error` instead of failing — a distinct branch from "no results", and the only one that tells the user why the chart is empty.', {
        condition: ['network_error'],
        layout: PODCAST_HEADER,
        view: 'podcast-home',
        api: {
          '/api/podcast/discover/top-charts': { network_error: true },
          '/api/podcast/subscriptions': { subscriptions: PODCAST_SUBSCRIPTIONS },
          '/api/podcast/playback-speeds': { speeds: PODCAST_SPEEDS }
        },
        prime: [['podcast', 'loadPlaybackSpeeds']],
        player: null
      }),
      browsing('podcast', 'Playing an episode', 'Player pane in, progress bar drawn. On the Phone viewport this becomes a mini-bar teleported to body, with the bar as a 2 px strip on the card’s bottom edge.', {
        condition: ['episodeName'],
        layout: PODCAST_HEADER,
        view: 'podcast-home',
        api: {
          '/api/podcast/discover/top-charts': { results: PODCAST_CHARTS },
          '/api/podcast/subscriptions': { subscriptions: PODCAST_SUBSCRIPTIONS },
          '/api/podcast/playback-speeds': { speeds: PODCAST_SPEEDS }
        },
        prime: [['podcast', 'loadPlaybackSpeeds']],
        player: {
          podcastName: 'Le Code a changé',
          episodeName: 'Épisode 214',
          // Left unset: the source passes displayEpisode.image_url, and with no
          // episode image AudioPlayer falls back to its bundled placeholder,
          // which is what an episode with no artwork shows on the unit.
          episodeImage: null,
          isPlaying: true,
          progress: { currentPosition: 812000, duration: 2940000, progressPercentage: 27.6 }
        }
      })
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}music_library`,
    source: 'music_library',
    title: 'Music Library',
    family: 'C — active player, with a browser',
    uses: 'AudioSourceStatus · AudioSourceLayout + AudioPlayer',
    via: 'browser',
    summary:
      'The richest of the three: the only source that passes tracks + currentIndex, which is what turns the mobile swipe into the three-cell text carousel, and the only one where hasEntityLinks is true — the artwork and the secondary line become links to the album and the artist. Both are Phone-viewport behaviours; the docked desktop card shows the full transport row instead.',
    scenarios: [
      starting('music_library'),
      browsing('music_library', 'One USB key', 'A single storage space, and so no storage picker at all: with one library every tab already shows all of it, and a one-button ButtonGroup would be a control with nothing to choose. The tabs below are the whole chrome.', {
        condition: ['storages=1'],
        layout: ML_HEADER,
        view: 'ml-home',
        ...mlSetup({ storages: ML_STORAGE_USB, albums: ML_ALBUMS, activeLibraryId: 1 }),
        player: null
      }),
      browsing('music_library', 'USB + two NAS shares', 'Three storage spaces, so the picker appears above the tabs — the only case that draws it. Every catalog read is scoped to one library_id, which is why the row has to come before the first tab loads rather than beside it.', {
        condition: ['storages=3'],
        layout: ML_HEADER,
        view: 'ml-home',
        ...mlSetup({ storages: ML_STORAGE_MIXED, albums: ML_ALBUMS, activeLibraryId: 2 }),
        player: null
      }),
      browsing('music_library', 'Building the library', 'A share just mounted: storages answer with scanning true and the albums tab is still empty, so the empty state becomes "building library…" with a spinner rather than "no music", which would read as a mistake the user made.', {
        condition: ['scanning'],
        layout: ML_HEADER,
        view: 'ml-home',
        ...mlSetup({ storages: ML_STORAGE_MIXED, albums: [], scanning: true, activeLibraryId: 2 }),
        player: null
      }),
      browsing('music_library', 'Storage with no music', 'Scan finished and found nothing — the other half of the pair above, and the reason the scan flag rides on the storages response rather than being inferred from an empty catalog.', {
        condition: ['albums=0'],
        layout: ML_HEADER,
        view: 'ml-home',
        ...mlSetup({ storages: ML_STORAGE_USB, albums: [], activeLibraryId: 1 }),
        player: null
      }),
      browsing('music_library', 'Playing, queue loaded', 'tracks + currentIndex are passed, so on the Phone viewport a horizontal swipe slides the neighbouring titles in locally rather than waiting for the backend echo.', {
        condition: ['tracks=3'],
        layout: ML_HEADER,
        view: 'ml-home',
        ...mlSetup({ storages: ML_STORAGE_MIXED, albums: ML_ALBUMS, activeLibraryId: 2 }),
        player: {
          // What displayTrack projects: title, artist, cover — nothing else
          // reaches this player's info block.
          title: 'Says',
          artist: 'Nils Frahm',
          artwork: albumPlaceholder,
          isPlaying: true,
          currentIndex: 1,
          tracks: [
            { title: 'Ambre', artist: 'Nils Frahm' },
            { title: 'Says', artist: 'Nils Frahm' },
            { title: 'Hammers', artist: 'Nils Frahm' }
          ],
          progress: { currentPosition: 192000, duration: 511000, progressPercentage: 37.6 },
          controls: { shuffle: true, starred: true, hasNext: true }
        }
      })
    ]
  }
];

/** The source page for a `source:<id>` selection, or undefined. */
export function sourcePageById(id) {
  return SOURCE_PAGES.find(page => page.id === id);
}

/** True for a `?c=` value that names a source page rather than a component. */
export function isSourcePageId(id) {
  return typeof id === 'string' && id.startsWith(SOURCE_PAGE_PREFIX);
}

/** Every envelope the ten pages can emit, for the checks against the models. */
export function allEvents() {
  return SOURCE_PAGES.flatMap(page => page.scenarios.flatMap(entry => entry.events));
}

/**
 * Every fabricated metadata record. The guardrail checks a record's own keys,
 * not nested ones, so handing it the snapshots alone would leave every metadata
 * field unguarded — which is the half that actually drifts.
 */
export function allMetadata() {
  return allEvents().map(event => event.data.full_state.metadata);
}

/** The state a scenario settles on, as the app reads it off the wire. */
export function settledState(entry) {
  return entry.events[entry.events.length - 1].data.full_state;
}
