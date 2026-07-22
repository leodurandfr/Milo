<!-- LyricsContent.vue — renders synced (highlight + auto-scroll) or plain lyrics.
     Keyed on the active source by the parent so useSourceProgress re-instantiates
     if the source changes while the modal is open. -->
<template>
  <div ref="scrollRef" class="lyrics-scroll" :class="{ 'is-synced': isSynced }">
    <template v-if="isSynced">
      <p v-for="(line, i) in synced" :key="i" :ref="el => setLineRef(el, i)"
        class="lyrics-line heading-2"
        :class="{ 'is-active': i === activeIndex, 'is-past': i < activeIndex }">
        {{ line.line || '♪' }}
      </p>
    </template>
    <div v-else class="lyrics-plain text-body">{{ plain }}</div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSourceProgress } from '@/composables/useSourceProgress';

// Multiroom (Snapcast) inserts a playback buffer between the source position and
// the audio the listener actually hears, so synced lyrics can feel off. Apply a
// fixed, empirically-tuned offset to the highlight while multiroom is active;
// direct mode has no buffer, so no offset. Sign convention: positive advances the
// highlight (lyrics run ahead of the raw position); negative would delay it.
const MULTIROOM_LEAD_MS = 300;

const props = defineProps({
  source: { type: String, required: true },
  synced: { type: Array, default: null },
  plain: { type: String, default: null }
});

const unifiedStore = useUnifiedAudioStore();
const { currentPosition, duration } = useSourceProgress(props.source, { compensateStaleness: true });

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

// Index of the last line whose timestamp has passed the current position.
const activeIndex = computed(() => {
  if (!isSynced.value) return -1;
  const pos = currentPosition.value + leadMs.value;
  const lines = props.synced;
  let idx = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].t <= pos) idx = i;
    else break;
  }
  return idx;
});

const scrollRef = ref(null);
const lineRefs = [];
function setLineRef(el, i) {
  if (el) lineRefs[i] = el;
}

function centerLine(i, behavior) {
  const el = lineRefs[i];
  const container = scrollRef.value;
  if (i < 0 || !el || !container) return;
  const top = el.offsetTop - container.clientHeight / 2 + el.clientHeight / 2;
  container.scrollTo({ top, behavior });
}

// Keep the active line centered: snap instantly to the current line on the
// first known position (opening mid-song), then follow smoothly as it advances.
// flush:'post' guarantees the line refs exist; immediate covers the case where
// the position is already known at mount so it's centered from the first frame.
let hasCentered = false;
watch(activeIndex, (i) => {
  if (i < 0) return;
  centerLine(i, hasCentered ? 'smooth' : 'auto');
  hasCentered = true;
}, { immediate: true, flush: 'post' });
</script>

<style scoped>
.lyrics-scroll {
  /* position:relative so the lines' offsetTop is measured against this
     container — centerLine()'s scroll math depends on it. */
  position: relative;
  height: min(60vh, 34rem);
  overflow-y: auto;
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

/* Room above/below so the first and last lines can reach the vertical center. */
.lyrics-scroll.is-synced {
  padding-block: 38%;
  text-align: center;
}

.lyrics-line {
  color: var(--color-text-light);
  transition: color var(--transition-fast), opacity var(--transition-fast);
}

.lyrics-line.is-active {
  color: var(--color-text);
}

.lyrics-line.is-past {
  opacity: 0.55;
}

.lyrics-plain {
  color: var(--color-text);
  white-space: pre-wrap;
}
</style>
