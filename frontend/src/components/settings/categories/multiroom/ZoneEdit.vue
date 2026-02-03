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
        <div class="clients-list">
          <ListItemButton
            v-for="target in availableTargets"
            :key="target.id"
            variant="background"
            action="toggle"
            icon-variant="standard"
            :toggle-variant="target.online ? 'primary' : 'secondary'"
            :model-value="selectedClients.includes(target.id)"
            @click="toggleClient(target.id)"
          >
            <template #icon>
              <div class="client-icon" :class="{ 'is-offline': !target.online }">
                <SvgIcon :name="getSpeakerIcon(target.id)" :size="28" />
              </div>
            </template>
            <template #title>
              <div class="client-title">
                <span :class="{ 'text-secondary': !target.online }">{{ target.name }}</span>
                <span v-if="!target.online" class="text-mono-small client-title__status">
                  {{ $t('multiroom.offline') }}
                </span>
                <span v-else class="text-mono-small client-title__type">
                  {{ getSpeakerTypeLabel(target.id) }}
                </span>
              </div>
            </template>
          </ListItemButton>
        </div>

        <!-- Crossover frequency (edit mode with subwoofer only) -->
        <template v-if="groupId && currentGroup?.has_subwoofer">
          <div class="section-divider"></div>
          <div class="form-group">
            <label class="text-mono">{{ $t('multiroom.crossover.crossoverFrequency') }}</label>
            <RangeSlider v-model="crossoverFrequency" :min="40" :max="200" :step="5" value-unit="Hz"
              @change="handleCrossoverChange" />
          </div>
        </template>
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
import { useI18n } from '@/services/i18n';
import Button from '@/components/ui/Button.vue';
import InputText from '@/components/ui/InputText.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const props = defineProps({
  // Group ID if editing an existing zone, null for creating new
  groupId: {
    type: String,
    default: null
  }
});

const emit = defineEmits(['back', 'saved']);

const { t } = useI18n();
const dspStore = useDspStore();
const multiroomStore = useMultiroomStore();
const saving = ref(false);
const deleting = ref(false);
const zoneName = ref('');
const originalZoneName = ref('');
const selectedClients = ref([]);
const crossoverFrequency = ref(80);

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

// Get speaker type label for display
function getSpeakerTypeLabel(macId) {
  const speakerType = dspStore.getClientSpeakerType(macId);
  return t(`multiroom.speakerTypes.${speakerType}`);
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

// Update crossover frequency on the zone via API
async function handleCrossoverChange(frequency) {
  if (!props.groupId) return;
  try {
    await dspStore.setZoneCrossoverFrequency(props.groupId, frequency);
  } catch (error) {
    console.error('Error updating crossover frequency:', error);
  }
}

// Initialize state when mounted
onMounted(async () => {
  if (currentGroup.value) {
    // Editing existing zone
    zoneName.value = currentGroup.value.name || '';
    originalZoneName.value = zoneName.value;
    selectedClients.value = [...(currentGroup.value.client_ids || [])];
    crossoverFrequency.value = currentGroup.value.crossover_frequency || 80;
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

// Sync crossover frequency when changed externally (WebSocket)
watch(
  () => currentGroup.value?.crossover_frequency,
  (newFreq) => {
    if (newFreq != null) {
      crossoverFrequency.value = newFreq;
    }
  }
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
  gap: var(--space-05);
}

.clients-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

/* Client icon */
.client-icon {
  display: flex;
  align-items: center;
  justify-content: center;
}

.client-icon.is-offline {
  opacity: 0.4;
}

/* Client title with type/status */
.client-title {
  display: flex;
  flex-direction: column;
}

.client-title__type,
.client-title__status {
  color: var(--color-text-secondary);
}

.text-secondary {
  color: var(--color-text-secondary);
}

.section-divider {
  height: 1px;
  background: var(--color-border);
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.form-group label {
  color: var(--color-text-secondary);
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
