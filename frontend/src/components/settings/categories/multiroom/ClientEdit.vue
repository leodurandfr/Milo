<!-- frontend/src/components/settings/categories/multiroom/ClientEdit.vue -->
<!-- Form for editing a single client's settings -->
<template>
  <div class="client-edit">
    <!-- Offline State -->
    <MessageContent
      v-if="isOffline"
      icon="multiroom"
      :title="$t('multiroom.speakerOffline', 'Speaker offline')"
      :subtitle="$t('multiroom.speakerOfflineDescription', 'This speaker is not connected. You can delete it if it is no longer in use.')"
      :cta-label="deleting ? $t('common.deleting', 'Deleting...') : $t('multiroom.deleteSpeaker', 'Delete speaker')"
      cta-variant="important"
      :cta-click="handleDelete"
    />

    <!-- Online State - Settings -->
    <template v-else>
      <!-- Speaker Name Input -->
      <section class="settings-section">
        <div class="section-group">
          <h2 class="heading-2">{{ $t('multiroom.speakerName', 'Speaker Name') }}</h2>
          <InputText v-model="clientName" :placeholder="client?.host" size="medium" :maxlength="50"
            @blur="saveClientName" />
        </div>
      </section>

      <!-- Speaker Type Selection -->
      <section class="settings-section">
        <div class="section-group">
          <h2 class="heading-2">{{ $t('multiroom.speakerType', 'Speaker Type') }}</h2>
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
                {{ $t('multiroom.crossover.subwooferNotInZone', 'Add this subwoofer to a zone to enable automatic crossover management. Lowpass (subwoofer) and highpass (other speakers) filters will be applied automatically.') }}
              </p>
            </template>

            <!-- Case 2: Subwoofer in zone -->
            <template v-else-if="isSubwoofer && isInZone">
              <h3 class="info-title text-mono">{{ $t('multiroom.crossover.lowpassActive', 'Lowpass filter active') }}</h3>
              <p class="text-mono">{{ $t('multiroom.crossover.lowpassDescription', 'This subwoofer only receives bass frequencies below the crossover frequency.') }}</p>
              <div class="crossover-frequency">
                <span class="info-label text-mono">{{ $t('multiroom.crossover.crossoverFrequency', 'Crossover frequency:') }}</span>
                <span class="crossover-value text-mono">{{ zoneCrossoverFrequency }} Hz</span>
              </div>
              <p class="text-mono">{{ $t('multiroom.crossover.highpassOnOthers', 'A highpass filter is applied to other speakers in the zone to remove bass (handled by this subwoofer).') }}</p>
              <p class="text-mono text-warning">{{ $t('multiroom.crossover.disablePhysicalCrossover', "Set your subwoofer's physical crossover to bypass/LFE to avoid filter stacking.") }}</p>
            </template>

            <!-- Case 3: Non-subwoofer in zone with subwoofer -->
            <template v-else-if="!isSubwoofer && isInZone && zoneHasSubwoofer">
              <h3 class="info-title text-mono">{{ $t('multiroom.crossover.highpassActive', 'Highpass filter active') }}</h3>
              <p class="text-mono">{{ $t('multiroom.crossover.highpassDescription', { freq: zoneCrossoverFrequency }, `Bass frequencies below ${zoneCrossoverFrequency} Hz are removed from this speaker and handled by the subwoofer in the zone.`) }}</p>
              <div class="crossover-frequency">
                <span class="info-label text-mono">{{ $t('multiroom.crossover.crossoverFrequency', 'Crossover frequency:') }}</span>
                <span class="crossover-value text-mono">{{ zoneCrossoverFrequency }} Hz</span>
              </div>
            </template>
          </div>
        </div>

      </section>

      <!-- Client Info -->
      <section class="settings-section">
        <div class="section-group">
          <h2 class="heading-2">{{ $t('multiroom.speakerInfo', 'Speaker Info') }}</h2>
          <div class="info-grid">
            <div class="info-item">
              <span class="info-label text-mono">{{ $t('clientDetails.hostname', 'Hostname') }}</span>
              <span class="info-value text-mono">{{ client?.host }}</span>
            </div>
            <div class="info-item">
              <span class="info-label text-mono">{{ $t('clientDetails.ipAddress', 'IP Address') }}</span>
              <span class="info-value text-mono">{{ client?.ip || 'Unknown' }}</span>
            </div>
          </div>
        </div>
      </section>
    </template>

  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useClientRegistryStore } from '@/stores/clientRegistryStore';
