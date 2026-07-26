<!-- frontend/src/components/settings/categories/multiroom/ZoneEdit.vue -->
<!-- Form for creating or editing a multiroom zone -->
<template>
  <div class="zone-edit">
    <!-- Zone Name Input -->
    <SettingsSection :title="t('equalizer.zones.zoneName')">
      <InputText
        v-model="zoneName"
        :placeholder="t('equalizer.zones.zoneNamePlaceholder')"
        size="medium"
        :maxlength="16"
        @blur="saveZoneName"
      />
    </SettingsSection>

    <!-- Client Selection -->
    <SettingsSection :title="t('equalizer.zones.selectClients')">
      <p class="text-mono zone-hint">{{ t('equalizer.zones.minimumClients') }}</p>
      <div class="clients-list">
        <SystemListItem
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
      v-if="!groupId && selectedClients.length >= 2"
      variant="brand"
      size="medium"
      class="action-button-sticky"
      :loading="saving"
      @click="handleCreate"
    >
      {{ t('equalizer.zones.createZone') }}
    </Button>

    <!-- Delete Zone (only when editing existing zone) -->
    <Button
      v-if="groupId"
      :variant="deleteState === 'idle' ? 'brand' : 'important'"
      size="medium"
      :disabled="deleteState === 'deleting'"
      :loading="deleteState === 'deleting'"
      @click="handleDelete"
    >
      {{ deleteLabel }}
    </Button>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { logger } from '@/services/logger';
import Button from '@/components/ui/Button.vue';
import InputText from '@/components/ui/InputText.vue';
import SystemListItem from '@/components/settings/categories/multiroom/SystemListItem.vue';
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
const deleteState = ref('idle'); // 'idle' | 'confirming' | 'deleting'
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
    // Adding client — optimistic update before await to avoid stale index
    selectedClients.value.push(clientId);
    if (props.groupId) {
      try {
        await multiroomStore.addClientToZone(props.groupId, clientId);
        // Watcher syncs final state from WebSocket
      } catch (error) {
        // Revert optimistic update
        const revertIndex = selectedClients.value.indexOf(clientId);
        if (revertIndex !== -1) selectedClients.value.splice(revertIndex, 1);
        logger.error('multiroom', 'Error adding client to zone', error);
      }
    }
  } else {
    // Prevent removing if it would drop below 2 clients
    if (selectedClients.value.length <= 2) return;

    // Removing client — optimistic update before await to avoid stale index
    selectedClients.value.splice(index, 1);
    if (props.groupId) {
      try {
        const response = await multiroomStore.removeClientFromZone(props.groupId, clientId);
        // Watcher syncs final state from WebSocket
        if (response.message && response.message.includes('deleted')) {
          emit('back');
        }
      } catch (error) {
        // Revert optimistic update
        selectedClients.value.push(clientId);
        logger.error('multiroom', 'Error removing client from zone', error);
      }
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
    logger.error('multiroom', 'Error saving zone name', error);
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

// Sync selectedClients when zone membership changes via WebSocket
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
    logger.error('multiroom', 'Error creating zone', error);
  } finally {
    saving.value = false;
  }
}

const deleteLabel = computed(() => {
  if (deleteState.value === 'confirming') return t('equalizer.zones.confirmDeleteZone');
  if (deleteState.value === 'deleting') return t('equalizer.zones.deletingZone');
  return t('equalizer.zones.deleteZone');
});

async function handleDelete() {
  if (!props.groupId) return;

  if (deleteState.value === 'idle') {
    deleteState.value = 'confirming';
    return;
  }

  deleteState.value = 'deleting';
  try {
    await multiroomStore.deleteZone(props.groupId);
    emit('back');
  } catch (error) {
    logger.error('multiroom', 'Error deleting zone', error);
    deleteState.value = 'idle';
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

.zone-hint {
  color: var(--color-text-secondary);
}

/* Sticky action button */
.action-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .clients-list {
    grid-template-columns: 1fr;
  }
}
</style>
