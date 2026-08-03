<!-- frontend/src/components/gallery/CanvasApp.vue -->
<!--
  What renders inside the gallery's iframe: exactly one primitive, driven by the
  parent's control panel over postMessage.

  Messages rather than a URL of encoded args, because args change on every
  keystroke and reloading the document each time would restart the component
  under the finger. The channel is same-origin and checked as such.

  Only serialisable values cross it. Slot content and store writes cannot, and a
  named object value would lose its name, so the parent sends the *key* of a
  choice and this side resolves it out of registry.js — which works because that
  file is bundled into both documents.

    parent -> here   { type: 'render', id, args, slots, presets, state }
                     { type: 'action', id, name }
    here -> parent   { type: 'ready' }                     once, on mount
                     { type: 'event', name, arg }          the component emitted
                     { type: 'args', args }                a v-model wrote back
-->
<template>
  <div class="canvas" :class="[surfaceClass, { 'canvas--bleed': bleed }]">
    <p v-if="!entry" class="canvas__empty text-mono-small">
      {{ id ? `No playground descriptor for "${id}".` : 'Waiting for the gallery…' }}
    </p>

    <component
      :is="entry.component"
      v-else-if="!entry.alwaysMounted"
      :key="id"
      v-bind="bound"
      v-on="listeners"
    >
      <template v-for="(content, name) in slotContent" :key="name" #[name]>
        <component :is="content.component" v-if="content.component" v-bind="content.props" />
        <template v-else>{{ content.text }}</template>
      </template>
    </component>

    <!--
      App-level furniture, mounted for every selection exactly as App.vue mounts
      it for the app. Whatever is `alwaysMounted` owns shared module state that
      only renders through a live instance, so it has to be here whether or not it
      is the primitive under inspection — that is what lets the InputText
      playground open the real keyboard.
    -->
    <component :is="global.component" v-for="global in ALWAYS_MOUNTED" :key="global.component.__name" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { REGISTRY, entryFor, AUDIO_SOURCES_ID } from './registry';
import { installApiHarness } from './canvasHttp';
import { describeEvents, callbackProps } from './controls';
import { useVirtualKeyboard } from '@/composables/useVirtualKeyboard';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useLyricsStore } from '@/stores/lyricsStore';

/** Descriptors the canvas keeps on screen regardless of what is selected. */
const ALWAYS_MOUNTED = Object.values(REGISTRY).filter(entry => entry.alwaysMounted);

const id = ref('');
const args = ref({});
const slotChoices = ref({});
const presetChoices = ref({});

/**
 * The stores a `state` descriptor may write, and the composable an `actions`
 * entry may drive. Instantiated once here rather than per descriptor so a
 * component and its controls see the same instance.
 *
 * Nothing fabricates values: every store below ships real defaults, so what the
 * canvas shows is a freshly configured unit, and a State control edits it from
 * there.
 */
const context = {
  unified: useUnifiedAudioStore(),
  settings: useSettingsStore(),
  lyrics: useLyricsStore(),
  keyboard: useVirtualKeyboard()
};

/**
 * The canvas may not drive the appliance.
 *
 * AudioPlayerFull is the one catalogued component that acts through the store
 * instead of emitting, and its transport buttons would otherwise POST to the
 * real backend on the same origin — a documentation page has no business
 * pausing what is playing in the next room. The command is reported to the
 * event log instead, which is what a reader opened the page to see. This is the
 * harness standing in for the outside world, not the component being altered:
 * every other path through the store is left alone.
 */
context.unified.sendCommand = (source, command, data) => {
  post({ type: 'event', name: `sendCommand(${source}, ${command})`, arg: data && JSON.stringify(data) });
  return Promise.resolve(true);
};

/**
 * The same rule one layer down, for the same reason.
 *
 * The source pages mount real browsing views, which run real stores, which
 * fetch and POST through `apiCall` rather than through the store method above —
 * radio's playStation, a playlist being created, a share being mounted. So the
 * writes are blocked and the reads are served from the scenario's own fixtures.
 * See canvasHttp.js for why a read is not simply passed through.
 */
installApiHarness((name, detail) => post({ type: 'event', name, arg: detail }));

const entry = computed(() => (id.value ? entryFor(id.value) : undefined));

