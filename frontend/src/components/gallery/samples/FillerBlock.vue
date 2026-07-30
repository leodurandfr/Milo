<!-- frontend/src/components/gallery/samples/FillerBlock.vue -->
<!--
  Stand-in slot content, for the composites whose slots take a whole view rather
  than a line of text.

  The gallery's rule is that a slot's content is either plain text or a real
  component with real props — a demo that fakes a store is a second frontend to
  maintain. A layout's `content` slot has no such component: what it receives is
  whatever the source's browser happens to be. So it receives this instead, which
  claims nothing: a labelled box of a stated height, there to give the layout
  something to scroll, cross-fade and make room for.
-->
<template>
  <div class="filler" :style="{ height: resolvedHeight }">
    <span class="text-mono-small">{{ label }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  /** What the slot would hold in the app — named, so the box is not a mystery. */
  label: {
    type: String,
    default: 'slot content'
  },
  /** A number is px; a string is used as given, so '100%' fills its slot. */
  height: {
    type: [Number, String],
    default: '100%'
  }
});

const resolvedHeight = computed(() =>
  typeof props.height === 'number' ? `${props.height}px` : props.height
);
</script>

<style scoped>
.filler {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  padding: var(--space-04);
  color: var(--color-text-light);
  background: var(--color-background-strong);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-03);
}
</style>
