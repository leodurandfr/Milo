// frontend/src/components/gallery/catalog.js
/**
 * The component catalogue behind /components.
 *
 * Metadata only — no Vue import, no component reference — for two reasons:
 * the page reads it for its headings and nav, and
 * tests/architecture/gallery.test.js reads it under Node to check that every
 * file in scope is listed and that no entry points at a deleted file.
 * The demos themselves live in demos/, one file per group.
 *
 * ## What is in scope, and why it is not a glob
 *
 * v1 was `components/ui/` and nothing else, which a directory listing could
 * check in both directions. The catalogue now also carries the *shared
 * composites* — the audio parts three or more features build their screens out
 * of, and the four settings wrappers half the app is made of — and those two
 * directories cannot be globbed: `AudioSourceView.vue` is a dispatcher with no
 * appearance of its own, `SettingsModal.vue` is an application.
 *
 * So SCOPE names the directories, and every `.vue` directly inside one is
 * either catalogued below or listed in EXCLUDED **with a reason**. That keeps
 * the property v1 had — a shared composite cannot land unlisted — without
 * forcing the dispatchers onto a page that exists to be looked at. Scanning is
 * one level deep on purpose: `settings/categories/` is per-feature screens,
 * which are coupled to their stores and would mean faking state.
 *
 * coupling says why an entry is not a pure props-in component: it reads a
 * store, is driven by a composable, or is position: fixed app chrome. It is
 * documentation rather than a capability flag — every entry renders in the
 * playground — and it earns its place because a reader who takes Dock for a
 * reusable primitive is the mistake worth preventing.
 */

/** Directories the guardrail scans, one level deep. */
export const SCOPE = [
  'components/ui',
  'components/audio',
  'components/settings',
  'components/radio',
  'components/podcasts',
];

/**
 * Screens, told apart from shared parts by name rather than one by one.
 *
 * A source directory mixes the two: `podcasts/` holds seven screens next to
 * four cards. Listing every screen in EXCLUDED would mean eighteen near-identical
 * paragraphs, which is noise a reader learns to skip — a rule stated once is
 * something they can check. A screen owns a store and a route's worth of state;
 * a part takes props. `AudioSourceView` is the archetype: useRichDisplay()
 * decides which player mounts and the file has no appearance of its own.
 *
 * `Skeleton*` is the exception the pattern needs: SkeletonPodcastDetails ends in
 * Details and is a loading placeholder, i.e. exactly the kind of pure part this
 * page exists to show.
 */
export function isScreen(file) {
  const name = file.split('/').pop().replace(/\.vue$/, '');
  if (name.startsWith('Skeleton')) return false;
  return /(View|Details|Source)$/.test(name);
}

/**
 * In scope, not a screen by name, and still deliberately not catalogued. The
 * reason is the point: it is what a reader gets instead of the entry, and what
 * the next person has to disagree with in writing before adding one.
 */
export const EXCLUDED = {
  'components/settings/SettingsModal.vue':
    'The settings application — ~840 lines wiring a dozen stores and every category screen. Its four building blocks are catalogued instead.',
};

/** Groups, in page order. */
export const GROUPS = [
  {
    id: 'actions',
    title: 'Actions',
    blurb: 'Everything that is tapped to do something. All four apply v-press internally.',
  },
  {
    id: 'controls',
    title: 'Input & controls',
    blurb: 'Value carriers. Each is v-model-based, and each emits change alongside update:modelValue so a caller can persist without watching.',
  },
  {
    id: 'feedback',
    title: 'Feedback & state',
    blurb: 'What the screen shows while it waits, and what it shows when there is nothing to show.',
  },
  {
    id: 'media',
    title: 'Media & content',
    blurb: 'Images and the two icon registries. Both grids below are derived from the components own registries, so a new icon appears here for free.',
  },
  {
    id: 'structure',
    title: 'Structure & overlays',
    blurb: 'Page chrome. Three of the four are position: fixed app furniture rather than primitives you compose with.',
  },
  {
    id: 'player',
    title: 'Player parts',
    blurb: 'The pieces the two shared players and the five browsers are assembled from. All five are props-in / events-out and know no store, which is exactly why they are shared.',
  },
  {
    id: 'layout',
    title: 'Source layouts',
    blurb: 'The five full-surface shapes a source can take: the browsing layout, the two shared players, the idle status card, and the screensaver over all of them. Which player mounts — and whether the status card takes over instead — is decided in one place, useRichDisplay().',
  },
  {
    id: 'cards',
    title: 'Cards & skeletons',
    blurb: 'What a browsing source lists, and the placeholder each one shows while it loads. The demos pair them: a skeleton\'s whole job is to have the shape of the card it stands in for, and nowhere else in the app do the two ever appear together.',
  },
  {
    id: 'settings',
    title: 'Settings composites',
    blurb: 'Four wrappers, ~130 lines together, behind every settings screen in the app — and the most-reused components in the frontend. They carry a title and a gap; everything else is slot content.',
  },
];

