<!-- LyricsContent.vue — renders synced (highlight + auto-scroll) or plain lyrics.
     Keyed on the active source by the parent so useSourceProgress re-instantiates
     if the source changes while the view is open. Owns no loader of its own: it
     reports readiness upward via update:ready, and LyricsView keeps a single
     loading screen covering both the LRCLIB lookup and this centring wait — one
     uninterrupted message instead of two swapping mid-wait. -->
<template>
  <div class="lyrics-content">
    <div ref="scrollRef" class="lyrics-scroll" :class="{ 'is-plain': !isSynced, 'is-ready': ready }"
      @scroll="handleScroll">
      <template v-if="isSynced">
        <!-- One uniform size; the three states differ only in opacity: the active
             line is fully lit, lines still to come are bright, past lines fade
             back. Opacity transitions per line so the highlight glides rather
             than snapping as the song advances. -->
        <p v-for="(line, i) in synced" :key="i" :ref="el => setLineRef(el, i)"
          class="lyrics-line display-1" :class="lineStateClass(i)">
          {{ line.line || '♪' }}
        </p>
      </template>
      <template v-else>
        <p v-for="(line, i) in plainLines" :key="i" class="lyrics-line display-1 is-plain-line">
          {{ line }}
        </p>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted, onUnmounted } from 'vue';
import { useLyricsStore } from '@/stores/lyricsStore';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { useTimer } from '@/composables/useTimer';

const props = defineProps({
  source: { type: String, required: true },
  synced: { type: Array, default: null },
  plain: { type: String, default: null }
});

const emit = defineEmits(['update:ready']);

const lyricsStore = useLyricsStore();
const timer = useTimer();
const { currentPosition, duration, isPositionInitialized } = useSourceProgress(props.source, { compensateStaleness: true });

// Sync when we have timestamped lines AND the source is a real player (a
// duration means it exposes a position clock; radio has neither → plain). We
// deliberately do NOT wait for the position to initialize — committing to the
// synced layout up-front avoids a plain→synced flip while the first periodic
// position tick arrives (position events are ~1-2 s apart, interpolated).
const isSynced = computed(() =>
  Array.isArray(props.synced) && props.synced.length > 0 && duration.value > 0
);

const plainLines = computed(() => (isSynced.value || !props.plain ? [] : props.plain.split('\n')));

// The playhead the lyrics are read against: the source's position corrected by
// the store's offset (0 in direct, minus the snapcast buffer in multiroom — see
// lyricsStore.syncOffsetMs). Both the highlight and the scroll read this one.
const syncedPosition = computed(() => currentPosition.value + lyricsStore.syncOffsetMs);

// Index of the last line whose timestamp has passed the current position.
const activeIndex = computed(() => {
  if (!isSynced.value || !isPositionInitialized.value) return -1;
  const pos = syncedPosition.value;
  const lines = props.synced;
  let idx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].t <= pos) idx = i;
    else break;
  }
  return idx;
});

// Per-line state → opacity only (size is uniform). Active line is fully lit,
// lines still to come stay bright, past lines fade back.
function lineStateClass(i) {
  if (i === activeIndex.value) return 'is-active';
  if (i > activeIndex.value) return 'is-upcoming';
  return 'is-past';
}

const scrollRef = ref(null);
const lineRefs = [];
function setLineRef(el, i) {
  if (el) lineRefs[i] = el;
}

const scrollKey = computed(() => `${lyricsStore.trackArtist}|||${lyricsStore.trackTitle}`);

// === Scrolling ===
//
// One move per line: the active line is brought to the centre when it becomes
// active, and the page rests until the next one. Interpolating continuously
// between lines was tried and reads as drift rather than as rhythm — the song
// is carried by the highlight, and the page should follow it, not anticipate it.
//
// What it does NOT use is scrollTo({behavior:'smooth'}): the browser's curve
// spends most of its budget in the first instants, which lands as a lurch and is
// not tunable. This is a cubic ease-in-out — it leaves slowly, crosses quickly,
// and settles slowly.
//
// Deliberately longer than the crossfade it accompanies (--transition-crossfade,
// 800ms): the words have finished handing over while the page is still coming to
// rest, which is what makes the movement felt rather than watched. They start
// together, which is what ties them; they need not end together.
const SCROLL_DURATION_MS = 1200;

