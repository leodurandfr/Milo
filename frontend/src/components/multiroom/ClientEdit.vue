<!-- frontend/src/components/multiroom/ClientEdit.vue -->
<!-- Form for editing a single client's settings -->
<template>
  <div class="client-edit">
    <!-- Speaker Name Input -->
    <section class="settings-section">
      <div class="section-group">
        <h2 class="heading-2">{{ $t('multiroom.speakerName', 'Speaker Name') }}</h2>
        <InputText
          v-model="clientName"
          :placeholder="client?.host"
          size="medium"
          :maxlength="50"
          @blur="saveClientName"
        />
      </div>
    </section>

    <!-- Speaker Type Selection -->
    <section class="settings-section">
      <div class="section-group">
        <h2 class="heading-2">{{ $t('multiroom.speakerType', 'Speaker Type') }}</h2>
        <div class="speaker-types">
          <ListItemButton
            v-for="type in speakerTypes"
            :key="type.value"
            :title="type.label"
            :variant="selectedSpeakerType === type.value ? 'active' : 'background'"
            action="radio"
            @click="selectSpeakerType(type.value)"
          />
        </div>

        <!-- Subwoofer Info (when subwoofer selected) -->
        <div v-if="selectedSpeakerType === 'subwoofer'" class="subwoofer-info">
          <p class="text-small">
            {{ $t('multiroom.subwooferAutoCrossover', 'Digital crossover filters will be automatically applied: lowpass on this subwoofer, highpass on other speakers in the zone.') }}
          </p>
          <p class="text-small">
            {{ $t('multiroom.subwooferDisablePhysical', "Set your subwoofer's crossover to maximum (LFE/Bypass) to avoid filter stacking.") }}
          </p>
          <div class="crossover-recommendation">
            <span class="text-small">{{ $t('multiroom.crossoverFrequency', 'Crossover frequency:') }}</span>
            <span class="crossover-value text-mono">{{ zoneCrossoverFrequency }} Hz</span>
          </div>
        </div>
      </div>

    </section>

    <!-- Client Info -->
    <section class="settings-section">
      <div class="section-group">
        <h2 class="heading-2">{{ $t('multiroom.speakerInfo', 'Speaker Info') }}</h2>
        <div class="client-info">
          <div class="info-row">
            <span class="info-label text-mono-small">Hostname</span>
            <span class="info-value text-mono">{{ client?.host }}</span>
          </div>
          <div class="info-row">
            <span class="info-label text-mono-small">{{ $t('info.ipAddress', 'IP Address') }}</span>
            <span class="info-value text-mono">{{ client?.ip || 'Unknown' }}</span>
          </div>
        </div>
      </div>
    </section>

  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useDspStore } from '@/stores/dspStore';
import InputText from '@/components/ui/InputText.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';

const props = defineProps({
  clientId: {
    type: String,
    required: true
  }
});

const emit = defineEmits(['back']);

const { t } = useI18n();
const multiroomStore = useMultiroomStore();
const dspStore = useDspStore();

const clientName = ref('');
const originalClientName = ref('');
const selectedSpeakerType = ref('bookshelf');
const zoneCrossoverFrequency = ref(80);

// Find client by ID
const client = computed(() =>
  multiroomStore.clients.find(c => c.id === props.clientId)
);

// Check if client is in a zone
const clientZone = computed(() => {
  if (!client.value?.dsp_id) return null;
  return dspStore.getZoneGroup(client.value.dsp_id);
});

const isInZone = computed(() => !!clientZone.value);

// Speaker type options
const speakerTypes = computed(() => [
  { value: 'satellite', label: t('multiroom.speakerTypes.satellite', 'Satellite speakers') },
  { value: 'bookshelf', label: t('multiroom.speakerTypes.bookshelf', 'Bookshelf speakers') },
  { value: 'tower', label: t('multiroom.speakerTypes.tower', 'Tower speakers') },
  { value: 'subwoofer', label: t('multiroom.speakerTypes.subwoofer', 'Subwoofer') }
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
    await multiroomStore.updateClientName(props.clientId, newName);
    originalClientName.value = newName;
  } catch (error) {
    console.error('Error saving client name:', error);
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
  gap: var(--space-02);
}

.subwoofer-info {
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  padding: var(--space-04);
  margin-top: var(--space-03);
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.subwoofer-info p {
  color: var(--color-text-secondary);
  margin: 0;
}

.crossover-recommendation {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.crossover-recommendation .text-small {
  color: var(--color-text-secondary);
}

.crossover-value {
  color: var(--color-text);
}

.client-info {
  display: flex;
  flex-direction: column;
  background: var(--color-background-strong);
  border-radius: var(--radius-04);
  overflow: hidden;
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-03) var(--space-04);
}

.info-row:not(:last-child) {
  border-bottom: 1px solid var(--color-border);
}

.info-label {
  color: var(--color-text-secondary);
}

.info-value {
  color: var(--color-text);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }
}
</style>
