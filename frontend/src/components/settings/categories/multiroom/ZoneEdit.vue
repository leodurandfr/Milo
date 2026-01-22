<!-- frontend/src/components/settings/categories/multiroom/ZoneEdit.vue -->
<!-- Form for creating or editing a multiroom zone -->
<template>
  <div class="zone-edit">
    <!-- Zone Name Input -->
    <section class="settings-section">
      <div class="section-group">
        <h2 class="heading-2">{{ $t('dsp.zones.zoneName', 'Zone Name') }}</h2>
        <InputText
          v-model="zoneName"
          :placeholder="$t('dsp.zones.zoneNamePlaceholder', 'e.g., Living Room')"
          size="medium"
          :maxlength="15"
          @blur="saveZoneName"
        />
      </div>
    </section>

    <!-- Client Selection -->
    <section class="settings-section">
      <div class="section-group">
        <h2 class="heading-2">{{ $t('dsp.zones.selectClients', 'Select Clients') }}</h2>
        <p class="description text-mono">
          {{ $t('dsp.zones.selectClientsDescription', 'Select at least 2 clients to create a zone.') }}
        </p>
        <div class="clients-list">
          <ListItemButton
            v-for="target in availableTargets"
            :key="target.id"
            variant="background"
            action="toggle"
            icon-variant="standard"
            :model-value="selectedClients.includes(target.id)"
            :disabled="!target.online"
            @click="toggleClient(target.id)"
          >
            <template #icon>
              <div class="client-icon-wrapper">
                <SvgIcon :name="getSpeakerIcon(target.id)" :size="28" />
                <span class="online-indicator" :class="{
                  'online-indicator--online': target.online && !hasSyncError(target.id),
                  'online-indicator--error': target.online && hasSyncError(target.id)
                }" />
              </div>
            </template>
            <template #title>
              <div class="client-title-wrapper">
                <span>{{ target.name }}</span>
                <span v-if="isSyncing(target.id)" class="sync-status sync-status--syncing">
                  {{ $t('multiroom.syncing') }}
                </span>
                <span v-else-if="hasSyncError(target.id)" class="sync-status sync-status--error">
                  {{ $t('multiroom.syncError') }}
                  <button type="button" class="retry-btn" @click.stop="handleRetrySync(target.id)">
                    {{ $t('multiroom.retrySync') }}
                  </button>
                </span>
              </div>
            </template>
          </ListItemButton>
        </div>
      </div>
    </section>

    <!-- Create Zone Button (only when creating new zone) -->
    <Button
      v-if="!groupId"
      variant="brand"
      size="medium"
      :loading="saving"
      :disabled="selectedClients.length < 2"
      @click="handleCreate"
    >
      {{ $t('dsp.zones.createZone', 'Create Zone') }}
    </Button>

    <!-- Delete Zone (only when editing existing zone) -->
    <Button
      v-if="groupId"
      variant="brand"
      size="medium"
      :disabled="deleting"
      :loading="deleting"
      @click="handleDelete"
    >
      {{ $t('dsp.zones.deleteZone', 'Delete Zone') }}
    </Button>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import Button from '@/components/ui/Button.vue';
import InputText from '@/components/ui/InputText.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const props = defineProps({
  // Group ID if editing an existing zone, null for creating new
  groupId: {
    type: String,
    default: null
  }
});

const emit = defineEmits(['back', 'saved']);

const dspStore = useDspStore();
const multiroomStore = useMultiroomStore();
const saving = ref(false);
const deleting = ref(false);
const zoneName = ref('');
const originalZoneName = ref('');
const selectedClients = ref([]);

// Get available clients from multiroomStore (single source of truth)
const availableTargets = computed(() => {
  return multiroomStore.clientList.map(client => ({
    id: client.mac_id,
    name: client.name,
    host: client.host,
    ip: client.ip,
    online: client.online
  }));
});

// Find the current zone being edited from multiroomStore
const currentGroup = computed(() => {
  if (!props.groupId) return null;
  return multiroomStore.zoneList.find(z => z.id === props.groupId);
});

// Get speaker icon name based on type
function getSpeakerIcon(macId) {
  const speakerType = dspStore.getClientSpeakerType(macId);
  const iconMap = {
    satellite: 'speakerSatellite',
    bookshelf: 'speakerShelf',
    tower: 'speakerColumn',
    subwoofer: 'speakerSub'
  };
  return iconMap[speakerType] || 'speakerShelf';
}

