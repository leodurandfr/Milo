<template>
  <div class="track-list">
    <slot />
  </div>
</template>

<style scoped>
.track-list {
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border-radius: var(--radius-04);
  /* The rows' own vertical padding makes up the rest, so every row keeps one
     height: a playlist's drag moves a row by the height of the one it grabbed. */
  padding: calc(var(--space-05) - var(--space-04)) var(--space-05);
}

/* A row cannot tell it is the last: a playlist wraps each in its own drag
   item, where every row is a last child. Transparent rather than gone, so the
   last row keeps the height the drag steps by. */
.track-list > :deep(.track-row:last-child),
.track-list > :deep(:last-child > .track-row),
.track-list > :deep(.skeleton-track-row:last-child) {
  border-bottom-color: transparent;
}
</style>
