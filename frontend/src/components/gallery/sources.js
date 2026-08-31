// frontend/src/components/gallery/sources.js
/**
 * The 11 audio sources, as pages of the gallery — the second axis.
 *
 * The catalogue next door answers "what does this component do"; this file
 * answers "what does a source look like, in every state it can reach". The two
 * are not the same question: AudioPlayerFull serves seven sources and none of
 * them shows the same thing, while CD alone moves between that player and the
 * status card on metadata the enum has no name for. A reader after either one
 * had to assemble it from four component pages and useRichDisplay's source
 * code.
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
 * `useSourceStatusDisplay()` derives the card's display state — the backend
 * enum plus CD's three pseudo-states — and `currentDeviceName` maps the
 * per-source identity field.
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
 * on the wire.
 *
 * Two scenarios that produce the same screen therefore keep two *names*, and the
 * collision surfaces instead of being swept under one — but surfacing it is
 * all the derivation does. What to do about it is a judgement, made once and
 * written down: either the app is wrong, which is the finding the page exists
 * for, or the screen is genuinely already documented and the second tab goes.
 * AirPlay has had one of each. Its cover gate once had three inputs and one
 * outcome, which was a finding; its pre-metadata window draws the same card as
 * its declined-cover one for a reason the card is right about, so it is stated
 * in the page summary and has no tab. A tab that repeats a screen costs more
 * than the state it documents — it teaches a reader that a new tab need not
 * mean a new screen, and after that none of them is worth opening.
 *
 * What is exact here is the *shape*: the envelope, `full_state`, and every field
 * name inside them. A metadata record is not a packet capture, and one thing it
 * leaves out on purpose — `emit_connection_state` puts `is_playing` and
 * `is_buffering` in every record of every media source, READY included (forced
 * off there, along with the media fields it drops). Restating both on every
 * scenario would put two tokens no decider can branch on into every name in
 * the select, which is the list a reader actually reads. They are stated here
 * once instead, and carried only where one of them is the difference. A field that *does* discriminate is carried even when it never
 * changes — Qobuz's `account_authenticated`, Mac's empty `client_names` — because a
 * reader who only ever sees the field on the interesting scenario concludes it
 * only exists there.
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
 * Their audio state barely varies — READY until something is in session, and
 * `hasRichDisplay` returns true for them whatever the record carries, ERROR
 * excepted — so outside that one scenario the event alone cannot tell two of
 * theirs apart. What does is the *catalogue* condition, and that arrives over
 * HTTP rather than the socket.
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
 * Plain data, no `.vue` import: the guardrail reads this file to check the pages
 * against ALL_AUDIO_SOURCES, every event against the backend's own models, and
 * every fabricated metadata key against the files that read it.
 * `SourceStage.vue` is what turns a scenario into a mounted component.
 */
import stationImageTurntable from './samples/station-image-turntable.webp';
import stationImageCapsule from './samples/station-image-capsule.webp';
import { musicPlaceholder } from '@/constants/placeholders';

/** Prefix that tells a source page apart from a catalogue entry in `?c=`. */
export const SOURCE_PAGE_PREFIX = 'source:';

/**
 * Two *sample* cover widths — not two thresholds.
 *
 * There is exactly one threshold and it is not here: it is
 * `UNTRUSTED_SENDER_MIN_ARTWORK_PX` (300 px) in `constants/imageQuality.js`,
 * which is what `useRichDisplay` compares against. These two are what real
 * senders push on either side of it — a media app's artwork, and the favicon of
 * whatever page a browser tab is playing from — so a reader of the AirPlay tabs
 * sees the two things that actually happen rather than 299 and 301.
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
 * The files that read what these events carry. Checked key by key by the
 * guardrail, which is what stops a fixture outliving the field it fabricates.
 */
export const METADATA_READERS = [
  'App.vue',
  'components/audio/AudioSourceView.vue',
  'components/audio/AudioPlayerFull.vue',
  'composables/useRichDisplay.js',
  'composables/useSourceStatusDisplay.js',
  'composables/useSourceProgress.js',
  'utils/playbackBuffering.js',
  'utils/nowPlayingMetadata.js',
  'stores/cdStore.js'
];

/**
 * The files that turn a record into a screen — the app's deciders. Every field
 * in BEHAVIOURAL_FIELDS must be read by one of them, or it is not behavioural
 * and has no business in a scenario's name.
 *
 * "Which screen" is the first two; the rest decide which *face* of it, which is
 * the same kind of difference and worth the same tab. playbackBuffering answers
 * the spinner, useSourceProgress answers whether the playhead advances or is
 * frozen. That last one is the whole reason `is_playing` is still here: it used
 * to gate AirPlay's and DLNA's rich display, and when those clauses went it
 * stopped choosing a component — this list going red is how that was noticed —
 * but it still separates a paused player from a playing one.
 */
