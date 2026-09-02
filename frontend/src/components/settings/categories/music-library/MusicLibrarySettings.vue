<!-- frontend/src/components/settings/categories/music-library/MusicLibrarySettings.vue -->
<!--
  Music Library settings screen — the music *origins* Navidrome indexes (SMB/NFS
  servers, each row opens ManageShare; auto-mounted USB keys, each row opens the
  rename modal), how they are presented in the library view, and a manual
  refresh. The backend mounts each origin read-only under /media/milo, gives it
  its own Navidrome library, and rescans on every change.

  Every row here is live: the storage list arrives on the `source/storages_changed`
  push, so this screen never refetches. A USB key is instant — its directory
  appears or vanishes. A NAS is not, and cannot be: a server that dies leaves a
  perfectly-formed mount behind, so the backend has to ask the far side
  (NetworkShareService._watch_share_liveness — statvfs every 30 s, three strikes,
  and the kernel's own CIFS timeout comes first). A power cut therefore greys the
  row in a couple of minutes, not at once. A key that is unplugged keeps its row
  (it keeps its Navidrome library and its index too), which is where it gets
  renamed, or forgotten for good.
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

      <p class="text-mono-medium ml-desc">{{ t('musicLibrary.shares.description') }}</p>

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
              <span v-if="share.track_count" class="ml-count">
                · {{ t('musicLibrary.songsCount', { count: share.track_count }) }}
              </span>
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

        <!-- USB storage — one row per known key, plugged in or not; tap to name
             it (or, once unplugged, to forget it). The no-key placeholder has
             nothing to name, so it stays inert. -->
        <ListItemButton v-for="row in usbRows" :key="row.key" variant="background"
          :interactive="row.known" :action="row.known ? 'caret' : 'none'" :title="row.label"
          @click="row.known && $emit('rename-usb', row.device)">
          <template #icon>
            <SourceBadge>USB</SourceBadge>
          </template>
          <template #subtitle>
            <span class="ml-sub text-body">
              <span class="ml-dot" :class="row.connected ? 'is-on' : 'is-off'" />
              {{ row.connected ? t('musicLibrary.usb.connected') : t('musicLibrary.usb.notConnected') }}
              <span v-if="row.known && row.trackCount" class="ml-count">
                · {{ t('musicLibrary.songsCount', { count: row.trackCount }) }}
              </span>
            </span>
          </template>
        </ListItemButton>
      </div>
    </SettingsSection>

    <!-- One tab per storage space in the library view, or all of them merged.
         Only worth showing once there is more than one space to separate. -->
    <ToggleSection v-if="store.storages.length > 1"
      :title="t('musicLibrary.storage.separateTitle')"
      :description="t('musicLibrary.storage.separateDescription')"
      :enabled="separateStorages" @change="handleSeparateToggle" />

    <SettingsSection>
      <template #header>
        <SectionHeader :title="t('musicLibrary.maintenance.title')" />
      </template>

      <p class="text-mono-medium ml-desc">{{ t('musicLibrary.maintenance.description') }}</p>

      <ScanProgress :open="busy" :has-bar="busy" :label="scanLabel" />

      <Button variant="brand" size="medium" left-icon="arrowClockwise"
        :disabled="busy" @click="onRefresh">
        {{ busy ? t('musicLibrary.maintenance.refreshing') : t('musicLibrary.maintenance.refresh') }}
      </Button>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { onMounted, watch, computed, ref } from 'vue';
import { useI18n } from '@/services/i18n';
import { useTimer } from '@/composables/useTimer';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import ToggleSection from '@/components/ui/ToggleSection.vue';
import ScanProgress from '@/components/settings/categories/music-library/ScanProgress.vue';
import SourceBadge from '@/components/settings/categories/music-library/SourceBadge.vue';
import Button from '@/components/ui/Button.vue';

defineEmits(['add-share', 'edit-share', 'rename-usb']);

const { t } = useI18n();
const store = useMusicLibraryStore();
const settingsStore = useSettingsStore();
const { updateSetting } = useSettingsAPI();

function typeLabel(type) {
  return type === 'nfs' ? 'NFS' : 'SMB';
}

function shareTitle(share) {
  const path = (share.path || '').replace(/^\/+/, '');
  return path ? `${share.name} / ${path}` : share.name;
}

// One row per USB key Milō knows — two keys (or one key with two partitions)
// are two rows — each with its live connection dot. The single placeholder row
// stands in for "no key ever plugged in", which is why `known` (not `connected`)
// is what makes a row tappable: an unplugged key is still renamed and forgotten
// from here.
const usbRows = computed(() =>
  store.usbDevices.length
    ? store.usbDevices.map((d) => ({
      key: d.id, label: d.name, known: true, connected: d.mounted,
      trackCount: d.track_count || 0, device: d,
    }))
    : [{ key: 'usb-none', label: t('musicLibrary.usb.title'), known: false, connected: false }]
);

// The scan flag is pushed with the storage list, so nothing is polled here.
// `inFlight` only covers the gap between our own request and the first push.
const REFRESH_RELEASE_MS = 15000;
const timer = useTimer();
const inFlight = ref(false);

const busy = computed(() => inFlight.value || store.isScanning);

// No count while a scan runs unless the storage space on screen actually has
// tracks: Navidrome's global counter is frozen for the whole scan (it read the
// previous scan's total for all 18 minutes of a 10 000-track index), so
// rendering it read "2419 tracks indexed…" forever and looked wedged.
const scanLabel = computed(() =>
  store.activeStorageTrackCount > 0
    ? t('musicLibrary.maintenance.refreshProgress', {
      count: store.activeStorageTrackCount,
    })
    : t('musicLibrary.maintenance.refreshingLabel')
);

// Our request is over as soon as the backend reports no scan running: it pushes
// `scanning: true` the moment Navidrome accepts one, so reaching false again
// means it finished.
watch(() => store.isScanning, (scanning) => { if (!scanning) inFlight.value = false; });

async function onRefresh() {
  if (busy.value) return;
  inFlight.value = true;
  // Safety net: if no scan state ever arrives (Navidrome not provisioned, a
  // dropped socket), release the button rather than leave it disabled for good.
  timer.setTimeout(() => { inFlight.value = false; }, REFRESH_RELEASE_MS);
  if (!await store.rescan()) inFlight.value = false;
}

const separateStorages = computed(
  () => settingsStore.musicLibrarySettings.separate_storages
);

async function handleSeparateToggle(enabled) {
  settingsStore.updateMusicLibrarySettings({ separate_storages: enabled });
  await updateSetting('music-library-settings', { separate_storages: enabled });
}

onMounted(() => {
  store.loadShares();
  // Carries the storage spaces and the scan flag; every later change is pushed.
  store.loadStorages({ force: true });
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

.ml-count {
  color: var(--color-text-light);
}

@media (max-aspect-ratio: 4/3) {
  .ml-list {
    grid-template-columns: 1fr;
  }
}
</style>
