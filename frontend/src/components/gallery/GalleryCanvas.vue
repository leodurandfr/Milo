<!-- frontend/src/components/gallery/GalleryCanvas.vue -->
<!--
  Hosts the canvas iframe and the viewport switcher.

  The iframe is what makes the switcher mean anything. Milō's breakpoints are
  `@media (max-aspect-ratio: 4/3)`, so the mobile branch depends on the ratio of
  a real viewport — a plain sized <div> would change the width and change
  nothing else, which looks like it works and does not. Resizing the iframe
  resizes an actual viewport, so the phone preset renders what a phone renders.
-->
<template>
  <div class="canvas-host">
    <div class="canvas-host__bar">
      <div class="canvas-host__switch">
        <button
          v-for="option in VIEWPORTS"
          :key="option.value"
          v-press
          type="button"
          class="canvas-host__preset text-mono-small"
          :class="{ 'canvas-host__preset--active': option.value === viewport }"
          @click="viewport = option.value"
        >
          {{ option.name }}
        </button>
      </div>
      <span class="canvas-host__size text-mono-small">
        {{ activeViewport.label }}<template v-if="scale < 1"> · {{ Math.round(scale * 100) }}%</template>
      </span>
    </div>

    <div class="canvas-host__stage">
      <div ref="fit" class="canvas-host__fit">
        <div class="canvas-host__device" :class="{ 'canvas-host__device--sized': isSized }" :style="deviceStyle">
          <iframe
            ref="frame"
            class="canvas-host__frame"
            :style="frameStyle"
            :src="CANVAS_SRC"
            title="Component canvas"
            @load="handleLoad"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';

const props = defineProps({
  /** Catalogue id of the primitive to render. */
  id: {
    type: String,
    default: ''
  },
  /** Current prop values, owned by the control panel. */
  args: {
    type: Object,
    default: () => ({})
  },
  /** slot name -> chosen key. Only the key crosses; the canvas resolves it. */
  slots: {
    type: Object,
    default: () => ({})
  },
  /** prop name -> chosen preset key, resolved canvas-side like the slots. */
  presets: {
    type: Object,
    default: () => ({})
  },
  /** state name -> value, applied by the descriptor's own writer in the canvas. */
  state: {
    type: Object,
    default: () => ({})
  }
});

const emit = defineEmits(['event', 'args']);

/**
 * `fill` stretches to the pane, which is the honest default on a desktop
 * browser. The other two are the two ratios the app actually ships against: the
 * kiosk panel sits above 4:3 and takes the wide branch, a phone sits below it
 * and takes the narrow one.
 */
/**
 * The canvas forces the virtual keyboard on.
 *
 * `useKeyboardAvailability()` gates it on `isKiosk()` — hostname === 'localhost',
 * i.e. the Pi's own panel — so from the computer this page is read on, both
 * `VirtualKeyboard` and `InputText`'s keyboard path would be unreachable. The
 * composable already documents `?virtualKeyboard=true` as the override for dev
 * and testing, and a component reference page is exactly that: what it should
 * show is what the unit does, not what a desktop happens to fall back to.
 */
const CANVAS_SRC = '/canvas.html?virtualKeyboard=true';

const VIEWPORTS = [
  { value: 'fill', name: 'Fill', label: 'fills the pane', width: null, height: null },
  { value: 'kiosk', name: 'Kiosk', label: '1280 × 800 — the unit', width: 1280, height: 800 },
  { value: 'phone', name: 'Phone', label: '390 × 844 — below 4:3', width: 390, height: 844 }
];

const viewport = ref('fill');
const frame = ref(null);
const fit = ref(null);
const frameReady = ref(false);
const available = ref({ width: 0, height: 0 });

const activeViewport = computed(() => VIEWPORTS.find(entry => entry.value === viewport.value));
const isSized = computed(() => !!activeViewport.value.width);

/**
 * Shrink-to-fit, never enlarge. A preset is a claim about a viewport's size, so
 * showing it larger than life would misreport it — but a 1280×800 frame does not
 * fit beside two panes on a laptop, and cropping it would misreport the layout.
 *
 * Scaling is a `transform`, deliberately: it changes what the frame *looks* like
 * without changing the viewport inside it, so the iframe still lays out at
 * 1280×800 and its aspect-ratio media queries still resolve against that.
 */
