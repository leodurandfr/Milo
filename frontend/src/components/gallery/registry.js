// frontend/src/components/gallery/registry.js
/**
 * What the playground needs to render one component on its own.
 *
 * Everything derivable is derived (see controls.js) — this file holds only what
 * a component cannot tell us about itself:
 *
 *   args      starting prop values. A required prop with no default must appear
 *             here or the canvas renders a broken instance.
 *   overrides per-prop control shape, for the two cases controls.js cannot read —
 *             a validator closing over an identifier, and a short useful list on
 *             a prop that has no validator at all.
 *   sync      emitted event -> the prop it writes back, so dragging a slider or
 *             flipping a toggle updates the control panel. `update:modelValue`
 *             is wired to `modelValue` implicitly and needs no entry.
 *   slots     slot name -> either plain text, or a map of named choices the panel
 *             offers as a select. Slot content cannot be sent over postMessage,
 *             so the parent sends the chosen *key* and the canvas resolves it
 *             here — which works because this file is bundled into both.
 *   presets   prop name -> a map of named values, for the object-typed props no
 *             widget can express: a song record, a progress record, a device
 *             list. Resolved by key on the canvas side exactly like a slot, and
 *             for the same reason — the *name* is the documentation, and it
 *             would not survive the trip. A preset satisfies a required prop.
 *   state     store writes a component depends on, exposed as controls. Nothing
 *             here is derivable: a store field is not introspectable the way a
 *             prop is.
 *   actions   named triggers the panel renders as buttons and the canvas runs.
 *   surface   which tone the canvas paints behind the component, as a function of
 *             the current args — a translucent variant drawn for a dark backdrop
 *             is illegible on the light stage, which is how a variant gets read
 *             as broken. It returns a tone CanvasApp.vue declares a class for
 *             ('contrast'), or nothing for the default stage, and
 *             it splits the same way the Variants tab's strips do, so a variant
 *             is judged against one surface on both tabs.
 *
 * The three store-coupled primitives — Dock, VolumeBar, VirtualKeyboard — are in
 * here rather than excluded, and they need no fabricated state to be worth
 * looking at: every store they read declares real defaults (`dockApps` all true,
 * `sourceOrder` the full source list, limits −80/−20 dB), so the canvas shows
 * them as a freshly configured unit would. Their `position: fixed` also resolves
 * honestly now, because the iframe is a viewport of its own. Their actions drive
 * the same paths a user does — the Dock's reveal action clicks its own drag pill
 * rather than reaching into the component.
 *
 * The shared composites (the player parts, the three layouts, the settings
 * wrappers) need no state either — that is the admission rule catalog.js states,
 * and what is left is props and slot content. Two things recur for them: `class`
 * in the args, because a component that fills a column in the app shrinks to its
 * content on a stage that centres, and a slot choice pointing at samples/, for
 * the slots that receive a whole view rather than a line of text.
 *
 * Vue imports live here rather than in catalog.js, which stays plain data so the
 * architecture test can read it under Node.
 */
import Button from '@/components/ui/Button.vue';
import IconButton from '@/components/ui/IconButton.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Toggle from '@/components/ui/Toggle.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import Radio from '@/components/ui/Radio.vue';
import InputText from '@/components/ui/InputText.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import DoubleRangeSlider from '@/components/ui/DoubleRangeSlider.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import NotificationBanner from '@/components/ui/NotificationBanner.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import SvgIcon, { ICON_NAMES } from '@/components/ui/SvgIcon.vue';
import AppIcon, { APP_ICON_NAMES, TILE_SIZE_PX } from '@/components/ui/AppIcon.vue';
import Logo from '@/components/ui/Logo.vue';
import Modal from '@/components/ui/Modal.vue';
import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import Dock from '@/components/ui/Dock.vue';
import VolumeBar from '@/components/ui/VolumeBar.vue';
import VirtualKeyboard from '@/components/ui/VirtualKeyboard.vue';
import ProgressBar from '@/components/audio/ProgressBar.vue';
import PlayerInfoText from '@/components/audio/PlayerInfoText.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import AudioPlayer from '@/components/audio/AudioPlayer.vue';
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
import PlayerTransport from '@/components/audio/PlayerTransport.vue';
import PlayerBody from '@/components/audio/PlayerBody.vue';
import PlayerTopRow from '@/components/audio/PlayerTopRow.vue';
import SourceBar from '@/components/audio/SourceBar.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';
import AudioSourceStatus from '@/components/audio/AudioSourceStatus.vue';
import StationCard from '@/components/radio/StationCard.vue';
import SkeletonStationCard from '@/components/radio/SkeletonStationCard.vue';
import PodcastCard from '@/components/podcasts/PodcastCard.vue';
import SkeletonPodcastCard from '@/components/podcasts/SkeletonPodcastCard.vue';
import EpisodeCard from '@/components/podcasts/EpisodeCard.vue';
import SkeletonEpisodeCard from '@/components/podcasts/SkeletonEpisodeCard.vue';
import GenreCard from '@/components/podcasts/GenreCard.vue';
import SkeletonPodcastDetails from '@/components/podcasts/SkeletonPodcastDetails.vue';
import SkeletonEpisodeDetails from '@/components/podcasts/SkeletonEpisodeDetails.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import ProgressStrip from '@/components/settings/ProgressStrip.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import FillerBlock from './samples/FillerBlock.vue';
import ControlSample from './samples/ControlSample.vue';
import TriggerSample from './samples/TriggerSample.vue';
import FavoriteSample from './samples/FavoriteSample.vue';
import SettingsSample from './samples/SettingsSample.vue';
import HeaderActionSample from './samples/HeaderActionSample.vue';
import SourceStage from './SourceStage.vue';
import { ALL_AUDIO_SOURCES, BROWSER_SOURCES } from '@/constants/audioSources';
import { PODCAST_GENRE_IDS } from '@/constants/podcastGenres';
import { DISPLAY_STATES, UNAVAILABLE_REASONS } from '@/composables/useSourceStatusDisplay';
import { SOURCE_PAGES, audioState, session, anchor, replayedState } from './sources';
import stationImageTurntable from './samples/station-image-turntable.webp';
import { musicPlaceholder, podcastPlaceholder } from '@/constants/placeholders';
import { UNITS, formatDuration } from '@/utils/units';