// Matches --easeInOutCubic in the design system (cubic-bezier(0.65, 0, 0.35, 1)).
function easeInOutCubic(t) {
  return t < 0.5 ? 4 * t * t * t : 1 - ((-2 * t + 2) ** 3) / 2;
}

// Anchors are measured, so they cost a layout read. Measuring the whole track in
// one pass and keeping it until the lyrics or the viewport change means a line
// change costs arithmetic, not a reflow — this runs on the kiosk's Pi.
let anchors = null;

// Land on the line instead of gliding to it: true on the first placement and
// after every invalidation, since fresh anchors describe a layout the current
// scrollTop knows nothing about (a new track, a resize).
let needsSnap = true;

function invalidateAnchors() {
  anchors = null;
  needsSnap = true;
}

function measureAnchors() {
  const container = scrollRef.value;
  if (!container || !props.synced) return null;
  const half = container.clientHeight / 2;
  const measured = props.synced.map((_, i) => {
    const el = lineRefs[i];
    return el ? el.offsetTop - half + el.clientHeight / 2 : null;
  });
  // One missing ref means the lines aren't painted yet, and measuring now would
  // freeze half-laid-out numbers into the cache for the rest of the track.
  return measured.some(a => a === null) ? null : measured;
}

// Gates the parent's loading screen only, and never goes back to false: a track
// change does NOT remount this component (the parent keys it on the source), so
// lowering it between tracks would flash the loader back in.
const hasCenteredOnce = ref(false);

let rafId = null;
let tween = null;

function stopTween() {
  if (rafId !== null) cancelAnimationFrame(rafId);
  rafId = null;
  tween = null;
}

function stepTween(now) {
  const container = scrollRef.value;
  if (!container || !tween) {
    rafId = null;
    return;
  }
  const progress = Math.min(1, (now - tween.startedAt) / SCROLL_DURATION_MS);
  container.scrollTop = tween.from + (tween.to - tween.from) * easeInOutCubic(progress);
  rafId = progress < 1 ? requestAnimationFrame(stepTween) : null;
  if (rafId === null) tween = null;
}

// Brings the active line to the centre. The lines can still be unpainted on the
// first call (the refs land a frame later), and a watch only re-fires on the
// next value CHANGE — so a paused or slow track would sit behind the loading
// state forever without asking again across a few frames.
function scrollToActiveLine(framesLeft = 10) {
  const container = scrollRef.value;
  const i = activeIndex.value;
  if (!container || i < 0) return;

  if (!anchors) anchors = measureAnchors();
  if (!anchors) {
    if (framesLeft > 0) requestAnimationFrame(() => scrollToActiveLine(framesLeft - 1));
    return;
  }

  const max = Math.max(0, container.scrollHeight - container.clientHeight);
  const to = Math.min(Math.max(anchors[i], 0), max);
  hasCenteredOnce.value = true;

  if (needsSnap) {
    stopTween();
    container.scrollTop = to;
    needsSnap = false;
    return;
  }

  // From wherever the page currently is, not from the previous line's anchor: a
  // line landing mid-glide continues the trip instead of restarting it, and a
  // hand-scrolled page is recovered from where the hand left it.
  tween = { from: container.scrollTop, to, startedAt: performance.now() };
  if (rafId === null) rafId = requestAnimationFrame(stepTween);
}

watch(activeIndex, () => scrollToActiveLine(), { immediate: true, flush: 'post' });

function relayout() {
  invalidateAnchors();
  scrollToActiveLine();
}

onMounted(() => window.addEventListener('resize', relayout));
onUnmounted(() => {
  stopTween();
  window.removeEventListener('resize', relayout);
});

// Two relayouts per track, deliberately: the store renames the track as soon as
// the lookup starts, while `synced` only lands when it answers. The first drops
// anchors measured on the previous lyrics; the second measures the new ones —
// and placing from here, not only from the activeIndex watch, is what covers a
// new track whose first line happens to carry the same index as the old one.
watch([scrollKey, () => props.synced], relayout, { flush: 'post' });

