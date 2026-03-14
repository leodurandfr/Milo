<!-- frontend/src/components/settings/categories/MacSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Latency Section -->
    <SettingsSection :title="t('macSettings.latency')">
      <!-- Target Latency Slider (5-500ms) -->
      <SettingItem :label="t('macSettings.targetLatency')">
        <RangeSlider
          v-model="config.target_latency_ms"
          :min="5"
          :max="500"
          :step="5"
          value-unit="ms"
        />
      </SettingItem>

      <!-- Latency Profile ButtonGroup -->
      <SettingItem :label="t('macSettings.latencyProfile')">
        <ButtonGroup
          :model-value="config.latency_profile"
          :options="profileOptions"
          mobile-layout="column"
          @change="handleProfileChange"
        />
      </SettingItem>

      <!-- Frame Length ButtonGroup -->
      <SettingItem :label="t('macSettings.frameLength')">
        <ButtonGroup
          :model-value="config.frame_length_ms"
          :options="frameLengthOptions"
          @change="handleFrameLengthChange"
        />
      </SettingItem>

      <!-- Warning message -->
      <p class="warning-text text-mono-small">
        {{ t('macSettings.warning') }}
      </p>
    </SettingsSection>

    <!-- Apply Button (requires service restart) -->
    <Button
      v-if="hasChanges"
      variant="brand"
      size="medium"
      class="apply-button-sticky"
      :disabled="isApplying"
      @click="applyChanges"
    >
      {{ isApplying ? t('macSettings.restarting') : t('macSettings.apply') }}
    </Button>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsStore } from '@/stores/settingsStore';
import axios from 'axios';
import Button from '@/components/ui/Button.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';

const { t } = useI18n();
const settingsStore = useSettingsStore();

// Local config for immediate UI responsiveness
const config = ref({
  target_latency_ms: 10,
  latency_profile: 'responsive',
  frame_length_ms: 4
});

// Original config to detect changes (synced from store)
const originalConfig = ref({
  target_latency_ms: 10,
  latency_profile: 'responsive',
  frame_length_ms: 4
});

const isApplying = ref(false);

// Profile options for ButtonGroup
const profileOptions = computed(() => [
  { label: t('macSettings.profiles.responsive'), value: 'responsive' },
  { label: t('macSettings.profiles.gradual'), value: 'gradual' },
  { label: t('macSettings.profiles.intact'), value: 'intact' }
]);

// Frame length options for ButtonGroup
const frameLengthOptions = [
  { label: '2ms', value: 2 },
  { label: '4ms', value: 4 },
  { label: '7ms', value: 7 },
  { label: '8ms', value: 8 },
  { label: '12ms', value: 12 }
];

// Check if config has changed from original
const hasChanges = computed(() => {
  return (
    config.value.target_latency_ms !== originalConfig.value.target_latency_ms ||
    config.value.latency_profile !== originalConfig.value.latency_profile ||
    config.value.frame_length_ms !== originalConfig.value.frame_length_ms
  );
});

// Sync local refs with the store
function syncFromStore() {
  const s = settingsStore.macRocSettings;
  config.value = {
    target_latency_ms: s.target_latency_ms,
    latency_profile: s.latency_profile,
    frame_length_ms: s.frame_length_ms
  };
  originalConfig.value = { ...config.value };
}

// Handle profile change
function handleProfileChange(value) {
  config.value.latency_profile = value;
}

// Handle frame length change
function handleFrameLengthChange(value) {
  config.value.frame_length_ms = value;
}

// Apply changes and restart service
async function applyChanges() {
  if (isApplying.value) return;

  try {
    isApplying.value = true;

    const response = await axios.put('/api/settings/mac-roc', {
      target_latency_ms: config.value.target_latency_ms,
      latency_profile: config.value.latency_profile,
      frame_length_ms: config.value.frame_length_ms
    });

    if (response.data.status === 'success') {
      originalConfig.value = { ...config.value };
    } else {
      console.error('Failed to apply Mac ROC config:', response.data.message);
    }
  } catch (error) {
    console.error('Error applying Mac ROC config:', error);
  } finally {
    isApplying.value = false;
  }
}

// Sync local config when store changes (e.g., WS event from another device)
watch(() => settingsStore.macRocSettings, syncFromStore, { deep: true });

onMounted(() => {
  syncFromStore();
});
</script>

<style scoped>
.warning-text {
  color: var(--color-text-secondary);
  margin-top: var(--space-02);
}

.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}
</style>
