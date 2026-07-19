<!-- frontend/src/components/settings/categories/music-library/MusicLibrarySettings.vue -->
<!--
  Music Library settings screen — the music *origins* Navidrome indexes (SMB/NFS
  servers, each row opens ManageShare; auto-mounted USB, read-only status) plus a
  manual library refresh. The backend mounts each origin read-only under
  /media/milo and rescans on every change.
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

    <SettingsSection>
      <template #header>
        <SectionHeader :title="t('musicLibrary.maintenance.title')" />
      </template>

      <p class="text-mono ml-desc">{{ t('musicLibrary.maintenance.description') }}</p>

      <div class="ml-progress" :class="{ 'is-open': busy }">
        <div class="ml-progress__inner">
          <div v-if="hasBar" class="ml-progress__track" :class="{ 'is-indeterminate': indeterminate }">
            <div class="ml-progress__fill" :style="fillStyle" />
          </div>
          <span class="ml-progress__label text-mono-small">
            {{ t('musicLibrary.maintenance.refreshProgress', { count: store.scanCount }) }}
          </span>
        </div>
      </div>

      <Button variant="background-strong" size="medium" left-icon="arrowClockwise"
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

// No scan WS event, so this view polls the status while a scan runs. `started`
// waits for Navidrome's counter to reset below the pre-scan `total` before the
// bar trusts it (the idle count equals `total`, which would flash the bar full).
const timer = useTimer();
const inFlight = ref(false);
const offlineShares = ref([]);
const total = ref(0);
const started = ref(false);

const busy = computed(() => inFlight.value || store.isScanning);
const progressPct = computed(() => {
  if (total.value <= 0) return null;
  if (!started.value) return 0;
  return Math.min(100, Math.max(0, Math.round((store.scanCount / total.value) * 100)));
});
watch(() => store.scanCount, (count) => {
  if (store.isScanning && total.value > 0 && count < total.value) started.value = true;
});

const hasBar = computed(() => busy.value && !offlineShares.value.length);
const indeterminate = computed(() => progressPct.value === null);
const fillStyle = computed(() => (indeterminate.value ? {} : { width: `${progressPct.value}%` }));

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
  total.value = store.scanCount;
  started.value = false;
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

/* Progress reveal — mirrors ToggleSection's expand (grid-rows + opacity + margin);
   the negative margin cancels the card's row gap while collapsed. */
.ml-progress {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  margin-top: calc(-1 * var(--space-04));
  transition:
    grid-template-rows var(--transition-fast),
    opacity var(--transition-fast),
    margin-top var(--transition-fast);
}

.ml-progress.is-open {
  grid-template-rows: 1fr;
  opacity: 1;
  margin-top: 0;
}

.ml-progress__inner {
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.ml-progress__track {
  height: 8px;
  background: var(--color-background-strong);
  border-radius: var(--radius-01);
  overflow: hidden;
}

.ml-progress__fill {
  height: 100%;
  background: var(--color-background-contrast);
  border-radius: var(--radius-01);
  transition: width var(--transition-medium);
}

/* Indeterminate variant (no pre-scan total): a segment sweeps instead of filling. */
.ml-progress__track.is-indeterminate .ml-progress__fill {
  width: 40%;
  animation: ml-progress-indeterminate 1.1s ease-in-out infinite;
}

@keyframes ml-progress-indeterminate {
  0% {
    transform: translateX(-120%);
  }

  100% {
    transform: translateX(280%);
  }
}

.ml-progress__label {
  color: var(--color-text-secondary);
}

.ml-maint-warn {
  color: var(--color-text-secondary);
}

/* Type badge in the row's icon slot (USB / SMB / NFS). */
.ml-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: var(--color-text-secondary);
  background: var(--color-background-neutral);
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
