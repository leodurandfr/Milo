// frontend/src/components/gallery/registry.js
/**
 * What the playground needs to render one primitive on its own.
 *
 * Everything derivable is derived (see controls.js) — this file holds only what
 * a component cannot tell us about itself:
 *
 *   args      starting prop values. A required prop with no default must appear
 *             here or the canvas renders a broken instance.
 *   slots     slot name -> plain text. Text only, on purpose: rich slot content
 *             is composition, and the Variants tab already shows it.
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
 *   state     store writes a component depends on, exposed as controls. Nothing
 *             here is derivable: a store field is not introspectable the way a
 *             prop is.
 *   actions   named triggers the panel renders as buttons and the canvas runs.
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
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';
import albumPlaceholder from '@/assets/images/album-placeholder.svg';

/** `null` first so a nullable icon prop can be cleared from the select. */
const OPTIONAL_ICON = { kind: 'enum', options: [null, ...ICON_NAMES] };
const REQUIRED_ICON = { kind: 'enum', options: ICON_NAMES };
const PIXEL_SIZE = { kind: 'enum', options: [16, 24, 32, 48, 64] };

const SELECT_OPTIONS = [
  { label: 'Low', value: 'low' },
  { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' }
];

export const REGISTRY = {
  Button: {
    component: Button,
    args: { variant: 'brand' },
    slots: { default: 'Button' },
    overrides: { leftIcon: OPTIONAL_ICON }
  },

  IconButton: {
    component: IconButton,
    args: { icon: 'play' },
    overrides: { icon: REQUIRED_ICON }
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
    // table — these are the choices the Slots section offers instead.
    slots: {
      icon: {
        none: null,
        'AppIcon — radio': { component: AppIcon, props: { name: 'radio', size: 32 } },
        'AppIcon — spotify': { component: AppIcon, props: { name: 'spotify', size: 32 } },
        'SvgIcon — speakerShelf': { component: SvgIcon, props: { name: 'speakerShelf', size: 24 } }
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
    slots: { default: 'Content revealed by the header toggle.' },
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
      variant: { kind: 'enum', options: ['default', 'dark'] },
      ctaVariant: { kind: 'enum', options: ['brand', 'background-strong', 'outline', 'important'] },
      ctaSecondaryVariant: { kind: 'enum', options: ['background-strong', 'brand', 'outline', 'important'] }
    }
  },

  LazyImage: {
    component: LazyImage,
    // The class goes on the component: its layers are absolutely positioned, so
    // the root collapses unless something sizes it.
    args: { src: albumPlaceholder, alt: 'Artwork', class: 'canvas-artwork' },
    overrides: { priority: { kind: 'enum', options: ['auto', 'high', 'low'] } }
  },

  SvgIcon: {
    component: SvgIcon,
    args: { name: 'play', size: 48 },
    overrides: {
      name: REQUIRED_ICON,
      size: { kind: 'enum', options: [16, 24, 32, 48, 64, 'small', 'medium', 'large'] }
    }
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
    overrides: { icon: OPTIONAL_ICON }
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
  }
};

/** The playground descriptor for one primitive, or undefined if it has none. */
export function entryFor(id) {
  return REGISTRY[id];
}
