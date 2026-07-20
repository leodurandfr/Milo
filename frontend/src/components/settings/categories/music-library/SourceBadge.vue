<!-- frontend/src/components/settings/categories/music-library/SourceBadge.vue -->
<!--
  Type badge for a ListItemButton's icon slot (NAS/USB status rows, discovered
  server rows, share rows) — e.g. "SMB", "NFS", "USB", "NAS".
-->
<template>
  <span class="source-badge text-mono-small"><slot /></span>
</template>

<style scoped>
.source-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: var(--color-text-secondary);
  background: var(--color-background-neutral);
}
</style>
