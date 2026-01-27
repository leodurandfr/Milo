<!-- frontend/src/components/settings/categories/MacosSettings.vue -->
<template>
  <div class="settings-container">
    <!-- Latency Section -->
    <section class="settings-section">
      <div class="settings-group">
        <h2 class="heading-2">{{ t('macSettings.latency') }}</h2>

        <!-- Target Latency Slider (5-500ms) -->
        <div class="setting-item-container">
          <div class="setting-item-label text-mono">
            {{ t('macSettings.targetLatency') }}
          </div>
          <div class="setting-item-control">
            <RangeSlider
              v-model="config.target_latency_ms"
              :min="5"
              :max="500"
              :step="5"
              value-unit="ms"
              @input="onConfigChange"
            />
          </div>
        </div>

        <!-- Latency Profile ButtonGroup -->
        <div class="setting-item-container">
          <div class="setting-item-label text-mono">
            {{ t('macSettings.latencyProfile') }}
          </div>
          <div class="setting-item-control">
            <ButtonGroup
              :model-value="config.latency_profile"
              :options="profileOptions"
              mobile-layout="column"
              @change="handleProfileChange"
            />
          </div>
        </div>

        <!-- Frame Length ButtonGroup -->
        <div class="setting-item-container">
          <div class="setting-item-label text-mono">
            {{ t('macSettings.frameLength') }}
          </div>
          <div class="setting-item-control">
            <ButtonGroup
              :model-value="config.frame_length_ms"
              :options="frameLengthOptions"
              @change="handleFrameLengthChange"
            />
          </div>
        </div>

        <!-- Warning message -->
        <p class="warning-text text-mono-small">
          {{ t('macSettings.warning') }}
        </p>
      </div>
    </section>

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
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import axios from 'axios';
import Button from '@/components/ui/Button.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';

const { t } = useI18n();
const { on } = useWebSocket();

// Local config for immediate UI responsiveness
const config = ref({
  target_latency_ms: 200,
  latency_profile: 'responsive',
  frame_length_ms: 7
});

// Original config to detect changes
const originalConfig = ref({
  target_latency_ms: 200,
  latency_profile: 'responsive',
  frame_length_ms: 7
});

const isApplying = ref(false);
const isLoading = ref(true);

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

// Load config from API
async function loadConfig() {
  try {
    isLoading.value = true;
    const response = await axios.get('/api/settings/mac-roc');
    if (response.data.status === 'success' && response.data.config) {
      const apiConfig = response.data.config;
      config.value = {
        target_latency_ms: apiConfig.target_latency_ms ?? 200,
        latency_profile: apiConfig.latency_profile ?? 'responsive',
        frame_length_ms: apiConfig.frame_length_ms ?? 7
      };
      // Store original for change detection
      originalConfig.value = { ...config.value };
    }
  } catch (error) {
    console.error('Error loading Mac ROC config:', error);
  } finally {
    isLoading.value = false;
  }
}

// Handle profile change
function handleProfileChange(value) {
  config.value.latency_profile = value;
}

// Handle frame length change
function handleFrameLengthChange(value) {
  config.value.frame_length_ms = value;
}

// Called when slider changes (no-op, just for tracking)
function onConfigChange() {
  // Config is already updated via v-model
}

// Apply changes and restart service
async function applyChanges() {
  if (isApplying.value) return;

  try {
    isApplying.value = true;

    const response = await axios.post('/api/settings/mac-roc', {
      target_latency_ms: config.value.target_latency_ms,
      latency_profile: config.value.latency_profile,
      frame_length_ms: config.value.frame_length_ms
    });

    if (response.data.status === 'success') {
      // Update original config to match current (no more "changes")
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

// WebSocket listener for config changes from other sources
function handleConfigChanged(msg) {
  if (msg.data?.config) {
    const apiConfig = msg.data.config;
    config.value = {
      target_latency_ms: apiConfig.target_latency_ms ?? config.value.target_latency_ms,
      latency_profile: apiConfig.latency_profile ?? config.value.latency_profile,
      frame_length_ms: apiConfig.frame_length_ms ?? config.value.frame_length_ms
    };
    originalConfig.value = { ...config.value };
  }
}

onMounted(async () => {
  await loadConfig();
  on('settings', 'mac_roc_changed', handleConfigChanged);
});

onUnmounted(() => {
  // Cleanup handled by WebSocket service
});
</script>

<style scoped>
.settings-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

.settings-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.setting-item-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.setting-item-label {
  color: var(--color-text-secondary);
}

.setting-item-control {
  display: flex;
  flex-direction: column;
  width: 100%;
}

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

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }
}
</style>
