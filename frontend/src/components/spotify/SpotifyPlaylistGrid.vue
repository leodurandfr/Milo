<template>
  <div class="playlist-grid">
    <!-- The rows shown, then the next one fading out under the button: there
         is more below. The clip's height is measured, so a reveal can grow
         into it rather than jump. -->
    <div ref="clipRef" class="clip" @transitionend.self="onTransitionEnd">
      <div ref="gridRef" class="cards-grid">
        <SpotifyCard v-for="playlist in rendered" :key="playlist.uri" :item="playlist"
          @click="$emit('select', playlist)" />
      </div>
    </div>
    <div v-if="hasMore" class="show-more">
      <Button variant="control" size="medium" @click="showMore">{{ t('spotify.showMore') }}</Button>
    </div>
  </div>
</template>

<script setup>
import { computed, inject, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useCardGridColumns } from '@/composables/useCardGridColumns';
import { NAVIGATION_ENTRY_STATE } from '@/composables/useNavigationStack';
import Button from '@/components/ui/Button.vue';
import SpotifyCard from './cards/SpotifyCard.vue';

const props = defineProps({
  // Library playlists, as /api/spotify/home lists them.
  items: {
    type: Array,
    required: true,
  },
  // Names this grid's row count in the navigation entry, for a view with two.
  stateKey: {
    type: String,
    required: true,
  },
});

defineEmits(['select']);

const FIRST_ROWS = 2;
const MORE_ROWS = 5;
// How much of the next row's cover shows under the fade: all of it.
const PEEK_SHARE = 1;
// The fade starts this far above that row, in the gap: started on the covers'
// top edge, a subpixel of it showed sharp above the step.
const FADE_LEAD_PX = 4;

const { t } = useI18n();
const { columns } = useCardGridColumns();

// Kept in the navigation entry: going back to the home remounts it and
// restores its scroll, which must find the rows it was scrolled to.
const state = inject(NAVIGATION_ENTRY_STATE, null)?.() ?? reactive({});
const rows = computed(() => state[props.stateKey] ?? FIRST_ROWS);

const hasMore = computed(() => props.items.length > rows.value * columns.value);
// The rows shown and, while there is more, the row that peeks under the fade.
const rendered = computed(() =>
  (hasMore.value ? props.items.slice(0, (rows.value + 1) * columns.value) : props.items)
);

const clipRef = ref(null);
const gridRef = ref(null);

// The clip's height: down to the peeking row's cover, or the whole grid. The
// fade runs over the whole of what shows of that row.
function measure() {
  const grid = gridRef.value;
  if (!hasMore.value) return { height: grid.offsetHeight, fade: 0 };
  const peek = grid.children[rows.value * columns.value];
  const shown = peek.offsetWidth * PEEK_SHARE;
  return { height: peek.offsetTop + shown, fade: shown + FADE_LEAD_PX };
}

function apply({ animate }) {
  const clip = clipRef.value;
  if (!clip || !gridRef.value) return;
  const { height, fade } = measure();
  if (!animate) clip.style.transition = 'none';
  clip.style.height = `${height}px`;
  clip.style.setProperty('--grid-fade', `${fade}px`);
  // Set at once, the clip is at rest: a reveal it cut short ends no
  // transition, and would leave the fade without its step.
  if (!animate) clip.style.setProperty('--grid-open', '0');
  if (!animate) {
    void clip.offsetHeight;
    clip.style.transition = '';
  }
}

// The reveal over: wholly shown, the clip lets the grid size itself (a cover
// that loads late, a column count that changes); with more still to show, the
// row now peeking recedes again.
function onTransitionEnd(event) {
  if (event.propertyName !== 'height') return;
  if (hasMore.value) clipRef.value.style.setProperty('--grid-open', '0');
  else clipRef.value.style.height = '';
}

async function showMore() {
  const clip = clipRef.value;
  // From the height it has now, which a released clip no longer states.
  clip.style.height = `${clip.offsetHeight}px`;
  // The fade's step sits in the gap between rows only at rest: while the
  // clip grows it would cross the covers as a line, so it is smoothed away.
  clip.style.setProperty('--grid-open', '1');
  state[props.stateKey] = rows.value + MORE_ROWS;
  await nextTick();
  apply({ animate: true });
}