export const DECIDERS = [
  'composables/useRichDisplay.js',
  'composables/useSourceStatusDisplay.js',
  'components/audio/AudioSourceView.vue',
  'utils/playbackBuffering.js',
  'composables/useSourceProgress.js'
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
 * `full_state`: the source state (the backend enum value, verbatim), then
 * `network_unavailable` when set — it replaces the state on screen, so it
 * belongs in the name for the same reason the state does — followed by the
 * behavioural metadata it carries, then the catalogue condition for the three
 * sources that have one.
 */
export function scenarioId(events, browser) {
  const settled = events[events.length - 1].data.full_state;
  const metadata = settled.metadata || {};
  const facts = BEHAVIOURAL_FIELDS
    .filter(key => metadata[key] !== undefined)
    .map(key => spell(key, metadata[key]));

  return [
    settled.source_state,
    ...(settled.network_unavailable ? [settled.network_unavailable] : []),
    ...facts,
    ...(browser?.condition ?? [])
  ].join(' ');
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
function fullState(
  source, sourceState, metadata,
  { transitioning = false, error = null, networkUnavailable = null } = {}
) {
  return {
    active_source: source,
    source_state: sourceState,
    transitioning,
    metadata,
    error,
    multiroom_enabled: false,
    equalizer_effects_enabled: true,
    network_unavailable: networkUnavailable
  };
}

/**
 * `source/state_changed` — the event every source lifecycle change rides on.
 * `data` carries the model's own three fields plus the injected snapshot, and
 * the snapshot is what the app reads: `new_state` and `metadata` are duplicated
 * inside it, and only podcastStore takes the metadata straight off the event.
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
 * `system/state_changed` — the state machine's settled snapshot, emitted when
 * it has finished moving on its own rather than at a source's request. Like
 * `transition_start` it declares nothing of its own beyond `source: 'system'`:
 * the payload that matters is the injected `full_state`.
 */
function systemStateChanged(source, sourceState, metadata = {}, options = {}) {
  return envelope('system', 'state_changed', 'system', {
    source: 'system',
    full_state: fullState(source, sourceState, metadata, options)
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

/**
 * `transitioning` is what every source's first state actually looks like — but
 * it is only *one* of the two ways STARTING reaches the wire, and the tab
 * documents that one.
 *
 * A source switch goes through `transition_to_source`, which sets the flag and
 * empties the metadata. A multiroom reroute goes through `exclusive_transition`,
 * which deliberately does neither: the flag stays false so the STARTING push
 * reaches the UI at all, and `metadata=None` keeps the current track on screen
 * while the source is released and re-acquired. `useRichDisplay` short-circuits
 * on the flag, not on the state, so that second form draws the card only for the
 * six sources whose own gate needs ACTIVE — CD keeps its player (its gate never
 * looks at the state) and the three browsers keep their layout (theirs returns
 * true unconditionally). Not a tab, because there is no stimulus a
 * dispatcher page can send that reaches it: the reroute's record is the *last
 * source's* metadata under a new state, and reproducing that means replaying
 * two events to document a window the app crosses in a second. Stated here so
 * the tab below is not read as the whole of STARTING.
 */
function starting(source) {
  return scenario(
    [transitionStart(source)],
    'Starting',
    'transitioning — the status card takes over whatever the source is, and the spinner replaces its icon. One of the two states that read as a sentence broken over two lines ("Démarrage de" / the source), so the phrase leads and the source name takes the emphasised line — which is also why French needs three keys for it, agreeing the article with the source noun. The 500 ms floor holds the *card\'s* phrase, not the card: `shouldShowSourceStatus` reads `transitioning` raw, so a transition that completes into a rich display hands the screen over at once and the floor never applies.'
  );
}

/** Nothing is playing yet: the source is up and idle. */
function ready(source, label, note, metadata = {}) {
  return scenario([stateChanged(source, 'ready', metadata)], label, note);
}

function active(source, label, note, metadata) {
  return scenario([stateChanged(source, 'active', metadata)], label, note);
}

/**
 * `SourceState.ERROR` — the source is not operational, which one place writes:
 * the state machine, when a transition fails. It stops the target, leaves it
 * *selected* and keeps the message in `full_state.error`, then broadcasts the
 * settled snapshot — so this is a `system/state_changed`, not a source event,
 * and the metadata is empty because a source that never started has none.
 *
 * The message the user reads rides on `system/error` — the state machine's own
 * event, emitted just before the settled snapshot, which raises App.vue's
 * banner over whatever is on screen. Not `source/error`: that is the other
 * channel, for an operation that failed on a source still standing (a station
 * that will not tune), and `broadcast_error`'s docstring is explicit that the
 * two never ride together. Neither is replayed here — the banner belongs to
 * the app shell, not to the source's own screen.
 */
function errored(source, note, message) {
  return scenario(
    [systemStateChanged(source, 'error', {}, { error: message })],
    'Error',
    note
  );
}

/**
 * The link is missing what this source needs. The backend has already crossed
 * NetworkManager's level with the source's own NETWORK_REQUIREMENT, so the
 * scenario states the *answer* — the same one field the app reads — rather
 * than re-deriving it: `no_network` when nothing is reachable, `no_internet`
 * when the LAN is up but has no route out. Either one drops the source to the
 * status card, browser sources included: a favourites grid whose every tap
 * fails is a worse screen than one naming the reason.
 */
function offline(source, reason, note, metadata = {}) {
  return scenario(
    [systemStateChanged(source, 'ready', metadata, { networkUnavailable: reason })],
    reason === 'no_network' ? 'No network' : 'No internet',
    note
  );
}

/**
 * A browser source's scenario: the record, plus the browser's own setup.
 *
 * READY or ACTIVE follows the stand-in, because that is what the backend does:
 * all three publish through `emit_connection_state(bool(<the thing in session>))`
 * — a tuned station, a current episode, a non-empty queue — so a favourites grid
 * with no player pane is a READY record. Neither state changes *which component*
 * mounts here (`hasRichDisplay` returns true for these three whatever they
 * carry), which is precisely why every one of them could say `active` and
 * nothing noticed.
 *
 * It does decide something, though, which is why the pane and the state are one
 * switch rather than two fields: `useSourcePlaybackVisibility` shows the player
 * pane on ACTIVE and hides it on READY. A scenario that drew a pane while
 * sending READY — or the reverse — would be documenting a screen the app cannot
 * produce.
 *
 * `browser.metadata` is for the fields that survive `PlaybackMetadata.split`
 * and reach a decider: radio's `is_buffering` is the only one so far, and it is
 * what tells "the stream is opening" apart from "it is playing". The rest of
 * what these three publish (station_id, episode_uuid, the queue) is read by
 * their own stores, which the stage does not mount — that is what the fixtures
 * below stand in for.
 */
function browsing(source, label, note, browser) {
  return scenario(
    [stateChanged(source, browser.player ? 'active' : 'ready', browser.metadata ?? {})],
    label,
    note,
    browser
  );
}

/** Shared by the CD scenarios that have a disc: identity + its tracklist. */
const CD_DISC = {
  disc_present: true,
  cache_ready: true,
  disc_id: 'yvYlA5_2ZK6mQvZ1kZ0rXqLg7dM-',
  disc_album: 'Felt',
  disc_artist: 'Nils Frahm',
  disc_year: '2011',
  disc_cover_url: musicPlaceholder,
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
      'The only Connect source Milō drives back: AudioPlayerFull with the full transport. Its rich display is gated on title + artist alone — Spotify is a trusted metadata provider, so no cover-quality check. There is no "active, no metadata" scenario here any more: the source no longer publishes ACTIVE unless go-librespot answered with a track, so the gap between the session opening and the first track event now reads as ready instead of as a card with nothing to draw.',
    scenarios: [
      starting('spotify'),
      ready('spotify', 'Ready', 'Connected to go-librespot, no phone has picked the speaker yet.'),
      active('spotify', 'Playing', 'Rich display earned: AudioPlayerFull, progress bar and transport. The buttons report to the event log instead of reaching the unit.', {
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: musicPlaceholder,
        is_playing: true,
        position: 192000,
        duration: 511000
      }),
      active('spotify', 'Paused', 'Same record, is_playing false — the glyph flips and useSourceProgress stops ticking.', {
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: musicPlaceholder,
        is_playing: false,
        position: 192000,
        duration: 511000
      }),
      active('spotify', 'Buffering', 'is_buffering swaps the play/pause glyph for a spinner (isSourceBuffering). The bar keeps its last position.', {
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: musicPlaceholder,
        is_playing: true,
        is_buffering: true,
        position: 0,
        duration: 511000
      }),
      offline(
        'spotify',
        'no_internet',
        'The link is up but has no route out — go-librespot is running and unreachable at once. AudioPlayerFull would keep its transport pointing at a daemon that cannot resolve anything, so the card takes over and names the reason, with the network settings one tap away.'
      ),
      errored(
        'spotify',
        'go-librespot not coming up. The source stays selected — that is what makes the retry possible — and the card says so: "Spotify / Error", with a Retry CTA that re-posts the source selection and re-runs the transition the state machine gave up on. The message itself is the banner\'s.',
        'go-librespot failed to start'
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
      ready(
        'qobuz',
        'Ready',
        'Account connected, waiting for the app to pick the speaker. The extra rides every record the source publishes, this one included — which is the point of carrying it here: account_authenticated is not a field that appears when something is wrong, it is a field that is always there and is sometimes false.',
        { account_authenticated: true }
      ),
      ready(
        'qobuz',
        'Ready, no account',
        'account_authenticated false — the only path to the second CTA in AudioSourceStatus, and only an explicit false arms it, so the CTA cannot flash before the proxy has answered. Tapping it calls inject("openSettings"), which is absent here, so it no-ops.',
        { account_authenticated: false }
      ),
      active(
        'qobuz',
        'Active, before now_playing',
        'Reachable, but only as an escape hatch: the source holds an active status carrying no track for a few poll ticks, then commits anyway so a proxy that never delivers one cannot wedge it in READY. No client_name: the proxy exposes no controller identity, so currentDeviceName is empty and the generic active line prints "Qobuz / playing", the same one DLNA lands on, which is why neither needs a branch of its own any more.',
        { is_playing: true, account_authenticated: true }
      ),
      active('qobuz', 'Playing', 'Trusted CDN cover, so no album_art_width gate — title + artist is enough. Read-only bar above the source bar, which names no device: with no client_name on the record the bar falls back to the source\'s own label, "Qobuz".', {
        title: 'Ambre',
        artist: 'Nils Frahm',
        album_art_url: musicPlaceholder,
        is_playing: true,
        account_authenticated: true,
        position: 64000,
        duration: 264000
      }),
      offline(
        'qobuz',
        'no_internet',
        'Same link, and the reason outranks the account question: with no route out the proxy cannot tell whether an account exists, so "Account not connected" would be a guess. Network first, and its CTA replaces the connect one.'
      ),
      errored(
        'qobuz',
        'The proxy sidecar will not start, which is also the moment the account state stops being knowable — so the connect CTA gives way to the retry one. Not because error outranks the account — the card resolves a missing prerequisite first, so an explicit account_authenticated false would still win here. There is none: a source that failed to start publishes no metadata at all, so the reason resolves to null and the error branch is what is left.',
        'qobuz-proxy failed to start'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}tidal`,
    source: 'tidal',
    title: 'TIDAL',
    family: 'C — active player',
    uses: 'AudioSourceStatus · AudioPlayerFull (showControls, seekable false)',
    via: 'dispatcher',
    summary:
      'Spotify\'s shape reached through a Unix socket instead of a WebSocket: the phone hands over a queue and Milō drives it back. One difference is visible on screen — the tisoc protocol has no seek command at all, so this is the only controlled source whose progress bar is inert (seekable false). Trusted CDN cover, so the rich display is gated on title + artist alone, like Spotify and unlike the two untrusted senders below.',
    scenarios: [
      starting('tidal'),
      ready('tidal', 'Ready', 'The daemon acknowledged startService and is advertising over mDNS; no phone has picked the speaker yet. Reaching this state is the proof the source is usable — a daemon that never answers would reject every session.'),
      active('tidal', 'Playing', 'Rich display earned: transport plus a bar that draws position but refuses a scrub. The buttons report to the event log instead of reaching the unit.', {
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: musicPlaceholder,
        is_playing: true,
        position: 192000,
        duration: 511000
      }),
      active('tidal', 'Paused', 'Same record, is_playing false. A paused track keeps its session and its cover — the daemon reports the end of one explicitly, so nothing here is a stale leftover.', {
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: musicPlaceholder,
        is_playing: false,
        position: 192000,
        duration: 511000
      }),
      active('tidal', 'Buffering', 'The daemon passes through BUFFERING on every track change, so this is a normal step rather than a stall: the spinner replaces the glyph and the bar holds its last position.', {
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: musicPlaceholder,
        is_playing: true,
        is_buffering: true,
        position: 0,
        duration: 511000
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
    uses: 'AudioSourceStatus · AudioPlayerFull (showControls false, showProgress FALSE)',
    via: 'dispatcher',
    summary:
      'The one passive player with no progress bar: an AirPlay sender that pauses announces it on no channel shairport-sync can pass on (measured 2026-08-07 — pfls/pend never fire, core/caps holds 0x01, D-Bus PlayerState still says "Playing" 96 s in, FramePosition keeps counting because shairport writes silence), so is_playing stays true and the bar ran on through a paused track. The sender draws its own position. The untrusted-sender gate lives here too: title, artist AND a cover above UNTRUSTED_SENDER_MIN_ARTWORK_PX (300). The cover size is the whole of it — a sender that publishes a real one is a media app, one that publishes a favicon is a browser tab. What the gate deliberately does *not* read is is_playing: a sender that quits ends the session and the source publishes READY on its own, so the only thing left carrying is_playing=false is a pause, and the card is not the answer to a pause. Two states are missing from the tabs on purpose. The first: ACTIVE is reached on shairport\'s `conn`, before any audio flows, carrying nothing but the name off X-Apple-Client-Name — the window Qobuz documents as "Active, before now_playing". It has no tab because it draws the same "Connecté à / Leo’s iPhone" as the declined-cover scenario below, and a tab that repeats a screen teaches a reader to stop trusting that a new tab is a new screen. A pause has no tab for the same reason, and it is the sharper case of the two: the gate has no is_playing clause — a sender that really quits sends `disc`, which clears the track, the cover and the name and publishes READY, so the card comes back on its own — and with showControls and showProgress both off, this player reads the flag nowhere else either. No transport to flip, no bar to freeze. A paused sender therefore draws the playing tab below, to the pixel: same markup, same computed styles, measured.',
    scenarios: [
      starting('airplay'),
      ready('airplay', 'Ready', 'shairport-sync advertising, nobody streaming.'),
      active(
        'airplay',
        'Active, favicon cover',
        `album_art_width ${FAVICON_COVER_PX} is under UNTRUSTED_SENDER_MIN_ARTWORK_PX (300, and the only number here that is a rule), so the rich display is declined and the card names the sender instead. This is what browser audio looks like — a page favicon where a media app would push a real cover — and it is the only reason the gate exists. It is also the screen the pre-metadata window lands on, for a different reason the card cannot show: it reads neither the cover width nor the flag, only the sender's name.`,
        {
          title: 'Ainsi parlait Zarathoustra',
          artist: 'Alain Bashung',
          album_art_url: musicPlaceholder,
          is_playing: true,
          client_name: 'Leo’s iPhone',
          album_art_width: FAVICON_COVER_PX
        }
      ),
      active('airplay', 'Playing', 'All three conditions met. The source bar carries the sender name. position and duration ride the record — the source ages them for a client connecting mid-track — but no bar is drawn from them, because nothing tells this source when the sender paused.', {
        title: 'Ainsi parlait Zarathoustra',
        artist: 'Alain Bashung',
        album_art_url: musicPlaceholder,
        is_playing: true,
        client_name: 'Leo’s iPhone',
        album_art_width: MEDIA_APP_COVER_PX,
        position: 41000,
        duration: 297000
      }),
      offline(
        'airplay',
        'no_network',
        'The LAN-only source\'s own case: shairport-sync needs the local network and nothing beyond it, so a router with no internet leaves it working and this scenario never fires. What does fire is the link disappearing entirely — no sender can reach the unit, and "Ready to connect" would be an invitation to nothing.'
      ),
      errored(
        'airplay',
        'shairport-sync failing to start is the common case — the port is taken, or the ALSA device is busy. The sender name goes with it, so the source that most depends on naming its sender falls back to naming itself: "AirPlay / Error", with the retry.',
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
      'Same untrusted-sender gate as AirPlay, and the same player — the difference is identity: UPnP exposes no "who is casting", so currentDeviceName hard-returns empty. That used to need a DLNA-only branch in the card; the generic active line ("DLNA / playing", whenever there is no sender to name) covers it now, and Qobuz’s twin went with it. client_name here is not a controller name either: it is the media *server* the audio is streamed from, resolved over SSDP seconds after playback starts, and absent until it is — the player’s source bar reads its own source label meanwhile.',
    scenarios: [
      starting('dlna'),
      ready(
        'dlna',
        'Ready',
        'The UPnP renderer is advertised, no controller has pushed anything. No client_name: nothing has been streamed yet, so no media server has been resolved — and an idle renderer would have none to name anyway.'
      ),
      active(
        'dlna',
        'Active, no cover',
        'A controller pushing a bare title: no album_art_width, so the gate declines. The generic active line prints "DLNA / playing" — the shape every source without a sender to name lands on, and what used to be a per-source branch dodging an idle fallback.',
        { title: 'Untitled', is_playing: true }
      ),
      active('dlna', 'Playing', 'A full-fat controller: cover above the floor, audio flowing, and the SSDP sweep has come back — so the source bar names the media server the track is streamed from instead of the source. Position is corrected every 30 s — the longest interval of any source that has one — so most of what the bar shows here is useSourceProgress interpolating.', {
        title: 'Hammers',
        artist: 'Nils Frahm',
        album_art_url: musicPlaceholder,
        is_playing: true,
        client_name: 'Freebox Server',
        album_art_width: MEDIA_APP_COVER_PX,
        position: 88000,
        duration: 331000
      }),
      active(
        'dlna',
        'Paused',
        'Same record, is_playing false — and the player stays, exactly as AirPlay\'s does: the gate has no playing clause, because a controller that pauses keeps the renderer connected and the flag alone cannot tell that from a session still going. What it *is* is the start of a countdown. A pause arms the source\'s auto-stop, and at T+audio.auto_stop_delay (two minutes by default — the 10 s in the constructor is a placeholder the settings overwrite) DlnaSource resets and publishes READY, which lands the screen on the idle card above with the phone still paused and still connected. AirPlay ends the same way by restarting shairport; the difference is invisible here and is the whole of why neither needs an is_playing clause.',
        {
          title: 'Hammers',
          artist: 'Nils Frahm',
          album_art_url: musicPlaceholder,
          is_playing: false,
          client_name: 'Freebox Server',
          album_art_width: MEDIA_APP_COVER_PX,
          position: 88000,
          duration: 331000
        }
      ),
      offline(
        'dlna',
        'no_network',
        'Same LAN requirement as AirPlay, same single case. A controller that cannot see the renderer is indistinguishable from a renderer that never advertised, which is why the card names the link rather than the source.'
      ),
      errored(
        'dlna',
        'The renderer failing to advertise. DLNA has no device name to lose, so the error screen is the same two-line shape as its idle one — the source, then the phrase — and the phrase is the whole difference, which is the point of dropping the fallback.',
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
      'The widest state matrix of any source here, and the one whose rich-display rule ignores source_state entirely: a disc that is loaded and ready shows the player whether it is playing or idle. Three of the screens below exist nowhere in the backend enum, all derived by useSourceStatusDisplay from the metadata of a READY record — which is exactly why those scenarios are named by the fields they set rather than by a state. Two of them are operations under way, loading_disc and ejecting, and they join the four backend members in DISPLAY_STATES. The third, an empty drive bay, is not a state at all but a missing prerequisite, and it sits with no_network / no_internet / no_account in UNAVAILABLE_REASONS — the one of the four with no CTA, since plugging a drive in is not something the UI can offer.',
    scenarios: [
      starting('cd'),
      ready(
        'cd',
        'No drive',
        'drive_connected false — the source is up but the hardware is missing. useSourceStatusDisplay reads this one field into the reason "no_drive", which replaces whatever the state would otherwise have said.',
        { drive_connected: false }
      ),
      ready(
        'cd',
        'Drive empty',
        'Drive present, no disc: the plain idle line, and the only CD scenario that reaches the generic READY branch.',
        { drive_connected: true }
      ),
      ready(
        'cd',
        'Reading the disc',
        'disc_present with no cache_ready/disc_id yet — the MusicBrainz lookup is in flight. Pseudo-state "loading_disc", spinner in place of the icon. A fallback DiscInfo always sets disc_id, so this window cannot hang.',
        { drive_connected: true, disc_present: true }
      ),
      active(
        'cd',
        'Spinning up the drive',
        'The window `_preload_track_1` opens on every start with a disc in: reader and mpv are loaded *paused* so a play tap resumes instantly, and while the drive spins up `_is_buffering` alone carries the record into ACTIVE — `is_playing` and `is_paused` are both still false. So the player is on screen with the spinner over the glyph and no bar at all: `_build_metadata` zeroes position and duration until a session is live or paused, and ProgressBar hides itself on a zero duration. Both fields are on the record — at 0 — which is why they are on the fixture too. It settles into the tab below a second later, when the preload parks itself paused.',
        {
          ...CD_DISC,
          drive_connected: true,
          title: 'Keep',
          artist: 'Nils Frahm',
          album_art_url: musicPlaceholder,
          is_playing: false,
          is_buffering: true,
          current_track: 1,
          position: 0,
          duration: 0
        }
      ),
      ready(
        'cd',
        'Disc ready, not playing',
        'source_state is still "ready" and the player shows anyway — the CD branch of hasRichDisplay never looks at the state. The backend projects the idle view here: track 1’s title and the disc artist, with position and duration zeroed so the bar stays hidden until a session is live.',
        {
          ...CD_DISC,
          drive_connected: true,
          is_playing: false,
          title: 'Keep',
          artist: 'Nils Frahm',
          album_art_url: musicPlaceholder,
          current_track: 1,
          position: 0,
          duration: 0
        }
      ),
      ready(
        'cd',
        'Disc not identified',
        'The same screen as above with the MusicBrainz lookup having found nothing — a burned disc, an obscure pressing, or any disc while the unit is offline. `_build_fallback_disc_info` answers with the TOC alone: a disc_id (so this is not the loading window), generic "Track N" titles from the real track count and durations, and album/artist/year/cover all None. The rich-display rule admits it anyway, since it asks for disc_present + cache_ready and never for an artist, so the player draws the track title it does know over "Unknown Artist" — which is the honest label here, the artist genuinely being unknown — and the disc placeholder stands in for the cover. This is the only CD record with no artist, and it is what AudioPlayerFull\'s snapshot rule had to be relaxed for: demanding title AND artist left the player on its empty seed and showed "Unknown Title" over a tracklist that listed the tracks correctly.',
        {
          drive_connected: true,
          disc_present: true,
          cache_ready: true,
          disc_id: 'JXbxvhCUq4rHKnvNGkzZgL3xIxA-',
          disc_album: null,
          disc_artist: null,
          disc_year: null,
          disc_cover_url: null,
          track_count: 4,
          tracks: [
            { number: 1, title: 'Track 1', duration: 312000 },
            { number: 2, title: 'Track 2', duration: 96000 },
            { number: 3, title: 'Track 3', duration: 268000 },
            { number: 4, title: 'Track 4', duration: 401000 }
          ],
          title: 'Track 1',
          artist: null,
          album_art_url: null,
          is_playing: false,
          current_track: 1,
          position: 0,
          duration: 0
        }
      ),
      active(
        'cd',
        'Changing track',
        'A track change republishes the *target* track at position 0 with is_buffering set, on purpose and before the ~1 s reader restart, so the bar snaps to 0:00 and freezes instead of interpolating the outgoing position and then jumping back. The spinner replaces the glyph (isSourceBuffering). The drive’s other buffering window is the preload above, and it is a different screen rather than the same one from the other side: there the bar is not drawn at all.',
        {
          ...CD_DISC,
          drive_connected: true,
          title: 'Kind',
          artist: 'Nils Frahm',
          album_art_url: musicPlaceholder,
          is_playing: true,
          is_buffering: true,
          current_track: 3,
          position: 0,
          duration: 268000
        }
      ),
      active('cd', 'Playing', 'AudioPlayerFull with the full transport. hasNext is false on the last track, mirroring the backend’s "next" no-op.', {
        ...CD_DISC,
        drive_connected: true,
        title: 'Kind',
        artist: 'Nils Frahm',
        album_art_url: musicPlaceholder,
        is_playing: true,
        current_track: 3,
        position: 74000,
        duration: 268000
      }),
      active(
        'cd',
        'Paused',
        'ACTIVE with is_playing false, which is a different record from "Disc ready, not playing" above even though both draw the player: a paused session is still a session (`_is_paused` counts towards the ACTIVE gate) so the backend keeps publishing position and duration, and the bar is drawn and frozen rather than hidden. The idle projection zeroes both, which is what hides it. Auto-stop is armed here too and lands on that idle screen: `_auto_stop_action` releases the drive but keeps `_current_track` and the position, so the disc stays visible and a tap on play resumes the same track.',
        {
          ...CD_DISC,
          drive_connected: true,
          title: 'Kind',
          artist: 'Nils Frahm',
          album_art_url: musicPlaceholder,
          is_playing: false,
          current_track: 3,
          position: 74000,
          duration: 268000
        }
      ),
      ready(
        'cd',
        'Ejecting',
        'ejecting wins over a ready disc in hasRichDisplay, so the player gives way to the card mid-eject rather than lingering over a disc that is leaving.',
        { ...CD_DISC, drive_connected: true, ejecting: true }
      ),
      errored(
        'cd',
        'The one source whose rich display ignores source_state — except here: ERROR drops to the card before the CD branch is ever reached, so a disc still in the drive is no longer drawn. The metadata goes with the failed start, which is also what tells the three pseudo-states apart from this one: they are READY records about the drive, not a source that failed.',
        'cd-paranoia failed to open the drive'
      )
    ]
  },

  {
    id: `${SOURCE_PAGE_PREFIX}bluetooth`,
    source: 'bluetooth',
    title: 'Bluetooth',
    family: 'C — active player',
    uses: 'AudioSourceStatus · AudioPlayerFull (showControls, seekable false)',
    via: 'dispatcher',
    summary:
      'The one source whose two feeds answer different questions: BlueALSA says who is connected, BlueZ AVRCP says what is playing — and the second is optional. So this is also the only source that moves between the card and the player on metadata alone, which is what the first two active records below show. AVRCP has no seek (inert bar, like TIDAL) and carries no cover either, so the one in the artwork slot was looked up from the track text by shared/artwork_resolver.py — album first, iTunes — and merged in at publish time; a miss leaves the slot on the source glyph. The disconnect CTA appears twice: on the card, and again as the player’s action button, since the card is gone exactly when a user wants to kick the phone off.',
    scenarios: [
      starting('bluetooth'),
      ready('bluetooth', 'Ready', 'Discoverable, nothing paired-and-connected. No CTA in this state.'),
      active('bluetooth', 'Connected, no AVRCP', 'A sender that publishes no player — or publishes an empty track — stays on the card: device_name fills the second line and the disconnect CTA appears. It routes through sendCommand, so here it reports to the event log.', {
        device_name: 'Leo’s iPhone'
      }),
      active('bluetooth', 'Playing', 'Title + artist is the whole gate — requiring the artist is what does the work AirPlay gets from its cover-size check, since a web video publishes a title and rarely an artist. Transport plus a bar that draws position and refuses a scrub. The cover is not the sender’s: AVRCP carries none, so it was resolved from this track’s own text and can perfectly well be absent, which leaves the slot on the source glyph.', {
        device_name: 'Leo’s iPhone',
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: musicPlaceholder,
        is_playing: true,
        position: 192000,
        duration: 511000
      }),
      active('bluetooth', 'Paused', 'Same record, is_playing false. Like the two untrusted senders there is no is_playing clause in the gate, and here there could not be one even in principle: this player draws a pause button, and dropping to the card on pause would delete the button that was just pressed.', {
        device_name: 'Leo’s iPhone',
        title: 'Says',
        artist: 'Nils Frahm',
        album_art_url: musicPlaceholder,
        is_playing: false,
        position: 192000,
        duration: 511000
      }),
      errored(
        'bluetooth',
        'bluealsa failing to come up takes the device name with it, so the disconnect CTA goes with the line it belonged to — the card cannot offer a disconnect from a source it can no longer address. What sits in the same slot instead is the retry, which is the one action that still means something here.',
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
      'The other mute receiver, and the only source whose device name is an array: several Macs can stream over ROC at once, and formatDeviceNames joins them across two lines. The card shows no CTA at all — its disconnect branch is Bluetooth\'s alone, which is also why the store\'s `disconnectSource("mac")` returning true without sending anything is never reached. The sender stops from its own side, and there is nothing for Milō to end: roc-vad streams unbroken 44.1 kHz for as long as Milō is the Mac\'s output, silence included, so "connected" here can never mean "playing" and there is no idle edge an auto-stop could key on either.',
    scenarios: [
      starting('mac'),
      ready(
        'mac',
        'Ready',
        'roc-recv is listening; no Mac is sending. The array is on the record either way — it is the source\'s one extra and it passes through in both states — so idle is an empty list rather than an absent field, which is what makes "one entry" below a length and not a presence.',
        { client_names: [] }
      ),
      active('mac', 'One Mac streaming', 'client_names is an array even with a single entry, which is why the name spells its length rather than its presence.', {
        client_names: ['Leo’s MacBook']
      }),
      active('mac', 'Two Macs streaming', 'The case the array exists for — formatDeviceNames breaks the second line, which is why status-line-2 carries white-space: pre-line.', {
        client_names: ['Leo’s MacBook', 'Studio iMac']
      }),
      offline(
        'mac',
        'no_network',
        'ROC is a LAN stream, so only a dead link blocks it. The sender list is the one Mac field this scenario does not send, and the only one whose absence is not a shortcut: a missing prerequisite outranks the state, so the card stops at the phrase and never reaches the "audio received from" wording that reads the names. Empty by construction here anyway — with no network there is nothing to receive from.'
      ),
      errored(
        'mac',
        'roc-recv failing to bind its port. The sender list empties, so the "audio received from" line — the one wording the card still chooses per source, because a ROC stream is not a connection to one device — gives way to the same two-line error screen every other source gets.',
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
      'hasRichDisplay returns true for the three browser sources whatever they carry — their own layout handles empty and loading — so the status card is reached in exactly two places: while transitioning, and in ERROR, which is checked before the per-source rules precisely because a browser whose every tap would fail is worse than no browser. The player is the one with no progress bar at all: a live stream has no duration, which is also why its command is resume_playback (re-tune) rather than resume.',
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
      browsing('radio', 'Tuning a station', 'ACTIVE before a single byte of audio: `_handle_play_station` sets the station and `_is_buffering` and publishes *before* trying the stream, so `emit_connection_state(bool(_current_station))` is already true. The pane is therefore in — the app shows it on ACTIVE, not on is_playing — with the station drawn and the transport spinning, while bufferingStationId marks the same card in the grid and the rest of it stays live, so a second tap goes somewhere rather than being swallowed by a full-screen loader. The one scenario here whose record carries metadata: `is_buffering` is what separates this from the playing tab *in the name*, and on a unit it is what isSourceBuffering reads. Not here — hasRichDisplay has already returned true for a browser source, so no decider looks at the record again, and the spinner on this page is the pane\'s own isLoading, transcribed below.', {
        condition: ['bufferingStationId'],
        layout: RADIO_HEADER,
        view: 'radio-favourites',
        metadata: { is_buffering: true },
        // The marked card in the grid, and only that: it comes from
        // RadioSource's own computed (isBuffering + metadata.station_id), which
        // lives in the wrapper the stage replaces — hence the prop.
        // `currentStation` and `isPlaying` are deliberately not passed, though
        // the wrapper does pass them: FavoritesView reads the first only as
        // `currentStation?.id === station.id && isPlaying`, and nothing is
        // playing yet, so neither would change anything on screen. The pane's
        // station is the fixture's, below.
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
        'mpv not coming up. Like the offline case above, the app leaves the browser by itself rather than drawing a favourites grid whose every tap would fail — so this is one of the two scenarios here that needs no stand-in. The favourites are still there; the retry CTA is what brings them back.',
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
      'The same two parts as Radio, with a progress bar and a swipe gesture — but swipeEnabled without a tracks queue, so the swipe seeks (±15/30 s) instead of skipping and no text carousel is built. Its header is the one that changes shape as you descend: title, subtitle and the back affordance are all driven by the current view.',
    scenarios: [
      starting('podcast'),
      browsing('podcast', 'Browsing the charts', 'The home view, three header actions and no back. Unlike the other two browsers this one fetches from the component rather than a store, so its charts are served as an HTTP fixture — the real loadData() runs.', {
        condition: ['results'],
        layout: PODCAST_HEADER,
        view: 'podcast-home',
        api: {
          '/api/podcast/discover/top-charts': { results: PODCAST_CHARTS },
          '/api/podcast/subscriptions': { subscriptions: PODCAST_SUBSCRIPTIONS }
        },
        player: null
      }),
      browsing('podcast', 'Catalogue unavailable', 'Podcast Index did not answer, so the backend sets `api_error` instead of failing — a distinct branch from "no results", and the only one that says why the chart is empty. Deliberately not the status card: the loss is one block. The subscriptions above it are local data and still play, which is the whole reason this stays a per-view message with a retry.', {
        condition: ['api_error'],
        layout: PODCAST_HEADER,
        view: 'podcast-home',
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
        'Distinct from api_error, which stays: that one says Podcast Index did not answer and is perfectly reachable while online. This one says the link itself has no route out, so the catalogue, the feeds and the audio are all gone at once.'
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

/** True for a `?c=` value that names a source page rather than a component. */
export function isSourcePageId(id) {
  return typeof id === 'string' && id.startsWith(SOURCE_PAGE_PREFIX);
}

/** Every envelope the 11 pages can emit, for the checks against the models. */
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
