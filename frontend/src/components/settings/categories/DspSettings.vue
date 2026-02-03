<!-- frontend/src/components/settings/categories/DspSettings.vue -->
<!-- DSP settings wrapper - imports DSP components from frontend/src/components/dsp -->
<template>
  <div class="dsp-settings">
    <!-- Main content -->
    <div class="content-wrapper">
      <Transition name="fade-slide" mode="out-in">
        <!-- State 1: DSP disabled -->
        <MessageContent
          v-if="!dspStore.isDspEffectsEnabled"
          key="disabled"
          icon="equalizer"
          :title="$t('dsp.effects_disabled', 'DSP Effects are disabled')"
        />

        <!-- State 2: DSP enabled but loading/connecting -->
        <MessageContent
          v-else-if="!dspStore.isConnected"
          key="loading"
          :loading="true"
          :loading-delay="0"
          :title="$t('dsp.connecting', 'Connecting to DSP...')"
        />

        <!-- State 3: DSP connected - controls -->
        <div v-else key="controls" class="dsp-controls">
          <!-- Propagation Error Banner -->
          <div v-if="dspStore.propagationErrors.length > 0" class="error-banner" @click="dspStore.clearPropagationErrors">
            <span class="error-icon">⚠</span>
            <span class="error-text">
              {{ $t('dsp.syncError', 'Failed to sync settings to') }}:
              {{ dspStore.propagationErrors.map(e => dspStore.getClientDisplayName(e.clientId)).join(', ') }}
            </span>
            <span class="error-dismiss">×</span>
          </div>

          <!-- Section 1: Zones (tabs + volumes) -->
          <ItemSelector
            ref="zoneTabsRef"
            :disabled="dspStore.isUpdating"
            @configure-zone="handleConfigureZone"
          />

          <!-- Section 2: 10 Bands Equalizer with presets dropdown -->
          <SettingsSection>
            <template #header>
              <SectionHeader :title="$t('dsp.equalizer.title', '10 Bands Equalizer')" :subtitle="selectedZoneName">
                <template #actions>
                  <Dropdown
                    :model-value="currentPresetValue"
                    :options="presetOptions"
                    :placeholder="$t('dsp.selectPreset', 'Preset')"
                    :disabled="dspStore.isUpdating"
                    @update:model-value="handlePresetChange"
                  />
                </template>
              </SectionHeader>
            </template>
            <ParametricEQ
              :filters="dspStore.filters"
              :filters-loaded="dspStore.filtersLoaded"
              :disabled="dspStore.isUpdating"
              :is-mobile="isMobile"
              @update:filter="handleFilterUpdate"
              @change="handleFilterChange"
            />
          </SettingsSection>

          <!-- Section 3: Advanced DSP (Compressor, Loudness, Delay) -->
          <AdvancedDsp :zone-name="selectedZoneName" />

          <!-- Level Meters -->
          <LevelMeters :client-ids="selectedClientIds" />
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import Dropdown from '@/components/ui/Dropdown.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import ItemSelector from './dsp/ItemSelector.vue';
import ParametricEQ from './dsp/ParametricEQ.vue';
import AdvancedDsp from './dsp/AdvancedDsp.vue';
import LevelMeters from './dsp/LevelMeters.vue';

const emit = defineEmits(['configure-zone']);

const { t } = useI18n();
const dspStore = useDspStore();
const { on } = useWebSocket();

// Local state
const isMobile = ref(false);
const zoneTabsRef = ref(null);

// Selected zone/client name from ZoneTabs component
const selectedZoneName = computed(() => {
  return zoneTabsRef.value?.selectedZoneName ?? '';
});

// Selected client IDs for level meters aggregation
const selectedClientIds = computed(() => {
  return zoneTabsRef.value?.selectedClientIds ?? [];
});

let unsubscribeFunctions = [];

// === PRESETS ===
// Convert builtin presets to Dropdown options format
const presetOptions = computed(() => {
  const options = [];

  // Manual is always first and selectable
  options.push({
    label: t('dsp.presets.manual', 'Manual'),
    value: 'manual'
  });

  // Add all builtin presets
  dspStore.builtinPresets.forEach(preset => {
    options.push({
      label: t(`dsp.presets.${preset.id}`, preset.id),
      value: preset.id
    });
  });

  return options;
});

