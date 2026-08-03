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
 *             ('contrast', 'medium'), or nothing for the default light stage, and
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
import AppIcon, { APP_ICON_NAMES } from '@/components/ui/AppIcon.vue';
import Logo from '@/components/ui/Logo.vue';
import Modal from '@/components/ui/Modal.vue';
import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import Dock from '@/components/ui/Dock.vue';
import VolumeBar from '@/components/ui/VolumeBar.vue';
import VirtualKeyboard from '@/components/ui/VirtualKeyboard.vue';
import ProgressBar from '@/components/audio/ProgressBar.vue';
import PlaybackControls from '@/components/audio/PlaybackControls.vue';
import PlayerInfoText from '@/components/audio/PlayerInfoText.vue';
import TrackRow from '@/components/audio/TrackRow.vue';
import DetailHeader from '@/components/audio/DetailHeader.vue';
import AudioPlayer from '@/components/audio/AudioPlayer.vue';
import AudioPlayerFull from '@/components/audio/AudioPlayerFull.vue';
import AudioSourceLayout from '@/components/audio/AudioSourceLayout.vue';
import AudioSourceStatus from '@/components/audio/AudioSourceStatus.vue';
import AudioScreensaver from '@/components/audio/AudioScreensaver.vue';
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
import SectionHeader from '@/components/settings/SectionHeader.vue';
import FillerBlock from './samples/FillerBlock.vue';
import ControlSample from './samples/ControlSample.vue';
import SettingsSample from './samples/SettingsSample.vue';
import SourceStage from './SourceStage.vue';
import { SOURCE_PAGES } from './sources';
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';
import { DISPLAY_STATES } from '@/composables/useSourceStatusDisplay';
import albumPlaceholder from '@/assets/images/album-placeholder.svg';
import stationImageTurntable from './samples/station-image-turntable.webp';

/** `null` first so a nullable icon prop can be cleared from the select. */
const OPTIONAL_ICON = { kind: 'enum', options: [null, ...ICON_NAMES] };
const REQUIRED_ICON = { kind: 'enum', options: ICON_NAMES };
const PIXEL_SIZE = { kind: 'enum', options: [16, 24, 32, 48, 64] };

