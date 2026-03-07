<!-- frontend/src/components/settings/categories/multiroom/ClientEdit.vue -->
<!-- Form for editing a single client's settings -->
<template>
  <div class="client-edit">
    <!-- Offline State -->
    <MessageContent
      v-if="isOffline"
      icon="multiroom"
      :title="t('multiroom.speakerOffline', { name: clientDisplayName })"
      :subtitle="t('multiroom.speakerOfflineDescription', { ip: client?.ip || 'Unknown' })"
      :cta-label="deleting ? t('common.deleting') : t('multiroom.deleteSpeaker')"
      cta-variant="important"
      :cta-click="handleDelete"
    />

    <!-- Online State - Settings -->
    <template v-else>
      <!-- Speaker Name Input -->
      <SettingsSection :title="t('multiroom.speakerName', 'Speaker Name')">
        <InputText v-model="clientName" :placeholder="client?.host" size="medium" :maxlength="16"
          @blur="saveClientName" />
      </SettingsSection>

      <!-- Speaker Type Selection -->
      <SettingsSection :title="t('multiroom.speakerType', 'Speaker Type')">
        <div class="speaker-types">
          <ListItemButton v-for="type in speakerTypes" :key="type.value" :title="type.label" variant="background"
            action="radio" icon-variant="standard" :model-value="selectedSpeakerType === type.value"
            @click="selectSpeakerType(type.value)">
            <template #icon>
              <SvgIcon :name="type.icon" :size="28" />
            </template>
          </ListItemButton>
        </div>

        <!-- Crossover Info Section -->
        <div v-if="showCrossoverInfo" class="crossover-info">
          <!-- Case 1: Subwoofer not in zone -->
          <template v-if="isSubwoofer && !isInZone">
            <p class="text-mono">
              {{ t('multiroom.crossover.subwooferNotInZone') }}
            </p>
          </template>

          <!-- Case 2: Subwoofer in zone -->
          <template v-else-if="isSubwoofer && isInZone">
            <h3 class="info-title heading-4">{{ t('multiroom.crossover.lowpassActive') }}</h3>
            <SettingItem :label="t('multiroom.crossover.crossoverFrequency')">
              <RangeSlider v-model="crossoverFrequency" :min="40" :max="200" :step="5" value-unit="Hz"
                @change="handleCrossoverChange" />
            </SettingItem>
            <p class="crossover-warning text-mono">{{ t('multiroom.crossover.disablePhysicalCrossover') }}</p>
          </template>

          <!-- Case 3: Non-subwoofer in zone with subwoofer -->
          <template v-else-if="!isSubwoofer && isInZone && zoneHasSubwoofer">
            <h3 class="info-title heading-4">{{ t('multiroom.crossover.highpassActive') }}</h3>
            <p class="text-mono">{{ t('multiroom.crossover.highpassDescription', { freq: zoneCrossoverFrequency }) }}</p>
          </template>
        </div>
      </SettingsSection>

      <!-- Client Info -->
      <SettingsSection :title="t('multiroom.speakerInfo', 'Speaker Info')">
        <div class="info-grid">
          <div class="info-item">
            <span class="info-label text-mono">{{ t('clientDetails.hostname', 'Hostname') }}</span>
            <span class="info-value text-mono">{{ client?.host }}</span>
          </div>
          <div class="info-item">
            <span class="info-label text-mono">{{ t('clientDetails.ipAddress', 'IP Address') }}</span>
            <span class="info-value text-mono">{{ client?.ip || 'Unknown' }}</span>
          </div>
        </div>
      </SettingsSection>
    </template>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSnapcastStore } from '@/stores/snapcastStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useEqualizerStore } from '@/stores/equalizerStore';
import InputText from '@/components/ui/InputText.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';

const props = defineProps({
  macId: {
    type: String,
    required: true
  }
});

const emit = defineEmits(['back']);

const { t } = useI18n();
const snapcastStore = useSnapcastStore();
const multiroomClientStore = useMultiroomStore();
const equalizerStore = useEqualizerStore();

const clientName = ref('');
const originalClientName = ref('');
const selectedSpeakerType = ref('bookshelf');
const deleting = ref(false);
const crossoverFrequency = ref(80);

// Find client by mac_id
const client = computed(() =>
  snapcastStore.clients.find(c => c.mac_id === props.macId)
);

// Check if client is offline
const isOffline = computed(() => {
  return client.value ? !client.value.online : true;
});

// Display name for offline message
const clientDisplayName = computed(() =>
  client.value?.name || client.value?.host || 'Unknown'
);

