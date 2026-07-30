<!-- frontend/src/components/gallery/CanvasApp.vue -->
<!--
  What renders inside the gallery's iframe: exactly one primitive, driven by the
  parent's control panel over postMessage.

  Messages rather than a URL of encoded args, because args change on every
  keystroke and reloading the document each time would restart the component
  under the finger. The channel is same-origin and checked as such.

  Only serialisable values cross it. Slot content and store writes cannot, so the
  parent sends the *key* of a choice and this side resolves it out of
  registry.js — which works because that file is bundled into both documents.

    parent -> here   { type: 'render', id, args, slots, state }
                     { type: 'action', id, name }
    here -> parent   { type: 'ready' }                     once, on mount
                     { type: 'event', name, arg }          the component emitted
                     { type: 'args', args }                a v-model wrote back
-->
<template>
  <div class="canvas">
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
import { REGISTRY, entryFor } from './registry';
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

const entry = computed(() => (id.value ? entryFor(id.value) : undefined));

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
 * Props handed to the component: the parent's args, plus a stub for every
 * callback-typed prop. MessageContent takes its CTAs as functions rather than
 * events, so without the stubs those buttons would render dead.
 */
const bound = computed(() => {
  if (!entry.value) return {};

  const props = { ...args.value };
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
  display: flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  min-height: 100vh;
  padding: var(--space-05);
  background: var(--color-background);
}

.canvas__empty {
  color: var(--color-text-light);
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
