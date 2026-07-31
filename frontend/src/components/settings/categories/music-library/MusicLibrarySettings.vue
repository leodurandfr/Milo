<!-- frontend/src/components/settings/categories/music-library/MusicLibrarySettings.vue -->
<!--
  Music Library settings screen — the music *origins* Navidrome indexes (SMB/NFS
  servers, each row opens ManageShare; auto-mounted USB keys, each row opens the
  rename modal) plus a manual library refresh. The backend mounts each origin
  read-only under /media/milo, gives it its own Navidrome library, and rescans on
  every change.
-->
<template>
  <SettingsContainer>
    <SettingsSection>
      <template #header>
        <SectionHeader :title="t('musicLibrary.shares.title')">
          <template #actions>
            <Button variant="outline" size="small" @click="$emit('add-share')">
              {{ t('musicLibrary.shares.addShare') }}
            </Button>
          </template>
        </SectionHeader>
      </template>

      <p class="text-mono ml-desc">{{ t('musicLibrary.shares.description') }}</p>

      <!-- 2-up on desktop (NAS/shares left, USB right, wrapping as rows fill);
           single column on mobile. -->
      <div class="ml-list">
        <!-- Network servers (SMB/NFS) — tap to edit. -->
        <ListItemButton v-for="share in store.shares" :key="share.id" variant="background"
          :title="shareTitle(share)" action="caret" @click="$emit('edit-share', share)">
          <template #icon>
            <SourceBadge>{{ typeLabel(share.type) }}</SourceBadge>
          </template>
          <template #subtitle>
            <span class="ml-sub text-body">
              <span class="ml-dot" :class="share.mounted ? 'is-on' : 'is-off'" />
              {{ share.host }}
              <span v-if="!share.mounted" class="ml-off">· {{ t('musicLibrary.shares.notConnected') }}</span>
            </span>
          </template>
        </ListItemButton>

        <!-- No NAS share yet: tap through to the add-share wizard. -->
        <ListItemButton v-if="!store.shares.length" variant="background" :title="t('musicLibrary.nas.title')"
          action="caret" @click="$emit('add-share')">
          <template #icon>
            <SourceBadge>NAS</SourceBadge>
          </template>
          <template #subtitle>
            <span class="ml-sub text-body">
              <span class="ml-dot is-off" />
              {{ t('musicLibrary.nas.notConnected') }}
            </span>
          </template>
        </ListItemButton>

        <!-- USB storage — one row per mounted partition, tap to name it; the
             no-key placeholder has nothing to name, so it stays inert. -->
        <ListItemButton v-for="row in usbRows" :key="row.key" variant="background"
          :interactive="row.connected" :action="row.connected ? 'caret' : 'none'" :title="row.label"
          @click="row.connected && $emit('rename-usb', row.device)">
          <template #icon>
            <SourceBadge>USB</SourceBadge>
          </template>
          <template #subtitle>
            <span class="ml-sub text-body">
              <span class="ml-dot" :class="row.connected ? 'is-on' : 'is-off'" />
              {{ row.connected ? t('musicLibrary.usb.connected') : t('musicLibrary.usb.notConnected') }}
            </span>
          </template>
        </ListItemButton>
      </div>
    </SettingsSection>

    <SettingsSection>
      <template #header>
        <SectionHeader :title="t('musicLibrary.maintenance.title')" />
      </template>

      <p class="text-mono ml-desc">{{ t('musicLibrary.maintenance.description') }}</p>

      <ScanProgress :open="busy" :has-bar="hasBar"
        :label="t('musicLibrary.maintenance.refreshProgress', { count: store.scanCount })" />

      <Button variant="brand" size="medium" left-icon="arrowClockwise"
        :disabled="busy" @click="onRefresh">
        {{ busy ? t('musicLibrary.maintenance.refreshing') : t('musicLibrary.maintenance.refresh') }}
      </Button>

      <p v-if="offlineShares.length" class="ml-maint-warn text-body">
        {{ t('musicLibrary.maintenance.cleanupDeferred', { shares: offlineShares.join(', ') }) }}
      </p>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { onMounted, watch, computed, ref } from 'vue';
import { useI18n } from '@/services/i18n';
import { useTimer } from '@/composables/useTimer';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import ScanProgress from '@/components/settings/categories/music-library/ScanProgress.vue';
import SourceBadge from '@/components/settings/categories/music-library/SourceBadge.vue';
import Button from '@/components/ui/Button.vue';

defineEmits(['add-share', 'edit-share', 'rename-usb']);

const { t } = useI18n();
const store = useMusicLibraryStore();

function typeLabel(type) {
  return type === 'nfs' ? 'NFS' : 'SMB';
}

function shareTitle(share) {
  const path = (share.path || '').replace(/^\/+/, '');
  return path ? `${share.name} / ${path}` : share.name;
}

// One row per mounted USB partition — two keys (or one key with two
// partitions) are two rows. The single placeholder row stands in for "no key
// plugged in", which is why `connected` is what makes a row tappable.
const usbRows = computed(() =>
  store.usbDevices.length
    ? store.usbDevices.map((d) => ({
      key: d.mountpoint, label: d.name, connected: true, device: d,
    }))
    : [{ key: 'usb-none', label: t('musicLibrary.usb.title'), connected: false }]
);

// No scan WS event: poll the status while a scan runs.
const timer = useTimer();
const inFlight = ref(false);
const offlineShares = ref([]);

const busy = computed(() => inFlight.value || store.isScanning);
const hasBar = computed(() => busy.value && !offlineShares.value.length);

let polling = false;
function trackScan() {
  if (polling) return;
  polling = true;
  const tick = () => {
    if (!store.isScanning) {
      polling = false;
      inFlight.value = false;
      return;
    }
    timer.setTimeout(async () => {
      await store.refreshScanStatus();
      tick();
    }, 2000);
  };
  tick();
}
watch(() => store.isScanning, (scanning) => { if (scanning) trackScan(); });

async function onRefresh() {
  if (busy.value) return;
  inFlight.value = true;
  offlineShares.value = [];
  const result = await store.refreshLibrary();
  if (!result.ok) {
    inFlight.value = false;
    return;
  }
  if (result.offlineShares?.length) offlineShares.value = result.offlineShares;
  trackScan();
}

onMounted(() => {
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

/* 2 columns on desktop (shares/NAS placeholder wrap against USB); a single
   column on mobile, where two half-width rows would be too cramped to read. */
.ml-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-01);
}

.ml-maint-warn {
  color: var(--color-text-secondary);
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
  .ml-list {
    grid-template-columns: 1fr;
  }
}
</style>
