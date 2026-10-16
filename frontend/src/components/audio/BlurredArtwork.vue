<!-- BlurredArtwork.vue — an artwork blurred once, then drawn as a plain picture.
     A CSS `filter: blur()` is recomputed from the image whenever its layer is
     redrawn — each step of the Lyrics backdrop's drift (on the Pi, the Lyrics
     view at 61 % of the GPU with the filter, 50 % with this, before the drift
     was stepped). Here the cover is drawn once, with the same filter, into a
     canvas only as fine as the blur leaves detail for, and the canvas is
     stretched to the box: the compositor then moves a texture. The cover fills
     the box like `object-fit: cover`, and fades toward its edges as the CSS
     filter does. Drawn again on a new image, a new filter, or a box whose shape
     changed; a box that only grew keeps its picture, stretched.
     A browser without canvas filters (WebKit: Safari, every iOS browser) gets
     the CSS filter on the canvas instead — the old cost, the same look. -->

<template>
  <canvas ref="canvasRef" class="blurred-artwork" :style="cssFilter" aria-hidden="true" />
</template>

<script>
// Whether a 2D canvas applies `ctx.filter`. WebKit exposes no such property, and
// a browser that ignored the value would draw the cover sharp: both are caught
// by reading the value back.
const CANVAS_FILTER = (() => {
  const ctx = document.createElement('canvas').getContext('2d');
  if (!ctx || !('filter' in ctx)) return false;
  ctx.filter = 'blur(1px)';
  return ctx.filter === 'blur(1px)';
})();
</script>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';

const props = defineProps({
  src: {
    type: String,
    default: ''
  },
  // The blur radius, as a design token.
  blur: {
    type: String,
    default: '--blur-04',
    validator: (v) => ['--blur-01', '--blur-02', '--blur-03', '--blur-04', '--blur-05'].includes(v)
  },
  saturate: {
    type: Number,
    default: 1
  },
  brightness: {
    type: Number,
    default: 1
  }
});

// The blur in canvas pixels. Past a few pixels of blur no detail is left
// finer than this, so a canvas drawn at that scale and stretched back to the
// box loses nothing to the eye.
const CANVAS_BLUR_PX = 6;
// Without canvas filters the CSS filter blurs the canvas, so it only needs to
// be fine enough for that blur to smooth over: a quarter of the box.
const FALLBACK_SCALE = 0.25;
// A box whose aspect moved by less than this keeps its picture, stretched:
// a width transition must not redraw it every frame.
const ASPECT_TOLERANCE = 0.05;

const canvasRef = ref(null);
let box = null;
let drawnAspect = null;
let image = null;
let loadToken = 0;
let resizeObserver = null;

const cssFilter = computed(() => (CANVAS_FILTER ? null : {
  filter: `blur(var(${props.blur})) saturate(${props.saturate}) brightness(${props.brightness})`
}));

function draw() {
  const canvas = canvasRef.value;
  if (!canvas || !box || !box.width || !box.height) return;
  const ctx = canvas.getContext('2d');
  if (!image) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawnAspect = null;
    return;
  }
  let scale = FALLBACK_SCALE;
  if (CANVAS_FILTER) {
    // An unresolved token draws nothing rather than a canvas as large as the
    // box at a blur too small to see.
    const blurPx = parseFloat(getComputedStyle(canvas).getPropertyValue(props.blur));
    if (!(blurPx > 0)) return;
    scale = Math.min(1, CANVAS_BLUR_PX / blurPx);
  }
  canvas.width = Math.max(8, Math.round(box.width * scale));
  canvas.height = Math.max(8, Math.round(box.height * scale));
  if (CANVAS_FILTER) {
    ctx.filter = `blur(${CANVAS_BLUR_PX}px) saturate(${props.saturate}) brightness(${props.brightness})`;
  }
  const fit = Math.max(canvas.width / image.naturalWidth, canvas.height / image.naturalHeight);
  const w = image.naturalWidth * fit;
  const h = image.naturalHeight * fit;
  ctx.drawImage(image, (canvas.width - w) / 2, (canvas.height - h) / 2, w, h);
  drawnAspect = box.width / box.height;
}

async function load() {
  const token = ++loadToken;
  if (!props.src) {
    image = null;
    draw();
    return;
  }
  const next = new Image();
  next.src = props.src;
  try {
    await next.decode();
  } catch {
    // An image that cannot be read draws nothing, as a broken <img> would.
    if (token === loadToken) {
      image = null;
      draw();
    }
    return;
  }
  if (token !== loadToken) return;
  image = next;
  draw();
}

watch(() => props.src, load);
watch(() => [props.blur, props.saturate, props.brightness], draw);

onMounted(() => {
  resizeObserver = new ResizeObserver(([entry]) => {
    box = entry.contentRect;
    const aspect = box.width / box.height;
    if (drawnAspect === null || Math.abs(aspect / drawnAspect - 1) > ASPECT_TOLERANCE) draw();
  });
  resizeObserver.observe(canvasRef.value);
  load();
});

onBeforeUnmount(() => {
  loadToken++;
  resizeObserver?.disconnect();
});
</script>

<style scoped>
.blurred-artwork {
  display: block;
  width: 100%;
  height: 100%;
}
</style>
