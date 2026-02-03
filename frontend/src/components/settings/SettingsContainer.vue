<!-- Shared container for multiple settings sections -->
<template>
  <div class="settings-container">
    <slot />
  </div>
</template>

<style scoped>
.settings-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}
</style>
