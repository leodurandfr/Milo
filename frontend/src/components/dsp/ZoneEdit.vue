<!-- frontend/src/components/dsp/ZoneEdit.vue -->
<!-- Form for creating or editing a DSP zone -->
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
          :maxlength="30"
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
          <label
            v-for="target in availableTargets"
            :key="target.id"
            class="client-item"
            :class="{
              disabled: !target.available || isClientInOtherZone(target.id),
              selected: selectedClients.includes(target.id),
              'with-rename': enableClientRenaming && selectedClients.includes(target.id)
            }"
          >
            <div class="radio-indicator" :class="{ checked: selectedClients.includes(target.id) }">
              <span class="radio-dot"></span>
            </div>
            <input
              type="checkbox"
              :checked="selectedClients.includes(target.id)"
              :disabled="!target.available || isClientInOtherZone(target.id)"
              @change="toggleClient(target.id)"
              class="hidden-checkbox"
            />

            <!-- When renaming is enabled and client is selected: show hostname + input -->
            <template v-if="enableClientRenaming && selectedClients.includes(target.id)">
              <div class="client-rename-row">
                <span class="client-hostname text-mono">{{ target.host || target.id }}</span>
                <span class="client-arrow">→</span>
                <InputText
                  v-model="clientNames[target.id]"
                  :placeholder="target.host || target.id"
                  size="small"
                  :maxlength="50"
                  class="client-name-input"
                  @click.stop
                />
              </div>
            </template>

            <!-- Default: show client name -->
            <template v-else>
              <span class="client-name">{{ target.name }}</span>
            </template>

            <span v-if="isClientInOtherZone(target.id)" class="badge badge-disabled">
              {{ getOtherZoneName(target.id) }}
            </span>
            <span v-if="!target.available" class="badge badge-offline">
              {{ $t('dsp.linkedClients.offline', 'Offline') }}
            </span>
          </label>
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
      variant="important"
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
import { ref, computed, onMounted } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import Button from '@/components/ui/Button.vue';
import InputText from '@/components/ui/InputText.vue';

const props = defineProps({
  // Group ID if editing an existing zone, null for creating new
  groupId: {
    type: String,
    default: null
  },
  // Enable client renaming in zone edit (used when called from MultiroomSettings)
  enableClientRenaming: {
    type: Boolean,
    default: false
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
// Client names for renaming (dsp_id -> display name)
const clientNames = ref({});

// Get available targets from store
const availableTargets = computed(() => dspStore.availableTargets);

// Find the current group being edited
const currentGroup = computed(() => {
  if (!props.groupId) return null;
  return dspStore.linkedGroups.find(g => g.id === props.groupId);
});

// Check if a client is in a different zone (not the one being edited)
function isClientInOtherZone(clientId) {
  for (const group of dspStore.linkedGroups) {
    // Skip the group being edited
    if (props.groupId && group.id === props.groupId) continue;

    if (group.client_ids && group.client_ids.includes(clientId)) {
      return true;
    }
  }
  return false;
}

// Get name of the zone that contains a client
function getOtherZoneName(clientId) {
  for (const group of dspStore.linkedGroups) {
    if (props.groupId && group.id === props.groupId) continue;

    if (group.client_ids && group.client_ids.includes(clientId)) {
      // Return custom name or generate from client names
      if (group.name) return group.name;

      const clientNames = group.client_ids
        .map(id => {
          const target = availableTargets.value.find(t => t.id === id);
          return target ? target.name : id;
        })
        .join(' + ');
      return clientNames;
    }
  }
  return '';
}

// Toggle client selection
async function toggleClient(clientId) {
  const index = selectedClients.value.indexOf(clientId);
  if (index === -1) {
    selectedClients.value.push(clientId);
  } else {
    selectedClients.value.splice(index, 1);
  }

  // Auto-save when editing existing zone (and still has 2+ clients)
  if (props.groupId && selectedClients.value.length >= 2) {
    try {
      await dspStore.linkClients(selectedClients.value, null, zoneName.value || null);
    } catch (error) {
      console.error('Error updating zone clients:', error);
    }
  }
}

// Save zone name on blur (only when editing existing zone)
async function saveZoneName() {
  if (!props.groupId) return;
  const newName = zoneName.value?.trim() || '';
  if (newName === originalZoneName.value) return;

  try {
    await dspStore.updateZoneName(props.groupId, newName);
    originalZoneName.value = newName;
  } catch (error) {
    console.error('Error saving zone name:', error);
  }
}

// Get the hostname for a client (for display)
function getClientHostname(dspId) {
  const target = availableTargets.value.find(t => t.id === dspId);
  return target?.host || dspId;
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

  // Initialize client names from multiroomStore
  if (props.enableClientRenaming) {
    multiroomStore.clients.forEach(client => {
      clientNames.value[client.dsp_id] = client.name || client.host;
    });
  }
});

// Create new zone (only used when groupId is null)
async function handleCreate() {
  if (selectedClients.value.length < 2) return;

  saving.value = true;
  try {
    // Save client names first if renaming is enabled
    if (props.enableClientRenaming) {
      for (const dspId of selectedClients.value) {
        const newName = clientNames.value[dspId]?.trim();
        if (newName) {
          const client = multiroomStore.clients.find(c => c.dsp_id === dspId);
          if (client && client.name !== newName) {
            await multiroomStore.updateClientName(client.id, newName);
          }
        }
      }
    }

    // Create the zone
    await dspStore.linkClients(selectedClients.value, null, zoneName.value || null);

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
    await dspStore.deleteZone(props.groupId);
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
  font-size: 13px;
  line-height: 1.5;
  margin: 0;
}

.clients-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  max-height: 300px;
  overflow-y: auto;
}

.client-item {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03);
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  cursor: pointer;
  transition: background var(--transition-fast), border-color var(--transition-fast);
  border: 2px solid transparent;
}

.client-item:hover:not(.disabled) {
  background: var(--color-background-medium);
}

.client-item.selected {
  border-color: var(--color-brand);
  background: var(--color-background-medium);
}

.client-item.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.hidden-checkbox {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

/* Custom radio indicator */
.radio-indicator {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 2px solid var(--color-text-light);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: border-color var(--transition-fast), background var(--transition-fast);
}

.radio-indicator.checked {
  border-color: var(--color-brand);
  background: var(--color-brand);
}

.radio-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: transparent;
  transition: background var(--transition-fast);
}

.radio-indicator.checked .radio-dot {
  background: var(--color-text-inverse);
}

.client-name {
  flex: 1;
  font-size: 14px;
  color: var(--color-text);
}

/* Client rename row (when enableClientRenaming is true) */
.client-rename-row {
  flex: 1;
  display: flex;
  align-items: center;
  gap: var(--space-02);
  min-width: 0;
}

.client-hostname {
  color: var(--color-text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.client-arrow {
  color: var(--color-text-light);
  font-size: 12px;
}

.client-name-input {
  flex: 1;
  min-width: 0;
}

.client-item.with-rename {
  flex-wrap: wrap;
}

.badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-02);
}

.badge-disabled {
  background: var(--color-background-medium);
  color: var(--color-text-light);
}

.badge-offline {
  background: var(--color-background-medium);
  color: var(--color-text-light);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }
}
</style>
