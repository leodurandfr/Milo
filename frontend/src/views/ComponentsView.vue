<!-- frontend/src/views/ComponentsView.vue -->
<!--
  The primitive gallery, at /components. Reachable by URL only — no Dock entry,
  no link from the app — and lazily imported, so the chunk is fetched the first
  time someone opens the page and costs an end user nothing.

  It is browsed from a computer, not from the unit's panel, so nothing here is
  sized for 1280×800. It still ships in the production build because that is the
  only way to reach it without a build step: the Vite dev server proxies to
  127.0.0.1:8000, so running it from a laptop would not find the Pi's backend.
  That is also why this is not Storybook — a static Storybook build would mean a
  second bundle and an nginx location in both deployment trees, and its Controls
  panel would still have to be written by hand, per story, since it cannot read a
  Vue prop validator either.

  Three panes: pick a primitive on the left, drive it in the middle, edit its
  props on the right. The route carries the selection (`?c=Button`) so a link
  survives a reload, and `meta.chrome: false` tells App.vue to drop the Dock and
  the warm colour filter over this route.

  The list holds three axes, coarsest first: the design tokens (foundations.js),
  the ten audio sources in every state they reach (sources.js), and the
  catalogued components themselves (catalog.js). Only the last two have a live
  instance to drive, so a foundations page replaces the canvas and the props
  pane both.
-->
<template>
  <div class="gallery" :class="{ 'gallery--with-panel': showPanel, 'gallery--nav-open': navOpen }">
    <!-- Both inert on a desktop width (display: none); the drawer only exists
         once the layout collapses to one column. -->
    <div class="gallery__scrim" @click="navOpen = false" />

    <GallerySidebar
      v-model:query="query"
      :selected="selected"
      class="gallery__sidebar"
      @select="select"
    />

    <main class="gallery__main">
      <header class="gallery__head">
        <button
          v-press
          type="button"
          class="gallery__menu text-mono-small"
          :aria-expanded="navOpen"
          @click="navOpen = true"
        >
          ☰ Components
        </button>
        <div class="gallery__title">
          <h2 class="heading-4">{{ header.title }}</h2>
          <span v-if="header.badge" class="gallery__badge text-mono-small">{{ header.badge }}</span>
          <code class="gallery__path text-mono-small">{{ header.path }}</code>
          <IconButton
            :icon="summaryOpen ? 'caretUp' : 'caretDown'"
            variant="ghost"
            size="small"
            color="var(--color-text-light)"
            :aria-expanded="summaryOpen"
            aria-label="Toggle description"
            @click="summaryOpen = !summaryOpen"
          />
        </div>
        <p v-if="summaryOpen" class="gallery__summary text-mono-small">{{ header.summary }}</p>
        <div v-if="tabOptions.length > 1" class="gallery__tabs">
          <button
            v-for="option in tabOptions"
            :key="option.value"
            v-press
            type="button"
            class="gallery__tab text-mono-small"
            :class="{ 'gallery__tab--active': option.value === tab }"
            @click="tab = option.value"
          >
            {{ option.label }}
          </button>
        </div>
      </header>

      <FoundationsPage v-if="foundationPage" :page="foundationPage" />

      <template v-else-if="tab === 'playground'">
        <div v-if="!playground" class="gallery__note text-mono-small">
          No playground descriptor for this one — see registry.js.
        </div>
        <GalleryCanvas
          v-else
          ref="canvas"
          :id="selected"
          :args="args"
          :slots="slotChoices"
          :presets="presetChoices"
          :state="stateValues"
          :default-viewport="sourcePage ? 'kiosk' : 'fill'"
          class="gallery__canvas"
          @event="pushEvent"
          @args="mergeArgs"
        />
      </template>

      <div v-else class="gallery__variants">
        <component :is="DEMOS[catalogEntry.group]" v-if="catalogEntry" />
      </div>
    </main>

    <aside v-if="showPanel" class="gallery__panel">
      <GalleryControls
        :descriptors="descriptors"
        :events="events"
        :args="args"
        :slot-options="slotOptions"
        :slot-choices="slotChoices"
        :preset-options="presetOptions"
        :preset-choices="presetChoices"
        :state="playground?.state || {}"
        :state-values="stateValues"
        :notes="playground?.notes || {}"
        :action-names="actionNames"
        :log="log"
        @update="mergeArgs"
        @slot="mergeSlots"
        @preset="mergePresets"
        @state="mergeState"
        @action="runAction"
      />
    </aside>
  </div>
</template>