// Belt-and-suspenders: never block the view forever on the loading state —
// reveal it as-is if centering still hasn't landed after a generous wait
// (e.g. activeIndex never resolves for this source/track).
timer.setTimeout(() => { hasCenteredOnce.value = true; }, 1500);

const ready = computed(() => !isSynced.value || hasCenteredOnce.value);

// Drives the parent's single loading screen. Immediate so a remount reports its
// state right away rather than leaving the loader stuck on a stale `true`.
watch(ready, (value) => emit('update:ready', value), { immediate: true });

function restoreScroll() {
  if (isSynced.value || !scrollRef.value) return;
  scrollRef.value.scrollTop = lyricsStore.getScrollPosition(scrollKey.value);
}

function handleScroll() {
  if (isSynced.value || !scrollRef.value) return;
  lyricsStore.saveScrollPosition(scrollKey.value, scrollRef.value.scrollTop);
}

onMounted(restoreScroll);
watch(scrollKey, restoreScroll, { flush: 'post' });
</script>

<style scoped>
.lyrics-content {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.lyrics-scroll {
  /* position:relative so the lines' offsetTop is measured against this
     container — the anchor math in measureAnchors() depends on it. Fills the
     full-screen body; scrolls internally. */
  position: relative;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  /* Hidden and offset down until centering lands, then rises into place. This is
     what the reader actually sees arrive: the component mounts under LyricsView's
     loading layer, so without its own rise the lyrics would merely fade in once
     that layer goes. Same cadence as .lyrics-fade-enter-active there, delay
     included — 200ms is the loader's leave duration, so the rise starts only once
     it has fully gone and the two never overlap. Plain lyrics are ready on the
     first render, so the class is there from the start. */
  opacity: 0;
  transform: translateY(var(--space-06));
  transition: opacity var(--transition-normal), transform var(--transition-normal);
  transition-delay: 200ms;
  padding-inline: var(--space-06);
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
  text-align: center;
  /* Room above/below so the first and last lines can reach the vertical center
     (vh, not %, since % padding resolves against width — huge on a wide screen). */
  padding-block: 42vh;
  /* Hide the scrollbar — auto-scroll drives this, it isn't hand-scrolled. */
  scrollbar-width: none;
  /* Soft fade over the bottom portion so lines dissolve as they scroll off,
     rather than hitting a hard edge (keywords, not hex → stylelint-safe). */
  -webkit-mask-image: linear-gradient(to bottom, black 0%, black 55%, transparent 100%);
  mask-image: linear-gradient(to bottom, black 0%, black 55%, transparent 100%);
}

.lyrics-scroll.is-ready {
  opacity: 1;
  transform: translateY(0);
}

.lyrics-scroll::-webkit-scrollbar {
  display: none;
}

.lyrics-scroll.is-plain {
  -webkit-mask-image: linear-gradient(to bottom, transparent 0%, black 45%, black 55%, transparent 100%);
  mask-image: linear-gradient(to bottom, transparent 0%, black 45%, black 55%, transparent 100%);
}

/* Light-on-dark over the blurred artwork backdrop; state modulates brightness
   through opacity only (color stays contrast-white so stylelint's
   no-color-literal rule holds).
   The dissolve starts with the glide to the next line and finishes before it
   (SCROLL_DURATION_MS is longer on purpose), so the handover is one gesture that
   settles rather than a fade followed by a move. */
.lyrics-line {
  color: var(--color-text-contrast);
  transition: opacity var(--transition-crossfade);
}

.lyrics-line.is-active {
  opacity: 1;
}

.lyrics-line.is-upcoming {
  opacity: 0.45;
}

.lyrics-line.is-past {
  opacity: 0.1;
  /* The line leaving has nearly twice the distance of the line arriving
     (1 → 0.1 against 0.45 → 1), so on the shared duration it would drop at twice
     the speed — an exit that snaps while the entrance strolls. Stretched to the
     same brightness-per-second: 0.9 / (0.55 / 800ms). Re-derive it if either
     opacity above moves, or the pair stops reading as one dissolve. */
  transition-duration: 1300ms;
}

.lyrics-line.is-plain-line {
  opacity: 0.9;
}
</style>
