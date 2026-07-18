<!-- frontend/src/components/settings/categories/music-library/MusicLibrarySettings.vue -->
<!--
  Music Library settings screen — one place for the music *origins* Navidrome
  indexes: configured SMB/NFS network servers (each row opens ManageShare) and
  auto-mounted USB storage (read-only status; always shown, plugged in or not).
  The backend mounts each origin read-only under /media/milo and rescans on every
  change — the rescan action lives in the nav header (SettingsModal), and the
  "building library…" progress surfaces in the Music Library source view itself.
-->
<template>
  <SettingsContainer>
    <SettingsSection>
      <template #header>
        <SectionHeader :title="t('musicLibrary.shares.title')">
          <template #actions>
            <Button variant="brand" size="small" left-icon="plus" @click="$emit('add-share')">
              {{ t('musicLibrary.shares.addShare') }}
            </Button>
          </template>
        </SectionHeader>
      </template>

      <p class="text-mono ml-desc">{{ t('musicLibrary.shares.description') }}</p>

      <div class="ml-list">
        <!-- Network servers (SMB/NFS) — tap to edit. -->
        <ListItemButton v-for="share in store.shares" :key="share.id" variant="background"
          :title="shareTitle(share)" action="caret" @click="$emit('edit-share', share)">
          <template #icon>
            <span class="ml-badge text-mono-small">{{ typeLabel(share.type) }}</span>
          </template>
          <template #subtitle>
            <span class="ml-sub text-body">
              <span class="ml-dot" :class="share.mounted ? 'is-on' : 'is-off'" />
              {{ share.host }}
              <span v-if="!share.mounted" class="ml-off">· {{ t('musicLibrary.shares.notConnected') }}</span>
            </span>
          </template>
        </ListItemButton>

        <!-- USB storage — read-only status, always present (detected or not). -->
        <div v-for="row in usbRows" :key="row.key" class="ml-static-row">
          <span class="ml-static-row__icon">
            <span class="ml-badge text-mono-small">USB</span>
          </span>
          <div class="ml-static-row__text">
            <span class="heading-4">{{ row.label }}</span>
            <span class="ml-sub text-body">
              <span class="ml-dot" :class="row.connected ? 'is-on' : 'is-off'" />
              {{ row.connected ? t('musicLibrary.usb.connected') : t('musicLibrary.usb.notConnected') }}
            </span>
          </div>
        </div>
      </div>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { onMounted, watch, computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useTimer } from '@/composables/useTimer';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Button from '@/components/ui/Button.vue';

defineEmits(['add-share', 'edit-share']);

const { t } = useI18n();
const store = useMusicLibraryStore();

function typeLabel(type) {
  return type === 'nfs' ? 'NFS' : 'SMB';
}

// Row heading: friendly server/label name + the folder it points at, e.g.
// "NAS-Leo / music". The host (IP) rides the subtitle beside the status dot.
function shareTitle(share) {
  const path = (share.path || '').replace(/^\/+/, '');
  return path ? `${share.name} / ${path}` : share.name;
}

// USB rows — one per mounted device, or a single placeholder so USB is always
// listed as a possible source (with a live plugged-in / not state).
const usbRows = computed(() =>
  store.usbDevices.length
    ? store.usbDevices.map((d) => ({ key: d.mountpoint, label: d.label, connected: true }))
    : [{ key: 'usb-none', label: t('musicLibrary.usb.title'), connected: false }]
);

// The rescan action lives in the nav header (an IconButton in SettingsModal);
// this view owns the follow-up polling so the header spinner (bound to
// store.isScanning) clears once Navidrome settles — there is no scan WS event.
const timer = useTimer();
function trackScan() {
  if (!store.isScanning) return;
  timer.setTimeout(async () => {
    await store.refreshScanStatus();
    trackScan();
  }, 2000);
}
watch(() => store.isScanning, (scanning) => { if (scanning) trackScan(); });

onMounted(() => {
  // Preloaded on modal open is not guaranteed; fetch (cached) on mount.
  store.loadShares();
  store.loadUsbDevices();
  store.refreshScanStatus();
  if (store.isScanning) trackScan();
});
</script>

<style scoped>
.ml-desc {
  color: var(--color-text-secondary);
}

.ml-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

/* Type badge in the row's icon slot (USB / SMB / NFS). */
.ml-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: var(--color-text-secondary);
  background: var(--color-background-strong);
}

/* Read-only USB row — mirrors ListItemButton's background variant, no action. */
.ml-static-row {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-02);
  border-radius: var(--radius-05);
  background: var(--color-background);
  box-shadow: inset 0 0 0 1px var(--color-border);
}

.ml-static-row__icon {
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border-radius: var(--radius-03);
  overflow: hidden;
}

.ml-static-row__text {
  flex: 1;
  min-height: 40px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
}

/* Subtitle: status dot + host/label. */
.ml-sub {
  display: inline-flex;
  align-items: center;
  gap: var(--space-01);
  color: var(--color-text-secondary);
}

/* Connection dot — replaces the "connected" word; the offline label carries the
   meaning when it's off (a lone dot would be ambiguous). */
.ml-dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.ml-dot.is-on {
  background: var(--color-success);
}

.ml-dot.is-off {
  background: var(--color-text-light);
}

.ml-off {
  color: var(--color-text-light);
}

@media (max-aspect-ratio: 4/3) {
  .ml-static-row {
    border-radius: var(--radius-04);
  }

  .ml-static-row__icon {
    width: 36px;
    height: 36px;
    border-radius: var(--radius-02);
  }

  .ml-static-row__text {
    min-height: 32px;
  }
}
</style>
