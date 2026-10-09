<!-- A column of SectionCards: the one gap between them, wherever they are stacked -->
<template>
  <div class="section-stack">
    <slot />
  </div>
</template>

<style scoped>
.section-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}
</style>
