<!-- frontend/src/components/gallery/GalleryControls.vue -->
<!--
  The inspector: props, slots, store state, actions, events.

  Props rows are built from what the component declares about itself (see
  controls.js) — nothing there is per-component. The other three sections cannot
  be derived and come from the playground descriptor: a slot is not a prop, a
  store field is not introspectable, and a trigger is not a value.

  One prop row is not derived either: an object-typed prop (a song, a progress
  record, a device list) has no widget that could express it, so the descriptor
  names a handful and the row becomes a select over those names. It stays in the
  Props section rather than a fourth one, because it *is* a prop — the reader
  needs to see it beside the booleans that switch on it.

  Nor is the note under a control. Half the controls here are read only under a
  condition — a branch the other props pick (`showProgress` is read only when
  `showControls` is off), a mode, a viewport, an animation that plays on the way
  out — and a panel built from `Component.props` cannot see any of it: it offers
  a switch that does nothing and says nothing. The descriptor names the condition
  in one line, which is the only per-control text on this panel.

  The chrome here is deliberately its own dense set of small controls rather than
  the app's primitives. That reverses the first version of this page, and for a
  reason: `Button` and `InputText` are sized for a finger on a 7-inch panel, and a
  reference panel read on a desktop needs several times their density. `Toggle`
  stays in its compact size, because a switch carries no text and fits as it is.
  `Dropdown` does not: its only small-typed variant, `minimal`, paints its label
  in `--color-text-contrast-50` for a dark surface, so on this panel it renders
  invisible. Enums use a native select instead, which also handles a 51-option
  icon list better than a custom menu would.
