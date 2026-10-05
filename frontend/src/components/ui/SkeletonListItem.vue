<!-- frontend/src/components/ui/SkeletonListItem.vue -->
<!--
  ListItemButton's placeholder, for a list whose rows are still being fetched.
  It keeps the row's box and typography (each bar sits in a line of the text
  class it stands for), so the list holds its height when the rows arrive.
-->
<template>
  <div class="skeleton-list-item" :class="{ 'skeleton-list-item--no-icon': !icon }" aria-hidden="true">
    <div v-if="icon" class="skeleton-list-item__icon shimmer"></div>
    <div class="skeleton-list-item__text" :class="{ 'skeleton-list-item__text--stacked': subtitle !== 'none' }">
      <span class="skeleton-list-item__line" :class="subtitle === 'none' ? 'heading-3' : 'heading-4'">
        <span class="skeleton-text-line shimmer skeleton-list-item__title"></span>&#8203;
      </span>
      <span v-if="subtitle !== 'none'" class="skeleton-list-item__line"
        :class="subtitle === 'mono' ? 'text-mono-small' : 'text-body'">
        <span class="skeleton-text-line shimmer skeleton-list-item__subtitle"></span>&#8203;
      </span>
    </div>
  </div>
</template>

<script setup>
defineProps({
  // The row's leading 40px tile (ListItemButton's #icon slot).
  icon: {
    type: Boolean,
    default: true
  },
  // The second line, in the typography the real row uses: 'body' is
  // ListItemButton's own subtitle, 'mono' a #subtitle slot in text-mono-small.
  subtitle: {
    type: String,
    default: 'none',
    validator: (value) => ['none', 'body', 'mono'].includes(value)
  }
})
</script>

<style scoped>
.skeleton-list-item {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-02);
  border-radius: var(--radius-05);
  background: var(--color-inset);
  box-shadow: inset 0 0 0 1px var(--color-border);
}

.skeleton-list-item--no-icon {
  padding: var(--space-03) var(--space-03) var(--space-03) var(--space-05);
}

.skeleton-list-item__icon {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-03);
}

.skeleton-list-item__text {
  flex: 1;
  min-width: 0;
  min-height: 40px;
  display: flex;
  align-items: center;
}

.skeleton-list-item__text--stacked {
  flex-direction: column;
  align-items: stretch;
  justify-content: center;
  gap: 2px;
}

.skeleton-list-item__line {
  display: flex;
  align-items: center;
  width: 100%;
}

.skeleton-list-item__title {
  width: 55%;
}

.skeleton-list-item__subtitle {
  width: 35%;
}

@media (max-aspect-ratio: 4/3) {
  .skeleton-list-item {
    border-radius: var(--radius-04);
  }

  .skeleton-list-item__text {
    min-height: 32px;
  }

  .skeleton-list-item__icon {
    width: 36px;
    height: 36px;
    border-radius: var(--radius-02);
  }
}
</style>
