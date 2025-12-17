<!-- frontend/src/components/settings/categories/EqualizerSettings.vue -->
<!-- Equalizer settings wrapper - imports DSP components from frontend/src/components/dsp -->
<template>
  <div class="equalizer-settings">
    <!-- Main content -->
    <div class="content-wrapper">
      <Transition name="fade-slide" mode="out-in">
        <!-- État 1: DSP désactivé -->
        <MessageContent
          v-if="!dspStore.isDspEffectsEnabled"
          key="disabled"
          icon="equalizer"
          :title="$t('dsp.effects_disabled', 'DSP Effects are disabled')"
        />

        <!-- État 2: DSP activé mais en chargement/connexion -->
        <MessageContent
          v-else-if="!dspStore.isConnected"
          key="loading"
          :loading="true"
          :loading-delay="0"
          :title="$t('dsp.connecting', 'Connecting to DSP...')"
        />

        <!-- État 3: DSP connecté - contrôles -->
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
          <ZoneTabs
            ref="zoneTabsRef"
            :disabled="dspStore.isUpdating"
          />

          <!-- Section 2: 10 Bands Equalizer with presets dropdown -->
          <section class="settings-section">
            <div class="section-group">
              <div class="section-header">
                <h2 class="heading-2">
                  {{ $t('dsp.equalizer.title', '10 Bands Equalizer') }}
                  <span v-if="selectedZoneName" class="zone-suffix">· {{ selectedZoneName }}</span>
                </h2>
                <Dropdown
                  v-model="currentPreset"
                  :options="presetOptions"
                  :placeholder="$t('dsp.selectPreset', 'Preset')"
                  :disabled="dspStore.isUpdating"
                  @change="handlePresetChange"
                />
              </div>
              <ParametricEQ
                :filters="dspStore.filters"
                :filters-loaded="dspStore.filtersLoaded"
                :disabled="dspStore.isUpdating"
                :is-mobile="isMobile"
                @update:filter="handleFilterUpdate"
                @change="handleFilterChange"
              />
            </div>
          </section>

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
import ZoneTabs from '@/components/dsp/ZoneTabs.vue';
import ParametricEQ from '@/components/dsp/ParametricEQ.vue';
import AdvancedDsp from '@/components/dsp/AdvancedDsp.vue';
import LevelMeters from '@/components/dsp/LevelMeters.vue';

const { t } = useI18n();
const dspStore = useDspStore();
const { on } = useWebSocket();

// Local state
const isMobile = ref(false);
const currentPreset = ref('');
const zoneTabsRef = ref(null);

// Selected zone/client name from ZoneTabs component
const selectedZoneName = computed(() => {
  return zoneTabsRef.value?.selectedZoneName ?? '';
});

// Selected client IDs for level meters aggregation
const selectedClientIds = computed(() => {
  return zoneTabsRef.value?.selectedClientIds ?? ['local'];
});

let unsubscribeFunctions = [];

// === PRESETS ===
// Default presets with gain values for 10 EQ bands
const defaultPresets = computed(() => [
  {
    id: 'flat',
    label: t('dsp.quickPresets.flat', 'Flat'),
    gains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
  },
  {
    id: 'bass_boost',
    label: t('dsp.quickPresets.bassBoost', 'Bass Boost'),
    gains: [6, 5, 3, 1, 0, 0, 0, 0, 0, 0]
  },
  {
    id: 'vocal',
    label: t('dsp.quickPresets.vocal', 'Vocal'),
    gains: [-2, -1, 0, 1, 2, 3, 2, 1, 0, 0]
  },
  {
    id: 'night',
    label: t('dsp.quickPresets.nightMode', 'Night'),
    gains: [-4, -2, 0, 1, 1, 1, 0, -1, -2, -3]
  }
]);

const userPresets = computed(() => dspStore.presets);

// Convert presets to Dropdown options format
const presetOptions = computed(() => {
  const options = [];

  // Default presets group
  defaultPresets.value.forEach(preset => {
    options.push({
      label: preset.label,
      value: `default:${preset.id}`
    });
  });

  // User presets group (if any)
  if (userPresets.value.length > 0) {
    userPresets.value.forEach(preset => {
      options.push({
        label: preset,
        value: `user:${preset}`
      });
    });
  }

  return options;
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

// === PRESET HANDLING ===
async function handlePresetChange(value) {
  if (!value) return;

  if (value.startsWith('default:')) {
    // Apply default preset
    const presetId = value.slice(8);
    const preset = defaultPresets.value.find(p => p.id === presetId);
    if (preset) {
      await applyDefaultPreset(preset);
    }
  } else if (value.startsWith('user:')) {
    // Load user preset
    const presetName = value.slice(5);
    await dspStore.loadPreset(presetName);
  }
}

async function applyDefaultPreset(preset) {
  for (let i = 0; i < dspStore.filters.length && i < preset.gains.length; i++) {
    const filter = dspStore.filters[i];
    if (filter.gain !== preset.gains[i]) {
      dspStore.updateFilter(filter.id, 'gain', preset.gains[i]);
      await dspStore.finalizeFilterUpdate(filter.id);
    }
  }
}

// Watch for active preset changes from store
watch(() => dspStore.activePreset, (newPreset) => {
  if (newPreset && !currentPreset.value.startsWith('default:')) {
    currentPreset.value = 'user:' + newPreset;
  }
}, { immediate: true });

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
    on('dsp', 'delay_changed', (e) => dspStore.handleDelayChanged(e)),
    on('dsp', 'mute_changed', (e) => dspStore.handleMuteChanged(e)),
    on('dsp', 'links_changed', (e) => dspStore.handleLinksChanged(e)),
    on('dsp', 'enabled_changed', (e) => dspStore.handleEnabledChanged(e))
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
.equalizer-settings {
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

/* Settings section pattern */
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

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
}

.section-header :deep(.dropdown) {
  max-width: 200px;
}

.zone-suffix {
  color: var(--color-text-secondary);
  font-weight: normal;
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
  .settings-section {
    border-radius: var(--radius-05);
  }
}
</style>
