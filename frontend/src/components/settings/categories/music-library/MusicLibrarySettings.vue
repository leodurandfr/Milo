<!-- frontend/src/components/settings/categories/music-library/MusicLibrarySettings.vue -->
<!--
  Music Library settings screen. Its (currently sole) concern is network shares:
  the list of configured SMB/NFS shares, each row opening the add/edit form
  (ManageShare) in the parent's navigation stack. The backend mounts each share
  read-only under /media/milo and rescans Navidrome on every write, so this
  screen only drives the CRUD — the "building library…" progress surfaces in the
  Music Library source view itself.
-->
<template>
  <SettingsContainer>
    <SettingsSection>
      <template #header>
        <div class="ml-shares-header">
          <h2 class="heading-2">{{ t('musicLibrary.shares.title') }}</h2>
          <p class="text-mono ml-shares-header__desc">{{ t('musicLibrary.shares.description') }}</p>
        </div>
      </template>

      <!-- Loading (first fetch only) -->
      <div v-if="store.sharesLoading && !store.sharesLoaded" class="ml-shares-loading">
        <LoadingSpinner :size="40" />
      </div>

      <!-- Empty -->
      <div v-else-if="!store.shares.length" class="ml-shares-empty text-mono">
        {{ t('musicLibrary.shares.empty') }}
      </div>

      <!-- List -->
      <div v-else class="ml-shares-list">
        <ListItemButton v-for="share in store.shares" :key="share.id" variant="background"
          :title="share.name" action="caret" @click="$emit('edit-share', share)">
          <template #icon>
            <span class="ml-share-badge text-mono-small">{{ typeLabel(share.type) }}</span>
          </template>
          <template #subtitle>
            <span class="ml-share-sub text-body">
              <span class="ml-share-status" :class="share.mounted ? 'is-on' : 'is-off'">
                {{ share.mounted ? t('musicLibrary.shares.connected') : t('musicLibrary.shares.notConnected') }}
              </span>
              · {{ shareLocation(share) }}
            </span>
          </template>
        </ListItemButton>
      </div>

      <Button variant="brand" left-icon="plus" @click="$emit('add-share')">
        {{ t('musicLibrary.shares.addShare') }}
      </Button>
    </SettingsSection>

    <!-- Library refresh — for music added directly on a NAS, which the file
         watcher can't see over a network mount. -->
    <SettingsSection :title="t('musicLibrary.shares.libraryTitle')">
      <p class="text-mono ml-lib-desc">{{ t('musicLibrary.shares.libraryDesc') }}</p>
      <Button variant="background-strong" size="medium" :loading="rescanning || store.isScanning" @click="doRescan">
        {{ store.isScanning ? t('musicLibrary.shares.scanning') : t('musicLibrary.shares.refreshLibrary') }}
      </Button>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Button from '@/components/ui/Button.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';

defineEmits(['add-share', 'edit-share']);

const { t } = useI18n();
const store = useMusicLibraryStore();

function typeLabel(type) {
  return type === 'nfs' ? 'NFS' : 'SMB';
}

function shareLocation(share) {
  return `${share.host} / ${share.path}`;
}

const rescanning = ref(false);
async function doRescan() {
  if (rescanning.value || store.isScanning) return;
  rescanning.value = true;
  await store.rescan();
  rescanning.value = false;
}

onMounted(() => {
  // Preloaded on modal open is not guaranteed; fetch (cached) on mount.
  store.loadShares();
  store.refreshScanStatus();
});
</script>

<style scoped>
.ml-shares-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.ml-shares-header__desc {
  color: var(--color-text-secondary);
}

.ml-lib-desc {
  color: var(--color-text-secondary);
}

.ml-shares-loading {
  display: flex;
  justify-content: center;
  padding: var(--space-05);
}

.ml-shares-empty {
  padding: var(--space-05);
  text-align: center;
  color: var(--color-text-secondary);
  background: var(--color-background);
  border-radius: var(--radius-04);
  border: 2px dashed var(--color-border);
}

.ml-shares-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

/* Type badge in the row's icon slot (SMB / NFS). */
.ml-share-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: var(--color-text-secondary);
  background: var(--color-background-strong);
}

/* Subtitle: live connection status + location. */
.ml-share-sub {
  color: var(--color-text-secondary);
}

.ml-share-status.is-on {
  color: var(--color-success);
}

.ml-share-status.is-off {
  color: var(--color-text-light);
}
</style>
