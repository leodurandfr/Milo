// frontend/src/components/gallery/sources.js
/**
 * The ten audio sources, as pages of the gallery — the second axis.
 *
 * The catalogue next door answers "what does this component do"; this file
 * answers "what does a source look like, in every state it can reach". The two
 * are not the same question: AudioPlayerFull serves five sources and none of
 * them shows the same thing, while CD alone reaches seven states across three
 * different components. A reader after either one had to assemble it from four
 * component pages and useRichDisplay's source code.
 *
 * ## A scenario is a snapshot, not a drawing
 *
 * Every scenario below is one `unifiedAudioStore.systemState` record — exactly
 * the shape the backend broadcasts. The canvas writes it into its own store and
 * mounts the real `AudioSourceView`, so *the app's own rules* decide what
 * appears: `useRichDisplay()` picks the player or the status card,
 * `rawSourceState` derives CD's three pseudo-states, `currentDeviceName` maps
 * the per-source identity field. Nothing here draws a screen — which is the
 * whole point, because a drawing is a second frontend and this is a fixture.
 *
 * It follows that a scenario cannot lie about a gate: `airplay/small-cover`
 * shows the status card because 128 is genuinely below
 * UNTRUSTED_SENDER_MIN_ARTWORK_PX, and it will start showing the player the day
 * someone changes that constant. That is the property worth having.
 *
 * The records are 100% fabricated on purpose. Reading the Bluetooth states must
 * not mean switching the appliance to Bluetooth, and the canvas iframe holds
 * its own Pinia, so nothing here can reach the unit — `CanvasApp` also replaces
 * `sendCommand`, which is the single path every action on these seven sources
 * takes (cdStore's eject and the Bluetooth disconnect included).
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
 * What a scenario supplies is what the backend would have, in three shapes,
 * because the stores leave three different ways in:
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
 * against ALL_AUDIO_SOURCES and every fabricated key against the files that
 * read it. `SourceStage.vue` is what turns a scenario into a mounted component.
 */
import albumPlaceholder from '@/assets/images/album-placeholder.svg';

/** Prefix that tells a source page apart from a catalogue entry in `?c=`. */
export const SOURCE_PAGE_PREFIX = 'source:';

/** A cover big enough to clear the untrusted-sender gate, and one that is not. */
const TRUSTED_COVER_PX = 600;
const FAVICON_COVER_PX = 128;

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
 * The files that read what these snapshots write. Checked key by key by the
 * guardrail, which is what stops a fixture outliving the field it fabricates.
 */
export const SNAPSHOT_READERS = [
  'components/audio/AudioSourceView.vue',
  'components/audio/AudioPlayerFull.vue',
  'composables/useRichDisplay.js',
  'composables/useSourceProgress.js',
  'utils/playbackBuffering.js',
  'stores/cdStore.js'
];

/**
 * The three browsers' headers, copied from their own call sites as *i18n keys*
 * rather than as text: the header is half of what "the real rendering" means,
 * and a hard-coded English string on a unit running in French would be a
 * different screen from the one being documented.
 */
const RADIO_HEADER = { titleKey: 'audioSources.radioSource.favoritesTitle', actions: ['search'] };
const PODCAST_HEADER = { titleKey: 'podcasts.podcasts', actions: ['heartOff', 'search', 'queue'] };
const ML_HEADER = { titleKey: 'audioSources.musicLibrary', actions: ['queue', 'search'] };

/** `transitioning` is what every source's first state actually looks like. */
function starting(source) {
  return {
    id: 'starting',
    label: 'Starting',
    note: 'transitioning — the status card takes over whatever the source is, and the spinner replaces its icon. Held for 500 ms minimum so a fast backend never flashes it.',
    systemState: { active_source: source, source_state: 'starting', transitioning: true, metadata: {} }
  };
}

/** Nothing is playing yet: the source is up and idle. */
function waiting(source, note, metadata = {}) {
  return {
    id: 'waiting',
    label: 'Waiting',
    note,
    systemState: { active_source: source, source_state: 'waiting', transitioning: false, metadata }
  };
}

