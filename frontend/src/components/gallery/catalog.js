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
  'components/audio/BrowserSourceViews.vue':
    'A wiring shell with no look of its own: it swaps a browser source\'s navigation (AudioSourceLayout) for AudioPlayerFull, both catalogued, and is shown at work on the Radio, Podcasts, Music Library and Spotify source pages, whose bar expands into it.',
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
    blurb: 'The four full-surface shapes a source can take: the browsing layout, the two shared players and the idle status card. Which player mounts — and whether the status card takes over instead — is decided in one place, useRichDisplay().',
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
    summary: 'Select over options. displayOverride shows a computed label while keeping the raw value. A trigger slot ({ toggle, isOpen, disabled }) replaces the select box with the caller\'s own button, such as an IconButton; the menu is unchanged.',
  },
  {
    id: 'RangeSlider',
    group: 'controls',
    file: 'components/ui/RangeSlider.vue',
    summary: 'Single-value slider, horizontal or vertical. Emits drag-start/drag-end so a caller can throttle writes to the value and commit once. With steps ([{ value, label }]) it snaps between evenly spaced stops, marks each on the track and shows the stop\'s label.',
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
    summary: 'The empty/error/loading state, with up to two CTAs. loadingDelay holds the spinner back so a fast response never flashes one. The card is the panel of wherever it is drawn; on-contrast drops it for use over artwork.',
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
    summary: 'Fixed overlay that springs to its content height and provides modalRequestHeightDelta to descendants that change size. It opens on isOpen *changing*, never on being mounted already true — so the playground starts closed, and ticking the box is what plays the entrance.',
  },
  {
    id: 'NavigationHeader',
    group: 'structure',
    file: 'components/ui/NavigationHeader.vue',
    summary: 'Title bar with an optional back affordance. It is a panel of wherever it is drawn — white on a page, the section color inside a modal (--color-panel) — so it takes no variant, and its trailing IconButtons keep their default.',
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
    summary: 'The transient volume readout. position: fixed, visible only while unifiedAudioStore.showVolumeBar is set, and its fill interpolates the volume between the two configured limits — all four of those live in stores, so they are in the State section rather than the props table. Its one prop is the surface tone: the bar is fixed above every view and cannot see what it is drawn on, so App.vue reads it from the dark surface (Lyrics) that declares itself.',
  },

  // --- Player parts ---
  {
    id: 'ProgressBar',
    group: 'player',
    file: 'components/audio/ProgressBar.vue',
    summary: 'The one playback bar, in five surfaces. Positions and durations are always milliseconds — the wire convention — and seek is emitted in ms too. It self-hides when duration is 0 or isReady is false, which is how a source that reports no duration (radio, Qobuz) shows no bar rather than an empty one.',
  },
  {
    id: 'PlayerInfoText',
    group: 'player',
    file: 'components/audio/PlayerInfoText.vue',
    summary: 'The playing bar\'s two stacked lines on its dark card: the title, kept to two lines there by PlayerBody, and an optional secondary line. Text only, no layout of its own — PlayerBody positions it, under the source bar when the card draws one.',
  },
  {
    id: 'TrackRow',
    group: 'player',
    file: 'components/audio/TrackRow.vue',
    summary: 'The tracklist row, shared by CD, six Music Library views and the Spotify browser. Its 7 boolean props are a matrix, not a list: current + playing swaps the number for the equaliser bars, editing swaps duration + menu for remove + drag grip, showMenu arms that menu in the first place, showCover prepends the thumbnail, showArtist adds the second line, and artistLink makes it open the artist.',
  },
  {
    id: 'DetailHeader',
    group: 'player',
    file: 'components/audio/DetailHeader.vue',
    summary: 'The album / playlist / episode header: cover art, or a tinted icon tile when icon is set instead (the virtual headers — Liked Songs, a genre). Up to three text lines, and an actions slot that renders before the built-in shuffle / play buttons.',
  },

  // --- Source layouts ---
  {
    id: 'AudioPlayer',
    group: 'layout',
    file: 'components/audio/AudioPlayer.vue',
    coupling: 'store',
    summary: 'The playing bar of the sources that have a browser, beside their navigation: one shell around PlayerBody, the body it shares with the full player, ranged left on its dark card. It reads what it draws from the state, so the now-playing state sits in the State section; what the source still adds is the heart over the cover (artwork-action) and radio\'s station behind a track on the phone (artwork-badge). Its expand button, top-left over the cover on the kiosk\'s card (on the phone, which has no button, a tap anywhere on the bar), emits expand, and the source answers by drawing AudioPlayerFull in place of its navigation; the album and the artist are emitted where the navigation says there is one to open. Its second form is only reachable through the Phone viewport: below 4:3 the docked card becomes a mini-bar teleported to body, with a swipe that steps or skips, over a carousel when the source has a queue.',
  },
  {
    id: 'PlayerTopRow',
    group: 'player',
    file: 'components/audio/PlayerTopRow.vue',
    summary: 'The full player\'s row of buttons: the way back to the navigation and what the source adds at the start (CD\'s tracklist), what the source adds at the end (the heart; CD\'s eject, Bluetooth\'s disconnect). Two slots and a layout; the full player places it heading the column on the kiosk and under the cover on the phone. The playing bar keeps the same order over its cover — expand top-left, heart top-right — without the row.',
  },
  {
    id: 'PlayerBody',
    group: 'player',
    file: 'components/audio/PlayerBody.vue',
    coupling: 'store',
    summary: 'What both players draw beside or under the cover: the title and its lines, the progress bar, the transport (PlayerTransport) — one implementation, read from the state through the player\'s one reading (usePlayerState, which the shell makes and the body and the transport take) and useSourceProgress, so the bar and the full player cannot drift apart. Drawn here alone, it makes that reading itself. surface="full" is AudioPlayerFull\'s centred column; surface="card" is AudioPlayer\'s, ranged left on the dark card, which draws the source bar only where nothing else on it says where the music comes from (a station playing a detected song), and folds into one row on the phone\'s mini-bar. The source bar hangs above the title out of the flow, so the lines stay centred whether it is there or not. A relative skip goes through its own playhead, so a burst shows its sum; the artist line is emitted (secondary-click) where the navigation says there is one to open.',
  },
  {
    id: 'PlayerTransport',
    group: 'player',
    file: 'components/audio/PlayerTransport.vue',
    coupling: 'store',
    summary: 'The transport both players draw: shuffle, the steps around the main button, repeat — each iff the state lists its command (utils/playerControls.js, through the player\'s usePlayerState), in one order, with one set of glyphs, rungs and inks. surface="plate" is the full player\'s light plate with its own transport scale; surface="card" is the playing bar\'s dark card at the bar\'s scale, where everything but the main button is player-extra, which the phone\'s mini-bar hides. It reads the store and sends its own commands; a relative skip is emitted instead, to PlayerBody, whose progress bar owns the playhead.',
  },
  {
    id: 'SourceBar',
    group: 'player',
    file: 'components/audio/SourceBar.vue',
    summary: 'Where the full player\'s music comes from: an icon at 32px and one label — the station a detected song plays on (its logo then replaces the source\'s AppIcon, or the label\'s generated avatar without one), the show an episode belongs to when the cover is the episode\'s own picture, the sending device, the Spotify account\'s owner, else the source\'s name. AudioPlayerFull decides the label and draws the bar on every source, at the centre of its top row.',
  },
  {
    id: 'AudioPlayerFull',
    group: 'layout',
    file: 'components/audio/AudioPlayerFull.vue',
    coupling: 'store',
    summary: 'The full-screen player, mounted by 6 components: the 5 sources with nothing to browse (TIDAL, Bluetooth, CD, AirPlay, Qobuz), for which it is the only view, and BrowserSourceViews, where it is the expanded view of the 4 browser sources — drawn whole, and left through a back button drawn only where that navigation is provided. Unlike AudioPlayer it reads unifiedAudioStore itself and sends its own commands, so the now-playing state sits in the State section rather than the props table — and so does every choice the old booleans made, decided by utils/playerControls.js: a button is drawn for each command the state lists in `controls`, the transport plate only when a main pair is among them (pause/resume, or a live stream\'s stop/resume_playback; the receivers list neither and draw no plate), track steps beside it or else the −15/+30 skip, shuffle and repeat at its two ends with their state read from `details`, and the bar interactive only while seek is. A top row frames the SourceBar, drawn on every source: what is not a command (a favorite, a like, CD\'s eject) is the source\'s, in the top-start and top-end slots; the album and artist are emitted (artwork-click, secondary-click), never followed, and only when the navigation BrowserSourceViews provides says there is one to open — so here, standing alone, the cover and the artist line are inert. hideContent replaces the column outright, slot and all. It reads the state only while its source matches the source prop — point them at different sources and it draws nothing it was handed, which is the guard against drawing a session that belongs to the source being left.',
  },
  {
    id: 'AudioSourceLayout',
    group: 'layout',
    file: 'components/audio/AudioSourceLayout.vue',
    summary: 'The browsing layout behind Radio, Podcasts and Music Library: a scroll container, a header, cross-faded content and a player pane that animates in beside it. The cross-fade is driven by contentKey — change it and the current content leaves as the next enters. The 6 header* props are forwarded one by one to NavigationHeader.',
  },
  {
    id: 'AudioSourceStatus',
    group: 'layout',
    file: 'components/audio/AudioSourceStatus.vue',
    summary: 'The card shown whenever the selected source has no rich display to give. Both lines are derived from (sourceType, displayState) over 10 sources and 9 states — the service starting or failed, ready, the session\'s four phases, and CD\'s two drive operations — plus the prerequisite that outranks them, so the 3 selects below are the whole component. There is no fall-through: line 1 names the source and line 2 says what it is doing, except in the two cases that read as one sentence over two lines — "Démarrage de <source>" and "Connecté à <sender>" — where the phrase leads and the name takes the emphasis. 5 mutually exclusive CTAs hang off it, in the order it resolves them: a missing prerequisite first — Qobuz connect for the account, eject for an unreadable disc, network settings for a missing link — then retry on error, then Bluetooth disconnect while a session is live.',
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
    summary: 'A genre tile for the podcast home. The image is not passed in: `value` is a Milō genre key and constants/podcastGenres.js resolves it to one of the 12 artworks, so an unknown value renders a tile with no image rather than a broken one.',
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
    summary: '14 lines and 23 consumers: a flex column that puts one gap between settings sections. It carries nothing else, and that is the entry — the alternative was 23 copies of the same two declarations.',
  },
  {
    id: 'SettingsSection',
    group: 'settings',
    file: 'components/settings/SettingsSection.vue',
    summary: 'The settings card, and the most-imported component in the frontend (32). Either a title prop or a header slot that replaces it — the slot wins, so passing both shows only the slot. Everything else is default-slot content.',
  },
  {
    id: 'ProgressStrip',
    group: 'settings',
    file: 'components/settings/ProgressStrip.vue',
    summary: 'The progress strip the settings screens share: Navidrome\'s scan, the add-share wizard, the multiroom analysis. `percent` null sweeps, for work with no known total; a number fills. Two modes rather than two components, because a second bar drawn elsewhere is how two of them come to look different.',
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