// Check if client is in a zone
const clientZone = computed(() => {
  return equalizerStore.getZoneGroup(props.macId);
});

const isInZone = computed(() => !!clientZone.value);

// Check if current speaker type is subwoofer
const isSubwoofer = computed(() => selectedSpeakerType.value === 'subwoofer');

// Check if zone contains a subwoofer
const zoneHasSubwoofer = computed(() => {
  if (!clientZone.value?.id) return false;
  return multiroomClientStore.hasOnlineSubwoofer(clientZone.value.id);
});

// Get zone crossover frequency for display (non-subwoofer clients)
const zoneCrossoverFrequency = computed(() => {
  return clientZone.value?.crossover_frequency || 80;
});

// Sync crossover frequency ref from zone data
watch(
  () => clientZone.value?.crossover_frequency,
  (newFreq) => {
    if (newFreq != null) {
      crossoverFrequency.value = newFreq;
    }
  },
  { immediate: true }
);

// Show crossover info when relevant
const showCrossoverInfo = computed(() => {
  // Always show for subwoofer (different message if not in zone)
  if (isSubwoofer.value) return true;
  // Show for non-subwoofer in zone with subwoofer
  if (isInZone.value && zoneHasSubwoofer.value) return true;
  return false;
});

// Speaker type options
const speakerTypes = computed(() => [
  { value: 'satellite', label: t('multiroom.speakerTypes.satellite', 'Satellite speakers'), icon: 'speakerSatellite' },
  { value: 'bookshelf', label: t('multiroom.speakerTypes.bookshelf', 'Bookshelf speakers'), icon: 'speakerShelf' },
  { value: 'tower', label: t('multiroom.speakerTypes.tower', 'Tower speakers'), icon: 'speakerColumn' },
  { value: 'subwoofer', label: t('multiroom.speakerTypes.subwoofer', 'Subwoofer'), icon: 'speakerSub' }
]);

async function handleCrossoverChange(frequency) {
  if (!clientZone.value?.id) return;
  try {
    await equalizerStore.setZoneCrossoverFrequency(clientZone.value.id, frequency);
  } catch (error) {
    console.error('Error updating crossover frequency:', error);
  }
}

async function selectSpeakerType(type) {
  if (type === selectedSpeakerType.value) return;

  selectedSpeakerType.value = type;

  if (type === 'subwoofer' && clientZone.value?.crossover_frequency != null) {
    crossoverFrequency.value = clientZone.value.crossover_frequency;
  }

  // Save immediately via PATCH /api/multiroom/clients/{mac_id}
  try {
    await multiroomClientStore.updateClient(props.macId, { speaker_type: type });
  } catch (error) {
    console.error('Error saving speaker type:', error);
  }
}

async function saveClientName() {
  const newName = clientName.value?.trim();
  if (!newName || newName === originalClientName.value) return;

  try {
    await multiroomClientStore.updateClient(props.macId, { name: newName });
    originalClientName.value = newName;
  } catch (error) {
    console.error('Error saving client name:', error);
  }
}

async function handleDelete() {
  if (deleting.value) return;

  deleting.value = true;
  try {
    const success = await multiroomClientStore.deleteClient(props.macId);
    if (success) {
      emit('back');
    }
  } catch (error) {
    console.error('Error deleting client:', error);
  } finally {
    deleting.value = false;
  }
}

onMounted(() => {
  if (client.value) {
    clientName.value = client.value.name || client.value.host;
    originalClientName.value = clientName.value;
    // Load current speaker type from client data or equalizerStore
    selectedSpeakerType.value = client.value.speaker_type || equalizerStore.getClientSpeakerType(props.macId);
  }
});
</script>

<style scoped>
.client-edit {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.speaker-types {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

.crossover-info {
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  padding: var(--space-04);
  margin-top: var(--space-03);
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.crossover-info .info-title {
  color: var(--color-text);
  margin: 0;
}

.crossover-info :deep(.slider-container.horizontal .range-track) {
  background: linear-gradient(to right,
    var(--slider-accent) 0%,
    var(--slider-accent) var(--progress),
    var(--color-background-neutral) var(--progress),
    var(--color-background-neutral) 100%);
}

.crossover-info p {
  color: var(--color-text-secondary);
  margin: 0;
}
.crossover-warning {
  padding-top: var(--space-01);
}

.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
}

.info-label {
  color: var(--color-text-secondary);
}

.info-value {
  color: var(--color-text);
  text-align: right;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .speaker-types {
    grid-template-columns: 1fr;
  }

  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
