<!-- LyricsContent.vue — renders synced (highlight + auto-scroll) or plain lyrics.
     Keyed on the active source by the parent so useSourceProgress re-instantiates
     if the source changes while the view is open. -->
<template>
  <div class="lyrics-content">
    <div ref="scrollRef" class="lyrics-scroll" :class="{ 'is-plain': !isSynced }"
      :style="{ opacity: ready ? 1 : 0 }" @scroll="handleScroll">
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
    <div v-if="!ready" class="lyrics-content-loading">
      <LyricsLoadingState :track-title="lyricsStore.trackTitle" :track-artist="lyricsStore.trackArtist"
        label-key="lyrics.preparing" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useLyricsStore } from '@/stores/lyricsStore';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { useTimer } from '@/composables/useTimer';
import LyricsLoadingState from './LyricsLoadingState.vue';

// Multiroom (Snapcast) inserts a playback buffer between the source position and
// the audio the listener actually hears, so synced lyrics can feel off. Apply a
// fixed, empirically-tuned offset to the highlight while multiroom is active;
// direct mode has no buffer, so no offset. Sign convention: positive advances the
// highlight (lyrics run ahead of the raw position); negative would delay it.
const MULTIROOM_LEAD_MS = 500;

const props = defineProps({
  source: { type: String, required: true },
  synced: { type: Array, default: null },
  plain: { type: String, default: null }
});

const unifiedStore = useUnifiedAudioStore();
const lyricsStore = useLyricsStore();
const timer = useTimer();
const { currentPosition, duration, isPositionInitialized } = useSourceProgress(props.source, { compensateStaleness: true });

const leadMs = computed(() =>
  unifiedStore.systemState.multiroom_enabled ? MULTIROOM_LEAD_MS : 0
);

// Sync when we have timestamped lines AND the source is a real player (a
// duration means it exposes a position clock; radio has neither → plain). We
// deliberately do NOT wait for the position to initialize — committing to the
// synced layout up-front avoids a plain→synced flip while the first periodic
// position tick arrives (position events are ~1-2 s apart, interpolated).
const isSynced = computed(() =>
  Array.isArray(props.synced) && props.synced.length > 0 && duration.value > 0
);

const plainLines = computed(() => (isSynced.value || !props.plain ? [] : props.plain.split('\n')));

// Index of the last line whose timestamp has passed the current position.
const activeIndex = computed(() => {
  if (!isSynced.value || !isPositionInitialized.value) return -1;
  const pos = currentPosition.value + leadMs.value;
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

function centerLine(i, behavior) {
  const el = lineRefs[i];
  const container = scrollRef.value;
  if (i < 0 || !el || !container) return false;
  const top = el.offsetTop - container.clientHeight / 2 + el.clientHeight / 2;
  container.scrollTo({ top, behavior });
  return true;
}

const scrollKey = computed(() => `${lyricsStore.trackArtist}|||${lyricsStore.trackTitle}`);

// Keep the active line centered: snap instantly to the current line on the
// first known position (opening mid-song), then follow smoothly as it advances.
// The very first attempt can land a frame before the just-mounted line refs are
// painted (centerLine returns false); watch() only re-fires on the next value
// CHANGE, so without a retry a paused/slow-changing track would leave the view
// stuck behind the loading state indefinitely. Retry across a few frames to
// catch up almost instantly in that case.
const hasCenteredOnce = ref(false);
// Which track the current centering belongs to. A track change does NOT remount
// this component (the parent keys it on the source), so comparing keys is what
// makes the next center snap like a fresh open instead of smoothly animating all
// the way up from the previous track's scroll position. Deliberately separate
// from hasCenteredOnce, which stays true so the loading overlay doesn't flash
// back in between tracks.
const centeredKey = ref(null);
function attemptCenter(i, behavior, framesLeft = 10) {
  if (centerLine(i, behavior)) {
    hasCenteredOnce.value = true;
    centeredKey.value = scrollKey.value;
  } else if (framesLeft > 0) {
    requestAnimationFrame(() => attemptCenter(i, behavior, framesLeft - 1));
  }
}
watch(activeIndex, (i) => {
  if (i < 0) return;
  attemptCenter(i, centeredKey.value === scrollKey.value ? 'smooth' : 'auto');
}, { immediate: true, flush: 'post' });

// Belt-and-suspenders: never block the view forever on the loading state —
// reveal it as-is if centering still hasn't landed after a generous wait
// (e.g. activeIndex never resolves for this source/track).
timer.setTimeout(() => { hasCenteredOnce.value = true; }, 1500);

const ready = computed(() => !isSynced.value || hasCenteredOnce.value);

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

.lyrics-content-loading {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-contrast);
}

.lyrics-scroll {
  /* position:relative so the lines' offsetTop is measured against this
     container — centerLine()'s scroll math depends on it. Fills the
     full-screen body; scrolls internally. */
  position: relative;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  transition: opacity var(--transition-in-out);
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

.lyrics-scroll::-webkit-scrollbar {
  display: none;
}

.lyrics-scroll.is-plain {
  -webkit-mask-image: linear-gradient(to bottom, transparent 0%, black 45%, black 55%, transparent 100%);
  mask-image: linear-gradient(to bottom, transparent 0%, black 45%, black 55%, transparent 100%);
}

/* Light-on-dark over the blurred artwork backdrop; state modulates brightness
   through opacity only (color stays contrast-white so stylelint's
   no-color-literal rule holds). Opacity eases so the highlight glides. */
.lyrics-line {
  color: var(--color-text-contrast);
  transition: opacity var(--transition-in-out);
}

.lyrics-line.is-active {
  opacity: 1;
}

.lyrics-line.is-upcoming {
  opacity: 0.45;
}

.lyrics-line.is-past {
  opacity: 0.1;
}

.lyrics-line.is-plain-line {
  opacity: 0.9;
}
</style>
