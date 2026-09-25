// frontend/src/components/gallery/sources.js
/**
 * The 10 audio sources, as pages of the gallery — the second axis.
 *
 * The catalogue next door answers "what does this component do"; this file
 * answers "what does a source look like, in every state it can reach". The two
 * are not the same question: AudioPlayerFull serves six sources and none of
 * them shows the same thing, while CD alone moves between that player and the
 * status card on a disc the state describes whether or not it plays. A reader
 * after either one had to assemble it from four component pages and
 * useRichDisplay's source code.
 *
 * ## A scenario is a stimulus, not a state someone named
 *
 * Every scenario below is a list of **WebSocket events in the backend's own
 * wire shape** — the envelope `ws_events.py::WsEvent.to_envelope` builds around
 * a `source/state`, whose data is the whole AudioState (`audio_wire.py`), every
 * key present. `SourceStage` hands each one to `unifiedAudioStore.updateState`,
 * which is the exact handler `App.vue` registers for the pair, so the state
 * goes through the same strict Zod schema a real broadcast does — and a state
 * missing one key is refused whole, which is why `audioState()` below builds
 * every one of them complete. Then *the app's own rules* decide what appears:
 * `richSourceFor()` picks the player or the status card, `displayStateFor()`
 * derives the card's display state, and the session's `senders` name who is
 * sending.
 *
 * This is a stricter rule than it looks, and it is the second attempt. The first
 * wrote a snapshot straight into the store and gave each one a hand-written
 * name — and hand-written names drift into *fiction*: "small cover" and "sender
 * stopped" were two AirPlay scenarios rendering the same screen, pixel for
 * pixel, because the status card never reads a cover width. Naming a scenario
 * after the screen it produces means knowing that screen, which is the app's
 * job, not this file's.
 *
 * So a scenario is named by `scenarioId()`, never by hand. Its first token is
 * the display state the app derives from the state (`displayStateFor`, one of
 * DISPLAY_STATES — the same list the scenario select and the card's validator
 * read), then the source's `availability` entry when the display state does not
 * already spell it, then the fields the app's deciders branch on: `playing
 * title artist artwork_width=128 senders=1` names a stimulus, and every token
 * after the first is a field on the wire.
 *
 * Two scenarios that produce the same screen therefore keep two *names*, and the
 * collision surfaces instead of being swept under one — but surfacing it is
 * all the derivation does. What to do about it is a judgement, made once and
 * written down: either the app is wrong, which is the finding the page exists
 * for, or the screen is genuinely already documented and the second tab goes.
 * A tab that repeats a screen costs more than the state it documents — it
 * teaches a reader that a new tab need not mean a new screen, and after that
 * none of them is worth opening.
 *
 * What is exact here is the *shape*: the envelope, the state and every key
 * inside it, checked by the guardrail against `audio_wire.py` and against the
 * commands each source's `COMMANDS` declares. One field is stamped at replay
 * rather than written: a position anchor's `at`, which is the instant the
 * backend published — a replay publishes now, and a fixed instant would put
 * every playing bar at its end.
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
 * `richSourceFor` draws their view whatever the session says — the view is
 * where the first station, episode or album is chosen — so outside a session
 * the state alone cannot tell two of theirs apart. What does is the
 * *catalogue* condition, and that arrives over HTTP rather than the socket.
 * Those scenarios therefore carry a `condition`, spelled with the real field
 * names of the fixture that produces it (`stations=0`, `scanning`), each token
 * checked by the guardrail against the scenario's own browser block. Two axes,
 * two vocabularies, both borrowed.
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
 * No `.vue` import: the guardrail reads this file under Node to check the pages
 * against ALL_AUDIO_SOURCES, every state against the backend's own models, and
 * every name token against the files that branch on it. `SourceStage.vue` is
 * what turns a scenario into a mounted component.
 */
import stationImageTurntable from './samples/station-image-turntable.webp';
import stationImageCapsule from './samples/station-image-capsule.webp';
import { musicPlaceholder } from '@/constants/placeholders';
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';
import { displayStateFor } from '@/composables/useSourceStatusDisplay';

/** Prefix that tells a source page apart from a catalogue entry in `?c=`. */
export const SOURCE_PAGE_PREFIX = 'source:';

/**
 * Two *sample* cover widths — not two thresholds.
 *
 * There is exactly one threshold and it is not here: it is
 * `UNTRUSTED_SENDER_MIN_ARTWORK_PX` (300 px) in `constants/imageQuality.js`,
 * which is what `richSourceFor` compares `details.artwork_width` against. These
 * two are what real senders push on either side of it — a media app's artwork,
 * and the favicon of whatever page a browser tab is playing from — so a reader
 * of the AirPlay tabs sees the two things that actually happen rather than 299
 * and 301.
 *
 * Which is also why they are literals rather than `THRESHOLD ± 1`: derived
 * values would follow the gate wherever it moved and stop being sizes anyone has
 * ever seen. The guardrail keeps them straddling the real constant instead, so
 * moving the gate past one of them fails a test rather than quietly flipping a
 * scenario's outcome while its note still describes the old one.
 */
export const MEDIA_APP_COVER_PX = 600;
export const FAVICON_COVER_PX = 128;

/**
 * The files that turn a state into a screen — the app's deciders. Every field
 * in BEHAVIOURAL_FIELDS must be read by one of them, or it is not behavioural
 * and has no business in a scenario's name.
 *
 * "Which screen" is the first three; the rest decide which *face* of it, which
 * is the same kind of difference and worth the same tab: useSourceProgress
 * answers whether the playhead advances and what a stopped source shows, and
 * nowPlayingMetadata what the player keeps naming — a title is its whole
 * requirement, which is what lets an unidentified disc (no artist) on screen.
 */
export const DECIDERS = [
  'composables/useRichDisplay.js',
  'composables/useSourceStatusDisplay.js',
  'components/audio/AudioSourceView.vue',
  'composables/useSourceProgress.js',
  'utils/nowPlayingMetadata.js'
];

