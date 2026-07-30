// frontend/src/components/gallery/catalog.js
/**
 * The primitive catalogue behind /components.
 *
 * Metadata only — no Vue import, no component reference — for two reasons:
 * the page reads it for its headings and nav, and
 * tests/architecture/gallery.test.js reads it under Node to check that every
 * components/ui/*.vue is listed and that no entry points at a deleted file.
 * The demos themselves live in demos/, one file per group.
 *
 * Scope is deliberately ui/ and nothing else. The ~92 per-source screens are
 * coupled to their stores; putting them here would mean faking state, which is
 * a second frontend to maintain. The shared composites (audio/, the
 * settings/ primitives) are a defensible later addition — they are genuinely
 * reused — but they need realistic props, so they are not in v1.
 *
 * coupling says why a primitive is not a pure props-in component: it reads a
 * store, is driven by a composable, or is position: fixed app chrome. It is
 * documentation rather than a capability flag — all 23 do render in the
 * playground — and it earns its place because a reader who takes Dock for a
 * reusable primitive is the mistake worth preventing.
 */

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
];

/** One entry per file in components/ui/. */
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
    summary: 'Indeterminate spinner. background is the variant for a light-on-dark surface.',
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
    summary: 'The per-source app tile. Rendered as-authored (no recolouring) — these carry brand colour.',
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
];

/** Entries of one group, in declaration order. */
export function entriesOf(groupId) {
  return ENTRIES.filter(entry => entry.group === groupId);
}

/** Lookup used by GalleryItem so a demo only has to name the component. */
export function entryById(id) {
  return ENTRIES.find(entry => entry.id === id);
}