-->
<template>
  <div class="controls">
    <section v-if="descriptors.length" class="controls__section">
      <h3 class="controls__section-title text-mono-small">Props</h3>

      <div v-for="prop in descriptors" :key="prop.name" class="controls__row">
        <div class="controls__head">
          <span class="controls__name text-mono-small">{{ prop.name }}</span>
          <span v-if="prop.required" class="controls__required text-mono-small">required</span>
          <span class="controls__type text-mono-small">{{ prop.types }}</span>
        </div>

        <!-- An object-typed prop: no widget can express it, so the descriptor
             names a few and this picks one. Sits ahead of the derived kinds
             because those would report it as `fixed` and show it read-only. -->
        <select
          v-if="presetOptions[prop.name]"
          class="controls__select text-mono-small"
          :value="presetChoices[prop.name] ?? presetOptions[prop.name][0]"
          @change="emitPatch('preset', { [prop.name]: $event.target.value })"
        >
          <option v-for="key in presetOptions[prop.name]" :key="key" :value="key">{{ key }}</option>
        </select>

        <select
          v-else-if="prop.kind === 'enum'"
          class="controls__select text-mono-small"
          :value="asSelectValue(args[prop.name])"
          @change="setEnum(prop, $event.target.value)"
        >
          <option v-for="option in optionsFor(prop.options)" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>

        <Toggle
          v-else-if="prop.kind === 'boolean'"
          :model-value="!!args[prop.name]"
          size="compact"
          @update:model-value="emitPatch('update', { [prop.name]: $event })"
        />

        <input
          v-else-if="prop.kind === 'number' || prop.kind === 'text'"
          class="controls__input text-mono-small"
          :type="prop.kind === 'number' ? 'number' : 'text'"
          :value="args[prop.name] == null ? '' : args[prop.name]"
          :placeholder="String(prop.default ?? '')"
          @input="setField(prop, $event.target.value)"
        >

        <code v-else class="controls__fixed text-mono-small">{{ preview(args[prop.name]) }}</code>

        <p v-if="notes[prop.name]" class="controls__note text-mono-small">{{ notes[prop.name] }}</p>
      </div>
    </section>

    <section v-if="slotNames.length" class="controls__section">
      <h3 class="controls__section-title text-mono-small">Slots</h3>
      <p class="controls__hint text-mono-small">
        Slot content is not a prop, so these are the choices the descriptor declares.
      </p>

      <div v-for="name in slotNames" :key="name" class="controls__row">
        <div class="controls__head">
          <span class="controls__name text-mono-small">{{ name }}</span>
        </div>
        <!-- One filling and nothing to pick from: a select with a single option
             is a control the reader cannot operate. It is still worth naming,
             because the slot is part of the component's surface. -->
        <code v-if="slotOptions[name].length < 2" class="controls__fixed text-mono-small">
          {{ slotOptions[name][0] }}
        </code>
        <select
          v-else
          class="controls__select text-mono-small"
          :value="asSelectValue(slotChoices[name] ?? slotOptions[name][0])"
          @change="emitPatch('slot', { [name]: $event.target.value })"
        >
          <option v-for="option in optionsFor(slotOptions[name])" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>

        <p v-if="notes[name]" class="controls__note text-mono-small">{{ notes[name] }}</p>
      </div>
    </section>

    <section v-if="stateNames.length" class="controls__section">
      <h3 class="controls__section-title text-mono-small">State</h3>
      <p class="controls__hint text-mono-small">
        Store fields this primitive reads. Written straight into the canvas's own stores.
      </p>

      <div v-for="name in stateNames" :key="name" class="controls__row">
        <div class="controls__head">
          <span class="controls__name text-mono-small">{{ name }}</span>
        </div>

        <select
          v-if="state[name].kind === 'enum'"
          class="controls__select text-mono-small"
          :value="asSelectValue(stateValues[name] ?? state[name].default)"
          @change="emitPatch('state', { [name]: $event.target.value })"
        >
          <option
            v-for="option in optionsFor(state[name].options)"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </select>
        <Toggle
          v-else-if="state[name].kind === 'boolean'"
          :model-value="!!(stateValues[name] ?? state[name].default)"
          size="compact"
          @update:model-value="emitPatch('state', { [name]: $event })"
        />
        <input
          v-else
          class="controls__input text-mono-small"
          type="number"
          :value="stateValues[name] ?? state[name].default"
          @input="emitPatch('state', { [name]: numberOrNull($event.target.value) })"
        >

        <p v-if="notes[name]" class="controls__note text-mono-small">{{ notes[name] }}</p>
      </div>
    </section>

    <section v-if="actionNames.length" class="controls__section">
      <h3 class="controls__section-title text-mono-small">Actions</h3>

      <div class="controls__actions">
        <button
          v-for="name in actionNames"
          :key="name"
          v-press
          type="button"
          class="controls__action text-mono-small"
          @click="$emit('action', name)"
        >
          {{ name }}
        </button>
      </div>
    </section>

    <section class="controls__section">
      <h3 class="controls__section-title text-mono-small">Events</h3>

      <div v-if="!events.length" class="controls__hint text-mono-small">
        Declares none — this primitive reports through callback props instead.
      </div>
      <div v-else class="controls__events">
        <span v-for="name in events" :key="name" class="controls__event text-mono-small">
          {{ name }}<template v-if="counts[name]"> ×{{ counts[name] }}</template>
        </span>
      </div>

      <ol v-if="log.length" class="controls__log">
        <li v-for="(item, index) in log" :key="index" class="text-mono-small">
          {{ item.name }}<template v-if="item.arg !== undefined"> → {{ preview(item.arg) }}</template>
        </li>
      </ol>
    </section>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import Toggle from '@/components/ui/Toggle.vue';

const props = defineProps({
  /** Control descriptors from `describeProps()`. */
  descriptors: {
    type: Array,
    default: () => []
  },
  /** Event names from `describeEvents()`. */
  events: {
    type: Array,
    default: () => []
  },
  /** Current prop values. */
  args: {
    type: Object,
    default: () => ({})
  },
  /** slot name -> array of choice keys the descriptor offers. */
  slotOptions: {
    type: Object,
    default: () => ({})
  },
  /** slot name -> chosen key. */
  slotChoices: {
    type: Object,
    default: () => ({})
  },
  /** prop name -> array of preset keys, for the props no widget can carry. */
  presetOptions: {
    type: Object,
    default: () => ({})
  },
  /** prop name -> chosen preset key. */
  presetChoices: {
    type: Object,
    default: () => ({})
  },
  /** The descriptor's `state` block. */
  state: {
    type: Object,
    default: () => ({})
  },
  /** state name -> current value. */
  stateValues: {
    type: Object,
    default: () => ({})
  },
  /** control name -> the one-line condition the descriptor states for it. */
  notes: {
    type: Object,
    default: () => ({})
  },
  /** Action names the descriptor declares. */
  actionNames: {
    type: Array,
    default: () => []
  },
  /** Newest-first list of `{ name, arg }` the canvas reported. */
  log: {
    type: Array,
    default: () => []
  }
});