/**
 * The stage tone the current args call for, from the descriptor's own `surface`
 * rule. Resolved here rather than sent by the parent: this side already holds
 * both the descriptor and the args, and a tone crossing postMessage as a third
 * field could only ever disagree with them.
 */
const surfaceClass = computed(() => {
  const tone = entry.value?.surface?.(args.value);
  return tone ? `canvas--${tone}` : null;
});

/**
 * The stage's inset, dropped for the one selection that is a whole screen.
 *
 * A primitive is an object on a stage and reads better with air around it; a
 * source is what the unit shows edge to edge, and the viewport presets name a
 * real panel ("1280 × 800 — the unit"). Padding the stage there hands the source
 * 1232 × 752 instead — a different aspect ratio, a different column count in
 * every grid, and a player pane taking a different share of the row. So the
 * source page gets the viewport it is labelled with.
 */
const bleed = computed(() => id.value === AUDIO_SOURCES_ID);

function post(message) {
  window.parent?.postMessage({ source: 'milo-canvas', ...message }, window.location.origin);
}

/**
 * Same constraint as the other direction: postMessage structured-clones, and a
 * Vue reactive object is a Proxy it refuses. DoubleRangeSlider writes back a
 * `{ min, max }`, so the unwrap has to be deep.
 */
function postArgs() {
  try {
    post({ type: 'args', args: JSON.parse(JSON.stringify(args.value)) });
  } catch {
    // A value that will not serialise is not one the control panel can edit.
  }
}

/**
 * The value each preset-driven prop currently holds, resolved from the chosen
 * key. A `null` choice is a value like any other (no progress bar, no device
 * name), so nothing here filters on truthiness.
 */
const presetValues = computed(() => {
  const presets = entry.value?.presets;
  if (!presets) return {};

  const resolved = {};
  for (const [name, choices] of Object.entries(presets)) {
    const keys = Object.keys(choices);
    resolved[name] = choices[presetChoices.value[name] ?? keys[0]];
  }
  return resolved;
});

/**
 * Props handed to the component: the parent's args, the resolved presets on top
 * (an object-typed prop has no editable arg to compete with), plus a stub for
 * every callback-typed prop. MessageContent takes its CTAs as functions rather
 * than events, so without the stubs those buttons would render dead.
 */
const bound = computed(() => {
  if (!entry.value) return {};

  const props = { ...args.value, ...presetValues.value };
  for (const name of callbackProps(entry.value.component)) {
    if (props[name] == null) {
      props[name] = () => post({ type: 'event', name: `${name}()` });
    }
  }
  return props;
});

/**
 * Resolved slot content. A descriptor's slot is either a plain string, or a map
 * of named choices the parent picks from by key.
 */
const slotContent = computed(() => {
  const slots = entry.value?.slots;
  if (!slots) return {};

  const resolved = {};
  for (const [name, definition] of Object.entries(slots)) {
    if (typeof definition === 'string') {
      resolved[name] = { text: definition };
      continue;
    }

    const keys = Object.keys(definition);
    const chosen = definition[slotChoices.value[name] ?? keys[0]];
    if (chosen) resolved[name] = chosen;
  }
  return resolved;
});

/**
 * One listener per declared event. Everything is reported to the parent; on top
 * of that, an event that carries a value writes it back into the args so the
 * control panel follows a drag or a tap — `update:modelValue` implicitly, and
 * whatever the descriptor's `sync` map names.
 */
const listeners = computed(() => {
  if (!entry.value) return {};

  const sync = { 'update:modelValue': 'modelValue', ...(entry.value.sync || {}) };
  const handlers = {};

  for (const name of describeEvents(entry.value.component)) {
    handlers[name] = (payload) => {
      const target = sync[name];
      if (target !== undefined && target !== null) {
        args.value = { ...args.value, [target]: payload };
        postArgs();
      }
      // A DOM event as a payload would serialise to an empty object; the name is
      // the information here, so only primitives are forwarded.
      const arg = payload !== null && typeof payload === 'object' ? undefined : payload;
      post({ type: 'event', name, arg });
    };
  }

  return handlers;
});

