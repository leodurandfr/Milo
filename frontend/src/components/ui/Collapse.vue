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
// collapse lands on 0, where a spring's negative lobe is clamped away on the
// wrapper but not on the clip, so it takes a monotone curve on both sides.
const COLLAPSE_TRANSITION = 'height var(--transition-medium)';
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

// Explicit px only while animating: settled, the height is CSS's (auto or 0),
// so content that changes size later is never cut to a stale value.
watch(() => props.open, async (open) => {
  const el = wrapperRef.value;
  if (!el || !settled) return;
  // What is on screen now, mid-animation included, so a reversal starts from
  // there rather than from a full height it never reached.
  const from = el.offsetHeight;
  // Measured after the patch, so slot content that follows the same flag counts.
  await nextTick();
  if (!innerRef.value) return;

  // A view kept mounted out of sight (a page under the modal's navigation) is
  // not what the clip frames: moving it would size the modal for the wrong page.
  if (!el.checkVisibility()) {
    el.style.height = '';
    return;
  }

  // Includes the inner padding that pays back the wrapper's negative margin.
  const to = open ? innerRef.value.offsetHeight : 0;
  if (to === from) {
    el.style.height = '';
    return;
  }

  springHeightDelta?.(
    to - from,
    open ? {} : { transition: COLLAPSE_TRANSITION, durationMs: COLLAPSE_FOLLOW_MS }
  );
  // Pin where it stood, untransitioned: the reads above already resolved the
  // patched class (0 or auto), which a transitioned pin would animate from.
  el.style.transition = 'none';
  el.style.height = `${from}px`;
  void el.offsetHeight;
  el.style.transition = '';
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
/* The negative margin cancels the parent's flex gap while closed; the inner
   padding gives it back once open, inside the animated height. Clipped
   vertically only: a slider's thumb and focus ring may pass its sides. */
.collapse {
  height: 0;
  overflow-x: visible;
  overflow-y: clip;
  margin-top: calc(-1 * var(--space-04));
  opacity: 0;
  transition:
    height var(--transition-medium),
    opacity var(--transition-medium);
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
  min-width: 0;   /* Allows children with long unbreakable text to ellipsize instead of overflowing */
}
</style>