function active(source, id, label, note, metadata) {
  return {
    id,
    label,
    note,
    systemState: { active_source: source, source_state: 'active', transitioning: false, metadata }
  };
}

/** A browser source's scenario: the store snapshot plus the browser's setup. */
function browsing(source, id, label, note, browser) {
  return {
    id,
    label,
    note,
    systemState: { active_source: source, source_state: 'active', transitioning: false, metadata: {} },
    browser
  };
}

/**
 * Radio stations as the favourites grid receives them, all with an empty
 * `favicon` — which is the one thing here that does not match a real unit, and
 * is deliberate.
 *
 * A station logo is never loaded directly: `getFaviconUrl` rewrites every
 * non-empty favicon to `/api/radio/favicon?url=…`, a backend endpoint that
 * fetches the logo from the station's own host and caches it. That is an
 * outbound fetch per card, on a page whose whole point is to render the same
 * way every time and without touching the unit — so the fixture takes the
 * other branch, and the grid shows StationCard's generated avatar.
 *
 * That branch is worth seeing on its own account: it is deterministic per name
 * (same station, same colour and glyph every time) and it is what most of a
 * station directory actually lands on, since a directory entry only carries a
 * usable logo some of the time. What the gallery cannot show is the proxied
 * logo beside it.
 */
