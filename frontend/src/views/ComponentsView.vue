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
-->
<template>
  <div class="gallery" :class="{ 'gallery--with-panel': showPanel }">
    <GallerySidebar
      v-model:query="query"
      :selected="selected"
      class="gallery__sidebar"
      @select="select"
    />

    <main class="gallery__main">
      <header class="gallery__head">
        <div class="gallery__title">
          <h2 class="heading-4">{{ selected }}</h2>
          <span v-if="entry?.coupling" class="gallery__badge text-mono-small">{{ entry.coupling }}</span>
          <code class="gallery__path text-mono-small">{{ entry?.file }}</code>
        </div>
        <p class="gallery__summary text-mono-small">{{ entry?.summary }}</p>
        <div class="gallery__tabs">
          <button
            v-for="option in TAB_OPTIONS"
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

      <template v-if="tab === 'playground'">
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
          class="gallery__canvas"
          @event="pushEvent"
          @args="mergeArgs"
        />
      </template>

      <div v-else class="gallery__variants">
        <component :is="DEMOS[entry.group]" v-if="entry" />
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
import { entryFor } from '@/components/gallery/registry';
import { describeProps, describeEvents } from '@/components/gallery/controls';
import GallerySidebar from '@/components/gallery/GallerySidebar.vue';
import GalleryCanvas from '@/components/gallery/GalleryCanvas.vue';
import GalleryControls from '@/components/gallery/GalleryControls.vue';
import ActionsDemo from '@/components/gallery/demos/ActionsDemo.vue';
import ControlsDemo from '@/components/gallery/demos/ControlsDemo.vue';
import FeedbackDemo from '@/components/gallery/demos/FeedbackDemo.vue';
import MediaDemo from '@/components/gallery/demos/MediaDemo.vue';
import StructureDemo from '@/components/gallery/demos/StructureDemo.vue';
import PlayerDemo from '@/components/gallery/demos/PlayerDemo.vue';
import LayoutDemo from '@/components/gallery/demos/LayoutDemo.vue';
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
const args = ref({});
const slotChoices = ref({});
const presetChoices = ref({});
const stateValues = ref({});
const log = ref([]);
const canvas = ref(null);

const selected = computed(() => {
  const wanted = route.query.c;
  return entryById(wanted) ? wanted : ENTRIES[0].id;
});

const entry = computed(() => entryById(selected.value));
const playground = computed(() => entryFor(selected.value));

const descriptors = computed(() =>
  playground.value ? describeProps(playground.value.component, playground.value.overrides || {}) : []
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
  for (const prop of describeProps(descriptor.component, descriptor.overrides || {})) {
    if (prop.default !== undefined) values[prop.name] = prop.default;
  }
  return { ...values, ...(descriptor.args || {}) };
}

function select(id) {
  router.replace({ query: { ...route.query, c: id } });
}

function mergeArgs(patch) {
  args.value = { ...args.value, ...patch };
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

/* Below a laptop width the three panes stop fitting; stack them instead. */
@media (max-width: 1100px) {
  .gallery,
  .gallery--with-panel {
    grid-template-columns: 1fr;
    grid-template-rows: auto minmax(0, 1fr) auto;
    height: auto;
    min-height: 100%;
  }

  .gallery__sidebar {
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
  }

  .gallery__panel {
    border-left: 0;
    border-top: 1px solid var(--color-border);
  }
}
</style>