// Current preset value for dropdown
const currentPresetValue = computed(() => {
  // If gains differ from active preset, show as manual
  if (dspStore.isManualMode && dspStore.activePreset !== 'manual') {
    return 'manual';
  }
  return dspStore.activePreset || 'manual';
});

// === MOBILE DETECTION ===
function updateMobileStatus() {
  const aspectRatio = window.innerWidth / window.innerHeight;
  isMobile.value = aspectRatio <= 4 / 3;
}

// === FILTER UPDATES ===
function handleFilterUpdate({ id, field, value }) {
  dspStore.updateFilter(id, field, value);
}

function handleFilterChange({ id, field, value }) {
  dspStore.finalizeFilterUpdate(id);
}

// === ZONE CONFIGURATION ===
function handleConfigureZone(groupId) {
  emit('configure-zone', groupId);
}

// === PRESET HANDLING ===
async function handlePresetChange(value) {
  if (!value) return;
  await dspStore.loadPreset(value);
}

// === WEBSOCKET HANDLERS ===
function handleDspFilterChanged(event) {
  dspStore.handleFilterChanged(event);
}

function handleDspFiltersReset() {
  dspStore.handleFiltersReset();
}

function handleDspStateChanged(event) {
  dspStore.handleStateChanged(event);
}

function handleDspPresetLoaded(event) {
  dspStore.handlePresetLoaded(event);
}

// === LIFECYCLE ===
onMounted(async () => {
  updateMobileStatus();
  window.addEventListener('resize', updateMobileStatus);

  // Initialize filters
  dspStore.initializeFilters();

  // Load enabled state from settings
  await dspStore.loadEnabledState();

  // Load available DSP targets (Milo + clients)
  await dspStore.loadTargets();

  // Load DSP status if effects are enabled
  if (dspStore.isDspEffectsEnabled) {
    await dspStore.loadStatus();
  }

  // Subscribe to WebSocket events
  unsubscribeFunctions.push(
    on('dsp', 'filter_changed', handleDspFilterChanged),
    on('dsp', 'filters_reset', handleDspFiltersReset),
    on('dsp', 'state_changed', handleDspStateChanged),
    on('dsp', 'preset_loaded', handleDspPresetLoaded),
    on('dsp', 'compressor_changed', (e) => dspStore.handleCompressorChanged(e)),
    on('dsp', 'loudness_changed', (e) => dspStore.handleLoudnessChanged(e)),
    on('dsp', 'enabled_changed', (e) => dspStore.handleEnabledChanged(e))
    // Note: Client names sync via multiroomStore.handleMultiroomEvent() in App.vue
  );
});

onUnmounted(() => {
  window.removeEventListener('resize', updateMobileStatus);
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
  dspStore.cleanup();
});

// Expose dspStore for parent component (header toggle)
defineExpose({
  dspStore
});
</script>

<style scoped>
.dsp-settings {
  display: flex;
  flex-direction: column;
}

.content-wrapper {
  display: flex;
  flex-direction: column;
  position: relative;
}

.dsp-controls {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

/* Error banner for propagation failures */
.error-banner {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  padding: var(--space-03);
  background: var(--color-error, #f44336);
  background: rgba(244, 67, 54, 0.15);
  border: 1px solid var(--color-error, #f44336);
  border-radius: var(--radius-04);
  color: var(--color-text);
  font-size: 13px;
  cursor: pointer;
}

.error-icon {
  font-size: 16px;
  flex-shrink: 0;
}

.error-text {
  flex: 1;
}

.error-dismiss {
  font-size: 18px;
  opacity: 0.7;
  flex-shrink: 0;
}

.error-banner:hover .error-dismiss {
  opacity: 1;
}

/* SectionHeader dropdown constraint */
:deep(.section-header__actions .dropdown) {
  max-width: 260px;
}

/* Transitions */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: opacity var(--transition-normal), transform var(--transition-normal);
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  :deep(.section-header__actions .dropdown) {
    max-width: none;
  }
}
</style>