/**
 * The fields the deciders branch on, in the order a name spells them.
 *
 * Not a taxonomy of our own: each is read in DECIDERS, and the guardrail fails
 * on any that is not. `resume` is the resume point standing in for a session
 * (what play would bring back); `title` and `artist` are the session's, or the
 * resume point's when there is no session; `artwork_width` is AirPlay's detail;
 * `senders` the session's list of who is sending. Everything else a state
 * carries — the artwork URL, the anchor, the duration, the station — is
 * *content*: it changes what the screen says, never which screen you get, so it
 * stays out of the name. The phase is not here because the first token already
 * says it.
 */
export const BEHAVIOURAL_FIELDS = ['resume', 'title', 'artist', 'artwork_width', 'senders'];

/**
 * How a field renders inside a name. Presence is enough for a string — the value
 * is content, and a track title in a tab would be noise; a number is itself the
 * discriminating part, so it is printed; an array prints its length, which is
 * what separates Mac's one sender from its two.
 */
function spell(key, value) {
  if (value === true) return key;
  if (Array.isArray(value)) return `${key}=${value.length}`;
  if (typeof value === 'string') return key;
  return `${key}=${value}`;
}

/** The behavioural facts a state carries, by BEHAVIOURAL_FIELDS. */
function factsOf(state) {
  const record = state.session ?? state.resume;
  const values = {
    resume: !state.session && state.resume ? true : undefined,
    title: record?.title ?? undefined,
    artist: record?.artist ?? undefined,
    artwork_width: state.details?.artwork_width ?? undefined,
    senders: state.session?.senders.length ? state.session.senders : undefined
  };
  return BEHAVIOURAL_FIELDS
    .filter(key => values[key] !== undefined)
    .map(key => spell(key, values[key]));
}

/**
 * A scenario's name, derived from what it sends — never written by hand.
 *
 * The final event is what the screen settles on, so the id reads its state: the
 * display state the app derives from it, then the source's availability entry —
 * unless the display state already spells it (reading a disc, ejecting), which
 * is asked of the derivation itself rather than listed here — followed by the
 * behavioural facts it carries, then the catalogue condition for the three
 * sources that have one.
 */
export function scenarioId(events, browser) {
  const state = events[events.length - 1].data;
  const display = displayStateFor(state);
  const reason = state.availability[state.source];
  const spelled = reason && displayStateFor({ ...state, availability: {} }) === display ? [reason] : [];

  return [display, ...spelled, ...factsOf(state), ...(browser?.condition ?? [])].join(' ');
}

/** Any id a session carries — 32 hex characters, as `Session.id` is. */
const SESSION_ID = '5f0c2a9e7b3d4c18a6e1f0b2d9c7a345';

/**
 * A complete AudioState, as `audio_wire.py::AudioState` publishes it: every key
 * present, all ten availability entries, a running service and nothing in
 * session. A scenario states only what differs.
 */
export function audioState(source, overrides = {}) {
  const { availability, ...rest } = overrides;
  return {
    source,
    switching: false,
    service: 'running',
    service_error: null,
    availability: {
      ...Object.fromEntries(ALL_AUDIO_SOURCES.map(entry => [entry, null])),
      ...availability
    },
    session: null,
    controls: [],
    resume: null,
    details: null,
    multiroom_enabled: false,
    equalizer_effects_enabled: true,
    ...rest
  };
}

/** A complete session (`SessionView`): every key present, nothing named. */
export function session(overrides = {}) {
  return {
    id: SESSION_ID,
    phase: 'playing',
    title: null,
    artist: null,
    album: null,
    artwork: null,
    senders: [],
    duration_ms: null,
    position: null,
    ...overrides
  };
}

/** A position anchor at `ms`. `at` is stamped at replay — see replayed(). */
export function anchor(ms, rate = 1) {
  return { ms, at: 0, rate };
}

/**
 * A state as it is published *now*: the anchor's `at` is the one instant the
 * state carries, and a replay is a publish. Left at its fixture value, a
 * playing bar would run from 1970 and sit at its end.
 */
export function replayedState(state, nowSeconds) {
  if (!state.session?.position) return state;
  return {
    ...state,
    session: { ...state.session, position: { ...state.session.position, at: nowSeconds } }
  };
}

/**
 * The wire envelope, as `WsEvent.to_envelope` builds it: `origin` is the
 * state's source. `timestamp` is the one field nothing on this side reads, so it
 * is pinned rather than faked — which also keeps a scenario byte-identical
 * across runs.
 */