const RADIO_FAVOURITES = [
  { id: 'st-nova', name: 'Radio Nova', favicon: '', countrycode: 'FR', genre: 'eclectic' },
  { id: 'st-fip', name: 'FIP', favicon: '', countrycode: 'FR', genre: 'eclectic' },
  { id: 'st-inter', name: 'France Inter', favicon: '', countrycode: 'FR', genre: 'talk' },
  { id: 'st-musique', name: 'France Musique', favicon: '', countrycode: 'FR', genre: 'classical' },
  { id: 'st-tsf', name: 'TSF Jazz', favicon: '', countrycode: 'FR', genre: 'jazz' },
  { id: 'st-nts', name: 'NTS Radio 1', favicon: '', countrycode: 'GB', genre: 'electronic' }
];

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
    // survive a scenario change — so without forcing the reads, picking
    // `scanning` after `one-usb` would show the previous scenario's albums and
    // its storage picker. `storagesLoaded` is private to the store, which is
    // why this is a prime rather than four more seeded flags.
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
      'The only Connect source Milō drives back: AudioPlayerFull with the full transport. Its rich display is gated on title + artist alone — Spotify is a trusted metadata provider, so no cover-quality check — which is why an active session with no metadata yet falls through every branch of the status card and prints the bare "waiting" line.',
    scenarios: [
      starting('spotify'),
      waiting('spotify', 'Connected to go-librespot, no phone has picked the speaker yet.'),
      active(
        'spotify',
        'active-no-metadata',
        'Active, no metadata',
        'The gap between the session opening and the first track event. hasRichDisplay wants title AND artist, so the card stays — and with no device name for Spotify it falls through to the single "waiting" line. A real, reachable branch.',
        { is_playing: true }
      ),
      active('spotify', 'playing', 'Playing', 'Rich display earned: AudioPlayerFull, progress bar and transport. The buttons report to the event log instead of reaching the unit.', {
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: albumPlaceholder,
        is_playing: true,
        position: 192000,
        duration: 511000
      }),
      active('spotify', 'paused', 'Paused', 'Same record, is_playing false — the glyph flips and useSourceProgress stops ticking.', {
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: albumPlaceholder,
        is_playing: false,
        position: 192000,
        duration: 511000
      }),
      active('spotify', 'buffering', 'Buffering', 'is_buffering swaps the play/pause glyph for a spinner (isSourceBuffering). The bar keeps its last position.', {
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: albumPlaceholder,
        is_playing: true,
        is_buffering: true,
        position: 0,
        duration: 511000
      })
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
      waiting('qobuz', 'Account connected, waiting for the app to pick the speaker.'),
      {
        id: 'no-account',
        label: 'Waiting, no account',
        note: 'account_authenticated false — the only path to the second CTA in AudioSourceStatus. Tapping it calls inject("openSettings"), which is absent here, so it no-ops.',
        systemState: {
          active_source: 'qobuz',
          source_state: 'waiting',
          transitioning: false,
          metadata: { account_authenticated: false }
        }
      },
      active(
        'qobuz',
        'active-pre-metadata',
        'Active, before now_playing',
        'The proxy exposes no controller identity, so currentDeviceName is empty and the generic active branch prints "Qobuz / playing" rather than falling back to "waiting".',
        { is_playing: true }
      ),
      active('qobuz', 'playing', 'Playing', 'Trusted CDN cover, so no album_art_width gate — title + artist is enough. Read-only bar above the source bar.', {
        title: 'Ambre',
        artist: 'Nils Frahm',
        album_art_url: albumPlaceholder,
        is_playing: true,
        client_name: 'Milō',
        position: 64000,
        duration: 264000
      })
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
      'The untrusted-sender gate lives here: title, artist, audio actually flowing AND a cover above UNTRUSTED_SENDER_MIN_ARTWORK_PX (300). Browser audio pushes a favicon-sized image, which is the case the gate exists for — the two failing scenarios below are the ones that keep the status card, and they are the reason AirPlay is the source whose device name matters most.',
    scenarios: [
      starting('airplay'),
      waiting('airplay', 'shairport-sync advertising, nobody streaming.'),
      active(
        'airplay',
        'small-cover',
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
        'sender-stopped',
        'Active, sender stopped',
        'The route stays connected and the backend keeps the stale cover, but is_playing flips false — the gate drops to the card rather than freezing a cover over audio that no longer plays.',
        {
          title: 'Ainsi parlait Zarathoustra',
          artist: 'Alain Bashung',
          album_art_url: albumPlaceholder,
          is_playing: false,
          client_name: 'Leo’s iPhone',
          album_art_width: TRUSTED_COVER_PX
        }
      ),
      active('airplay', 'playing', 'Playing', 'All four conditions met. The source bar carries the sender name; position is corrected only every 30 s, so useSourceProgress interpolates between broadcasts.', {
        title: 'Ainsi parlait Zarathoustra',
        artist: 'Alain Bashung',
        album_art_url: albumPlaceholder,
        is_playing: true,
        client_name: 'Leo’s iPhone',
        album_art_width: TRUSTED_COVER_PX,
        position: 41000,
        duration: 297000
      })
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
      waiting('dlna', 'The UPnP renderer is advertised, no controller has pushed anything.'),
      active(
        'dlna',
        'active-no-cover',
        'Active, no cover',
        'A controller pushing a bare title: no album_art_width, so the gate declines. The dedicated DLNA active branch prints "DLNA / playing" — without it this would read as "waiting" while audio was flowing.',
        { title: 'Untitled', is_playing: true, client_name: 'DLNA' }
      ),
      active('dlna', 'playing', 'Playing', 'A full-fat controller: cover above the floor, audio flowing. Read-only bar plus the static DLNA source bar.', {
        title: 'Hammers',
        artist: 'Nils Frahm',
        album_art_url: albumPlaceholder,
        is_playing: true,
        client_name: 'DLNA',
        album_art_width: TRUSTED_COVER_PX,
        position: 88000,
        duration: 331000
      })
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
      'The widest state matrix of the ten, and the one source whose rich-display rule ignores source_state entirely: a disc that is loaded and ready shows the player whether it is playing or idle. The three pseudo-states below (no_drive, loading_disc, ejecting) exist nowhere in the backend enum — AudioSourceView derives them from metadata, and they are the only way to reach three of AudioSourceStatus’s lines.',
    scenarios: [
      starting('cd'),
      waiting('cd', 'drive_connected false — the source is active but the hardware is missing. Derived pseudo-state "no_drive".', { drive_connected: false }),
      {
        id: 'no-disc',
        label: 'Drive empty',
        note: 'Drive present, no disc: the plain idle line.',
        systemState: {
          active_source: 'cd',
          source_state: 'waiting',
          transitioning: false,
          metadata: { drive_connected: true }
        }
      },
      {
        id: 'loading-disc',
        label: 'Reading the disc',
        note: 'disc_present with no cache_ready/disc_id yet — the MusicBrainz lookup is in flight. Pseudo-state "loading_disc", spinner in place of the icon. A fallback DiscInfo always sets disc_id, so this window cannot hang.',
        systemState: {
          active_source: 'cd',
          source_state: 'waiting',
          transitioning: false,
          metadata: { drive_connected: true, disc_present: true }
        }
      },
      {
        id: 'disc-idle',
        label: 'Disc ready, not playing',
        note: 'source_state is still "waiting" and the player shows anyway — the CD branch of hasRichDisplay never looks at the state. The backend projects the idle view here: track 1’s title and the disc artist, with position and duration zeroed so the bar stays hidden until a session is live.',
        systemState: {
          active_source: 'cd',
          source_state: 'waiting',
          transitioning: false,
          metadata: {
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
        }
      },
      active('cd', 'playing', 'Playing', 'AudioPlayerFull with the full transport. hasNext is false on the last track, mirroring the backend’s "next" no-op.', {
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
      {
        id: 'ejecting',
        label: 'Ejecting',
        note: 'ejecting wins over a ready disc in hasRichDisplay, so the player gives way to the card mid-eject rather than lingering over a disc that is leaving.',
        systemState: {
          active_source: 'cd',
          source_state: 'waiting',
          transitioning: false,
          metadata: { ...CD_DISC, drive_connected: true, ejecting: true }
        }
      }
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
      waiting('bluetooth', 'Discoverable, nothing paired-and-connected. No CTA in this state.'),
      active('bluetooth', 'connected', 'Connected', 'device_name fills the second line and the disconnect CTA appears. It routes through sendCommand, so here it reports to the event log.', {
        device_name: 'Leo’s iPhone'
      })
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
      waiting('mac', 'roc-recv is listening; no Mac is sending.'),
      active('mac', 'one-sender', 'One Mac streaming', 'client_names is an array even with a single entry.', {
        client_names: ['Leo’s MacBook']
      }),
      active('mac', 'two-senders', 'Two Macs streaming', 'The case the array exists for — formatDeviceNames breaks the second line, which is why status-line-2 carries white-space: pre-line.', {
        client_names: ['Leo’s MacBook', 'Studio iMac']
      })
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
      'hasRichDisplay returns true unconditionally for the three browser sources — their own layout handles empty and loading — so the status card is only ever reached while transitioning. The player is the one with no progress bar at all: a live stream has no duration, which is also why its command is resume_playback (re-tune) rather than resume.',
    scenarios: [
      starting('radio'),
      browsing('radio', 'favourites-loading', 'Favourites loading', 'favoritesInitialized false — the grid is sixteen SkeletonStationCards. It is the state a cold boot opens on, and the only one where the count on screen is a guess rather than the truth.', {
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        props: { isLoading: true },
        seed: { radio: { favoritesInitialized: false } },
        player: null
      }),
      browsing('radio', 'no-favourites', 'No favourites yet', 'Initialised and empty, which is a different thing from loading and is why favoritesInitialized exists: MessageContent says there is nothing rather than shimmering for ever at a unit that simply has no favourites.', {
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        api: { '/api/radio/stations': { stations: [] } },
        prime: [['radio', 'loadStations', true]],
        player: null
      }),
      browsing('radio', 'favourites', 'Favourites, mixed artwork', 'The real favourites grid, six real StationCards in the image variant. Every one takes the generated-avatar branch: a station logo is a backend proxy fetch (getFaviconUrl → /api/radio/favicon), which this page does not make — see RADIO_FAVOURITES for why, and for what that leaves unshown.', {
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: null
      }),
      browsing('radio', 'buffering', 'Tuning a station', 'bufferingStationId marks one card while the stream opens; the rest of the grid stays live, so a second tap goes somewhere rather than being swallowed by a full-screen loader.', {
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        props: { bufferingStationId: 'st-nova' },
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: null
      }),
      browsing('radio', 'playing-station', 'Playing a station', 'The pane animates in and the content gives up 340 px; the playing card is marked in the grid. No #progress slot — a live stream has no duration, so the bar would have nothing to show.', {
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        props: { isPlaying: true, currentStation: RADIO_FAVOURITES[0] },
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: {
          // No track recognised yet: the station is the whole info block, and
          // the artwork is the station's own (empty favicon → generated avatar).
          station: { name: 'Radio Nova', artwork: '' },
          track: null,
          isPlaying: true,
          controls: { favorite: true }
        }
      }),
      browsing('radio', 'track-detected', 'Track detected', 'Shazam matched the stream: the station drops to the kicker and the track takes the title. On the Phone viewport the station icon also slides in behind the cover — switch the viewport to see it.', {
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        props: { isPlaying: true, currentStation: RADIO_FAVOURITES[0] },
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: {
          station: { name: 'Radio Nova', artwork: '' },
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
      browsing('podcast', 'browsing', 'Browsing the charts', 'The home view, three header actions and no back. Unlike the other two browsers this one fetches from the component rather than a store, so its charts are served as an HTTP fixture — the real loadData() runs.', {
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
      browsing('podcast', 'charts-offline', 'Charts unreachable', 'The Podcast Index is the one source that depends on the internet rather than the LAN, so the backend answers `network_error` instead of failing — a distinct branch from "no results", and the only one that tells the user why the chart is empty.', {
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
      browsing('podcast', 'playing', 'Playing an episode', 'Player pane in, progress bar drawn. On the Phone viewport this becomes a mini-bar teleported to body, with the bar as a 2 px strip on the card’s bottom edge.', {
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
      browsing('music_library', 'one-usb', 'One USB key', 'A single storage space, and so no storage picker at all: with one library every tab already shows all of it, and a one-button ButtonGroup would be a control with nothing to choose. The tabs below are the whole chrome.', {
        layout: ML_HEADER,
        view: 'ml-home',
        ...mlSetup({ storages: ML_STORAGE_USB, albums: ML_ALBUMS, activeLibraryId: 1 }),
        player: null
      }),
      browsing('music_library', 'usb-and-shares', 'USB + two NAS shares', 'Three storage spaces, so the picker appears above the tabs — the only case that draws it. Every catalog read is scoped to one library_id, which is why the row has to come before the first tab loads rather than beside it.', {
        layout: ML_HEADER,
        view: 'ml-home',
        ...mlSetup({ storages: ML_STORAGE_MIXED, albums: ML_ALBUMS, activeLibraryId: 2 }),
        player: null
      }),
      browsing('music_library', 'scanning', 'Building the library', 'A share just mounted: storages answer with scanning true and the albums tab is still empty, so the empty state becomes "building library…" with a spinner rather than "no music", which would read as a mistake the user made.', {
        layout: ML_HEADER,
        view: 'ml-home',
        ...mlSetup({ storages: ML_STORAGE_MIXED, albums: [], scanning: true, activeLibraryId: 2 }),
        player: null
      }),
      browsing('music_library', 'empty', 'Storage with no music', 'Scan finished and found nothing — the other half of the pair above, and the reason the scan flag rides on the storages response rather than being inferred from an empty catalog.', {
        layout: ML_HEADER,
        view: 'ml-home',
        ...mlSetup({ storages: ML_STORAGE_USB, albums: [], activeLibraryId: 1 }),
        player: null
      }),
      browsing('music_library', 'playing', 'Playing, queue loaded', 'tracks + currentIndex are passed, so on the Phone viewport a horizontal swipe slides the neighbouring titles in locally rather than waiting for the backend echo.', {
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

/**
 * Every fabricated record, at both levels: the snapshot itself and the metadata
 * inside it. The guardrail checks a record's own keys, not nested ones, so
 * handing it only the snapshots would leave every metadata field unguarded —
 * which is the half that actually drifts.
 */
export function allRecords() {
  return SOURCE_PAGES.flatMap(page =>
    page.scenarios.flatMap(scenario => [scenario.systemState, scenario.systemState.metadata])
  );
}