<script setup>
import { ref, computed, watch, provide } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ENTRIES, entryById } from '@/components/gallery/catalog';
import { sourcePageById } from '@/components/gallery/sources';
import { foundationPageById, isFoundationId } from '@/components/gallery/foundations';
import { entryFor, overridesFor, AUDIO_SOURCES_ID } from '@/components/gallery/registry';
import { describeProps, describeEvents } from '@/components/gallery/controls';
import GallerySidebar from '@/components/gallery/GallerySidebar.vue';
import GalleryCanvas from '@/components/gallery/GalleryCanvas.vue';
import FoundationsPage from '@/components/gallery/FoundationsPage.vue';
import GalleryControls from '@/components/gallery/GalleryControls.vue';
import IconButton from '@/components/ui/IconButton.vue';
import ActionsDemo from '@/components/gallery/demos/ActionsDemo.vue';
import ControlsDemo from '@/components/gallery/demos/ControlsDemo.vue';
import FeedbackDemo from '@/components/gallery/demos/FeedbackDemo.vue';
import MediaDemo from '@/components/gallery/demos/MediaDemo.vue';
import StructureDemo from '@/components/gallery/demos/StructureDemo.vue';
import PlayerDemo from '@/components/gallery/demos/PlayerDemo.vue';
import LayoutDemo from '@/components/gallery/demos/LayoutDemo.vue';
import CardsDemo from '@/components/gallery/demos/CardsDemo.vue';
import SettingsDemo from '@/components/gallery/demos/SettingsDemo.vue';

// One demo per catalogue group. Keyed by group id, and tests/architecture/
// gallery.test.js checks the two sets match — a group with no demo would
// otherwise render as a blank tab.
const DEMOS = {
  actions: ActionsDemo,
  controls: ControlsDemo,
  feedback: FeedbackDemo,
  media: MediaDemo,
  structure: StructureDemo,
  player: PlayerDemo,
  layout: LayoutDemo,
  cards: CardsDemo,
  settings: SettingsDemo
};

const TAB_OPTIONS = [
  { label: 'Playground', value: 'playground' },
  { label: 'Variants', value: 'variants' }
];

/** Newest-first, and bounded: a slider drag emits on every frame. */
const LOG_LIMIT = 40;

const route = useRoute();
const router = useRouter();

const query = ref('');
const tab = ref('playground');
/** Drives the mobile drawer only; the desktop grid ignores it. */
const navOpen = ref(false);
/**
 * The header summary is one click away rather than always on. Kept across
 * selections on purpose — it is a reading preference, not part of the
 * selection, so neither the `watch(selected)` reset below nor the `?c=` link
 * touches it.
 */
const summaryOpen = ref(false);
const args = ref({});
const slotChoices = ref({});
const presetChoices = ref({});
const stateValues = ref({});
const log = ref([]);
const canvas = ref(null);

const selected = computed(() => {
  const wanted = route.query.c;
  const known = !!entryById(wanted) || wanted === AUDIO_SOURCES_ID || isFoundationId(wanted);
  return known ? wanted : ENTRIES[0].id;
});

const catalogEntry = computed(() => entryById(selected.value));
const playground = computed(() => entryFor(selected.value));

/**
 * The third axis: the design tokens themselves. A page of them has no live
 * instance, so it replaces the canvas outright rather than rendering in it —
 * which is also why the props pane disappears on its own (there is no
 * descriptor for `showPanel` to find).
 */
const foundationPage = computed(() => foundationPageById(selected.value));

/**
 * Which source is on the stage — read from the args rather than the selection,
 * because the ten share one entry and the `page` select is what moves between
 * them. Everything the header shows follows from it.
 */
const sourcePage = computed(() =>
  selected.value === AUDIO_SOURCES_ID ? sourcePageById(args.value.page) : undefined
);

/**
 * The four header fields, from whichever of the two catalogues answers. A
 * source page has no single file to name, so the `<code>` slot carries the
 * components it is made of instead — which is the same question the path
 * answers for a primitive: where does what I am looking at come from.
 */
const header = computed(() => {
  const foundation = foundationPage.value;
  if (foundation) {
    return { title: foundation.title, path: 'assets/styles/design-system.css', summary: foundation.summary };
  }

  const page = sourcePage.value;
  if (page) {
    return { title: page.title, badge: page.family, path: page.uses, summary: page.summary };
  }

  const entry = catalogEntry.value;
  return {
    title: selected.value,
    badge: entry?.coupling,
    path: entry?.file,
    summary: entry?.summary
  };
});