const SELECT_OPTIONS = [
  { label: 'Low', value: 'low' },
  { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' }
];

/**
 * Simulated now-playing records for AudioPlayerFull, which reads the store
 * rather than props.
 *
 * Simulating state is what a documentation page does — the catalogue's caution
 * is about *scale* (ninety-odd per-source screens would be a second frontend),
 * not about fabrication itself. What matters is that a fabrication cannot rot
 * quietly: each key below is checked against the files that read it, so
 * renaming `album_art_url` in the component turns the guardrail red instead of
 * leaving a beautiful player rendering from a record nothing consumes.
 *
 * `source` travels with the record because `useSourceProgress` only ticks while
 * `active_source` matches, so the two have to be written together.
 */
const NOW_PLAYING = {
  'Spotify — playing': {
    source: 'spotify',
    metadata: {
      title: 'Says',
      artist: 'Nils Frahm',
      album_art_url: albumPlaceholder,
      is_playing: true,
      position: 192000,
      duration: 511000
    }
  },
  'CD — paused': {
    source: 'cd',
    metadata: {
      title: 'Ambre',
      artist: 'Nils Frahm',
      album_art_url: albumPlaceholder,
      is_playing: false,
      position: 64000,
      duration: 264000
    }
  },
  'CD — drive spinning up': {
    source: 'cd',
    metadata: { title: 'Ambre', artist: 'Nils Frahm', is_playing: false, disc_present: true }
  },
  'AirPlay — receiver, sender named': {
    source: 'airplay',
    metadata: {
      title: 'Ainsi parlait Zarathoustra',
      artist: 'Alain Bashung',
      album_art_url: albumPlaceholder,
      is_playing: true,
      client_name: 'Leo’s iPhone',
      position: 41000,
      duration: 297000
    }
  },
  'Nothing playing yet': { source: 'spotify', metadata: {} }
};

/** The files that read what NOW_PLAYING writes. Checked by the guardrail. */
const NOW_PLAYING_READERS = [
  'components/audio/AudioPlayerFull.vue',
  'composables/useSourceProgress.js',
  'utils/playbackBuffering.js'
];

export const REGISTRY = {
  Button: {
    component: Button,
    args: { variant: 'brand' },
    slots: { default: 'Button' },
    overrides: { leftIcon: OPTIONAL_ICON },
    // Six of the seven variants are self-coloured; `on-dark` is a translucent
    // white plate with white text, and shows as neither on the light stage.
    surface: args => (args.variant === 'on-dark' ? 'contrast' : null)
  },

  IconButton: {
    component: IconButton,
    args: { icon: 'play' },
    overrides: { icon: REQUIRED_ICON },
    // `on-grey` is the translucent dark plate the app puts over artwork, so its
    // backdrop is a mid tone rather than a dark one — the split ActionsDemo's two
    // strips make.
    surface: args => {
      if (args.variant === 'on-grey') return 'medium';
      return ['on-dark', 'ghost'].includes(args.variant) ? 'contrast' : null;
    }
  },

  ButtonGroup: {
    component: ButtonGroup,
    args: { modelValue: 'medium', options: SELECT_OPTIONS },
    overrides: {
      modelValue: { kind: 'enum', options: SELECT_OPTIONS.map(option => option.value) },
      inactiveVariant: { kind: 'enum', options: ['outline-neutral', 'background-neutral'] }
    }
  },

  ListItemButton: {
    component: ListItemButton,
    args: { title: 'Loudness', subtitle: 'A subtitle stacks under the title', action: 'toggle' },
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
    overrides: { modelValue: { kind: 'enum', options: SELECT_OPTIONS.map(option => option.value) } }
  },

  RangeSlider: {
    component: RangeSlider,
    args: { modelValue: 60 },
    overrides: { orientation: { kind: 'enum', options: ['horizontal', 'vertical'] } }
  },

  DoubleRangeSlider: {
    component: DoubleRangeSlider,
    args: { modelValue: { min: 20, max: 80 } }
  },

  LoadingSpinner: {
    component: LoadingSpinner,
    args: { size: 48 },
    overrides: { size: PIXEL_SIZE },
    // The `background` variant is the same spinner on its own light plate: on the
    // light stage the plate is invisible and the variant looks identical.
    surface: args => (args.variant === 'background' ? 'contrast' : null)
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
      variant: { kind: 'enum', options: ['default', 'dark'] },
      ctaVariant: { kind: 'enum', options: ['brand', 'background-strong', 'outline', 'important'] },
      ctaSecondaryVariant: { kind: 'enum', options: ['background-strong', 'brand', 'outline', 'important'] }
    },
    // `dark` drops the card and colours every line white, for the blurred artwork
    // the Lyrics view lays it over.
    surface: args => (args.variant === 'dark' ? 'contrast' : null)
  },

  LazyImage: {
    component: LazyImage,
    // The class goes on the component: its layers are absolutely positioned, so
    // the root collapses unless something sizes it.
    args: { src: albumPlaceholder, alt: 'Artwork', class: 'canvas-artwork' },
    overrides: { priority: { kind: 'enum', options: ['auto', 'high', 'low'] } },
    // The default slot lays over the image rather than beside it — where a
    // caller puts a play affordance or a badge on top of the artwork.
    slots: {
      default: {
        none: null,
        'IconButton — a play badge': { component: IconButton, props: { icon: 'play', variant: 'on-grey' } }
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
    // The validator closes over APP_ICON_NAMES, so it cannot be read from source.
    overrides: {
      name: { kind: 'enum', options: APP_ICON_NAMES },
      size: { kind: 'enum', options: [32, 64, 72, 'small', 'medium', 'large'] }
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
    args: { isOpen: true },
    slots: { default: 'Modal content. It springs to the height of what it holds.' }
  },

  NavigationHeader: {
    component: NavigationHeader,
    args: { title: 'Radio Nova', subtitle: 'Paris, France', showBack: true },
    overrides: { icon: OPTIONAL_ICON },
    // The slot hands down the icon variant matching the header's own — a scoped
    // prop the canvas cannot pass to a fixed choice, so the variant is pinned
    // here and the Variants tab is where that wiring is shown.
    slots: {
      actions: {
        none: null,
        'IconButton — search': { component: IconButton, props: { icon: 'search', variant: 'on-dark' } }
      }
    }
  },

  Dock: {
    component: Dock,
    state: {
      activeSource: {
        kind: 'enum',
        // The dock hides itself on `none`, and refuses to reveal — so the useful
        // starting point is a source that is playing.
        options: ['none', ...ALL_AUDIO_SOURCES],
        default: 'spotify',
        apply: (value, stores) => { stores.unified.systemState.active_source = value; }
      },
      transitioning: {
        kind: 'boolean',
        default: false,
        apply: (value, stores) => { stores.unified.systemState.transitioning = value; }
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
    }
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
    // `dark` is the light-fill variant drawn for the surfaces over artwork — on
    // the light stage its fill is white on white.
    surface: args => (args.variant === 'dark' ? 'contrast' : null)
  },

  PlaybackControls: {
    component: PlaybackControls,
    args: { isPlaying: true }
  },

  PlayerInfoText: {
    component: PlayerInfoText,
    args: {
      kicker: 'Radio Nova',
      title: 'Ainsi parlait Zarathoustra',
      secondary: 'Alain Bashung',
      class: 'canvas-column'
    }
  },

  TrackRow: {
    component: TrackRow,
    args: { number: 4, showArtist: true, showMenu: true, coverUrl: albumPlaceholder, class: 'canvas-column' },
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
    }
  },

  DetailHeader: {
    component: DetailHeader,
    args: {
      imageSrc: albumPlaceholder,
      title: 'Spaces',
      subtitle: 'Nils Frahm',
      subtitleMeta: '2013 · 17 tracks · 1 h 21',
      showFavorite: true,
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
          props: { icon: 'threeDots', variant: 'on-dark', size: 'small' }
        }
      }
    }
  },

  AudioPlayer: {
    component: AudioPlayer,
    args: {
      source: 'music_library',
      visible: true,
      artwork: albumPlaceholder,
      title: 'Says',
      isPlaying: true,
      swipeEnabled: true,
      currentIndex: 1
    },
    presets: {
      // Read only when swipeEnabled: the carousel renders the neighbours' text
      // locally so a swipe never waits for the backend echo.
      tracks: {
        'Three-track queue': [
          { title: 'Ambre', artist: 'Nils Frahm' },
          { title: 'Says', artist: 'Nils Frahm' },
          { title: 'Hammers', artist: 'Nils Frahm' }
        ],
        'Empty queue': []
      }
    },
    // Every slot takes a component this catalogue already carries, which is the
    // composition the three sources actually build — nothing is stood in for.
    slots: {
      info: {
        'PlayerInfoText': {
          component: PlayerInfoText,
          props: { kicker: 'Liked Songs', title: 'Says', secondary: 'Nils Frahm' }
        },
        none: null
      },
      progress: {
        'ProgressBar — dark': {
          component: ProgressBar,
          props: {
            currentPosition: 192000,
            duration: 511000,
            progressPercentage: 37.6,
            variant: 'dark',
            interactive: false
          }
        },
        none: null
      },
      controls: {
        'default — the built-in play/pause': null,
        'PlaybackControls': { component: PlaybackControls, props: { isPlaying: true } }
      },
      'artwork-badge': {
        none: null,
        'AppIcon — radio': { component: AppIcon, props: { name: 'radio', size: 32 } }
      }
    }
  },

  AudioPlayerFull: {
    component: AudioPlayerFull,
    args: { source: 'spotify', showControls: true, class: 'canvas-fill' },
    state: {
      nowPlaying: {
        kind: 'enum',
        options: Object.keys(NOW_PLAYING),
        default: 'Spotify — playing',
        apply: (value, stores) => {
          const record = NOW_PLAYING[value] ?? NOW_PLAYING['Spotify — playing'];
          stores.unified.systemState.active_source = record.source;
          stores.unified.systemState.metadata = { ...record.metadata };
        },
        // What the writer above invents, and where it is read. Declared so the
        // guardrail can check the two still agree — see gallery.test.js.
        records: Object.values(NOW_PLAYING).map(record => record.metadata),
        readBy: NOW_PLAYING_READERS
      }
    },
    slots: {
      // CD's two: the eject/tracklist row, and the tracklist itself, which
      // replaces the whole info column when hideContent is set.
      'action-buttons': {
        none: null,
        'IconButton — eject': { component: IconButton, props: { icon: 'eject', variant: 'on-grey' } }
      },
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
    args: { sourceType: 'bluetooth', displayState: 'active' },
    overrides: {
      // The validator is `value === 'none' || ALL_AUDIO_SOURCES.includes(value)`
      // — not a literal-array test, so there is nothing for the parser to read.
      sourceType: { kind: 'enum', options: ['none', ...ALL_AUDIO_SOURCES] },
      // Same: the validator defers to DISPLAY_STATES, so the list is borrowed
      // from the composable that derives it rather than restated here — which
      // is what stops the select outliving a state the app stopped producing.
      displayState: { kind: 'enum', options: [...DISPLAY_STATES] }
    },
    // Only read in the `active` branch, and the array is the ROC case: several
    // Macs streaming at once, which formatDeviceNames joins across two lines.
    presets: {
      deviceName: {
        'One sender': 'Leo’s iPhone',
        'Two senders (ROC)': ['Leo’s MacBook', 'Studio iMac'],
        none: ''
      }
    }
  },

  AudioScreensaver: {
    component: AudioScreensaver,
    args: {
      isVisible: true,
      mode: 'media',
      artwork: albumPlaceholder,
      title: 'Ainsi parlait Zarathoustra',
      subtitle: 'Alain Bashung',
      stationName: 'Radio Nova',
      sourceType: 'bluetooth'
    },
    overrides: {
      // Doubles as an AppIcon name in simple mode, and has no validator of its
      // own — the accepted set is AppIcon's, which is the source list.
      sourceType: { kind: 'enum', options: [null, ...ALL_AUDIO_SOURCES] },
      // The bottom-bar glyph for the sources with no favicon (AirPlay's sender).
      // An AppIcon name, not an SvgIcon one — the bar renders <AppIcon>.
      stationIcon: { kind: 'enum', options: [null, ...APP_ICON_NAMES] }
    },
    presets: {
      progress: {
        none: null,
        'Mid-episode': {
          currentPosition: 812000,
          duration: 2940000,
          progressPercentage: 27.6,
          isReady: true
        }
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
          props: { icon: 'heart', variant: 'background-strong', size: 'small' }
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
          image_url: albumPlaceholder,
          is_subscribed: false
        },
        'Subscribed': {
          uuid: 'a2',
          name: 'Le Code a changé',
          publisher: 'France Inter',
          image_url: albumPlaceholder,
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
    // `date_published` is epoch seconds, the Podcast Index unit, and `duration`
    // is seconds — the same pair TrackRow reads as seconds and ProgressBar as
    // milliseconds. Fixed values rather than a computed "now": a date that moves
    // with the clock would make the card read differently every day.
    presets: {
      episode: {
        'With show and date': {
          uuid: 'e1',
          name: 'Les gens qui parlent à leurs plantes',
          image_url: albumPlaceholder,
          duration: 2940,
          date_published: 1750000000,
          podcast: { name: 'Le Code a changé', image_url: albumPlaceholder }
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
    args: { label: 'True Crime', value: 'true_crime' },
    // The twelve artworks live in the component; `value` picks one, and a value
    // it does not know renders the tile with no image at all.
    overrides: {
      value: {
        kind: 'enum',
        options: [
          'comedy', 'society_and_culture', 'news', 'true_crime', 'business', 'education',
          'health_and_fitness', 'sports', 'arts', 'science', 'tv_and_film', 'music', 'unknown'
        ]
      }
    }
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

  SettingItem: {
    component: SettingItem,
    args: { label: 'Startup volume', class: 'canvas-column' },
    slots: { default: { 'A control': { component: ControlSample } } }
  },

  SectionHeader: {
    component: SectionHeader,
    args: { title: 'Stations', subtitle: '24 saved', class: 'canvas-column' },
    slots: {
      actions: {
        none: null,
        'IconButton — add': { component: IconButton, props: { icon: 'plus', variant: 'brand' } }
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