/** `null` first so a nullable icon prop can be cleared from the select. */
const OPTIONAL_ICON = { kind: 'enum', options: [null, ...ICON_NAMES] };
const REQUIRED_ICON = { kind: 'enum', options: ICON_NAMES };
/** A slider's `unit`: none, or one formatUnit() writes. */
const UNIT_OPTIONS = { kind: 'enum', options: ['', ...UNITS] };

const PIXEL_SIZE = { kind: 'enum', options: [16, 24, 32, 48, 64] };

/**
 * What a source adds after the transport: radio's favorite, in a toggle's box,
 * inked from the slot's own `ink` as RadioSource inks it.
 */
const TRANSPORT_END = {
  none: null,
  'IconButton — radio’s favorite': { component: FavoriteSample, scoped: true }
};

const SELECT_OPTIONS = [
  { label: 'Low', value: 'low' },
  { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' }
];

/**
 * Simulated now-playing states for AudioPlayerFull, which reads the store
 * rather than props.
 *
 * Simulating state is what a documentation page does — the catalogue's caution
 * is about *scale* (ninety-odd per-source screens would be a second frontend),
 * not about fabrication itself. What matters is that a fabrication cannot rot
 * quietly: each is published through the store's own handler, whose strict
 * schema refuses a state that drifted from the wire, and each session field it
 * sets is checked against the files that read it — so renaming `artwork` in the
 * component turns the guardrail red instead of leaving a beautiful player
 * rendering from a field nothing consumes.
 *
 * A record exists for a *component* behaviour — the transport, a pause, the
 * loading spinner, the receiver's source bar, the snapshot rule with no artist
 * to name — never for a source. What a given source reaches, in the states it
 * actually reaches, is the source pages' subject (sources.js), and they hold the
 * fixtures and the guardrails for it. Adding a record here to document a source
 * is how the two surfaces start drifting, one of them wrong.
 *
 * `source` travels with the record and is applied to the `source` **prop** as
 * well, because the player reads its slice of the state only while the two
 * agree: left to disagree, the session and the controls belong to another
 * source and the player draws nothing it was handed.
 *
 * The browser sources' records each stand for a control the player draws from
 * `controls` and `details` (utils/playerControls): the toggles and the links,
 * the relative skip, the live stream's stop, a resume point in place of a
 * session. Their `details` carry what the wire does.
 */
const NOW_PLAYING = {
  // `skip` is listed as it is for a CD, and still draws nothing: the track steps
  // take the places beside the main button, and only a source with no step
  // offers −15 / +30 there.
  'Spotify — playing': {
    source: 'spotify',
    session: {
      phase: 'playing',
      title: 'Says',
      artist: 'Nils Frahm',
      artwork: musicPlaceholder,
      duration_ms: 511000,
      position: anchor(192000)
    },
    controls: ['pause', 'seek', 'skip', 'next', 'prev', 'set_shuffle', 'set_repeat'],
    details: {
      kind: 'spotify',
      account: 'owner',
      signing_in: false,
      context_uri: 'spotify:playlist:chill',
      context_name: 'Chill appart',
      track_uri: 'spotify:track:says',
      album_uri: 'spotify:album:spaces',
      artist_uri: 'spotify:artist:nils',
      shuffle: true,
      repeat: 'context'
    }
  },
  // Nothing in session: the resume point is what the player names, and the
  // `stop` the source lists beside `resume` is not a second main button.
  'Music Library — saved queue (resume point)': {
    source: 'music_library',
    resume: {
      title: 'Ambre',
      artist: 'Nils Frahm',
      artwork: musicPlaceholder,
      duration_ms: 264000,
      position_ms: 64000
    },
    controls: ['resume', 'play_index', 'stop'],
    details: {
      kind: 'music_library',
      queue: [],
      queue_index: 0,
      shuffle: false,
      track_id: 's-2',
      album_id: 'al-1',
      artist_id: 'ar-3'
    }
  },
  'Podcast — skip pair': {
    source: 'podcast',
    session: {
      phase: 'playing',
      title: 'Les gens qui parlent à leurs plantes',
      artist: 'Le Code a changé',
      artwork: podcastPlaceholder,
      duration_ms: 2940000,
      position: anchor(812000)
    },
    controls: ['pause', 'seek', 'skip'],
    details: {
      kind: 'podcast',
      episode: {
        uuid: 'e1',
        name: 'Les gens qui parlent à leurs plantes',
        image_url: podcastPlaceholder,
        podcast: { uuid: 'a1', name: 'Le Code a changé', image_url: podcastPlaceholder }
      }
    }
  },
  'Radio — live stream, song recognized': {
    source: 'radio',
    session: {
      phase: 'playing',
      title: 'Ainsi parlait Zarathoustra',
      artist: 'Alain Bashung',
      artwork: musicPlaceholder
    },
    controls: ['stop', 'next', 'prev'],
    details: {
      kind: 'radio',
      station: radioStation('st-nova', 'Radio Nova', stationImageTurntable),
      track: { title: 'Ainsi parlait Zarathoustra', artist: 'Alain Bashung', artwork: musicPlaceholder }
    }
  },
  // No logo: the cover slot draws the station's generated avatar, which is its
  // identity rather than a stand-in.
  'Radio — stopped, no logo': {
    source: 'radio',
    resume: { title: 'FIP' },
    controls: ['resume_playback', 'next', 'prev'],
    details: { kind: 'radio', station: radioStation('st-fip', 'FIP', null), track: null }
  },
  'CD — paused': {
    source: 'cd',
    session: {
      phase: 'paused',
      title: 'Ambre',
      artist: 'Nils Frahm',
      artwork: musicPlaceholder,
      duration_ms: 264000,
      position: anchor(64000)
    },
    controls: ['resume', 'seek', 'skip', 'next', 'prev', 'play_track', 'eject']
  },
  'CD — loading a track': {
    source: 'cd',
    session: {
      phase: 'loading',
      title: 'Ambre',
      artist: 'Nils Frahm',
      duration_ms: 264000,
      position: anchor(0)
    },
    controls: ['pause', 'next', 'prev', 'play_track', 'eject']
  },
  'AirPlay — receiver, sender named': {
    source: 'airplay',
    session: {
      phase: 'playing',
      title: 'Ainsi parlait Zarathoustra',
      artist: 'Alain Bashung',
      artwork: musicPlaceholder,
      senders: ['Leo’s iPhone'],
      duration_ms: 297000,
      position: anchor(41000)
    },
    controls: [],
    details: { kind: 'airplay', artwork_width: 600 }
  },
  // The one shape that reaches this player with no artist: a disc no lookup
  // identified comes back with a real "Track N" title and nothing else, and a
  // title is the whole of what the player needs to name it.
  'CD — unidentified disc': {
    source: 'cd',
    session: {
      phase: 'playing',
      title: 'Track 3',
      duration_ms: 264000,
      position: anchor(64000)
    },
    controls: ['pause', 'seek', 'skip', 'next', 'prev', 'play_track', 'eject']
  }
};

/** RadioDetails.station, every field present. */
function radioStation(id, name, favicon) {
  return {
    id, name, favicon,
    url: `https://streams.example/${id}.mp3`,
    country: 'France', genre: 'eclectic', bitrate: 128, codec: 'MP3'
  };
}

/** A complete ResumeView: every key present, nothing named. */
function resumePoint(overrides) {
  return {
    title: null, artist: null, album: null, artwork: null, duration_ms: null, position_ms: null,
    ...overrides
  };
}

/** The files that read the session fields NOW_PLAYING sets. Checked by the guardrail. */
const NOW_PLAYING_READERS = [
  'components/audio/AudioPlayerFull.vue',
  'composables/usePlayerControls.js',
  'composables/usePlayerMetadata.js',
  'composables/useSourceProgress.js',
  'utils/nowPlayingMetadata.js',
  'utils/nowPlayingArtwork.js'
];

/**
 * The now-playing state both store-coupled players read, published through
 * the app's own handler. Shared by PlayerTransport and PlayerBody.
 */
const NOW_PLAYING_STATE = {
  kind: 'enum',
  options: Object.keys(NOW_PLAYING),
  default: 'Spotify — playing',
  apply: (value, stores) => {
    const record = NOW_PLAYING[value] ?? NOW_PLAYING['Spotify — playing'];
    // Published through the app's own handler, whole — the schema is
    // strict, so a partial state would be refused and the last one kept.
    const state = audioState(record.source, {
      session: record.session ? session(record.session) : null,
      resume: record.resume ? resumePoint(record.resume) : null,
      controls: record.controls,
      details: record.details ?? null
    });
    stores.unified.updateState({
      category: 'source',
      type: 'state',
      data: replayedState(state, Date.now() / 1000)
    });
    // Applied to the props too — see NOW_PLAYING. Only on the change, so
    // pointing the prop elsewhere by hand still holds.
    return { source: record.source };
  },
  // What the writer above invents, and where it is read. Declared so the
  // guardrail can check the two still agree — see gallery.test.js.
  records: Object.values(NOW_PLAYING).flatMap(record => [record.session, record.resume].filter(Boolean)),
  readBy: NOW_PLAYING_READERS
};

export const REGISTRY = {
  Button: {
    component: Button,
    args: { variant: 'brand' },
    slots: { default: 'Button' },
    overrides: { leftIcon: OPTIONAL_ICON },
    // Six of the seven variants are self-coloured; `on-contrast` is a white
    // glint, white on white without the contrast surface it is drawn for.
    surface: args => (args.variant === 'on-contrast' ? 'contrast' : null)
  },

  IconButton: {
    component: IconButton,
    args: { icon: 'play' },
    overrides: { icon: REQUIRED_ICON },
    // A ghost inherits its ink, so it reads on the default stage.
    surface: args => (['on-contrast', 'glass-on-contrast'].includes(args.variant) ? 'contrast' : null)
  },

  ButtonGroup: {
    component: ButtonGroup,
    args: { modelValue: 'medium', options: SELECT_OPTIONS },
    overrides: {
      modelValue: { kind: 'enum', options: SELECT_OPTIONS.map(option => option.value) },
      inactiveVariant: { kind: 'enum', options: ['outline-neutral', 'surface'] }
    }
  },

  ListItemButton: {
    component: ListItemButton,
    args: { title: 'Loudness', subtitle: 'A subtitle stacks under the title', action: 'toggle' },
    notes: {
      iconVariant: 'Sizes what the icon slot holds, so it changes nothing until that slot is filled.'
    },
    // The leading icon is a slot, not a prop, so it cannot appear in the props
    // table — these are the choices the Slots section offers instead. `title`
    // and `subtitle` are slots *over* props of the same name: filling one
    // replaces the text the prop would have printed.
    slots: {
      icon: {
        none: null,
        'AppIcon — radio': { component: AppIcon, props: { name: 'radio', size: 32 } },
        'AppIcon — spotify': { component: AppIcon, props: { name: 'spotify', size: 32 } },
        'SvgIcon — speakerShelf': { component: SvgIcon, props: { name: 'speakerShelf', size: 24 } }
      },
      title: {
        'none — the title prop shows': null,
        'text override': { text: 'Slotted title' }
      },
      subtitle: {
        'none — the subtitle prop shows': null,
        'text override': { text: 'Slotted subtitle' }
      }
    }
  },

  Toggle: {
    component: Toggle,
    args: { modelValue: true, title: 'Labelled toggle' }
  },

  ToggleSection: {
    component: ToggleSection,
    args: { title: 'Compressor', enabled: true },
    slots: {
      default: 'Content revealed by the header toggle.',
      title: {
        'none — the title prop shows': null,
        'text override': { text: 'Slotted title' }
      },
      // Sits beside the header toggle, so it holds the row's secondary control.
      actions: {
        none: null,
        'IconButton — reset': { component: IconButton, props: { icon: 'arrowCounterClockwise', size: 'small' } }
      }
    },
    // No v-model here: the section reports through `change`.
    sync: { change: 'enabled' }
  },

  Radio: {
    component: Radio,
    args: { modelValue: false }
  },

  InputText: {
    component: InputText,
    args: { modelValue: '', placeholder: 'Type here' },
    overrides: {
      icon: OPTIONAL_ICON,
      type: { kind: 'enum', options: ['text', 'password', 'number'] }
    }
  },

  Dropdown: {
    component: Dropdown,
    args: { modelValue: 'medium', options: SELECT_OPTIONS },
    overrides: { modelValue: { kind: 'enum', options: SELECT_OPTIONS.map(option => option.value) } },
    slots: {
      trigger: {
        none: null,
        'IconButton — the Update Manager version menu': { component: TriggerSample, scoped: true }
      }
    }
  },

  RangeSlider: {
    component: RangeSlider,
    args: { modelValue: 60 },
    overrides: {
      orientation: { kind: 'enum', options: ['horizontal', 'vertical'] },
      // The validator defers to UNITS, so the list is borrowed from it.
      unit: UNIT_OPTIONS
    },
    // Log-spaced values on evenly spaced stops, the shape the delay settings use.
    presets: {
      steps: {
        'Continuous': null,
        'Delays (log)': [10, 30, 60, 300, 1800, 3600]
          .map(value => ({ value, label: formatDuration(value, 'english') }))
      }
    }
  },

  DoubleRangeSlider: {
    component: DoubleRangeSlider,
    args: { modelValue: { min: 20, max: 80 } },
    overrides: { unit: UNIT_OPTIONS }
  },

  LoadingSpinner: {
    component: LoadingSpinner,
    args: { size: 48 },
    overrides: { size: PIXEL_SIZE }
  },

  NotificationBanner: {
    component: NotificationBanner,
    args: {
      title: 'CamillaDSP restart failed',
      detail: 'hw:Loopback,0,0 is held by snapclient — check the routing mode.',
      dismissable: true
    }
  },

  MessageContent: {
    component: MessageContent,
    args: {
      icon: 'radio',
      title: 'No favourites yet',
      subtitle: 'Stations you like end up here',
      details: 'Search for a station, then tap the heart to keep it.',
      ctaLabel: 'Open search',
      ctaSecondaryLabel: 'Retry'
    },
    overrides: {
      icon: OPTIONAL_ICON,
      variant: { kind: 'enum', options: ['default', 'on-contrast'] },
      ctaVariant: { kind: 'enum', options: ['brand', 'control', 'outline', 'important'] },
      ctaSecondaryVariant: { kind: 'enum', options: ['control', 'brand', 'outline', 'important'] }
    },
    // `on-contrast` drops the card and colours every line white, for the blurred
    // artwork the Lyrics view lays it over.
    surface: args => (args.variant === 'on-contrast' ? 'contrast' : null)
  },

  LazyImage: {
    component: LazyImage,
    // The class goes on the component: its layers are absolutely positioned, so
    // the root collapses unless something sizes it.
    args: { src: musicPlaceholder, alt: 'Artwork', class: 'canvas-artwork' },
    overrides: { priority: { kind: 'enum', options: ['auto', 'high', 'low'] } },
    // The default slot lays over the image rather than beside it — where a
    // caller puts a play affordance or a badge on top of the artwork.
    slots: {
      default: {
        none: null,
        'LoadingSpinner — a loading veil': { component: LoadingSpinner, props: { size: 48 } }
      }
    }
  },

  SvgIcon: {
    component: SvgIcon,
    args: { name: 'play', size: 48 },
    overrides: {
      name: REQUIRED_ICON,
      size: { kind: 'enum', options: [16, 24, 32, 48, 64, 'small', 'medium', 'large'] }
    },
    // Every fill is rewritten to currentColor, including one inside a <mask>, so a
    // luminance-masked glyph disappears when currentColor is dark. The keyboard
    // ones are only ever drawn on VirtualKeyboard's light-on-dark keys — same
    // reason MediaDemo gives them their own strip.
    surface: args => (String(args.name).startsWith('keyboard') ? 'contrast' : null)
  },

  AppIcon: {
    component: AppIcon,
    args: { name: 'spotify', size: 64 },
    // Both validators close over a list, so neither can be read from source.
    overrides: {
      name: { kind: 'enum', options: APP_ICON_NAMES },
      size: {
        kind: 'enum',
        options: [...Object.values(TILE_SIZE_PX), ...Object.keys(TILE_SIZE_PX)]
      }
    }
  },

  Logo: {
    component: Logo,
    // position: fixed resolves against the iframe's own viewport, so the two
    // anchors land where they land in the app.
    args: { position: 'center' }
  },

  Modal: {
    component: Modal,
    // Closed on purpose. `isVisible` starts false and the watcher on `isOpen`
    // has no `immediate`, so a modal mounted already-open never opens — the app
    // never does that (every call site keeps it mounted and flips the prop), and
    // opening it from the panel is also the only way to see its entrance.
    args: { isOpen: false },
    notes: { isOpen: 'Mounted closed: the modal opens on the prop changing, so tick this to play its entrance.' },
    slots: { default: 'Modal content. It springs to the height of what it holds.' }
  },

  NavigationHeader: {
    component: NavigationHeader,
    args: { title: 'Radio Nova', subtitle: 'Paris, France', showBack: true },
    overrides: { icon: OPTIONAL_ICON },
    notes: {
      icon: 'The icon is the header\u2019s other lead-in, so it is drawn only while showBack is off.',
      titleMuted: 'Only the single-line title is muted \u2014 with a subtitle set, the pair replaces it.'
    },
    slots: {
      actions: {
        none: null,
        'IconButton — search': { component: IconButton, props: { icon: 'search' } }
      }
    }
  },

  Dock: {
    component: Dock,
    notes: {
      source: 'Drawn by the indicator under the dock, which is only positioned while the dock is revealed \u2014 run the Reveal action first.'
    },
    state: {
      source: {
        kind: 'enum',
        // The dock hides itself on `none`, and refuses to reveal — so the useful
        // starting point is a source that is playing.
        options: ['none', ...ALL_AUDIO_SOURCES],
        default: 'spotify',
        apply: (value, stores) => { stores.unified.systemState.source = value; }
      },
      switching: {
        kind: 'boolean',
        default: false,
        apply: (value, stores) => { stores.unified.systemState.switching = value; }
      },
      lyricsOpen: {
        kind: 'boolean',
        default: false,
        apply: (value, stores) => { stores.lyrics.isOpen = value; }
      }
    },
    actions: {
      // Clicks the dock's own drag pill, which is one of the three reveal paths a
      // user has. Nothing is reached into: `showDock` stays private.
      'Reveal (tap the pill)': () => document.querySelector('.dock-indicator')?.click()
    }
  },

  VolumeBar: {
    component: VolumeBar,
    state: {
      visible: {
        kind: 'boolean',
        default: true,
        apply: (value, stores) => { stores.unified.showVolumeBar = value; }
      },
      global_volume_db: {
        kind: 'number',
        default: -45,
        apply: (value, stores) => { stores.unified.volumeState.global_volume_db = value; }
      },
      min_db: {
        kind: 'number',
        default: -80,
        apply: (value, stores) => { stores.settings.volumeLimits.min_db = value; }
      },
      max_db: {
        kind: 'number',
        default: -20,
        apply: (value, stores) => { stores.settings.volumeLimits.max_db = value; }
      }
    },
    // `on-contrast` is the white-fill variant App.vue picks for a dark ground —
    // on the default stage its fill is white on white.
    surface: args => (args.variant === 'on-contrast' ? 'contrast' : null)
  },

  VirtualKeyboard: {
    component: VirtualKeyboard,
    // Mounted permanently by the canvas, as App.vue mounts it for the app: the
    // keyboard's visibility lives in module state that `useVirtualKeyboard()`
    // shares, so an instance has to already be on screen for anything — an
    // action here, or a tap on the InputText playground — to make it appear.
    // The canvas therefore does not render it a second time as the selection.
    alwaysMounted: true,
    actions: {
      'Open (text)': (ctx) => ctx.keyboard.open({ value: 'Radio Nova', placeholder: 'Station name' }),
      'Open (empty)': (ctx) => ctx.keyboard.open({ placeholder: 'Wi-Fi password' }),
      Close: (ctx) => ctx.keyboard.close()
    }
  },

  ProgressBar: {
    component: ProgressBar,
    // Milliseconds, the wire convention the component documents: 3:12 of 4:05.
    args: { currentPosition: 192000, duration: 245000, progressPercentage: 78.4 },
    // `on-contrast` is the light-fill variant drawn for the surfaces over
    // artwork — on the default stage its fill is white on white.
    surface: args => (args.variant === 'on-contrast' ? 'contrast' : null)
  },

  PlayerTransport: {
    component: PlayerTransport,
    args: { source: 'spotify' },
    notes: {
      nowPlaying: 'Each record carries the source it belongs to and moves the source prop with it.',
      end: 'What the source adds after the row that is not a command — radio’s favorite — at the end a toggle takes, held by a spacer at the other so the main button stays centred.'
    },
    state: {
      nowPlaying: NOW_PLAYING_STATE
    },
    slots: {
      end: TRANSPORT_END
    }
  },

  PlayerTopRow: {
    component: PlayerTopRow,
    args: { class: 'canvas-column' },
    slots: {
      start: {
        'IconButton — CD’s tracklist': {
          component: IconButton, props: { icon: 'queue', variant: 'control', size: 'medium' }
        },
        none: null
      },
      end: {
        'IconButton — eject': { component: IconButton, props: { icon: 'eject', variant: 'control', size: 'medium' } },
        none: null
      }
    }
  },

  PlayerBody: {
    component: PlayerBody,
    args: { source: 'spotify', surface: 'full', class: 'canvas-column' },
    notes: {
      nowPlaying: 'Each record carries the source it belongs to and moves the source prop with it.',
      surface: 'full is the full player’s centred column on a light ground; card is the playing bar’s dark card, ranged left, whose phone form folds into one row.',
      info: 'Taken by the playing bar for its swipe carousel; the body draws its own lines otherwise.'
    },
    state: {
      nowPlaying: NOW_PLAYING_STATE
    },
    slots: {
      info: {
        'default — the body’s own lines': null
      },
      'transport-end': TRANSPORT_END
    }
  },

  SourceBar: {
    component: SourceBar,
    args: { source: 'radio', label: 'Radio Nova' },
    // The validator is `ALL_AUDIO_SOURCES.includes(value)`, which the panel
    // cannot read off a list it does not see.
    overrides: { source: { kind: 'enum', options: [...ALL_AUDIO_SOURCES] } }
  },

  PlayerInfoText: {
    component: PlayerInfoText,
    args: {
      title: 'Ainsi parlait Zarathoustra',
      secondary: 'Alain Bashung',
      class: 'canvas-column'
    }
  },

  TrackRow: {
    component: TrackRow,
    args: { number: 4, showArtist: true, showMenu: true, coverUrl: musicPlaceholder, class: 'canvas-column' },
    notes: {
      playing: 'Swaps the number for the equaliser bars, so it is only read on the current row.'
    },
    // `duration` is seconds here, unlike ProgressBar's milliseconds — the row
    // formats what the catalogue hands it, and Subsonic reports seconds.
    presets: {
      song: {
        'Track': { title: 'Says', artist: 'Nils Frahm', duration: 511 },
        'Long title': {
          title: 'Ambre — a very long track title that has to elide before it reaches the duration',
          artist: 'Nils Frahm',
          duration: 264
        },
        'No artist (showArtist has nothing to show)': { title: 'Untitled', duration: 128 }
      }
    },
    // Filled, the slot replaces the ⋯ button: Spotify puts its own menu there.
    slots: {
      menu: {
        'none — the showMenu button': null,
        'IconButton — a caller\'s own menu trigger': {
          component: IconButton,
          props: { icon: 'threeDots', variant: 'ghost', size: 'small' }
        }
      }
    }
  },

  DetailHeader: {
    component: DetailHeader,
    args: {
      imageSrc: musicPlaceholder,
      title: 'Spaces',
      subtitle: 'Nils Frahm',
      subtitleMeta: '2013 · 17 tracks · 1 h 21',
      class: 'canvas-column'
    },
    // `icon` swaps the cover for a tinted tile — the virtual headers (Liked
    // Songs, a genre) take that branch. No validator on it, so the list is here.
    overrides: { icon: OPTIONAL_ICON },
    slots: {
      actions: {
        none: null,
        'IconButton — the playlist Edit affordance': {
          component: IconButton,
          props: { icon: 'threeDots', variant: 'on-contrast', size: 'small' }
        }
      }
    }
  },

  AudioPlayer: {
    component: AudioPlayer,
    args: { source: 'spotify', visible: true },
    notes: {
      nowPlaying: 'Each record carries the source it belongs to and moves the source prop with it; only the four browser sources’ records are offered. The swipe carousel is the phone form: Music Library on the Phone viewport.'
    },
    // What the bar draws is read from the state, through the body it shares
    // with the full player: the same records, narrowed to its four sources.
    state: {
      nowPlaying: {
        ...NOW_PLAYING_STATE,
        options: Object.keys(NOW_PLAYING).filter(name => BROWSER_SOURCES.includes(NOW_PLAYING[name].source))
      }
    },
    // What a source still adds to its bar: radio's station behind a track on
    // the phone, and its favorite after the transport.
    slots: {
      'artwork-badge': {
        none: null,
        'AppIcon — radio': { component: AppIcon, props: { name: 'radio', size: 32 } }
      },
      'transport-end': TRANSPORT_END
    }
  },

  AudioPlayerFull: {
    component: AudioPlayerFull,
    args: { source: 'spotify', class: 'canvas-fill' },
    notes: {
      nowPlaying: 'Each record carries the source it belongs to and moves the source prop with it.',
      'content-replace': 'Takes the place of the whole info column, and only while hideContent is on.',
      'top-end': 'What the source adds that is not a command — CD’s eject, Bluetooth’s disconnect — at the end of the top row, drawn only when a slot fills it.',
      'transport-end': 'Radio’s favorite after the transport, in the box a toggle takes, so the main button stays centred.'
    },
    state: {
      nowPlaying: NOW_PLAYING_STATE
    },
    slots: {
      // The two ends of the top row, drawn only when one is filled: CD's
      // tracklist toggle at the start; CD's eject, Bluetooth's disconnect at
      // the end. The tracklist itself replaces the info column while
      // hideContent is set.
      'top-start': {
        none: null,
        'IconButton — CD’s tracklist': { component: IconButton, props: { icon: 'queue', variant: 'control', size: 'medium' } }
      },
      'top-end': {
        none: null,
        'IconButton — eject': { component: IconButton, props: { icon: 'eject', variant: 'control', size: 'medium' } }
      },
      'transport-end': TRANSPORT_END,
      'content-replace': {
        'FillerBlock — CD’s tracklist': {
          component: FillerBlock,
          props: { label: 'content-replace slot — the CD tracklist' }
        }
      }
    }
  },

  AudioSourceLayout: {
    component: AudioSourceLayout,
    args: {
      headerTitle: 'Podcasts',
      headerSubtitle: '12 subscriptions',
      headerShowBack: true,
      gradient: 'podcast',
      showPlayer: true,
      contentKey: 'home',
      class: 'canvas-fill'
    },
    overrides: { headerIcon: OPTIONAL_ICON },
    notes: {
      headerIcon: 'Forwarded to NavigationHeader, which draws an icon only while headerShowBack is off.',
      headerTitleMuted: 'Forwarded too, and it mutes the single-line title only \u2014 clear headerSubtitle to see it.'
    },
    slots: {
      // Taller than the stage on purpose: the gradient sits in the top 66% and
      // the scroll-crossing fade only means something with somewhere to scroll.
      content: {
        'Tall block — scrolls past the gradient': {
          component: FillerBlock,
          props: { label: 'content slot — the source’s own browser', height: 1200 }
        },
        'Short block': {
          component: FillerBlock,
          props: { label: 'content slot', height: 240 }
        }
      },
      player: {
        'Player-shaped block': {
          component: FillerBlock,
          props: { label: 'player slot — AudioPlayer in the app' }
        },
        none: null
      },
      'header-actions': {
        none: null,
        'IconButton — search': {
          component: IconButton,
          props: { icon: 'search', variant: 'ghost' }
        }
      }
    }
  },

  AudioSourceStatus: {
    component: AudioSourceStatus,
    args: { sourceType: 'bluetooth', displayState: 'playing' },
    overrides: {
      // The validator is `value === 'none' || ALL_AUDIO_SOURCES.includes(value)`
      // — not a literal-array test, so there is nothing for the parser to read.
      sourceType: { kind: 'enum', options: ['none', ...ALL_AUDIO_SOURCES] },
      // Same: the validator defers to DISPLAY_STATES, so the list is borrowed
      // from the composable that derives it rather than restated here — which
      // is what stops the select outliving a state the app stopped producing.
      displayState: { kind: 'enum', options: [...DISPLAY_STATES] },
      // Same again, and null is a real value here: it is what "the source can
      // work" looks like, so the select must be able to go back to it.
      unavailableReason: { kind: 'enum', options: [null, ...UNAVAILABLE_REASONS] }
    },
    // Only read while a session is live (loading, playing, paused, connected);
    // two senders is the ROC case: several Macs streaming at once, which
    // formatDeviceNames joins across two lines.
    presets: {
      deviceName: {
        'One sender': ['Leo’s iPhone'],
        'Two senders (ROC)': ['Leo’s MacBook', 'Studio iMac'],
        none: []
      }
    }
  },

  StationCard: {
    component: StationCard,
    args: { variant: 'card', class: 'canvas-column' },
    // The card's two branches, and which one a favicon selects. A same-origin
    // path renders as-is (a custom station's upload in the app, a bundled
    // sample here); an empty one takes the generated-avatar path. What no
    // preset offers is an external logo — getFaviconUrl sends that one to
    // /api/radio/favicon, i.e. an outbound fetch from the unit per render.
    presets: {
      station: {
        'Named, no favicon': { name: 'Radio Nova', favicon: '', countrycode: 'FR', genre: 'eclectic' },
        'With a custom image': {
          name: 'Radio Nova', favicon: stationImageTurntable, countrycode: 'FR', genre: 'eclectic'
        },
        'Country only': { name: 'FIP', favicon: '', countrycode: 'FR' },
        'Long name, no metadata': {
          name: 'France Musique — la nuit autour du jazz et des musiques improvisées',
          favicon: ''
        }
      }
    },
    slots: {
      actions: {
        none: null,
        'IconButton — favourite': {
          component: IconButton,
          props: { icon: 'heart', variant: 'control', size: 'small' }
        }
      }
    }
  },

  SkeletonStationCard: {
    component: SkeletonStationCard,
    args: { class: 'canvas-artwork' }
  },

  PodcastCard: {
    component: PodcastCard,
    args: { showActions: true, class: 'canvas-column' },
    presets: {
      podcast: {
        'Not subscribed': {
          uuid: 'a1',
          name: 'Le Code a changé',
          publisher: 'France Inter',
          image_url: podcastPlaceholder,
          is_subscribed: false
        },
        'Subscribed': {
          uuid: 'a2',
          name: 'Le Code a changé',
          publisher: 'France Inter',
          image_url: podcastPlaceholder,
          is_subscribed: true
        },
        'No artwork': { uuid: 'a3', name: 'Affaires sensibles', publisher: 'France Inter' }
      }
    }
  },

  SkeletonPodcastCard: {
    component: SkeletonPodcastCard,
    args: { class: 'canvas-column' }
  },

  EpisodeCard: {
    component: EpisodeCard,
    args: { showCompleteButton: true, class: 'canvas-column' },
    // `date_published` is epoch seconds, as the parser emits it, and `duration`
    // is seconds — the same pair TrackRow reads as seconds and ProgressBar as
    // milliseconds. Fixed values rather than a computed "now": a date that moves
    // with the clock would make the card read differently every day.
    presets: {
      episode: {
        'With show and date': {
          uuid: 'e1',
          name: 'Les gens qui parlent à leurs plantes',
          image_url: podcastPlaceholder,
          duration: 2940,
          date_published: 1750000000,
          podcast: { name: 'Le Code a changé', image_url: podcastPlaceholder }
        },
        'No date, no show': { uuid: 'e2', name: 'Épisode sans métadonnées', duration: 1800 },
        'Long title': {
          uuid: 'e3',
          name: 'Un titre d’épisode assez long pour dépasser la largeur de la carte et devoir être coupé',
          duration: 5400,
          date_published: 1750000000,
          podcast: { name: 'Affaires sensibles' }
        }
      }
    }
  },

  SkeletonEpisodeCard: {
    component: SkeletonEpisodeCard,
    args: { class: 'canvas-column' }
  },

  GenreCard: {
    component: GenreCard,
    args: { label: 'True Crime', value: 'PODCASTSERIES_TRUE_CRIME' },
    // Borrowed, never restated: the ids are Milō's genre vocabulary, and a
    // select written from the labels instead (`comedy` for PODCASTSERIES_COMEDY)
    // is what left every tile here imageless, the default included.
    overrides: { value: { kind: 'enum', options: PODCAST_GENRE_IDS } }
  },

  SkeletonPodcastDetails: {
    component: SkeletonPodcastDetails,
    args: { class: 'canvas-column' }
  },

  SkeletonEpisodeDetails: {
    component: SkeletonEpisodeDetails,
    args: { class: 'canvas-column' }
  },

  SettingsContainer: {
    component: SettingsContainer,
    args: { class: 'canvas-column' },
    // Declares no props: the gap between children is the entire component, so
    // the sample has to be two real sections for there to be a gap to see.
    slots: { default: { 'Two settings sections': { component: SettingsSample } } }
  },

  SettingsSection: {
    component: SettingsSection,
    args: { title: 'Volume', class: 'canvas-column' },
    slots: {
      // The header slot replaces the built-in <h2>, so with a choice made the
      // `title` prop above stops showing — which is the thing worth seeing.
      header: {
        'none — the title prop shows': null,
        'SectionHeader — replaces the title': {
          component: SectionHeader,
          props: { title: 'Volume', subtitle: 'Startup level and limits' }
        }
      },
      default: { 'A control': { component: ControlSample } }
    }
  },

  ProgressStrip: {
    component: ProgressStrip,
    // `open` defaults false and the strip collapses to nothing, which in the
    // playground reads as a component that failed to render.
    args: { open: true, label: 'Measuring your network… about 12 s remaining',
            percent: 45, class: 'canvas-column' },
  },

  SettingItem: {
    component: SettingItem,
    args: { label: 'Startup volume', class: 'canvas-column' },
    slots: { default: { 'A control': { component: ControlSample } } }
  },

  SectionHeader: {
    component: SectionHeader,
    args: { title: 'Stations', subtitle: '24 saved', class: 'canvas-column' },
    slots: {
      title: {
        'none — the title prop shows': null,
        'text override': { text: 'Slotted title' }
      },
      actions: {
        none: null,
        'Button — add': { component: HeaderActionSample }
      }
    }
  }
};

/**
 * The sources axis: one descriptor, two selects.
 *
 * Kept out of REGISTRY, which is checked one-for-one against the catalogue —
 * this names no single file and would read there as a descriptor for a
 * component that does not exist. Everything downstream is unchanged: the canvas
 * mounts `component`, the panel derives its controls from the same
 * `describeProps`, and `entryFor` looks in both.
 *
 * `overrides` is a *function* here, which is the one thing the primitives never
 * needed: `scenario`'s options depend on which `page` is selected, and a static
 * map cannot express that. `describeProps` is handed the resolved map, so the
 * panel renders two ordinary selects and nothing else in the pipeline knows the
 * difference. ComponentsView clamps an enum arg that falls out of range, which
 * is what moves `scenario` to the new source's first state when `page` changes.
 */
export const AUDIO_SOURCES_ID = 'AudioSources';

export const SOURCE_REGISTRY = {
  [AUDIO_SOURCES_ID]: {
    component: SourceStage,
    args: {
      page: SOURCE_PAGES[0].id,
      scenario: SOURCE_PAGES[0].scenarios[0].id,
      class: 'canvas-fill'
    },
    overrides: (args) => {
      const page = SOURCE_PAGES.find(entry => entry.id === args.page) ?? SOURCE_PAGES[0];
      return {
        page: { kind: 'enum', options: SOURCE_PAGES.map(entry => entry.id) },
        scenario: { kind: 'enum', options: page.scenarios.map(scenario => scenario.id) }
      };
    }
  }
};

/**
 * A descriptor's per-prop control shapes, resolved against the current args.
 *
 * Static for every primitive; a function for the sources page, where one select
 * narrows the other. Callers hand in whatever args they hold — the panel its
 * live ones, the guardrail the descriptor's own starting values.
 */
export function overridesFor(descriptor, args = {}) {
  const overrides = descriptor?.overrides;
  if (typeof overrides === 'function') return overrides(args);
  return overrides || {};
}

/** The playground descriptor for one primitive or the sources page. */
export function entryFor(id) {
  return REGISTRY[id] ?? SOURCE_REGISTRY[id];
}
