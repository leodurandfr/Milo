<!-- frontend/src/components/settings/categories/multiroom/ZoneEdit.vue -->
<!-- Form for creating or editing a multiroom zone -->
<template>
  <SectionStack>
    <!-- Zone Name Input -->
    <SectionCard :title="t('equalizer.zones.zoneName')">
      <InputText
        v-model="zoneName"
        :placeholder="t('equalizer.zones.zoneNamePlaceholder')"
        :maxlength="24"
        @blur="saveZoneName"
      />
    </SectionCard>

    <!-- Client Selection -->
    <SectionCard :title="t('equalizer.zones.selectClients')" :description="t('equalizer.zones.minimumClients')">      <div class="clients-list">
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

    </SectionCard>

    <!-- What the server refused (a name, a member, the creation or the
         deletion) and the Create button, in one sticky footer: two at bottom 0
         would overlap. -->
    <div class="sticky-footer">
      <SettingsRefusal
        :detail="refusal"
        :kind="refusalMessage === 'settings.writeDeferred' ? 'warning' : 'error'"
      >{{ t(refusalMessage) }}</SettingsRefusal>
      <Button
        v-if="!groupId && selectedClients.length >= 2"
        variant="brand"
        size="medium"
        class="action-button" floating
        :disabled="!zoneName.trim()"
        :loading="saving"
        @click="handleCreate"
      >
        {{ t('equalizer.zones.createZone') }}
      </Button>
    </div>

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
  </SectionStack>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { logger } from '@/services/logger';
import { RECORDED_NOT_APPLIED } from '@/constants/multiroomWrites';
import Button from '@/components/ui/Button.vue';
import InputText from '@/components/ui/InputText.vue';
import SystemListItem from '@/components/settings/categories/multiroom/SystemListItem.vue';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import SettingsRefusal from '@/components/settings/SettingsRefusal.vue';

const props = defineProps({
  // Group ID if editing an existing zone, null for creating new
  groupId: {
    type: String,
    default: null
  }
});

const emit = defineEmits(['back']);

const { t } = useI18n();
const multiroomStore = useMultiroomStore();
const saving = ref(false);
const deleteState = ref('idle'); // 'idle' | 'confirming' | 'deleting'
const zoneName = ref('');
const originalZoneName = ref('');
const selectedClients = ref([]);
// The server's detail for the last write it refused, and the sentence that says
// which kind it was: an edit put back, a member recorded whose speaker has not
// taken the zone's EQ yet, or an action that did not happen.
const refusal = ref(null);
const refusalMessage = ref('settings.writeRefused');

// Clears the last refusal before a write, and names the sentence for its own.
function expectRefusal(messageKey) {
  refusal.value = null;
  refusalMessage.value = messageKey;
}
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
      expectRefusal('settings.writeRefused');
      const { ok, status } = await multiroomStore.addClientToZone(props.groupId, clientId, { errorRef: refusal });
      // On success the watcher syncs the final state from the WebSocket.
      if (!ok) {
        // A 502 is a client that joined, whose speaker did not take the zone's
        // EQ yet: said as saved, not as refused.
        if (status === RECORDED_NOT_APPLIED) refusalMessage.value = 'settings.writeDeferred';
        // Show the store's membership, not the one before the tap: after a 502
        // the zone_changed event may already be in, so the watcher fires no more.
        selectedClients.value = [...(currentGroup.value?.client_ids || [])];
      }
    }
  } else {
    // Prevent removing if it would drop below 2 clients
    if (selectedClients.value.length <= 2) return;

    // Removing client — optimistic update before await to avoid stale index
    selectedClients.value.splice(index, 1);
    if (props.groupId) {
      try {
        expectRefusal('settings.writeRefused');
        const response = await multiroomStore.removeClientFromZone(props.groupId, clientId, { errorRef: refusal });
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
  // A zone always has a name: clearing the field restores it rather than
  // sending one the backend refuses.
  if (!newName) {
    zoneName.value = originalZoneName.value;
    return;
  }

  try {
    expectRefusal('settings.writeRefused');
    await multiroomStore.updateZone(props.groupId, { name: newName }, { errorRef: refusal });
    originalZoneName.value = newName;
  } catch (error) {
    if (zoneName.value?.trim() === newName) zoneName.value = originalZoneName.value;
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
  const name = zoneName.value.trim();
  if (!name || selectedClients.value.length < 2) return;

  saving.value = true;
  try {
    // Use multiroomStore directly for zone creation (consistent with edit operations)
    expectRefusal('settings.actionFailed');
    await multiroomStore.createZone(name, selectedClients.value, { errorRef: refusal });
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
    expectRefusal('settings.actionFailed');
    await multiroomStore.deleteZone(props.groupId, { errorRef: refusal });
    emit('back');
  } catch (error) {
    logger.error('multiroom', 'Error deleting zone', error);
    deleteState.value = 'idle';
  }
}
</script>

<style scoped>
.clients-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

/* Sticky footer: the refusal and the Create button (as in HardwareSettings) */
.sticky-footer {
  position: sticky;
  bottom: 0;
  z-index: 10;
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.sticky-footer:empty {
  display: none;
}

.action-button {
  width: 100%;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) and (max-width: 1023px) {
  .clients-list {
    grid-template-columns: 1fr;
  }
}
</style>