/**
 * A source page has no Variants demo: the demos are one file per catalogue
 * group, and a source belongs to none. Its states are the playground's own
 * `scenario` control, which is the tab it would have pointed at anyway. A
 * foundations page has neither tab — it is one planche. Below two options the
 * row is not rendered at all: a lone always-active button is a control that
 * does nothing.
 */
const tabOptions = computed(() => {
  if (foundationPage.value) return [];
  return sourcePage.value ? TAB_OPTIONS.filter(option => option.value === 'playground') : TAB_OPTIONS;
});

const descriptors = computed(() =>
  playground.value ? describeProps(playground.value.component, overridesFor(playground.value, args.value)) : []
);

const events = computed(() =>
  playground.value ? describeEvents(playground.value.component) : []
);

/** The props pane exists only where there is a live instance to drive. */
const showPanel = computed(() => tab.value === 'playground' && !!playground.value);

/**
 * Slot choices offered per slot, as keys. A descriptor's slot is either a plain
 * string — nothing to choose, so it is not listed — or a map of named options.
 */
const slotOptions = computed(() => {
  const slots = playground.value?.slots ?? {};
  const offered = {};
  for (const [name, definition] of Object.entries(slots)) {
    if (typeof definition !== 'string') offered[name] = Object.keys(definition);
  }
  return offered;
});

/** prop name -> the preset keys the descriptor names for it. */
const presetOptions = computed(() => {
  const presets = playground.value?.presets ?? {};
  const offered = {};
  for (const [name, choices] of Object.entries(presets)) offered[name] = Object.keys(choices);
  return offered;
});

const actionNames = computed(() => Object.keys(playground.value?.actions ?? {}));

/**
 * The Variants tab reuses the per-group demos untouched; each GalleryItem reads
 * this and renders nothing when it is not the selected primitive. That keeps one
 * file per group instead of one per component, and the icon grids with it.
 */
provide('gallerySelectedId', selected);

/** Vue's own defaults first, then whatever the descriptor starts from. */
function initialArgs(id) {
  const descriptor = entryFor(id);
  if (!descriptor) return {};

  const values = {};
  for (const prop of describeProps(descriptor.component, overridesFor(descriptor, descriptor.args || {}))) {
    if (prop.default !== undefined) values[prop.name] = prop.default;
  }
  return { ...values, ...(descriptor.args || {}) };
}

function select(id) {
  router.replace({ query: { ...route.query, c: id } });
  navOpen.value = false;
}

/**
 * One select can narrow another (the sources page: `page` decides which
 * `scenario`s exist), so after every write the enum args are checked against
 * the options they would now be offered, and a value that is no longer one of
 * them falls back to the first. Only a value that is actually set is touched —
 * an unset enum stays unset, which is how a primitive's optional icon keeps
 * rendering as "—" rather than silently acquiring the first glyph.
 */
function mergeArgs(patch) {
  const next = { ...args.value, ...patch };
  const descriptor = playground.value;
  if (!descriptor) {
    args.value = next;
    return;
  }

  for (const prop of describeProps(descriptor.component, overridesFor(descriptor, next))) {
    if (prop.kind !== 'enum' || !prop.options?.length) continue;
    if (next[prop.name] === undefined) continue;
    if (!prop.options.includes(next[prop.name])) next[prop.name] = prop.options[0];
  }
  args.value = next;
}

function mergeSlots(patch) {
  slotChoices.value = { ...slotChoices.value, ...patch };
}

function mergePresets(patch) {
  presetChoices.value = { ...presetChoices.value, ...patch };
}

function mergeState(patch) {
  stateValues.value = { ...stateValues.value, ...patch };
}

function runAction(name) {
  canvas.value?.runAction(name);
}

function pushEvent(entryReported) {
  log.value = [entryReported, ...log.value].slice(0, LOG_LIMIT);
}

/** Whatever each state descriptor declares as its own starting value. */
function initialState(id) {
  const descriptor = entryFor(id);
  const values = {};
  for (const [name, state] of Object.entries(descriptor?.state ?? {})) {
    values[name] = state.default;
  }
  return values;
}

/** First key of each slot's option map — the descriptor's own default choice. */
function initialSlots(id) {
  const descriptor = entryFor(id);
  const choices = {};
  for (const [name, definition] of Object.entries(descriptor?.slots ?? {})) {
    if (typeof definition !== 'string') choices[name] = Object.keys(definition)[0];
  }
  return choices;
}

