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
          <ListItemButton
            v-for="target in availableTargets"
            :key="target.id"
            variant="background"
            action="toggle"
            icon-variant="standard"
            :model-value="selectedClients.includes(target.id)"
            :disabled="!target.available || isClientInOtherZone(target.id)"
            @click="toggleClient(target.id)"
          >
            <template #icon>
              <SvgIcon :name="getSpeakerIcon(target.id)" :size="28" />
            </template>
            <template #title>
              <div class="client-title">
                <span>{{ target.name }}</span>
                <span v-if="isClientInOtherZone(target.id)" class="badge">{{ getOtherZoneName(target.id) }}</span>
                <span v-if="!target.available" class="badge">{{ $t('dsp.linkedClients.offline', 'Offline') }}</span>
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
const saving = ref(false);
const deleting = ref(false);
const zoneName = ref('');
const originalZoneName = ref('');
const selectedClients = ref([]);

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

      const names = group.client_ids
        .map(id => {
          const target = availableTargets.value.find(t => t.id === id);
          return target ? target.name : id;
        })
        .join(' + ');
      return names;
    }
  }
  return '';
}

// Get speaker icon name based on type
function getSpeakerIcon(dspId) {
  const speakerType = dspStore.getClientSpeakerType(dspId);
  const iconMap = {
    satellite: 'speakerSatellite',
    bookshelf: 'speakerShelf',
    tower: 'speakerColumn',
    subwoofer: 'speakerSub'
  };
  return iconMap[speakerType] || 'speakerShelf';
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

// Create new zone (only used when groupId is null)
async function handleCreate() {
  if (selectedClients.value.length < 2) return;

  saving.value = true;
  try {
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
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

.client-title {
  display: flex;
  align-items: center;
  gap: var(--space-02);
}

.badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: var(--radius-02);
  background: var(--color-background-medium);
  color: var(--color-text-light);
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