// A new width moves the rows: measured again at once. Only the width — the
// grid also grows when a reveal adds rows, and answering that at once would
// cut the reveal's animation short.
let resizes = null;
let width = 0;
onMounted(() => {
  apply({ animate: false });
  if (!hasMore.value) clipRef.value.style.height = '';
  resizes = new ResizeObserver(([entry]) => {
    if (entry.contentRect.width === width) return;
    width = entry.contentRect.width;
    if (hasMore.value) apply({ animate: false });
  });
  resizes.observe(gridRef.value);
});
onBeforeUnmount(() => resizes?.disconnect());

watch([() => props.items, columns], async () => {
  await nextTick();
  apply({ animate: false });
  if (!hasMore.value) clipRef.value.style.height = '';
});
</script>

<style scoped>
/* The fade's length, registered so it can shrink away on the reveal of the
   last rows instead of snapping off. */
@property --grid-fade {
  syntax: '<length>';
  inherits: false;
  initial-value: 0px;
}

/* 0 at rest, 1 while a reveal runs: registered so the fade's step eases away
   and back rather than switching. */
@property --grid-open {
  syntax: '<number>';
  inherits: false;
  initial-value: 0;
}

.playlist-grid {
  position: relative;
  display: flex;
  flex-direction: column;
}

/* The fade spans what shows of the next row and never reaches the row above.
   At rest that row starts already faded, at 35%: the step falls in the gap
   between the rows, where there is nothing to draw an edge on. From there it
   is gone by 80% of the cover, so the row reads as receding rather than as a
   cover whose bottom happens to be lighter. While a reveal runs
   (--grid-open: 1) the same curve starts from 100%, with no step to cross
   the covers as the clip grows. */
.clip {
  --grid-mask: linear-gradient(to bottom,
    black calc(100% - var(--grid-fade)),
    color-mix(in srgb, black calc(35% + 65% * var(--grid-open)), transparent) calc(100% - var(--grid-fade)),
    color-mix(in srgb, black calc(29% + 53% * var(--grid-open)), transparent) calc(100% - var(--grid-fade) * 0.9),
    color-mix(in srgb, black calc(23% + 42% * var(--grid-open)), transparent) calc(100% - var(--grid-fade) * 0.8),
    color-mix(in srgb, black calc(17% + 32% * var(--grid-open)), transparent) calc(100% - var(--grid-fade) * 0.7),
    color-mix(in srgb, black calc(12% + 23% * var(--grid-open)), transparent) calc(100% - var(--grid-fade) * 0.6),
    color-mix(in srgb, black calc(8% + 15% * var(--grid-open)), transparent) calc(100% - var(--grid-fade) * 0.5),
    color-mix(in srgb, black calc(4% + 9% * var(--grid-open)), transparent) calc(100% - var(--grid-fade) * 0.4),
    color-mix(in srgb, black calc(2% + 2% * var(--grid-open)), transparent) calc(100% - var(--grid-fade) * 0.3),
    transparent calc(100% - var(--grid-fade) * 0.2));
  overflow: hidden;
  -webkit-mask-image: var(--grid-mask);
  mask-image: var(--grid-mask);
  transition:
    height 0.45s var(--easeInOutCubic),
    --grid-fade 0.45s var(--easeInOutCubic),
    --grid-open var(--transition-medium);
}

.cards-grid {
  display: grid;
  grid-template-columns: repeat(var(--card-grid-columns), minmax(0, 1fr));
  row-gap: var(--space-05);
  column-gap: var(--space-03);
}

/* Low in the fade, where the row that peeks is all but masked. */
.show-more {
  position: absolute;
  inset-inline: 0;
  bottom: var(--space-01);
  display: flex;
  justify-content: center;
  pointer-events: none;
}

.show-more > * {
  pointer-events: auto;
}
</style>