/** Runs each `state` descriptor's own writer with the value the panel holds. */
function applyState(values) {
  const descriptors = entry.value?.state;
  if (!descriptors) return;

  for (const [name, descriptor] of Object.entries(descriptors)) {
    const value = values?.[name] ?? descriptor.default;
    try {
      descriptor.apply(value, context);
    } catch (error) {
      post({ type: 'event', name: `state:${name} failed`, arg: String(error?.message ?? error) });
    }
  }
}

function runAction(name) {
  const action = entry.value?.actions?.[name];
  if (!action) return;
  try {
    action(context);
    post({ type: 'event', name: `action:${name}` });
  } catch (error) {
    post({ type: 'event', name: `action:${name} failed`, arg: String(error?.message ?? error) });
  }
}

function handleMessage(event) {
  if (event.origin !== window.location.origin) return;
  const data = event.data;
  if (data?.source !== 'milo-gallery') return;

  if (data.type === 'render') {
    id.value = data.id ?? '';
    args.value = data.args ?? {};
    slotChoices.value = data.slots ?? {};
    presetChoices.value = data.presets ?? {};
    applyState(data.state);
  } else if (data.type === 'action') {
    runAction(data.name);
  }
}

onMounted(() => {
  window.addEventListener('message', handleMessage);
  post({ type: 'ready' });
});

onUnmounted(() => {
  window.removeEventListener('message', handleMessage);
});
</script>

<style scoped>
.canvas {
  --canvas-pad: var(--space-05);
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  min-height: 100vh;
  padding: var(--canvas-pad);
  background: var(--color-background);
}

/* Zeroes the pad rather than the padding, so the `100vh - 2 * pad` height below
   follows it — one value, and the stage cannot end up taller than it is wide.
   Two classes deep so it outranks `.canvas` whatever the stylesheet order. */
.canvas.canvas--bleed {
  --canvas-pad: 0px;
}

/* The height a component that fills its host gets here. Definite on purpose,
   and not `align-self: stretch`: a stretched flex line inside a `min-height`
   container resolves to the content's own height, and every fill component
   declares `height: 100%`, which then has nothing to resolve against — the
   player rendered as a band a third of the stage tall instead of filling it. */
.canvas :deep(.canvas-fill),
.canvas :deep(.audio-player) {
  height: calc(100vh - 2 * var(--canvas-pad));
}

/* The two non-default stage tones, named by a descriptor's `surface`. A variant
   is only legible over the backdrop it was drawn for, so the stage follows the
   args instead of staying light and reporting the variant as broken. */
.canvas--contrast {
  color: var(--color-text-contrast);
  background: var(--color-background-contrast);
}

/* Translucent, so it composites over the body's own background into the mid tone
   a plate over artwork actually sits on — the app never paints this one solid. */
.canvas--medium {
  background: var(--color-background-medium-32);
}

.canvas__empty {
  color: var(--color-text-light);
}

/* The shared composites are block-level: a row, a card, a header fills its
   column in the app, but the stage centres what it holds, so without a width
   they shrink to their content and read as broken. Handed through their args. */
.canvas :deep(.canvas-column) {
  width: 100%;
  max-width: 560px;
}

/* AudioPlayer sizes itself to its host (width and height 100%), and its host in
   the app is AudioSourceLayout's 340px sticky pane — restated here because the
   stage is not that pane, or the player is a full-width band.

   Named by its own class rather than handed one through the args: its root is a
   Teleport, so an inherited `class` has no element to land on and Vue says so.
   That the selector is scoped under `.canvas` is the useful half — below 4:3 the
   player teleports to body, out of this subtree, so the mobile mini-bar keeps
   its own full-width geometry and only the docked form is constrained. */
.canvas :deep(.audio-player) {
  width: 340px;
}

/* AudioSourceLayout and AudioPlayerFull are `height: 100%` of a pane they do not
   have here, and the stage centres what it holds — so without this they collapse
   to their content and neither the scroll container nor the player animation has
   room to happen. Handed through their args like LazyImage's class below; the
   height comes from the shared rule at the top. */
.canvas :deep(.canvas-fill) {
  flex: 1;
  min-width: 0;
}

/* LazyImage is handed this class through its args: its layers are absolutely
   positioned, so the root collapses to nothing unless something sizes it. */
.canvas :deep(.canvas-artwork) {
  width: 180px;
  height: 180px;
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
}
</style>
