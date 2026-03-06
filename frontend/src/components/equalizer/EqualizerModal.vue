<!-- frontend/src/components/equalizer/EqualizerModal.vue -->
<template>
  <div class="equalizer-modal">
    <NavigationHeader :title="t('equalizer.title')">
      <template #actions="{ iconType }">
        <Toggle :modelValue="equalizerStore.isEqualizerEffectsEnabled" :type="iconType"
          :disabled="equalizerStore.isTogglingEnabled" @change="handleEqualizerToggle" />
      </template>
    </NavigationHeader>

    <div class="main-content">
      <Transition name="fade-slide">
        <!-- State 1: Equalizer disabled -->
        <MessageContent v-if="!equalizerStore.isEqualizerEffectsEnabled" key="disabled" icon="equalizer"
          :title="t('equalizer.effects_disabled')" />

        <!-- State 2: Equalizer enabled but loading/connecting -->
        <MessageContent v-else-if="!equalizerStore.isConnected" key="loading" :loading="true" :loading-delay="0"
          :title="t('equalizer.connecting')" />

        <!-- State 3: Equalizer connected - controls -->
        <div v-else key="controls" class="controls-content">
          <!-- Propagation Error Banner -->
          <div v-if="equalizerStore.propagationErrors.length > 0" class="error-banner"
            @click="equalizerStore.clearPropagationErrors">
            <span class="error-icon">⚠</span>
            <span class="error-text">
              {{ t('equalizer.syncError') }}:
              {{equalizerStore.propagationErrors.map(e => equalizerStore.getClientDisplayName(e.clientId)).join(', ')
              }}
            </span>
            <span class="error-dismiss">×</span>
          </div>

          <!-- Section 1: Zones (tabs) -->
          <ItemSelector ref="zoneTabsRef" :disabled="equalizerStore.isUpdating" />

          <!-- Section 2: 10 Bands Equalizer with presets dropdown -->
          <SettingsSection>
            <template #header>
              <div class="eq-header">
                <div class="eq-header__title">
                  <h2 class="heading-2">{{ selectedZoneName }}</h2>
                </div>
                <Button v-if="equalizerStore.isPresetEdited" variant="brand" size="small"
                  :disabled="equalizerStore.isUpdating" @click="handleSaveCustomPreset">
                  {{ t('equalizer.presets.save') }}
                </Button>
                <Dropdown :model-value="currentPresetValue" :options="presetOptions"
                  :display-override="presetDisplayOverride" :placeholder="t('equalizer.selectPreset')"
                  :disabled="equalizerStore.isUpdating" size="small" class="eq-header__dropdown"
                  @update:model-value="handlePresetChange" />
              </div>
            </template>
            <ParametricEQ :filters="equalizerStore.filters" :filters-loaded="equalizerStore.filtersLoaded"
              :disabled="equalizerStore.isUpdating" :is-mobile="isMobile" @update:filter="handleFilterUpdate"
              @change="handleFilterChange" />
          </SettingsSection>

          <!-- Section 3: Loudness -->
          <ToggleSection :title="t('equalizer.loudness.title')" :enabled="equalizerStore.loudness.enabled"
            @change="handleLoudnessToggle">
            <div class="effect-controls">
              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.loudness.lowBoost') }}</label>
                <RangeSlider :model-value="equalizerStore.loudness.low_boost" :min="0" :max="15" :step="0.5"
                  value-unit=" dB" @update:model-value="(v) => equalizerStore.loudness.low_boost = v"
                  @change="handleLoudnessChange('low_boost', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.loudness.highBoost') }}</label>
                <RangeSlider :model-value="equalizerStore.loudness.high_boost" :min="0" :max="15" :step="0.5"
                  value-unit=" dB" @update:model-value="(v) => equalizerStore.loudness.high_boost = v"
                  @change="handleLoudnessChange('high_boost', $event)" />
              </div>
            </div>
          </ToggleSection>

          <!-- Section 4: Compressor -->
          <ToggleSection :title="t('equalizer.compressor.title')" :enabled="equalizerStore.compressor.enabled"
            @change="handleCompressorToggle">
            <div class="effect-controls">
              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.compressor.ratio') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.ratio" :min="1" :max="20" :step="0.5"
                  value-unit=":1" @update:model-value="(v) => equalizerStore.compressor.ratio = v"
                  @change="handleCompressorChange('ratio', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.compressor.threshold') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.threshold" :min="-60" :max="0" :step="1"
                  value-unit=" dB" @update:model-value="(v) => equalizerStore.compressor.threshold = v"
                  @change="handleCompressorChange('threshold', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.compressor.attack') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.attack" :min="0.1" :max="100" :step="0.1"
                  value-unit=" ms" @update:model-value="(v) => equalizerStore.compressor.attack = v"
                  @change="handleCompressorChange('attack', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.compressor.release') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.release" :min="10" :max="1000" :step="10"
                  value-unit=" ms" @update:model-value="(v) => equalizerStore.compressor.release = v"
                  @change="handleCompressorChange('release', $event)" />
              </div>

              <div class="control-item">
                <label class="text-mono-small">{{ t('equalizer.compressor.makeup') }}</label>
                <RangeSlider :model-value="equalizerStore.compressor.makeup_gain" :min="0" :max="30" :step="0.5"
                  value-unit=" dB" @update:model-value="(v) => equalizerStore.compressor.makeup_gain = v"
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
import NavigationHeader from '@/components/ui/NavigationHeader.vue';
import Toggle from '@/components/ui/Toggle.vue';
import Button from '@/components/ui/Button.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import MessageContent from '@/components/ui/MessageContent.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import ToggleSection from '@/components/settings/ToggleSection.vue';
import ItemSelector from './ItemSelector.vue';
import ParametricEQ from './ParametricEQ.vue';
import LevelMeters from './LevelMeters.vue';

const { t } = useI18n();
const equalizerStore = useEqualizerStore();

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

// === EQUALIZER TOGGLE ===
async function handleEqualizerToggle(enabled) {
  await equalizerStore.toggleEqualizerEffectsEnabled(enabled);
}

// === PRESETS ===
const presetOptions = computed(() => {
  const options = [];

  options.push({
    label: t('equalizer.presets.custom'),
    value: 'custom'
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
  if (equalizerStore.isCustomMode && equalizerStore.activePreset !== 'custom') {
    return 'custom';
  }
  return equalizerStore.activePreset || 'custom';
});

// When preset is edited, override dropdown display to show "Edited"
const presetDisplayOverride = computed(() => {
  if (equalizerStore.isPresetEdited) {
    return t('equalizer.presets.edited');
  }
  return null;
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

async function handleSaveCustomPreset() {
  await equalizerStore.saveCustomPreset();
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
  position: relative;
  display: flex;
  flex-direction: column;
}

/* Cross-fade: entering content appears after leaving starts fading */
:deep(.fade-slide-enter-active) {
  transition-delay: 100ms;
}

/* Cross-fade: leaving content overlays absolutely (doesn't affect height) */
:deep(.fade-slide-leave-active) {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
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

/* EQ section header layout */
.eq-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-02);
}

.eq-header__title {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  min-width: 0;
}

.eq-header__subtitle {
  color: var(--color-text-secondary);
}

.eq-header__dropdown {
  max-width: 256px;
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
  .eq-header {
    flex-wrap: wrap;
  }

  .eq-header__dropdown {
    flex: 0 0 100%;
    order: 3;
  }

  .eq-header__dropdown {
    max-width: none;
  }

  .effect-controls {
    grid-template-columns: 1fr;
  }
}
</style>