/** Same rule for the preset props: whichever the descriptor declares first. */
function initialPresets(id) {
  const descriptor = entryFor(id);
  const choices = {};
  for (const [name, definition] of Object.entries(descriptor?.presets ?? {})) {
    choices[name] = Object.keys(definition)[0];
  }
  return choices;
}

watch(selected, (id) => {
  // The sources page offers no Variants tab, so arriving on it from that tab
  // would leave the pane blank and the button that got you there gone. Keyed on
  // the id rather than sourcePage, which reads args this watcher has yet to set.
  if (id === AUDIO_SOURCES_ID || isFoundationId(id)) tab.value = 'playground';
  args.value = initialArgs(id);
  slotChoices.value = initialSlots(id);
  presetChoices.value = initialPresets(id);
  stateValues.value = initialState(id);
  log.value = [];
}, { immediate: true });
</script>

<style scoped>
/* Two panes by default — the props pane only exists where there is a live
   instance to drive, so the Variants tab gives its width back to the content. */
.gallery {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  height: 100%;
  background: var(--color-background);
}

.gallery--with-panel {
  grid-template-columns: 260px minmax(0, 1fr) 320px;
}

.gallery__sidebar {
  border-right: 1px solid var(--color-border);
}

/* The hamburger and the drawer scrim exist only once the layout collapses. */
.gallery__menu,
.gallery__scrim {
  display: none;
}

.gallery__main {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  min-width: 0;
  padding: var(--space-04);
  overflow-y: auto;
}

.gallery__head {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.gallery__title {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-02);
}

.gallery__badge {
  padding: 0 var(--space-01);
  color: var(--color-warning);
  background: var(--color-warning-subtle);
  border-radius: var(--radius-01);
}

.gallery__path {
  color: var(--color-text-light);
}

.gallery__summary {
  max-width: 78ch;
  margin: 0;
  color: var(--color-text-secondary);
}

.gallery__tabs {
  display: flex;
  gap: var(--space-01);
}

.gallery__tab {
  padding: var(--space-01) var(--space-03);
  color: var(--color-text-secondary);
  background: var(--color-background-neutral);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-02);
  cursor: pointer;
}

.gallery__tab--active {
  color: var(--color-text-contrast);
  background: var(--color-brand);
  border-color: var(--color-brand);
}

.gallery__canvas {
  flex: 1;
  min-height: 0;
}

.gallery__note {
  padding: var(--space-04);
  color: var(--color-text-secondary);
  background: var(--color-background-strong);
  border-radius: var(--radius-03);
}

.gallery__variants {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.gallery__panel {
  padding: var(--space-03);
  overflow-y: auto;
  background: var(--color-background-neutral);
  border-left: 1px solid var(--color-border);
}

/* Below a laptop width the three panes stop fitting. Rather than stack the
   23-item list on top of every visit, the canvas and its controls become the
   single column and the list slides in from the side on demand. */
@media (max-width: 1100px) {
  .gallery,
  .gallery--with-panel {
    grid-template-columns: 1fr;
    /* The kiosk shell pins html/#app at 100dvh with overflow hidden, so the
       document itself never scrolls. On desktop each pane scrolls internally;
       collapsed, the stacked column becomes the one scroll container instead. */
    height: 100%;
    overflow-y: auto;
  }

  /* Panes grow to content and let the column above scroll as one — a nested
     `overflow-y: auto` here would swallow the touch gesture. */
  .gallery__main,
  .gallery__panel {
    overflow: visible;
  }

  /* The list leaves the grid (it is `position: fixed`), so only the canvas and
     the panel remain to auto-flow into rows. */
  .gallery__sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 20;
    width: min(280px, 82vw);
    transform: translateX(-100%);
    transition: transform var(--transition-fast);
  }

  .gallery--nav-open .gallery__sidebar {
    transform: translateX(0);
    box-shadow: var(--shadow-raised-02);
  }

  .gallery__scrim {
    position: fixed;
    inset: 0;
    z-index: 10;
    display: block;
    background: var(--color-background-scrim);
    opacity: 0;
    pointer-events: none;
    transition: opacity var(--transition-fast);
  }

  .gallery--nav-open .gallery__scrim {
    opacity: 0.5;
    pointer-events: auto;
  }

  .gallery__menu {
    display: inline-flex;
    align-self: flex-start;
    padding: var(--space-01) var(--space-03);
    color: var(--color-text-secondary);
    background: var(--color-background-neutral);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-02);
    cursor: pointer;
  }

  .gallery__panel {
    border-left: 0;
    border-top: 1px solid var(--color-border);
  }
}
</style>