/** One entry per catalogued file, grouped as the sidebar lists them. */
export const ENTRIES = [
  // --- Actions ---
  {
    id: 'Button',
    group: 'actions',
    file: 'components/ui/Button.vue',
    summary: '7 variants x 2 sizes. loading keeps the variant styling; loading + disabled greys out. A labelless spinner button is IconButton, not this.',
  },
  {
    id: 'IconButton',
    group: 'actions',
    file: 'components/ui/IconButton.vue',
    summary: 'Square, aspect-ratio 1. The icon colour is derived from the variant unless color overrides it.',
  },
  {
    id: 'ButtonGroup',
    group: 'actions',
    file: 'components/ui/ButtonGroup.vue',
    summary: 'Segmented control over options. The selected option is always outline; inactiveVariant styles the rest. mobileLayout picks the reflow below 4:3.',
  },
  {
    id: 'ListItemButton',
    group: 'actions',
    file: 'components/ui/ListItemButton.vue',
    summary: 'The settings row. action swaps the trailing affordance (caret / Toggle / Radio); interactive: false renders a plain div for a read-only row.',
  },

  // --- Input & controls ---
  {
    id: 'Toggle',
    group: 'controls',
    file: 'components/ui/Toggle.vue',
    summary: 'Boolean switch. compact is the size ListItemButton embeds.',
  },
  {
    id: 'ToggleSection',
    group: 'controls',
    file: 'components/ui/ToggleSection.vue',
    coupling: 'modal',
    summary: 'A SettingsSection whose header toggle expands its content. Injects modalRequestHeightDelta to let a host Modal spring to the new height — the inject is null-safe, so it degrades to a plain CSS grid animation here.',
  },
  {
    id: 'Radio',
    group: 'controls',
    file: 'components/ui/Radio.vue',
    summary: 'A single boolean pill, not a group: exclusivity is the callers job.',
  },
  {
    id: 'InputText',
    group: 'controls',
    file: 'components/ui/InputText.vue',
    summary: 'Text field. On the unit it routes focus to VirtualKeyboard rather than the native one, and the canvas forces that path on with ?virtualKeyboard=true — so tapping this opens the kiosk keyboard here too, which is the behaviour a desktop browser could not otherwise show.',
  },
  {
    id: 'Dropdown',
    group: 'controls',
    file: 'components/ui/Dropdown.vue',
    summary: 'Select over options. displayOverride shows a computed label while keeping the raw value.',
  },
  {
    id: 'RangeSlider',
    group: 'controls',
    file: 'components/ui/RangeSlider.vue',
    summary: 'Single-value slider, horizontal or vertical. Emits drag-start/drag-end so a caller can throttle writes to the value and commit once.',
  },
  {
    id: 'DoubleRangeSlider',
    group: 'controls',
    file: 'components/ui/DoubleRangeSlider.vue',
    summary: 'Min/max pair with a gap floor between the handles. modelValue is { min, max }.',
  },
  {
    id: 'VirtualKeyboard',
    group: 'controls',
    file: 'components/ui/VirtualKeyboard.vue',
    coupling: 'composable',
    summary: 'The kiosk keyboard. Mounted once at app level and driven by useVirtualKeyboard() rather than placed by hand, so the Actions below open it through that composable instead of through props. It normally refuses to render off the unit (isKiosk); the canvas overrides that with ?virtualKeyboard=true.',
  },

  // --- Feedback & state ---
  {
    id: 'LoadingSpinner',
    group: 'feedback',
    file: 'components/ui/LoadingSpinner.vue',
    summary: 'Indeterminate spinner, drawn in currentColor at the same optical weight as an icon of the same size — so it can stand in for one. It carries no surface: the light plate it used to offer is AppIcon\'s loading state.',
  },
  {
    id: 'NotificationBanner',
    group: 'feedback',
    file: 'components/ui/NotificationBanner.vue',
    summary: 'Inline notice. Also the shape the WebSocket log handler renders backend errors into.',
  },
  {
    id: 'MessageContent',
    group: 'feedback',
    file: 'components/ui/MessageContent.vue',
    summary: 'The empty/error/loading state, with up to two CTAs. loadingDelay holds the spinner back so a fast response never flashes one. dark drops the card for use over artwork.',
  },

  // --- Media & content ---
  {
    id: 'LazyImage',
    group: 'media',
    file: 'components/ui/LazyImage.vue',
    summary: 'Artwork with a fallback chain: src, then fallbackName (a deterministic generated avatar) or fallback (a static asset). lazy defers the fetch; the default slot overlays the image.',
  },
  {
    id: 'SvgIcon',
    group: 'media',
    file: 'components/ui/SvgIcon.vue',
    summary: 'Inline SVG, recoloured to currentColor and given per-instance ids so two copies cannot collide on url(#id). A string size (small/medium/large) sizes from CSS instead of attributes.',
  },
  {
    id: 'AppIcon',
    group: 'media',
    file: 'components/ui/AppIcon.vue',
    summary: 'The per-source app tile. Rendered as-authored (no recolouring) — these carry brand colour. loading swaps the artwork for a spinner and keeps the tile, so a source coming up does not leave a hole where its icon sits.',
  },
  {
    id: 'Logo',
    group: 'media',
    file: 'components/ui/Logo.vue',
    coupling: 'fixed',
    summary: 'The wordmark, position: fixed at one of two anchors. In the playground those anchors resolve against the iframe, so they land where they land in the app; in Variants it sits inside a transformed box, which is what confines a fixed child to a card.',
  },

  // --- Structure & overlays ---
  {
    id: 'Modal',
    group: 'structure',
    file: 'components/ui/Modal.vue',
    summary: 'Teleported overlay that springs to its content height and provides modalRequestHeightDelta to descendants that change size.',
  },
  {
    id: 'NavigationHeader',
    group: 'structure',
    file: 'components/ui/NavigationHeader.vue',
    summary: 'Title bar with an optional back affordance. The actions slot receives the icon variant matching the header variant, so trailing IconButtons stay legible on both.',
  },
  {
    id: 'Dock',
    group: 'structure',
    file: 'components/ui/Dock.vue',
    coupling: 'store',
    summary: 'The bottom source switcher: position: fixed, reads three stores (dock order from settings, lyrics availability, active source) and emits the four app-opening events. App furniture mounted once, not a primitive to reuse. The State section drives what it reads; Reveal taps its own drag pill, one of the three paths a user has.',
  },
  {
    id: 'VolumeBar',
    group: 'structure',
    file: 'components/ui/VolumeBar.vue',
    coupling: 'store',
    summary: 'The transient volume readout. position: fixed, visible only while unifiedAudioStore.showVolumeBar is set, and its fill interpolates the volume between the two configured limits — all four of those live in stores, so they are in the State section rather than the props table.',
  },

  // --- Player parts ---
  {
    id: 'ProgressBar',
    group: 'player',
    file: 'components/audio/ProgressBar.vue',
    summary: 'The one playback bar, in five surfaces. Positions and durations are always milliseconds — the wire convention — and seek is emitted in ms too. It self-hides when duration is 0 or isReady is false, which is how a source that reports no duration (radio, Qobuz) shows no bar rather than an empty one.',
  },
  {
    id: 'PlaybackControls',
    group: 'player',
    file: 'components/audio/PlaybackControls.vue',
    summary: 'prev / play-pause / next, with the centre button larger than its neighbours. isBuffering swaps the glyph for a spinner while a source spins up; hasNext false disables next for the sources with no "last track". Emits only — it holds no playback state.',
  },
  {
    id: 'PlayerInfoText',
    group: 'player',
    file: 'components/audio/PlayerInfoText.vue',
    summary: 'The three stacked lines every player shows: an optional kicker with its own thumbnail (station, podcast), the title, and a secondary line. Text and one image, no layout of its own — each player positions it.',
  },
  {
    id: 'TrackRow',
    group: 'player',
    file: 'components/audio/TrackRow.vue',
    summary: 'The tracklist row, shared by CD and six Music Library views. Its four state props are a matrix, not a list: current + playing swaps the number for the equaliser bars, editing swaps duration + menu for remove + drag grip, showCover prepends the thumbnail, showArtist adds the second line.',
  },
  {
    id: 'DetailHeader',
    group: 'player',
    file: 'components/audio/DetailHeader.vue',
    summary: 'The album / playlist / episode header: cover art, or a tinted icon tile when icon is set instead (the virtual headers — Liked Songs, a genre). Up to three text lines, and an actions slot that renders before the built-in favourite / shuffle / play buttons.',
  },

  // --- Source layouts ---
  {
    id: 'AudioPlayer',
    group: 'layout',
    file: 'components/audio/AudioPlayer.vue',
    summary: 'The player for the three sources that have a browser (Radio, Podcasts, Music Library). Props-down / events-up over eleven props — it knows no store and no command name, which is what lets one component serve three sources. Its second form is only reachable through the Phone viewport: the Teleport is disabled above 4:3, so below it the docked sidebar card becomes a mini-bar teleported to body, expanding into a full sheet.',
  },
  {
    id: 'AudioPlayerFull',
    group: 'layout',
    file: 'components/audio/AudioPlayerFull.vue',
    coupling: 'store',
    summary: 'The player for the sources with nothing to browse (Spotify, CD, AirPlay, DLNA, Qobuz). Unlike AudioPlayer it reads unifiedAudioStore itself and sends its own commands, so the now-playing record sits in the State section rather than the props table, and the four booleans are the whole layout matrix. Its progress is gated on active_source matching the source prop — point them at different sources and the bar freezes, which is the guard useSourceProgress exists for.',
  },
  {
    id: 'AudioSourceLayout',
    group: 'layout',
    file: 'components/audio/AudioSourceLayout.vue',
    summary: 'The browsing layout behind Radio, Podcasts and Music Library: a scroll container, a header, cross-faded content and a player pane that animates in beside it. The cross-fade is driven by contentKey — change it and the current content leaves as the next enters. The nine header* props are forwarded one by one to NavigationHeader.',
  },
  {
    id: 'AudioSourceStatus',
    group: 'layout',
    file: 'components/audio/AudioSourceStatus.vue',
    summary: 'The card shown whenever the active source has no rich display to give. Both lines are derived from (sourceType, displayState) over ten sources and seven states — the four backend ones plus CD\'s three — so the two selects below are the whole component. There is no fall-through: line 1 names the source and line 2 says what it is doing, except in the two cases that read as one sentence over two lines — "Démarrage de <source>" and "Connecté à <sender>" — where the phrase leads and the name takes the emphasis. Three mutually exclusive CTAs hang off it: retry on error, Bluetooth disconnect while active, Qobuz connect while ready without an account.',
  },
  {
    id: 'AudioScreensaver',
    group: 'layout',
    file: 'components/audio/AudioScreensaver.vue',
    coupling: 'fixed',
    summary: 'The idle takeover: position: fixed at z-index 7000, over whatever was on screen. media mode is blurred artwork + title + an optional station bar and progress bar; simple mode is an icon and two lines (Bluetooth, Mac). Turning isVisible off plays the leave animation, which lifts each element towards where its AudioPlayerFull counterpart sits. The bottom bar is gated on stationName alone — stationIcon without it renders nothing.',
  },

  // --- Cards & skeletons ---
  {
    id: 'StationCard',
    group: 'cards',
    file: 'components/radio/StationCard.vue',
    summary: 'A radio station, in two shapes the same component serves: `card` is the horizontal row of the search and favourites lists, `image` is the bare favicon tile of the favourites grid. It mounts SkeletonStationCard over itself until the artwork resolves, so the generated SVG fallback never pops into view — the only card here that owns its own skeleton.',
  },
  {
    id: 'SkeletonStationCard',
    group: 'cards',
    file: 'components/radio/SkeletonStationCard.vue',
    summary: 'The tile-shaped placeholder StationCard lays over itself while a favicon loads. No props: it is one shimmering square, sized by whatever it overlays.',
  },
  {
    id: 'PodcastCard',
    group: 'cards',
    file: 'components/podcasts/PodcastCard.vue',
    summary: 'A show, in the search results and the subscription list. `position` prefixes the chart rank, `showActions` arms the subscribe/unsubscribe button, and `is_subscribed` on the podcast itself decides which of the two that button is.',
  },
  {
    id: 'SkeletonPodcastCard',
    group: 'cards',
    file: 'components/podcasts/SkeletonPodcastCard.vue',
    summary: 'The only skeleton with a variant, and the two stand in for different components: `card` is PodcastCard in the home grid, while `row` is used once, inside SkeletonPodcastDetails, where it covers the show page\'s DetailHeader. So its name matches one of its two jobs — pair each with what it replaces below and the mismatch is the thing to see.',
  },
  {
    id: 'EpisodeCard',
    group: 'cards',
    file: 'components/podcasts/EpisodeCard.vue',
    coupling: 'composable',
    summary: 'An episode row. Its meta line is not a prop but a computation: useEpisodePlaybackStatus() reads the podcast and audio stores to decide between "now playing", "already listened", the time remaining, or the plain duration. Untouched stores mean the plain duration, which is what a freshly booted unit shows.',
  },
  {
    id: 'SkeletonEpisodeCard',
    group: 'cards',
    file: 'components/podcasts/SkeletonEpisodeCard.vue',
    summary: 'EpisodeCard\'s placeholder — cover, two text lines and the round action button, in shimmer. No props.',
  },
  {
    id: 'GenreCard',
    group: 'cards',
    file: 'components/podcasts/GenreCard.vue',
    summary: 'A genre tile for the podcast home. The image is not passed in: the component holds the twelve genre artworks and picks by `value`, so an unknown value renders a tile with no image rather than a broken one.',
  },
  {
    id: 'SkeletonPodcastDetails',
    group: 'cards',
    file: 'components/podcasts/SkeletonPodcastDetails.vue',
    summary: 'The whole show page while it loads: a DetailHeader-shaped block over a run of episode rows. No props — it mimics a layout, not a component.',
  },
  {
    id: 'SkeletonEpisodeDetails',
    group: 'cards',
    file: 'components/podcasts/SkeletonEpisodeDetails.vue',
    summary: 'The same for a single episode page — cover, title block and the description paragraph as shimmering bars. No props.',
  },

  // --- Settings composites ---
  {
    id: 'SettingsContainer',
    group: 'settings',
    file: 'components/settings/SettingsContainer.vue',
    summary: 'Fourteen lines and nineteen consumers: a flex column that puts one gap between settings sections. It carries nothing else, and that is the entry — the alternative was nineteen copies of the same two declarations.',
  },
  {
    id: 'SettingsSection',
    group: 'settings',
    file: 'components/settings/SettingsSection.vue',
    summary: 'The settings card, and the most-imported component in the frontend (28). Either a title prop or a header slot that replaces it — the slot wins, so passing both shows only the slot. Everything else is default-slot content.',
  },
  {
    id: 'SettingItem',
    group: 'settings',
    file: 'components/settings/SettingItem.vue',
    summary: 'A label above its control. Omitting the label renders the wrapper alone, which is how a full-width control opts out of the label without a second component.',
  },
  {
    id: 'SectionHeader',
    group: 'settings',
    file: 'components/settings/SectionHeader.vue',
    summary: 'Title and optional subtitle on the left, an actions slot on the right, stacking to a column below 4:3. Distinct from SettingsSection.title: this is a header placed inside a card, not the card heading.',
  },
];

/** Entries of one group, in declaration order. */
export function entriesOf(groupId) {
  return ENTRIES.filter(entry => entry.group === groupId);
}

/** Lookup used by GalleryItem so a demo only has to name the component. */
export function entryById(id) {
  return ENTRIES.find(entry => entry.id === id);
}
