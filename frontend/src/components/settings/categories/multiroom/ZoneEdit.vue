<!-- frontend/src/components/settings/categories/multiroom/ZoneEdit.vue -->
<!-- Form for creating or editing a multiroom zone -->
<template>
  <div class="zone-edit">
    <!-- Zone Name Input -->
    <SettingsSection :title="t('equalizer.zones.zoneName', 'Zone Name')">
      <InputText
        v-model="zoneName"
        :placeholder="t('equalizer.zones.zoneNamePlaceholder', 'e.g., Living Room')"
        size="medium"
        :maxlength="16"
        @blur="saveZoneName"
      />
    </SettingsSection>

    <!-- Client Selection -->
    <SettingsSection :title="t('equalizer.zones.selectClients', 'Select Clients')">
      <div class="clients-list">
        <SpeakerListItem
          v-for="target in availableTargets"
          :key="target.id"
          :name="target.name"
          :mac-id="target.id"
          :online="target.online"
          action="toggle"
          :toggle-variant="target.online ? 'primary' : 'secondary'"
          :model-value="selectedClients.includes(target.id)"
          @click="toggleClient(target.id)"
        />
      </div>

    </SettingsSection>

    <!-- Create Zone Button (only when creating new zone) -->
    <Button
      v-if="!groupId"
      variant="brand"
      size="medium"
      :loading="saving"
      :disabled="selectedClients.length < 2"
      @click="handleCreate"
    >
      {{ t('equalizer.zones.createZone', 'Create Zone') }}
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
      {{ t('equalizer.zones.deleteZone', 'Delete Zone') }}
    </Button>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMultiroomStore } from '@/stores/multiroomStore';
import Button from '@/components/ui/Button.vue';
import InputText from '@/components/ui/InputText.vue';
import SpeakerListItem from '@/components/settings/categories/multiroom/SpeakerListItem.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

const props = defineProps({
  // Group ID if editing an existing zone, null for creating new
  groupId: {
    type: String,
    default: null
  }
});

const emit = defineEmits(['back', 'saved']);

const { t } = useI18n();
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

.clients-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .clients-list {
    grid-template-columns: 1fr;
  }
}
</style>