function stateEvent(state) {
  return { category: 'source', type: 'state', origin: state.source, data: state, timestamp: 0 };
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

/** One published state for `source`: what a scenario states, over the defaults. */
function published(source, label, note, overrides = {}) {
  return scenario([stateEvent(audioState(source, overrides))], label, note);
}

/**
 * A source switch under way: `switching` with the service starting, which is
 * what every source's first state looks like — and, since the multiroom switch
 * raises `switching` too (D7), what a reroute looks like while the source is
 * released and taken again. Nothing is listed in `controls` meanwhile.
 */
function starting(source) {
  return published(
    source,
    'Starting',
    'switching with the service starting — the status card takes over whatever the source is, and the spinner replaces its icon. One of the two states that read as a sentence broken over two lines ("Démarrage de" / the source), so the phrase leads and the source name takes the emphasised line — which is also why French needs three keys for it, agreeing the article with the source noun. The 500 ms floor holds the *card\'s* phrase, not the card: AudioSourceView reads `switching` raw, so a switch that completes into a rich display hands the screen over at once and the floor never applies.',
    { switching: true, service: 'starting' }
  );
}

/**
 * A failed start: the state machine stops the target, leaves it *selected* with
 * `service: failed` and the reason in `service_error` — whose message is for the
 * journal, never shown. Nothing is in session, because a source that never
 * started has none. What the user reads rides on `source/error` and raises
 * App.vue's banner over whatever is on screen; it is not replayed here — the
 * banner belongs to the app shell, not to the source's own screen.
 */
function errored(source, note, message) {
  return published(source, 'Error', note, {
    service: 'failed',
    service_error: { reason: 'start_failed', message }
  });
}

/**
 * The link is missing what this source needs. The backend has already crossed
 * NetworkManager's level with the source's own NETWORK_REQUIREMENT, so the
 * scenario states the *answer* — the source's availability entry — rather than
 * re-deriving it: `no_network` when nothing is reachable, `no_internet` when
 * the LAN is up but has no route out. Either one drops the source to the status
 * card, browser sources included: a favourites grid whose every tap fails is a
 * worse screen than one naming the reason.
 */
function offline(source, reason, note) {
  return published(
    source,
    reason === 'no_network' ? 'No network' : 'No internet',
    note,
    { availability: { [source]: reason } }
  );
}

/**
 * A browser source's scenario: the state, plus the browser's own setup.
 *
 * `browser.state` is what the backend publishes alongside the catalogue the
 * fixtures serve — nothing in session for a grid being browsed, a session for a
 * station tuning or an episode playing, a resume point for a station stopped
 * but still tuned. None of it changes *which component* mounts here
 * (`richSourceFor` draws these three whatever the session says); it is what the
 * pane and the transport read.
 */
function browsing(source, label, note, browser) {
  return scenario([stateEvent(audioState(source, browser.state ?? {}))], label, note, browser);
}

/** Radio's `details.station`, from the favourites entry the grid shows. */
function radioStation(station) {
  return {
    id: station.id,
    name: station.name,
    url: `https://streams.example/${station.id}.mp3`,
    country: station.countrycode === 'GB' ? 'United Kingdom' : 'France',
    genre: station.genre ?? null,
    favicon: station.favicon || null,
    bitrate: 128,
    codec: 'MP3'
  };
}

/** One track of the sample disc, as `CdTrack` carries it. */
const CD_TRACKS = [
  { number: 1, title: 'Keep', duration_ms: 312000 },
  { number: 2, title: 'Snippet', duration_ms: 96000 },
  { number: 3, title: 'Kind', duration_ms: 268000 },
  { number: 4, title: 'Unter', duration_ms: 401000 }
];

/** A disc MusicBrainz identified: `details.disc`. */
const CD_DISC = {
  id: 'yvYlA5_2ZK6mQvZ1kZ0rXqLg7dM-',
  album: 'Felt',
  artist: 'Nils Frahm',
  year: '2011',
  cover_url: musicPlaceholder,
  tracks: CD_TRACKS
};

/** `details` of the CD with the sample disc in, `current` its 1-based track. */
function cdDetails(current, disc = CD_DISC) {
  return { kind: 'cd', disc, current_track: current, artwork_pending: false };
}

/** A CD session on track `number` of the sample disc. */
function cdSession(number, overrides = {}) {
  const track = CD_TRACKS[number - 1];
  return session({
    title: track.title,
    artist: CD_DISC.artist,
    album: CD_DISC.album,
    artwork: CD_DISC.cover_url,
    duration_ms: track.duration_ms,
    ...overrides
  });
}

/** The track the Connect sources play in every scenario of theirs. */
const SAYS = {
  title: 'Says',
  artist: 'Nils Frahm',
  album: 'Spaces',
  artwork: musicPlaceholder,
  duration_ms: 511000
};

/** The AirPlay sender's track, and who sends it. */
const ZARATHOUSTRA = {
  title: 'Ainsi parlait Zarathoustra',
  artist: 'Alain Bashung',
  album: 'Bleu pétrole',
  senders: ['Leo’s iPhone']
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
 *
 * Served on one scenario only, and that is the rule rather than an oversight:
 * the dropdown sits inside the transport, so a scenario with no player pane has
 * nothing that could read this — a fixture nobody fetches documents nothing and
 * outlives the call it stands in for.
 */
const PODCAST_SPEEDS = [0.8, 1.0, 1.2, 1.5, 1.8, 2.0];

/** The shows the unit is subscribed to, as the home's first block lists them. */
const PODCAST_SUBSCRIPTIONS = [
  { uuid: 'sub-1', name: 'Le Code a changé', publisher: 'France Inter', is_subscribed: true },
  { uuid: 'sub-2', name: 'Affaires sensibles', publisher: 'France Inter', is_subscribed: true }
];

/**
 * An Apple top-charts page. `image_url` is left off so LazyImage takes
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
      ['musicLibrary', 'loadAlbums', { force: true }],
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
    uses: 'AudioSourceStatus · AudioPlayerFull',
    via: 'dispatcher',
    summary:
      'The only Connect source Milō drives back with a full transport: `controls` lists pause or resume, next, prev and — once the track plays — seek, and AudioPlayerFull draws a button for each command listed and nothing else. Its rich display needs a title and nothing more: Spotify is a trusted metadata provider, so no cover-quality check. A session opens only at the first track go-librespot can name, so there is no "connected, nothing to draw" screen: the gap before it reads as ready.',
    scenarios: [
      starting('spotify'),
      published('spotify', 'Ready', 'Connected to go-librespot, no phone has picked the speaker yet. Nothing in session, so nothing in `controls` either — there is no transport to offer before a phone hands over a queue.'),
      published('spotify', 'Loading', 'A track is on its way: the play/pause glyph gives way to a spinner and the bar holds 0:00, because `seek` is never listed while loading — scrubbing a track that has not started would be refused.', {
        session: session({ ...SAYS, phase: 'loading', position: anchor(0) }),
        controls: ['pause', 'next', 'prev']
      }),
      published('spotify', 'Playing', 'Rich display earned: AudioPlayerFull, progress bar and transport, the bar interactive because `seek` is listed. The buttons report to the event log instead of reaching the unit.', {
        session: session({ ...SAYS, phase: 'playing', position: anchor(192000) }),
        controls: ['pause', 'seek', 'skip', 'next', 'prev']
      }),
      published('spotify', 'Paused', 'Same session, phase paused: `controls` trades pause for resume, the glyph flips, and useSourceProgress stops advancing the anchor.', {
        session: session({ ...SAYS, phase: 'paused', position: anchor(192000) }),
        controls: ['resume', 'seek', 'skip', 'next', 'prev']
      }),
      offline(
        'spotify',
        'no_internet',
        'The link is up but has no route out — go-librespot is running and unreachable at once. AudioPlayerFull would keep its transport pointing at a daemon that cannot resolve anything, so the card takes over and names the reason, with the network settings one tap away.'
      ),
      errored(
        'spotify',
        'go-librespot not coming up. The source stays selected — that is what makes the retry possible — and the card says so: "Spotify / Error", with a Retry CTA that re-posts the source selection and re-runs the start the state machine gave up on. The message itself is the banner\'s.',
        'go-librespot failed to start'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}qobuz`,
    source: 'qobuz',
    title: 'Qobuz',
    family: 'B — passive player',
    uses: 'AudioSourceStatus · AudioPlayerFull',
    via: 'dispatcher',
    summary:
      'Receiver-driven: the source declares no command at all, so `controls` is always empty and AudioPlayerFull draws a source bar where a transport would be, under a read-only progress bar. The only source with an account: `availability.qobuz` reads `no_account` when the one-time login is missing, which swaps the idle line for the connect CTA — and a missing link outranks it, since with no route out the account cannot be checked.',
    scenarios: [
      starting('qobuz'),
      published('qobuz', 'Ready', 'Account connected, waiting for the app to pick the speaker: availability is null, which is what "the source can work" looks like — the account is not a field that appears when something is wrong, it is the absence of a reason.'),
      published(
        'qobuz',
        'Ready, no account',
        'availability no_account — the only path to the connect CTA in AudioSourceStatus. Tapping it calls inject("openSettings"), which is absent here, so it no-ops.',
        { availability: { qobuz: 'no_account' } }
      ),
      published('qobuz', 'Loading', 'The app picked a track and the proxy is fetching it. Trusted CDN cover, so no artwork_width gate — a title is enough for the player, which draws the spinner-free receiver bar: with no command listed there is no play button to spin.', {
        session: session({ ...SAYS, phase: 'loading', position: anchor(0) })
      }),
      published('qobuz', 'Playing', 'Read-only bar above the source bar, which names no device: the proxy exposes no controller identity, so `senders` is empty and the bar falls back to the source\'s own label, "Qobuz".', {
        session: session({ ...SAYS, phase: 'playing', position: anchor(64000) })
      }),
      published('qobuz', 'Paused', 'The app paused. Nothing on this screen is a transport, so what changes is the playhead alone: the anchor stops advancing.', {
        session: session({ ...SAYS, phase: 'paused', position: anchor(64000) })
      }),
      offline(
        'qobuz',
        'no_internet',
        'Same link, and the reason outranks the account question: with no route out the proxy cannot tell whether an account exists, so "Account not connected" would be a guess. Network first, and its CTA replaces the connect one.'
      ),
      errored(
        'qobuz',
        'The proxy sidecar will not start. With nothing in availability the card resolves the error branch and offers the retry; an account missing on top of it would still win, since the card answers a missing prerequisite first.',
        'qobuz-proxy failed to start'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}tidal`,
    source: 'tidal',
    title: 'TIDAL',
    family: 'C — active player',
    uses: 'AudioSourceStatus · AudioPlayerFull',
    via: 'dispatcher',
    summary:
      'Spotify\'s shape reached through a Unix socket instead of a WebSocket: the phone hands over a queue and Milō drives it back. One difference is visible on screen — the tisoc protocol has no seek command at all, so `seek` is never in `controls` and this is the only controlled source whose progress bar is inert. Trusted CDN cover, so a title is the whole gate, like Spotify and unlike AirPlay.',
    scenarios: [
      starting('tidal'),
      published('tidal', 'Ready', 'The daemon acknowledged startService and is advertising over mDNS; no phone has picked the speaker yet. Reaching this state is the proof the source is usable — a daemon that never answers would reject every session.'),
      published('tidal', 'Loading', 'The daemon passes through BUFFERING on every track change, so this is a normal step rather than a stall: the spinner replaces the glyph. No anchor yet — the daemon has not reported a position — so the bar is not drawn.', {
        session: session({ ...SAYS, phase: 'loading' }),
        controls: ['pause', 'next', 'prev']
      }),
      published('tidal', 'Playing', 'Rich display earned: transport plus a bar that draws position but refuses a scrub, because `seek` is not listed. The buttons report to the event log instead of reaching the unit.', {
        session: session({ ...SAYS, phase: 'playing', position: anchor(192000) }),
        controls: ['pause', 'next', 'prev']
      }),
      published('tidal', 'Paused', 'Same session, phase paused. A paused track keeps its session and its cover — the daemon reports the end of one explicitly, so nothing here is a stale leftover.', {
        session: session({ ...SAYS, phase: 'paused', position: anchor(192000) }),
        controls: ['resume', 'next', 'prev']
      }),
      offline(
        'tidal',
        'no_internet',
        'The daemon streams from Tidal\'s CDN and authenticates over TLS, so no route out means nothing it can do — the card takes over rather than leaving a transport pointing at a dead session.'
      ),
      errored(
        'tidal',
        'The daemon did not come up, or came up and never acknowledged startService — the source treats both as a failed start, because a daemon stuck before that acknowledgement would advertise a speaker that silently refuses every phone. Retry re-posts the source selection.',
        'Tidal Connect failed to start'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}airplay`,
    source: 'airplay',
    title: 'AirPlay',
    family: 'B — passive player',
    uses: 'AudioSourceStatus · AudioPlayerFull',
    via: 'dispatcher',
    summary:
      'The receiver whose phase depends on the stream: a Buffered sender (iPhone Music) says when it pauses, a Realtime one (a Mac\'s system audio) never does, so that session stays `connected` — "Connecté à <sender>", never a player whose bar runs on through a pause. The untrusted-sender gate lives here too: a title AND `details.artwork_width` above UNTRUSTED_SENDER_MIN_ARTWORK_PX (300). The cover size is the whole of it — a sender that publishes a real one is a media app, one that publishes a favicon is a browser tab. No command is declared, so `controls` is empty and the player draws the source bar, naming the sender from `senders`.',
    scenarios: [
      starting('airplay'),
      published('airplay', 'Ready', 'shairport-sync advertising, nobody streaming.'),
      published('airplay', 'Connected, no track', 'A Realtime sender — a Mac casting its system audio — or a sender connected before any audio: nothing to name and no pause to hear of, so the phase is connected and the card names the sender from `senders`.', {
        session: session({ phase: 'connected', senders: ['Leo’s MacBook'] }),
        details: { kind: 'airplay', artwork_width: null }
      }),
      published('airplay', 'Loading, no cover yet', 'The track is named before its cover arrives, and artwork_width is null until it does — so the gate declines the player and the card stands in, naming the sender, rather than drawing a player with an empty artwork slot.', {
        session: session({ ...ZARATHOUSTRA, phase: 'loading' }),
        details: { kind: 'airplay', artwork_width: null }
      }),
      published(
        'airplay',
        'Playing, favicon cover',
        `artwork_width ${FAVICON_COVER_PX} is under UNTRUSTED_SENDER_MIN_ARTWORK_PX (300, and the only number here that is a rule), so the rich display is declined and the card names the sender instead. This is what browser audio looks like — a page favicon where a media app would push a real cover — and it is the only reason the gate exists.`,
        {
          session: session({ ...ZARATHOUSTRA, phase: 'playing', artwork: musicPlaceholder, duration_ms: 297000, position: anchor(41000) }),
          details: { kind: 'airplay', artwork_width: FAVICON_COVER_PX }
        }
      ),
      published('airplay', 'Playing', 'Title and a cover wide enough: AudioPlayerFull, with the sender named in the source bar and a read-only bar drawn from the anchor — a Buffered sender reports its pauses, so this bar stops when the music does.', {
        session: session({ ...ZARATHOUSTRA, phase: 'playing', artwork: musicPlaceholder, duration_ms: 297000, position: anchor(41000) }),
        details: { kind: 'airplay', artwork_width: MEDIA_APP_COVER_PX }
      }),
      published('airplay', 'Paused', 'The sender paused and said so. The gate reads no phase — the player stays, and the card is not the answer to a pause — so what changes is the playhead, frozen at the anchor.', {
        session: session({ ...ZARATHOUSTRA, phase: 'paused', artwork: musicPlaceholder, duration_ms: 297000, position: anchor(41000) }),
        details: { kind: 'airplay', artwork_width: MEDIA_APP_COVER_PX }
      }),
      offline(
        'airplay',
        'no_network',
        'The LAN-only source\'s own case: shairport-sync needs the local network and nothing beyond it, so a router with no internet leaves it working and this scenario never fires. What does fire is the link disappearing entirely — no sender can reach the unit, and "Ready to connect" would be an invitation to nothing.'
      ),
      errored(
        'airplay',
        'shairport-sync failing to start is the common case — the port is taken, or the ALSA device is busy. No session, so no sender to name: the source that most depends on naming its sender falls back to naming itself, "AirPlay / Error", with the retry.',
        'shairport-sync failed to start'
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
      'The widest matrix here, and the one source whose player shows with nothing in session: a disc that is READY publishes a resume point (track 1 at 0:00 by default, D8), and a resume point with a title is enough for the player. Everything else about the drive is its availability entry — no_drive, no_disc, reading_disc, unreadable_disc, ejecting. Two of those are operations under way, drawn as the display states loading_disc and ejecting with a spinner; the other three are missing prerequisites, and only unreadable_disc has a CTA: eject, the one way out of a slot drive.',
    scenarios: [
      starting('cd'),
      published('cd', 'No drive', 'availability no_drive — the source is up but the hardware is missing. The card names the reason and offers nothing: plugging a drive in is not something the UI can do.', {
        availability: { cd: 'no_drive' }
      }),
      published('cd', 'Drive empty', 'availability no_disc: a drive with nothing in it. No CTA either, and no `details` — the CD describes a disc only once it is READY or unreadable.', {
        availability: { cd: 'no_disc' }
      }),
      published('cd', 'Reading the disc', 'availability reading_disc — the TOC read and the MusicBrainz lookup are under way. The display state is loading_disc, a spinner in place of the icon, and `eject` is already listed: a disc can be taken back while it is read.', {
        availability: { cd: 'reading_disc' },
        controls: ['eject']
      }),
      published('cd', 'Disc unreadable', 'availability unreadable_disc, with `details.disc` null: the drive holds something Milō cannot read. The card offers eject, because a slot drive has no button of its own (E67) and the disc would otherwise be stuck.', {
        availability: { cd: 'unreadable_disc' },
        controls: ['eject'],
        details: cdDetails(null, null)
      }),
      published('cd', 'Disc ready, not playing', 'Nothing in session and the player shows anyway: the resume point names track 1 at 0:00 — what a play press would start — and useSourceProgress draws that frozen bar from `position_ms`. The transport is live because `controls` lists resume, seek and the track commands with no session behind them.', {
        resume: { title: 'Keep', artist: 'Nils Frahm', album: 'Felt', artwork: musicPlaceholder, duration_ms: 312000, position_ms: 0 },
        controls: ['resume', 'seek', 'skip', 'next', 'prev', 'play_track', 'eject'],
        details: cdDetails(1)
      }),
      published(
        'cd',
        'Disc not identified',
        'The same screen as above with the MusicBrainz lookup having found nothing — a burned disc, an obscure pressing, or any disc while the unit is offline. The TOC alone answers: generic "Track N" titles from the real track count and durations, and no album, artist, year or cover. The player is admitted on the title alone and draws "Unknown Artist" — the honest label here — over the disc placeholder. Demanding an artist too is what once left this player on its empty seed over a tracklist that listed the tracks correctly.',
        {
          resume: { title: 'Track 1', artist: null, album: null, artwork: null, duration_ms: 312000, position_ms: 0 },
          controls: ['resume', 'seek', 'skip', 'next', 'prev', 'play_track', 'eject'],
          details: cdDetails(1, {
            id: 'JXbxvhCUq4rHKnvNGkzZgL3xIxA-',
            album: null,
            artist: null,
            year: null,
            cover_url: null,
            tracks: CD_TRACKS.map(track => ({ ...track, title: `Track ${track.number}` }))
          })
        }
      ),
      published('cd', 'Loading a track', 'A play press or a track change opens a session in loading while the reader restarts: the bar snaps to 0:00 on the target track and stays there, since `seek` is not listed while loading, and the spinner replaces the glyph.', {
        session: cdSession(3, { phase: 'loading', position: anchor(0) }),
        controls: ['pause', 'next', 'prev', 'play_track', 'eject'],
        details: cdDetails(3)
      }),
      published('cd', 'Playing', 'AudioPlayerFull with the full transport. On the last track `next` leaves `controls`, and the button with it.', {
        session: cdSession(4, { phase: 'playing', position: anchor(74000) }),
        controls: ['pause', 'seek', 'skip', 'prev', 'play_track', 'eject'],
        details: cdDetails(4)
      }),
      published('cd', 'Paused', 'A paused session is still a session, and the screen says so by its transport — resume instead of pause — while the bar freezes at the anchor. Auto-stop ends it on the idle screen above, keeping the track and the second as the resume point, so the disc stays visible and play resumes where it was.', {
        session: cdSession(3, { phase: 'paused', position: anchor(74000) }),
        controls: ['resume', 'seek', 'skip', 'next', 'prev', 'play_track', 'eject'],
        details: cdDetails(3)
      }),
      published('cd', 'Ejecting', 'availability ejecting outranks the disc: the display state is ejecting, a spinner, and the player gives way to the card rather than lingering over a disc that is leaving. `eject` is not listed — it is already happening.', {
        availability: { cd: 'ejecting' }
      }),
      errored(
        'cd',
        'The one failure that takes the disc off screen: a failed start publishes no resume point and no details, so the card is what is left, with the retry. The drive states above are availability on a running source, not a source that failed.',
        'cd-paranoia failed to open the drive'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}bluetooth`,
    source: 'bluetooth',
    title: 'Bluetooth',
    family: 'C — active player',
    uses: 'AudioSourceStatus · AudioPlayerFull',
    via: 'dispatcher',
    summary:
      'The one source whose two feeds answer different questions: BlueALSA says who is linked, BlueZ AVRCP says what is playing — and the second is optional. So a link with nothing to name is `connected` and draws the card, and a sender publishing a track draws the player, which is what the first two sessions below show. AVRCP has no seek (never listed, so the bar is inert, like TIDAL) and carries no cover either: the one in the artwork slot was looked up from the track text by shared/artwork_resolver.py. `disconnect` is listed for every session, so the CTA appears twice: on the card, and again as the player’s action button, since the card is gone exactly when a user wants to kick the phone off. A connected sender that does have a player (a Mac for its first 100 s) lists resume, next and prev as well and draws the same card, so it has no tab.',
    scenarios: [
      starting('bluetooth'),
      published('bluetooth', 'Ready', 'Discoverable, nothing linked. No CTA in this state.'),
      published('bluetooth', 'Connected, no track', 'A sender that publishes no player — or an empty track — stays on the card: `senders` fills the second line and the disconnect CTA appears. It routes through sendCommand, so here it reports to the event log.', {
        session: session({ phase: 'connected', senders: ['Leo’s iPhone'] }),
        controls: ['disconnect']
      }),
      published('bluetooth', 'Playing', 'Status playing and a stream flowing. A title is the gate; transport plus a bar that draws position and refuses a scrub. The cover is not the sender’s: AVRCP carries none, so it was resolved from this track’s own text and can perfectly well be absent.', {
        session: session({ ...SAYS, phase: 'playing', senders: ['Leo’s iPhone'], position: anchor(192000) }),
        controls: ['pause', 'next', 'prev', 'disconnect']
      }),
      published('bluetooth', 'Paused', 'Status paused. The player stays — it draws a pause button, and dropping to the card on pause would delete the button that was just pressed — with resume in place of pause.', {
        session: session({ ...SAYS, phase: 'paused', senders: ['Leo’s iPhone'], position: anchor(192000) }),
        controls: ['resume', 'next', 'prev', 'disconnect']
      }),
      errored(
        'bluetooth',
        'bluealsa failing to come up leaves no session, so no device to name and no disconnect: the card cannot offer to drop a link the source can no longer address. What sits in the same slot instead is the retry, which is the one action that still means something here.',
        'bluealsa failed to start'
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
      'The mute receiver: one session whose `senders` is every Mac streaming over ROC, joined across two lines by formatDeviceNames, and whose phase is always `connected` — roc-vad streams unbroken 44.1 kHz for as long as Milō is the Mac\'s output, silence included, so "connected" here can never mean "playing". No command is declared, so the card shows no CTA at all: the sender stops from its own side, and there is nothing for Milō to end.',
    scenarios: [
      starting('mac'),
      published('mac', 'Ready', 'roc-recv is listening; no Mac is sending, so nothing is in session and the card invites a connection.'),
      published('mac', 'One Mac streaming', '`senders` is a list even with a single entry, which is why the name spells its length rather than its presence. The card reads "Audio received from" rather than "Connected to": a ROC stream is not a link to one device.', {
        session: session({ phase: 'connected', senders: ['Leo’s MacBook'] })
      }),
      published('mac', 'Two Macs streaming', 'The case the list exists for — formatDeviceNames breaks the second line, which is why status-line-2 carries white-space: pre-line.', {
        session: session({ phase: 'connected', senders: ['Leo’s MacBook', 'Studio iMac'] })
      }),
      offline(
        'mac',
        'no_network',
        'ROC is a LAN stream, so only a dead link blocks it. A missing prerequisite outranks the session, so the card stops at the phrase and never reaches the "audio received from" wording — and with no network there is nothing to receive from anyway.'
      ),
      errored(
        'mac',
        'roc-recv failing to bind its port. No session, so the "audio received from" line gives way to the same two-line error screen every other source gets.',
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
      'richSourceFor draws the three browser sources whatever the session says — their own layout handles empty and loading — so the status card is reached in exactly three places: while switching, when the service failed, and when the link is missing (a favourites grid whose every tap would fail is worse than no grid). The player is the one with no progress bar at all: a live stream has no duration, and no pause either — its transport is `stop` while tuned and `resume_playback` (re-tune) once stopped.',
    scenarios: [
      starting('radio'),
      browsing('radio', 'Favourites loading', 'favoritesInitialized false — the grid is sixteen SkeletonStationCards. It is the state a cold boot opens on, and the only one where the count on screen is a guess rather than the truth.', {
        condition: ['favoritesInitialized=false'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        // The view's guard is `isLoading || !favoritesInitialized`, so a prop
        // and a seed here would be one fact told twice — either alone draws the
        // skeletons. The seed is the half kept: it is the half the name states.
        seed: { radio: { favoritesInitialized: false } },
        player: null
      }),
      browsing('radio', 'No favourites yet', 'Initialised and empty, which is a different thing from loading and is why favoritesInitialized exists: MessageContent says there is nothing rather than shimmering for ever at a unit that simply has no favourites. `next` and `prev` step through the favourites, so with none they are not listed.', {
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
        state: { controls: ['next', 'prev'] },
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: null
      }),
      browsing('radio', 'Tuning a station', 'A session in loading before a single byte of audio: the station is named and the transport spins, while bufferingStationId marks the same card in the grid and the rest of it stays live, so a second tap goes somewhere rather than being swallowed by a full-screen loader.', {
        condition: ['bufferingStationId'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        state: {
          session: session({
            phase: 'loading',
            title: RADIO_STATION_WITH_IMAGE.name,
            album: RADIO_STATION_WITH_IMAGE.name,
            artwork: RADIO_STATION_WITH_IMAGE.favicon
          }),
          controls: ['stop', 'next', 'prev'],
          details: { kind: 'radio', station: radioStation(RADIO_STATION_WITH_IMAGE), track: null }
        },
        // The marked card in the grid, and only that: it comes from
        // RadioSource's own computed, which lives in the wrapper the stage
        // replaces — hence the prop. `currentStation` and `isPlaying` are
        // deliberately not passed, though the wrapper does pass them:
        // FavoritesView reads the first only as `currentStation?.id ===
        // station.id && isPlaying`, and nothing is playing yet, so neither would
        // change anything on screen. The pane's station is the fixture's, below.
        props: { bufferingStationId: 'st-nova' },
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: {
          station: { name: RADIO_STATION_WITH_IMAGE.name, artwork: RADIO_STATION_WITH_IMAGE.favicon },
          track: null,
          isPlaying: false,
          isLoading: true,
          controls: { favorite: true }
        }
      }),
      browsing('radio', 'Playing a station', 'The pane animates in and the content gives up 340 px; the playing card is marked in the grid. No track recognised yet, so the station is the whole info block and its own image is the artwork. No #progress slot either — a live stream has no duration, so the bar would have nothing to show.', {
        condition: ['currentStation', 'artwork'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        state: {
          session: session({
            phase: 'playing',
            title: RADIO_STATION_WITH_IMAGE.name,
            album: RADIO_STATION_WITH_IMAGE.name,
            artwork: RADIO_STATION_WITH_IMAGE.favicon
          }),
          controls: ['stop', 'next', 'prev'],
          details: { kind: 'radio', station: radioStation(RADIO_STATION_WITH_IMAGE), track: null }
        },
        props: { isPlaying: true, currentStation: RADIO_STATION_WITH_IMAGE },
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: {
          // Both halves of the same station: the grid card and the pane resolve
          // their image from one favicon, exactly as RadioSource does through
          // getFaviconUrl on the station it plays.
          station: { name: RADIO_STATION_WITH_IMAGE.name, artwork: RADIO_STATION_WITH_IMAGE.favicon },
          track: null,
          isPlaying: true,
          controls: { favorite: true }
        }
      }),
      browsing('radio', 'Stopped, still tuned', 'The screen a stop leaves: no session, and a resume point naming the station a play press would re-tune — so the pane stays, drawn on that station, and `controls` offers resume_playback where the playing screen offered stop. The recognised track annotates a running stream and goes with it.', {
        condition: ['currentStation', 'artwork'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        state: {
          resume: {
            title: RADIO_STATION_WITH_IMAGE.name,
            artist: null,
            album: RADIO_STATION_WITH_IMAGE.name,
            artwork: RADIO_STATION_WITH_IMAGE.favicon,
            duration_ms: null,
            position_ms: null
          },
          controls: ['resume_playback', 'next', 'prev'],
          details: { kind: 'radio', station: radioStation(RADIO_STATION_WITH_IMAGE), track: null }
        },
        props: { isPlaying: false, currentStation: RADIO_STATION_WITH_IMAGE },
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: {
          station: { name: RADIO_STATION_WITH_IMAGE.name, artwork: RADIO_STATION_WITH_IMAGE.favicon },
          track: null,
          isPlaying: false,
          controls: { favorite: true }
        }
      }),
      browsing('radio', 'Playing, no station image', 'The same pane for a station that carries no image: AudioPlayer takes fallbackName and renders the generated monogram, in the artwork frame and blurred behind it both. Most of a directory looks like this, which is why the avatar is a branch rather than a placeholder.', {
        condition: ['currentStation'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        state: {
          session: session({
            phase: 'playing',
            title: RADIO_STATION_NO_IMAGE.name,
            album: RADIO_STATION_NO_IMAGE.name
          }),
          controls: ['stop', 'next', 'prev'],
          details: { kind: 'radio', station: radioStation(RADIO_STATION_NO_IMAGE), track: null }
        },
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
      browsing('radio', 'Track detected', 'Shazam matched the stream: `details.track` carries it, the session takes its title, artist and cover, and the station drops to the kicker. The station image is what the kicker icon shows — and on the Phone viewport the same image slides in behind the cover, so switch the viewport to see the pair overlap.', {
        condition: ['currentStation', 'artwork', 'track'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        state: {
          session: session({
            phase: 'playing',
            title: 'Ainsi parlait Zarathoustra',
            artist: 'Alain Bashung',
            album: RADIO_STATION_WITH_IMAGE.name,
            artwork: musicPlaceholder
          }),
          controls: ['stop', 'next', 'prev'],
          details: {
            kind: 'radio',
            station: radioStation(RADIO_STATION_WITH_IMAGE),
            track: { title: 'Ainsi parlait Zarathoustra', artist: 'Alain Bashung', artwork: musicPlaceholder }
          }
        },
        props: { isPlaying: true, currentStation: RADIO_STATION_WITH_IMAGE },
        api: { '/api/radio/stations': { stations: RADIO_FAVOURITES } },
        prime: [['radio', 'loadStations', true]],
        player: {
          station: { name: RADIO_STATION_WITH_IMAGE.name, artwork: RADIO_STATION_WITH_IMAGE.favicon },
          // The kicker (and the mobile badge) are gated on the *track* having
          // artwork, not on the station having any — so this is the only shape
          // that reaches either.
          track: { title: 'Ainsi parlait Zarathoustra', artist: 'Alain Bashung', artwork: musicPlaceholder },
          isPlaying: true,
          controls: { favorite: false }
        }
      }),
      offline(
        'radio',
        'no_internet',
        'The decision this source forced: the favourites grid is local data, so it stays browsable while every station it lists is unreachable. Documented here as the card instead — a tap that fails silently is worse than a screen that says why — and the grid returns the moment the link does.'
      ),
      errored(
        'radio',
        'mpv not coming up. Like the offline case above, the app leaves the browser by itself rather than drawing a favourites grid whose every tap would fail — so this is one of the scenarios here that needs no stand-in. The favourites are still there; the retry CTA is what brings them back.',
        'mpv failed to start'
      )
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
      'The same two parts as Radio, with a progress bar and a swipe gesture — but swipeEnabled without a tracks queue, so the swipe seeks (±15/30 s) instead of skipping and no text carousel is built. `set_speed` is listed even with nothing in session, since the speed is chosen before an episode starts. Its header is the one that changes shape as you descend: title, subtitle and the back affordance are all driven by the current view.',
    scenarios: [
      starting('podcast'),
      browsing('podcast', 'Browsing the charts', 'The home view, three header actions and no back. Unlike the other two browsers this one fetches from the component rather than a store, so its charts are served as an HTTP fixture — the real loadData() runs.', {
        condition: ['results'],
        layout: PODCAST_HEADER,
        view: 'podcast-home',
        state: { controls: ['set_speed'] },
        api: {
          '/api/podcast/discover/top-charts': { results: PODCAST_CHARTS },
          '/api/podcast/subscriptions': { subscriptions: PODCAST_SUBSCRIPTIONS }
        },
        player: null
      }),
      browsing('podcast', 'Catalogue unavailable', 'Apple did not answer, so the backend sets `api_error` instead of failing — a distinct branch from "no results", and the only one that says why the chart is empty. Deliberately not the status card: the loss is one block. The subscriptions above it are local data and still play, which is the whole reason this stays a per-view message with a retry.', {
        condition: ['api_error'],
        layout: PODCAST_HEADER,
        view: 'podcast-home',
        state: { controls: ['set_speed'] },
        api: {
          '/api/podcast/discover/top-charts': { api_error: true },
          '/api/podcast/subscriptions': { subscriptions: PODCAST_SUBSCRIPTIONS }
        },
        player: null
      }),
      browsing('podcast', 'Playing an episode', 'Player pane in, progress bar drawn. On the Phone viewport this becomes a mini-bar teleported to body, with the bar as a 2 px strip on the card’s bottom edge.', {
        condition: ['episodeName'],
        layout: PODCAST_HEADER,
        view: 'podcast-home',
        state: {
          session: session({
            phase: 'playing',
            title: 'Épisode 214',
            artist: 'Le Code a changé',
            album: 'Le Code a changé',
            duration_ms: 2940000,
            position: anchor(812000)
          }),
          controls: ['pause', 'seek', 'skip', 'set_speed'],
          details: {
            kind: 'podcast',
            episode: {
              uuid: 'ep-214',
              name: 'Épisode 214',
              image_url: null,
              podcast: { uuid: 'sub-1', name: 'Le Code a changé', image_url: null }
            },
            speed: 1
          }
        },
        api: {
          '/api/podcast/discover/top-charts': { results: PODCAST_CHARTS },
          '/api/podcast/subscriptions': { subscriptions: PODCAST_SUBSCRIPTIONS },
          '/api/podcast/playback-speeds': { speeds: PODCAST_SPEEDS }
        },
        prime: [['podcast', 'loadPlaybackSpeeds']],
        player: {
          podcastName: 'Le Code a changé',
          episodeName: 'Épisode 214',
          // Left unset: the source passes the episode's image_url, and with no
          // episode image the shared fallback helper answers with the bundled
          // microphone, which is what an episode with no artwork shows on the unit.
          episodeImage: null,
          isPlaying: true,
          progress: { currentPosition: 812000, duration: 2940000, progressPercentage: 27.6 }
        }
      }),
      offline(
        'podcast',
        'no_internet',
        'Distinct from api_error, which stays: that one says Apple did not answer and is perfectly reachable while online. This one says the link itself has no route out, so the catalogue, the feeds and the audio are all gone at once.'
      ),
      errored(
        'podcast',
        'mpv not coming up, which is a different failure from the unreachable chart above: that one answers api_error and leaves the browser working, this one takes the source down and hands the screen to the status card.',
        'mpv failed to start'
      )
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
      'The richest of the three: the only source whose details carry a queue, which is what turns the mobile swipe into the three-cell text carousel, and the only one where hasEntityLinks is true — the artwork and the secondary line become links to the album and the artist. Both are Phone-viewport behaviours; the docked desktop card shows the full transport row instead. It needs no network, so it has no offline screen: its own two reasons (no_storage, catalog_unavailable) are drawn by its view, with the storage wizard at hand, never by the card.',
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
        state: {
          session: session({ ...SAYS, phase: 'playing', position: anchor(192000) }),
          controls: ['pause', 'seek', 'skip', 'next', 'prev', 'set_shuffle', 'play_index', 'stop'],
          details: {
            kind: 'music_library',
            queue: [
              { id: 's-1', title: 'Ambre', artist: 'Nils Frahm', albumId: 'al-2', artistId: 'ar-3' },
              { id: 's-2', title: 'Says', artist: 'Nils Frahm', albumId: 'al-2', artistId: 'ar-3' },
              { id: 's-3', title: 'Hammers', artist: 'Nils Frahm', albumId: 'al-2', artistId: 'ar-3' }
            ],
            queue_index: 1,
            shuffle: true,
            track_id: 's-2',
            album_id: 'al-2',
            artist_id: 'ar-3'
          }
        },
        ...mlSetup({ storages: ML_STORAGE_MIXED, albums: ML_ALBUMS, activeLibraryId: 2 }),
        player: {
          // What nowPlaying projects: title, artist, cover — nothing else
          // reaches this player's info block.
          title: 'Says',
          artist: 'Nils Frahm',
          artwork: musicPlaceholder,
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
      }),
      errored(
        'music_library',
        'mpv not coming up. Navidrome and the mounted shares are untouched by it — the catalogue is still there — but nothing can be played from it, so the browser gives way to the card rather than offering a library that cannot sound.',
        'mpv failed to start'
      )
    ]
  }
];

/** The source page for a `source:<id>` selection, or undefined. */
export function sourcePageById(id) {
  return SOURCE_PAGES.find(page => page.id === id);
}

/** Every envelope the 10 pages can emit, for the checks against the models. */
export function allEvents() {
  return SOURCE_PAGES.flatMap(page => page.scenarios.flatMap(entry => entry.events));
}

/** The state a scenario settles on — the data of its last `source/state`. */
export function settledState(entry) {
  return entry.events[entry.events.length - 1].data;
}