const emit = defineEmits(['update', 'slot', 'preset', 'state', 'action']);

/** `null` has no place in a select, so it travels as this sentinel. */
const NONE = '—';

const slotNames = computed(() => Object.keys(props.slotOptions));
const stateNames = computed(() => Object.keys(props.state));

const counts = computed(() => {
  const tally = {};
  for (const item of props.log) tally[item.name] = (tally[item.name] || 0) + 1;
  return tally;
});

function optionsFor(values) {
  return (values || []).map(value => ({
    label: value === null || value === '' ? NONE : String(value),
    value: value === null || value === '' ? NONE : String(value)
  }));
}

function asSelectValue(value) {
  return value === null || value === undefined || value === '' ? NONE : String(value);
}

function emitPatch(channel, patch) {
  emit(channel, patch);
}

/**
 * Selects carry strings, but a prop may be typed for numbers (a pixel size), so
 * the original option is looked up rather than the string handed back.
 */
function setEnum(prop, raw) {
  if (raw === NONE) return emitPatch('update', { [prop.name]: null });
  const original = prop.options.find(option => String(option) === raw);
  emitPatch('update', { [prop.name]: original === undefined ? raw : original });
}

function numberOrNull(raw) {
  const value = raw === '' ? null : Number(raw);
  return Number.isNaN(value) ? null : value;
}

function setField(prop, raw) {
  emitPatch('update', { [prop.name]: prop.kind === 'number' ? numberOrNull(raw) : raw });
}

function preview(value) {
  if (value === undefined) return 'undefined';
  if (typeof value === 'function') return 'ƒ (stubbed → event log)';
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
</script>

<style scoped>
.controls {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.controls__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.controls__section-title {
  margin: 0;
  color: var(--color-text);
  text-transform: uppercase;
}

.controls__row {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  padding-bottom: var(--space-02);
  border-bottom: 1px solid var(--color-border);
}

.controls__head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-01);
}

.controls__name {
  color: var(--color-text);
}

.controls__required {
  padding: 0 var(--space-01);
  color: var(--color-warning);
  background: var(--color-warning-subtle);
  border-radius: var(--radius-01);
}

.controls__type,
.controls__hint {
  margin: 0;
  color: var(--color-text-light);
}

/* Reads as an annotation of the control above it, not as a second control. */
.controls__note {
  margin: 0;
  color: var(--color-text-light);
  border-left: 2px solid var(--color-border);
  padding-left: var(--space-02);
}

.controls__input {
  width: 100%;
  box-sizing: border-box;
  padding: var(--space-01) var(--space-02);
  color: var(--color-text);
  background: var(--color-background-strong);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-02);
}

.controls__input:focus,
.controls__select:focus {
  outline: 1px solid var(--color-brand);
}

.controls__select {
  width: 100%;
  box-sizing: border-box;
  padding: var(--space-01) var(--space-02);
  color: var(--color-text);
  background: var(--color-background-strong);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-02);
}

.controls__fixed {
  display: block;
  padding: var(--space-01) var(--space-02);
  color: var(--color-text-secondary);
  background: var(--color-background-strong);
  border-radius: var(--radius-02);
  overflow-wrap: anywhere;
}

.controls__actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-01);
}

.controls__action {
  padding: var(--space-01) var(--space-02);
  color: var(--color-text);
  background: var(--color-background-strong);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-02);
  cursor: pointer;
}

.controls__events {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-01);
}

.controls__event {
  padding: 0 var(--space-02);
  color: var(--color-text-secondary);
  background: var(--color-background-strong);
  border-radius: var(--radius-full);
}

.controls__log {
  display: flex;
  flex-direction: column;
  max-height: 160px;
  margin: 0;
  padding: var(--space-02);
  overflow-y: auto;
  list-style: none;
  color: var(--color-text-secondary);
  background: var(--color-background-strong);
  border-radius: var(--radius-02);
}
</style>
