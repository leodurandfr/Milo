<template>
  <div class="show-more-clip">
    <!-- What shows, then the next item fading out under the button: there is
         more below. The clip's height is measured, so a reveal can grow into it
         rather than jump. The slot's root is the list; its children are the
         items, the one at `peekIndex` peeking. -->
    <div ref="clipRef" class="clip" @transitionend.self="onTransitionEnd">
      <slot />
    </div>
    <div v-if="hasMore" class="show-more">
      <Button variant="control" size="medium" @click="showMore">{{ t('spotify.showMore') }}</Button>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import Button from '@/components/ui/Button.vue';

const props = defineProps({
  // The index of the item that peeks under the fade: how many show above it.
  peekIndex: {
    type: Number,
    required: true,
  },
  // Whether there is more to show: the peeking item is rendered only then.
  hasMore: {
    type: Boolean,
    required: true,
  },
  // How much of the peeking item shows: all of it by default; a card shows its
  // cover, not the title under it.
  peekExtent: {
    type: Function,
    default: (item) => item.offsetHeight,
  },
  // What the list is drawn from, and in how many columns: a change re-measures
  // the clip at once. The press's own reveal is animated instead.
  items: {
    type: Array,
    default: null,
  },
  columns: {
    type: Number,
    default: 1,
  },
});

// Asks for more: the parent shows it, and the clip grows into it.
const emit = defineEmits(['more']);

// The fade starts this far above the peeking item, in the gap: started on its
// top edge, a subpixel of it showed sharp above the step.
const FADE_LEAD_PX = 4;

const { t } = useI18n();
const clipRef = ref(null);
const list = () => clipRef.value?.firstElementChild ?? null;

// The clip's height: down to what shows of the peeking item, or the whole
// list. The fade runs over the whole of what shows of that item.
function measure() {
  const el = list();
  if (!props.hasMore) return { height: el.offsetHeight, fade: 0 };
  const peek = el.children[props.peekIndex];
  if (!peek) return { height: el.offsetHeight, fade: 0 };
  const shown = props.peekExtent(peek);
  return { height: peek.offsetTop + shown, fade: shown + FADE_LEAD_PX };
}

// Out of the document (a page its KeepAlive kept), nothing measures: owed,
// and paid once it is back.
let owed = false;

function apply({ animate }) {
  const clip = clipRef.value;
  if (!clip || !list()) return;
  if (!clip.isConnected) {
    owed = true;
    return;
  }
  const { height, fade } = measure();
  if (!animate) clip.style.transition = 'none';
  clip.style.height = `${height}px`;
  clip.style.setProperty('--clip-fade', `${fade}px`);
  // Set at once, the clip is at rest: a reveal it cut short ends no
  // transition, and would leave the fade without its step.
  if (!animate) clip.style.setProperty('--clip-open', '0');
  if (!animate) {
    void clip.offsetHeight;
    clip.style.transition = '';
  }
}

// The reveal over: wholly shown, the clip lets the list size itself (a cover
// that loads late, a column count that changes); with more still to show, the
// item now peeking recedes again.
function onTransitionEnd(event) {
  if (event.propertyName !== 'height') return;
  if (props.hasMore) clipRef.value.style.setProperty('--clip-open', '0');
  else clipRef.value.style.height = '';
}

async function showMore() {
  const clip = clipRef.value;
  // From the height it has now, which a released clip no longer states.
  clip.style.height = `${clip.offsetHeight}px`;
  // The fade's step sits in the gap between items only at rest: while the
  // clip grows it would cross them as a line, so it is smoothed away.
  clip.style.setProperty('--clip-open', '1');
  emit('more');
  await nextTick();
  apply({ animate: true });
}

function settle() {
  apply({ animate: false });
  if (!props.hasMore && clipRef.value) clipRef.value.style.height = '';
}

// A new width moves the items: measured again at once. Only the width — the
// list also grows when a reveal adds items, and answering that at once would
// cut the reveal's animation short.
let resizes = null;
let width = 0;
onMounted(() => {
  settle();
  resizes = new ResizeObserver(([entry]) => {
    const now = entry.contentRect.width;
    // Zero: taken out of the document, which moved nothing.
    if (!now || (now === width && !owed)) return;
    width = now;
    if (owed) {
      owed = false;
      settle();
    } else if (props.hasMore) {
      apply({ animate: false });
    }
  });
  resizes.observe(list());
});
onBeforeUnmount(() => resizes?.disconnect());

watch([() => props.items, () => props.columns], async () => {
  await nextTick();
  settle();
});
</script>

<style scoped>
/* The fade's length, registered so it can shrink away on the reveal of the
   last items instead of snapping off. */
@property --clip-fade {
  syntax: '<length>';
  inherits: false;
  initial-value: 0px;
}

/* 0 at rest, 1 while a reveal runs: registered so the fade's step eases away
   and back rather than switching. */
@property --clip-open {
  syntax: '<number>';
  inherits: false;
  initial-value: 0;
}

.show-more-clip {
  position: relative;
  display: flex;
  flex-direction: column;
}

/* The fade spans what shows of the next item and never reaches the one above.
   At rest that item starts already faded, at 35%: the step falls in the gap
   between them, where there is nothing to draw an edge on. From there it is
   gone by 80% of the item, so it reads as receding rather than as an item
   whose bottom happens to be lighter. While a reveal runs (--clip-open: 1)
   the same curve starts from 100%, with no step to cross the items as the
   clip grows. */
.clip {
  --clip-mask: linear-gradient(to bottom,
    black calc(100% - var(--clip-fade)),
    color-mix(in srgb, black calc(35% + 65% * var(--clip-open)), transparent) calc(100% - var(--clip-fade)),
    color-mix(in srgb, black calc(29% + 53% * var(--clip-open)), transparent) calc(100% - var(--clip-fade) * 0.9),
    color-mix(in srgb, black calc(23% + 42% * var(--clip-open)), transparent) calc(100% - var(--clip-fade) * 0.8),
    color-mix(in srgb, black calc(17% + 32% * var(--clip-open)), transparent) calc(100% - var(--clip-fade) * 0.7),
    color-mix(in srgb, black calc(12% + 23% * var(--clip-open)), transparent) calc(100% - var(--clip-fade) * 0.6),
    color-mix(in srgb, black calc(8% + 15% * var(--clip-open)), transparent) calc(100% - var(--clip-fade) * 0.5),
    color-mix(in srgb, black calc(4% + 9% * var(--clip-open)), transparent) calc(100% - var(--clip-fade) * 0.4),
    color-mix(in srgb, black calc(2% + 2% * var(--clip-open)), transparent) calc(100% - var(--clip-fade) * 0.3),
    transparent calc(100% - var(--clip-fade) * 0.2));
  overflow: hidden;
  -webkit-mask-image: var(--clip-mask);
  mask-image: var(--clip-mask);
  transition:
    height 0.45s var(--easeInOutCubic),
    --clip-fade 0.45s var(--easeInOutCubic),
    --clip-open var(--transition-medium);
}

/* Low in the fade, where the item that peeks is all but masked. */
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