import { useDspStore } from '@/stores/dspStore';
import InputText from '@/components/ui/InputText.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const props = defineProps({
  clientId: {
    type: String,
    required: true
  }
});

const emit = defineEmits(['back']);

const { t } = useI18n();
const multiroomStore = useMultiroomStore();
const clientRegistryStore = useClientRegistryStore();
const dspStore = useDspStore();

const clientName = ref('');
const originalClientName = ref('');
const selectedSpeakerType = ref('bookshelf');
const zoneCrossoverFrequency = ref(80);
const deleting = ref(false);

// Find client by ID
const client = computed(() =>
  multiroomStore.clients.find(c => c.id === props.clientId)
);

// Check if client is offline
const isOffline = computed(() => {
  return client.value ? !client.value.available : true;
});

// Check if client is in a zone
const clientZone = computed(() => {
  if (!client.value?.dsp_id) return null;
  return dspStore.getZoneGroup(client.value.dsp_id);
});

const isInZone = computed(() => !!clientZone.value);

// Check if current speaker type is subwoofer
const isSubwoofer = computed(() => selectedSpeakerType.value === 'subwoofer');

// Check if zone contains a subwoofer
const zoneHasSubwoofer = computed(() => {
  if (!clientZone.value?.id) return false;
  return dspStore.hasSubwooferInZone(clientZone.value.id);
});

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

async function selectSpeakerType(type) {
  if (type === selectedSpeakerType.value) return;

  selectedSpeakerType.value = type;

  // Save immediately if client has dsp_id
  if (client.value?.dsp_id) {
    try {
      await dspStore.setClientSpeakerType(client.value.dsp_id, type);
    } catch (error) {
      console.error('Error saving speaker type:', error);
    }
  }
}

async function saveClientName() {
  const newName = clientName.value?.trim();
  if (!newName || newName === originalClientName.value) return;

  try {
    await clientRegistryStore.updateClientName(props.clientId, newName);
    originalClientName.value = newName;
  } catch (error) {
    console.error('Error saving client name:', error);
  }
}

async function handleDelete() {
  if (deleting.value || !client.value?.dsp_id) return;

  deleting.value = true;
  try {
    const success = await clientRegistryStore.deleteClient(client.value.dsp_id);
    if (success) {
      emit('back');
    }
  } catch (error) {
    console.error('Error deleting client:', error);
  } finally {
    deleting.value = false;
  }
}

onMounted(async () => {
  if (client.value) {
    clientName.value = client.value.name || client.value.host;
    originalClientName.value = clientName.value;
    // Load current speaker type
    selectedSpeakerType.value = dspStore.getClientSpeakerType(client.value.dsp_id);

    // Load zone crossover frequency if client is in a zone
    if (clientZone.value) {
      zoneCrossoverFrequency.value = await dspStore.getZoneAutoCrossover(clientZone.value.id);
    }
  }
});
</script>

<style scoped>
.client-edit {
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
  gap: var(--space-03);
}

.crossover-info .info-title {
  color: var(--color-text);
  margin: 0;
}

.crossover-info p {
  color: var(--color-text-secondary);
  margin: 0;
}

.crossover-info .text-warning {
  color: var(--color-brand);
}

.crossover-frequency {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-03) var(--space-04);
  background: var(--color-background-neutral);
  border-radius: var(--radius-03);
}

.crossover-value {
  color: var(--color-text);
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
  .settings-section {
    border-radius: var(--radius-05);
  }

  .speaker-types {
    grid-template-columns: 1fr;
  }

  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