const scale = computed(() => {
  if (!isSized.value) return 1;

  const { width, height } = activeViewport.value;
  const { width: maxWidth, height: maxHeight } = available.value;
  if (!maxWidth || !maxHeight) return 1;

  return Math.min(1, maxWidth / width, maxHeight / height);
});

/** The box the scaled frame actually occupies, so centring has something to centre. */
const deviceStyle = computed(() => {
  if (!isSized.value) return { width: '100%', height: '100%' };

  const { width, height } = activeViewport.value;
  return { width: `${width * scale.value}px`, height: `${height * scale.value}px` };
});

const frameStyle = computed(() => {
  if (!isSized.value) return { width: '100%', height: '100%' };

  const { width, height } = activeViewport.value;
  return {
    width: `${width}px`,
    height: `${height}px`,
    transform: `scale(${scale.value})`,
    transformOrigin: 'top left'
  };
});

/**
 * postMessage structured-clones its payload, and a Vue reactive object is a
 * Proxy, which it refuses outright (`DataCloneError`). The args are JSON-shaped
 * by construction — primitives, and arrays of them — so a round-trip is both the
 * cheapest way to hand over plain data and a deep unwrap, which `toRaw()` alone
 * would not give for a nested options array.
 */
function plain(value) {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return {};
  }
}

function send() {
  if (!frameReady.value || !frame.value?.contentWindow) return;
  frame.value.contentWindow.postMessage(
    {
      source: 'milo-gallery',
      type: 'render',
      id: props.id,
      args: plain(props.args),
      slots: plain(props.slots),
      presets: plain(props.presets),
      state: plain(props.state)
    },
    window.location.origin
  );
}

/** Fires a declared action inside the canvas. Exposed for the page to call. */
function runAction(name) {
  if (!frameReady.value || !frame.value?.contentWindow) return;
  frame.value.contentWindow.postMessage(
    { source: 'milo-gallery', type: 'action', id: props.id, name },
    window.location.origin
  );
}

defineExpose({ runAction });

function handleLoad() {
  // The iframe announces itself with `ready`; this only covers a reload that
  // races ahead of the listener below.
  send();
}

function handleMessage(event) {
  if (event.origin !== window.location.origin) return;
  const data = event.data;
  if (data?.source !== 'milo-canvas') return;

  if (data.type === 'ready') {
    frameReady.value = true;
    send();
  } else if (data.type === 'event') {
    emit('event', { name: data.name, arg: data.arg });
  } else if (data.type === 'args') {
    emit('args', data.args);
  }
}

watch(() => [props.id, props.args, props.slots, props.presets, props.state], send, { deep: true });

let observer = null;

onMounted(() => {
  window.addEventListener('message', handleMessage);

  // `clientWidth/Height` rather than a rect: they are layout pixels, so the
  // kiosk's own `ui_scale` transform on an ancestor cannot skew the fit. The
  // observed element sits inside the stage's padding, which is why the padding
  // needs no constant here.
  observer = new ResizeObserver(([entry]) => {
    const box = entry.target;
    available.value = { width: box.clientWidth, height: box.clientHeight };
  });
  if (fit.value) observer.observe(fit.value);
});

onUnmounted(() => {
  window.removeEventListener('message', handleMessage);
  observer?.disconnect();
  observer = null;
});
</script>

<style scoped>
.canvas-host {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  min-height: 0;
  height: 100%;
}

.canvas-host__bar {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  flex-wrap: wrap;
}

.canvas-host__size {
  color: var(--color-text-light);
}

.canvas-host__switch {
  display: flex;
  gap: var(--space-01);
}

.canvas-host__preset {
  padding: var(--space-01) var(--space-02);
  color: var(--color-text-secondary);
  background: var(--color-background-neutral);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-02);
  cursor: pointer;
}

.canvas-host__preset--active {
  color: var(--color-text-contrast);
  background: var(--color-brand);
  border-color: var(--color-brand);
}

.canvas-host__stage {
  flex: 1;
  min-height: 320px;
  padding: var(--space-04);
  overflow: hidden;
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
}

/* The measured box. Its padding-free size is what a preset is scaled to fit. */
.canvas-host__fit {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
}

.canvas-host__device {
  position: relative;
  overflow: hidden;
}

/* A shadow only on a preset: at `fill` the frame *is* the stage, and lifting it
   off its own background would read as a border, not a device. */
.canvas-host__device--sized {
  border-radius: var(--radius-03);
  box-shadow: var(--shadow-raised-02);
}

.canvas-host__frame {
  display: block;
  border: 0;
  background: var(--color-background);
}
</style>
