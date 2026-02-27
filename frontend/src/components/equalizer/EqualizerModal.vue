<!-- frontend/src/components/equalizer/EqualizerModal.vue -->
<template>
  <div class="equalizer-modal">
    <ModalHeader :title="$t('equalizer.title')">
      <template #actions="{ iconType }">
        <Toggle
          :modelValue="equalizerStore.isEqualizerEffectsEnabled"
          :type="iconType"
          :disabled="equalizerStore.isTogglingEnabled"
          @change="handleEqualizerToggle"
        />
      </template>
    </ModalHeader>

    <div class="main-content">
      <Transition name="fade-slide" mode="out-in">
        <!-- State 1: Equalizer disabled -->
        <MessageContent
          v-if="!equalizerStore.isEqualizerEffectsEnabled"
          key="disabled"
          icon="equalizer"
          :title="$t('equalizer.effects_disabled')"
        />

        <!-- State 2: Equalizer enabled but loading/connecting -->
        <MessageContent
          v-else-if="!equalizerStore.isConnected"
          key="loading"
          :loading="true"
          :loading-delay="0"
          :title="$t('equalizer.connecting')"
        />

        <!-- State 3: Equalizer connected - controls -->
        <div v-else key="controls" class="controls-content">
          <!-- Propagation Error Banner -->
          <div v-if="equalizerStore.propagationErrors.length > 0" class="error-banner" @click="equalizerStore.clearPropagationErrors">
            <span class="error-icon">⚠</span>
            <span class="error-text">
              {{ $t('equalizer.syncError') }}:
              {{ equalizerStore.propagationErrors.map(e => equalizerStore.getClientDisplayName(e.clientId)).join(', ') }}
            </span>
            <span class="error-dismiss">×</span>
          </div>

          <!-- Section 1: Zones (tabs) -->
          <ItemSelector
            ref="zoneTabsRef"
            :disabled="equalizerStore.isUpdating"
          />

          <!-- Section 2: 10 Bands Equalizer with presets dropdown -->
          <SettingsSection>
            <template #header>
              <SectionHeader :title="$t('equalizer.equalizer.title')" :subtitle="selectedZoneName">
                <template #actions>
                  <Dropdown
                    :model-value="currentPresetValue"
                    :options="presetOptions"
                    :placeholder="$t('equalizer.selectPreset')"
                    :disabled="equalizerStore.isUpdating"
                    @update:model-value="handlePresetChange"
                  />
                </template>
              </SectionHeader>
            </template>
            <ParametricEQ
              :filters="equalizerStore.filters"
              :filters-loaded="equalizerStore.filtersLoaded"
              :disabled="equalizerStore.isUpdating"
              :is-mobile="isMobile"
              @update:filter="handleFilterUpdate"
              @change="handleFilterChange"
            />
          </SettingsSection>

          <!-- Section 3: Loudness -->
          <ToggleSection
            :title="$t('equalizer.loudness.title')"
            :enabled="equalizerStore.loudness.enabled"
            @change="handleLoudnessToggle"
          >
            <div class="effect-controls">
              <div class="control-item">
                <label class="text-mono-small">{{ $t('equalizer.loudness.lowBoost') }}</label>
                <RangeSlider :model-value="equalizerStore.loudness.low_boost" :min="0" :max="15" :step="0.5" value-unit=" dB"
                  @update:model-value="(v) => equalizerStore.loudness.low_boost = v"
                  @change="handleLoudnessChange('low_boost', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ $t('equalizer.loudness.highBoost') }}</label>
                <RangeSlider :model-value="equalizerStore.loudness.high_boost" :min="0" :max="15" :step="0.5" value-unit=" dB"
                  @update:model-value="(v) => equalizerStore.loudness.high_boost = v"
                  @change="handleLoudnessChange('high_boost', $event)" />
              </div>
            </div>
          </ToggleSection>

          <!-- Section 4: Compressor -->
          <ToggleSection
            :title="$t('equalizer.compressor.title')"
            :enabled="equalizerStore.compressor.enabled"
            @change="handleCompressorToggle"
          >
            <div class="effect-controls">
              <div class="control-item">
                <label class="text-mono-small">{{ $t('equalizer.compressor.ratio') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.ratio" :min="1" :max="20" :step="0.5" value-unit=":1"
                  @update:model-value="(v) => equalizerStore.compressor.ratio = v"
                  @change="handleCompressorChange('ratio', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ $t('equalizer.compressor.threshold') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.threshold" :min="-60" :max="0" :step="1" value-unit=" dB"
                  @update:model-value="(v) => equalizerStore.compressor.threshold = v"
                  @change="handleCompressorChange('threshold', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ $t('equalizer.compressor.attack') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.attack" :min="0.1" :max="100" :step="0.1" value-unit=" ms"
                  @update:model-value="(v) => equalizerStore.compressor.attack = v"
                  @change="handleCompressorChange('attack', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ $t('equalizer.compressor.release') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.release" :min="10" :max="1000" :step="10" value-unit=" ms"
                  @update:model-value="(v) => equalizerStore.compressor.release = v"
                  @change="handleCompressorChange('release', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ $t('equalizer.compressor.makeup') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.makeup_gain" :min="0" :max="30" :step="0.5" value-unit=" dB"
                  @update:model-value="(v) => equalizerStore.compressor.makeup_gain = v"
                  @change="handleCompressorChange('makeup_gain', $event)" />
              </div>
            </div>
          </ToggleSection>

          <!-- Level Meters -->
          <LevelMeters :client-ids="selectedClientIds" />
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import ModalHeader from '@/components/ui/ModalHeader.vue';
import Toggle from '@/components/ui/Toggle.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import ToggleSection from '@/components/settings/ToggleSection.vue';
import ItemSelector from './ItemSelector.vue';
import ParametricEQ from './ParametricEQ.vue';
import LevelMeters from './LevelMeters.vue';

const { t } = useI18n();
const equalizerStore = useEqualizerStore();
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

// === EQUALIZER TOGGLE ===
async function handleEqualizerToggle(enabled) {
  await equalizerStore.toggleEqualizerEffectsEnabled(enabled);
}

// === PRESETS ===
const presetOptions = computed(() => {
  const options = [];

  options.push({
    label: t('equalizer.presets.manual'),
    value: 'manual'
  });

  equalizerStore.builtinPresets.forEach(preset => {
    options.push({
      label: t(`equalizer.presets.${preset.id}`, preset.id),
      value: preset.id
    });
  });

  return options;
});

const currentPresetValue = computed(() => {
  if (equalizerStore.isManualMode && equalizerStore.activePreset !== 'manual') {
    return 'manual';
  }
  return equalizerStore.activePreset || 'manual';
});

// === MOBILE DETECTION ===
function updateMobileStatus() {
  const aspectRatio = window.innerWidth / window.innerHeight;
  isMobile.value = aspectRatio <= 4 / 3;
}

// === FILTER UPDATES ===
function handleFilterUpdate({ id, field, value }) {
  equalizerStore.updateFilter(id, field, value);
}

function handleFilterChange({ id }) {
  equalizerStore.finalizeFilterUpdate(id);
}

// === PRESET HANDLING ===
async function handlePresetChange(value) {
  if (!value) return;
  await equalizerStore.loadPreset(value);
}

// === LOUDNESS ===
async function handleLoudnessToggle(enabled) {
  await equalizerStore.updateLoudness({ enabled });
}

async function handleLoudnessChange(field, value) {
  await equalizerStore.updateLoudness({ [field]: value, enabled: equalizerStore.loudness.enabled });
}

// === COMPRESSOR ===
async function handleCompressorToggle(enabled) {
  await equalizerStore.updateCompressor({ enabled });
}

async function handleCompressorChange(field, value) {
  await equalizerStore.updateCompressor({ [field]: value, enabled: equalizerStore.compressor.enabled });
}

// === LIFECYCLE ===
onMounted(async () => {
  updateMobileStatus();
  window.addEventListener('resize', updateMobileStatus);

  // Register WebSocket event listeners FIRST (before any async operations)
  // to prevent race condition where events arrive during initialization
  unsubscribeFunctions.push(
    on('equalizer', 'filter_changed', (e) => equalizerStore.handleFilterChanged(e)),
    on('equalizer', 'filters_reset', () => equalizerStore.handleFiltersReset()),
    on('equalizer', 'state_changed', (e) => equalizerStore.handleStateChanged(e)),
    on('equalizer', 'preset_loaded', (e) => equalizerStore.handlePresetLoaded(e)),
    on('equalizer', 'compressor_changed', (e) => equalizerStore.handleCompressorChanged(e)),
    on('equalizer', 'loudness_changed', (e) => equalizerStore.handleLoudnessChanged(e)),
    on('equalizer', 'enabled_changed', (e) => equalizerStore.handleEnabledChanged(e))
  );

  // Initialize filters
  equalizerStore.initializeFilters();

  // Load enabled state from settings
  await equalizerStore.loadEnabledState();

  // Load available equalizer targets (Milo + clients)
  await equalizerStore.loadTargets();

  // Load equalizer status if effects are enabled
  if (equalizerStore.isEqualizerEffectsEnabled) {
    await equalizerStore.loadStatus();
  }
});

onUnmounted(() => {
  window.removeEventListener('resize', updateMobileStatus);
  unsubscribeFunctions.forEach(unsubscribe => unsubscribe());
  equalizerStore.cleanup();
});
</script>

<style scoped>
.equalizer-modal {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.main-content {
  display: flex;
  flex-direction: column;
}

.controls-content {
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
:deep(.section-header__actions .dropdown-trigger) {
  min-width: 260px;
}

/* Loudness / Compressor controls grid */
.effect-controls {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
}

.control-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.control-item label {
  color: var(--color-text-secondary);
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  :deep(.section-header__actions .dropdown) {
    max-width: none;
  }

  .effect-controls {
    grid-template-columns: 1fr;
  }
}
</style>
