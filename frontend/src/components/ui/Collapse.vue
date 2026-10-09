<!-- frontend/src/components/ui/Collapse.vue -->
<!-- Content that expands/collapses in a host Modal on the clip's own curve -->
<template>
  <div ref="wrapperRef" class="collapse" :class="{ 'is-open': open }" :inert="!open"
    @transitionend.self="onTransitionEnd" @transitioncancel.self="onTransitionCancel">
    <div ref="innerRef" class="collapse__inner">
      <slot />
    </div>
  </div>
</template>

<script setup>
import { ref, watch, inject, nextTick, onMounted } from 'vue';

const props = defineProps({
  open: { type: Boolean, required: true }
});

// The wrapper rides the SAME curve as the Modal clip, so frame and content are
// equal at every frame. requestHeightDelta set the frame to its target at once
// and let the observer re-aim the clip spring on every frame of the content's
// own (shorter) animation — the flicker. Same reasoning as MultiroomItem:
// collapse lands a padding above 0, which a tall content's spring lobe passes,
// clamped away on the wrapper but not on the clip, so it takes a monotone
// curve on both sides — the CSS's closing curve, which must stay this one.
const COLLAPSE_TRANSITION = 'height var(--transition-collapse)';
const COLLAPSE_FOLLOW_MS = 450;

const springHeightDelta = inject('modalSpringHeightDelta', null);
const wrapperRef = ref(null);
const innerRef = ref(null);

// A flag a view sets from its own onMounted (SpotifySettings reads the store
// there) is the state the view opens with, not a change: it is drawn as is.
// Announced, it springs the clip during the navigation that mounts the view,
// measured against both views stacked, and the follow window then swallows the
// shrink when the leaving view goes — the modal kept the old page's height.
let settled = false;
onMounted(() => requestAnimationFrame(() => { settled = true; }));

// Explicit px only while animating: settled, the height is CSS's (auto or the
// overhang), so content that changes size later is never cut to a stale value.
watch(() => props.open, async (open) => {
  const el = wrapperRef.value;
  if (!el || !settled) return;
  // What is on screen now, mid-animation included, so a reversal starts from
  // there rather than from a full height it never reached. Pinned before the
  // patch flips the class, so its height never resolves under the pin: the
  // `transition: none` that guarded against it also cut the opacity fade dead.
  const from = el.offsetHeight;
  el.style.height = `${from}px`;
  // Measured after the patch, so slot content that follows the same flag counts.
  await nextTick();
  if (!innerRef.value) return;

  // A view kept mounted out of sight (a page under the modal's navigation) is
  // not what the clip frames: moving it would size the modal for the wrong page.
  if (!el.checkVisibility()) {
    el.style.height = '';
    return;
  }

  // Both include the inner paddings that pay back the wrapper's negative
  // margins; closed, what is left is the overhang into the card's padding.
  const to = open ? innerRef.value.offsetHeight : -parseFloat(getComputedStyle(el).marginBottom);
  if (to === from) {
    el.style.height = '';
    return;
  }

  springHeightDelta?.(
    to - from,
    open ? {} : { transition: COLLAPSE_TRANSITION, durationMs: COLLAPSE_FOLLOW_MS }
  );
  el.style.height = `${to}px`;
});

function onTransitionEnd(event) {
  if (event.propertyName === 'height') wrapperRef.value.style.height = '';
}

// A reversal cancels too, and must keep its new target; only a transition cut
// by the view leaving the screen would otherwise leave a stale px height.
function onTransitionCancel(event) {
  if (event.propertyName === 'height' && !wrapperRef.value.checkVisibility()) {
    wrapperRef.value.style.height = '';
  }
}
</script>

<style scoped>
/* The negative top margin cancels the parent's flex gap while closed; the
   inner padding gives it back once open, inside the animated height. The
   bottom overhangs the card's own padding (--space-05, SettingsSection's) and
   pays it back the same way, so the clip edge is the card's edge: the content
   is uncovered by the card as it grows, never cut a padding short of it. That
   makes it the last child of its card, as both callers place it. Clipped
   vertically only: a slider's thumb and focus ring may pass its sides.
   Closing, the content fades out faster than the height leaves, so it is gone
   before the edge reaches it. */
.collapse {
  height: var(--space-05);
  overflow-x: visible;
  overflow-y: clip;
  margin-top: calc(-1 * var(--space-04));
  margin-bottom: calc(-1 * var(--space-05));
  opacity: 0;
  transition:
    height var(--transition-collapse),
    opacity var(--transition-fast);
}

.collapse.is-open {
  height: auto;
  opacity: 1;
  transition:
    height var(--transition-spring-light),
    opacity var(--transition-medium);
}

/* flow-root keeps a last child's bottom margin (a stepped slider's room for
   its ticks) inside the measured height: escaped, offsetHeight missed it while
   the wrapper's overflow kept it, and the card jumped when height went auto. */
.collapse__inner {
  display: flow-root;
  padding-top: var(--space-04);
  padding-bottom: var(--space-05);
  min-width: 0;   /* Allows children with long unbreakable text to ellipsize instead of overflowing */
  transform: translateY(calc(-1 * var(--space-02)));
  transition: transform var(--transition-fast);
}

/* none, not translateY(0), once open: a transform would make the inner the
   containing block of anything fixed inside it. The shift stays within the
   inner padding, so the wrapper's top edge never cuts the content. */
.collapse.is-open .collapse__inner {
  transform: none;
  transition: transform var(--transition-spring-light);
}
</style>