// Sync status helpers
function hasSyncError(macId) {
  return multiroomStore.hasSyncError(macId);
}

function isSyncing(macId) {
  return multiroomStore.isSyncing(macId);
}

async function handleRetrySync(macId) {
  // Sync retry not yet implemented - will auto-sync on reconnect
  console.warn(`Retry sync for ${macId} - feature not yet implemented`);
}

// Toggle client selection
async function toggleClient(clientId) {
  const index = selectedClients.value.indexOf(clientId);

  if (index === -1) {
    // Adding client to zone
    if (props.groupId) {
      try {
        await multiroomStore.addClientToZone(props.groupId, clientId);
        // State update comes via WebSocket, but update local state for responsiveness
        selectedClients.value.push(clientId);
      } catch (error) {
        console.error('Error adding client to zone:', error);
        // Don't update local state on error
      }
    } else {
      // Just creating new zone, not yet saved - update local state only
      selectedClients.value.push(clientId);
    }
  } else {
    // Removing client from zone
    if (props.groupId) {
      try {
        const response = await multiroomStore.removeClientFromZone(props.groupId, clientId);
        // Update local state after successful backend call
        selectedClients.value.splice(index, 1);

        // Check if zone was deleted (< 2 clients remaining)
        if (response.message && response.message.includes('deleted')) {
          emit('back'); // Navigate back since zone was deleted
        }
      } catch (error) {
        console.error('Error removing client from zone:', error);
        // State unchanged on error - stays in sync
      }
    } else {
      // Just creating new zone, not yet saved - update local state only
      selectedClients.value.splice(index, 1);
    }
  }
}

// Save zone name on blur (only when editing existing zone)
async function saveZoneName() {
  if (!props.groupId) return;
  const newName = zoneName.value?.trim() || '';
  if (newName === originalZoneName.value) return;

  try {
    await multiroomStore.updateZone(props.groupId, { name: newName });
    originalZoneName.value = newName;
  } catch (error) {
    console.error('Error saving zone name:', error);
  }
}

// Initialize state when mounted
onMounted(async () => {
  if (currentGroup.value) {
    // Editing existing zone
    zoneName.value = currentGroup.value.name || '';
    originalZoneName.value = zoneName.value;
    selectedClients.value = [...(currentGroup.value.client_ids || [])];
  } else {
    // Creating new zone
    selectedClients.value = [];
    zoneName.value = '';
  }
});

// Sync selectedClients when zone membership changes via WebSocket (AC4: real-time updates)
watch(
  () => currentGroup.value?.client_ids,
  (newClientIds) => {
    if (newClientIds && props.groupId) {
      selectedClients.value = [...newClientIds];
    }
  },
  { deep: true }
);

// Create new zone (only used when groupId is null)
async function handleCreate() {
  if (selectedClients.value.length < 2) return;

  saving.value = true;
  try {
    // Use multiroomStore directly for zone creation (consistent with edit operations)
    await multiroomStore.createZone(zoneName.value || 'New Zone', selectedClients.value);
    emit('back');
  } catch (error) {
    console.error('Error creating zone:', error);
  } finally {
    saving.value = false;
  }
}

async function handleDelete() {
  if (!props.groupId) return;

  deleting.value = true;
  try {
    await multiroomStore.deleteZone(props.groupId);
    emit('back');
  } catch (error) {
    console.error('Error deleting zone:', error);
  } finally {
    deleting.value = false;
  }
}
</script>

<style scoped>
.zone-edit {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

.section-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.description {
  color: var(--color-text-secondary);
}

.clients-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

/* Client icon with online indicator */
.client-icon-wrapper {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}

.online-indicator {
  position: absolute;
  bottom: 0;
  right: 0;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-text-secondary);
  border: 2px solid var(--color-background-neutral);
}

.online-indicator--online {
  background: var(--color-success, #22c55e);
}

.online-indicator--error {
  background: var(--color-error, #ef4444);
}

/* Client title with sync status */
.client-title-wrapper {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sync-status {
  font-size: var(--font-size-small);
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

.sync-status--syncing {
  color: var(--color-text-secondary);
}

.sync-status--error {
  color: var(--color-error, #ef4444);
}

.retry-btn {
  font-size: var(--font-size-small);
  color: var(--color-brand);
  background: none;
  border: none;
  cursor: pointer;
  text-decoration: underline;
  padding: 0;
}

.retry-btn:hover {
  opacity: 0.8;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .clients-list {
    grid-template-columns: 1fr;
  }
}
</style>
