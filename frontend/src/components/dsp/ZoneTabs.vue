<!-- frontend/src/components/dsp/ZoneTabs.vue -->
<!-- Zone tabs + Preset selector with Save/Reset buttons -->
<template>
  <div class="zone-section">
    <!-- Zone tabs -->
    <div v-if="targets.length > 1" class="zone-tabs">
      <button
        v-for="target in targets"
        :key="target.id"
        v-press.light
        class="zone-tab"
        :class="{
          active: selectedTarget === target.id,
          linked: isLinked(target.id),
          unavailable: !target.available
        }"
        :disabled="!target.available"
        @click="selectTarget(target.id)"
      >
        {{ target.name }}
        <span v-if="isLinked(target.id)" class="link-badge">
          <SvgIcon name="link" size="12" />
        </span>
      </button>
    </div>

    <!-- Preset row: dropdown + Save + Reset -->
    <div class="preset-row">
      <label class="preset-label">{{ $t('dsp.preset', 'Preset') }}</label>

      <select
        v-model="currentPreset"
        class="preset-select"
        :disabled="disabled"
        @change="handlePresetChange"
      >
        <option value="">{{ $t('dsp.selectPreset', '-- Select --') }}</option>

        <!-- Default presets -->
        <optgroup :label="$t('dsp.defaultPresets', 'Default')">
          <option
            v-for="preset in defaultPresets"
            :key="preset.id"
            :value="'default:' + preset.id"
          >
            {{ preset.label }}
          </option>
        </optgroup>

        <!-- User presets -->
        <optgroup v-if="userPresets.length > 0" :label="$t('dsp.myPresets', 'My Presets')">
          <option
            v-for="preset in userPresets"
            :key="preset"
            :value="'user:' + preset"
          >
            {{ preset }}
          </option>
        </optgroup>
      </select>

      <button
        class="preset-btn"
        :disabled="disabled || isSaving"
        @click="openSaveDialog"
      >
        <SvgIcon name="save" size="16" />
      </button>

      <button
        class="preset-btn"
        :disabled="disabled || !currentPreset"
        @click="resetToPreset"
      >
        <SvgIcon name="reset" size="16" />
      </button>
    </div>

    <!-- Save Preset Dialog -->
    <PresetSaveDialog
      :is-open="showSaveDialog"
      :initial-name="saveDialogInitialName"
      :saving="isSaving"
      @close="showSaveDialog = false"
      @save="handleSavePreset"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue';
import { useDspStore } from '@/stores/dspStore';
import { useI18n } from '@/services/i18n';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import PresetSaveDialog from './PresetSaveDialog.vue';

const { t } = useI18n();
const dspStore = useDspStore();

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['targetChange', 'presetApplied']);

// Local state
const currentPreset = ref('');
const showSaveDialog = ref(false);
const isSaving = ref(false);

// Default presets with gain values for 10 EQ bands
// Bands: 31Hz, 63Hz, 125Hz, 250Hz, 500Hz, 1kHz, 2kHz, 4kHz, 8kHz, 16kHz
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

// Computed
const targets = computed(() => dspStore.availableTargets);
const selectedTarget = computed(() => dspStore.selectedTarget);
const userPresets = computed(() => dspStore.presets);

const saveDialogInitialName = computed(() => {
  if (currentPreset.value.startsWith('user:')) {
    return currentPreset.value.slice(5);
  }
  return '';
});

// Check if a client is linked to others
function isLinked(clientId) {
  return dspStore.isClientLinked(clientId);
}

// Select a target/zone
async function selectTarget(targetId) {
  if (targetId !== selectedTarget.value) {
    await dspStore.selectTarget(targetId);
    // Clear preset selection when switching zones
    currentPreset.value = '';
    emit('targetChange', targetId);
  }
}

// Handle preset selection change
async function handlePresetChange() {
  if (!currentPreset.value) return;

  if (currentPreset.value.startsWith('default:')) {
    // Apply default preset
    const presetId = currentPreset.value.slice(8);
    const preset = defaultPresets.value.find(p => p.id === presetId);
    if (preset) {
      await applyDefaultPreset(preset);
    }
  } else if (currentPreset.value.startsWith('user:')) {
    // Load user preset
    const presetName = currentPreset.value.slice(5);
    await dspStore.loadPreset(presetName);
  }

  emit('presetApplied', currentPreset.value);
}

// Apply a default preset (quick preset)
async function applyDefaultPreset(preset) {
  for (let i = 0; i < dspStore.filters.length && i < preset.gains.length; i++) {
    const filter = dspStore.filters[i];
    if (filter.gain !== preset.gains[i]) {
      dspStore.updateFilter(filter.id, 'gain', preset.gains[i]);
      await dspStore.finalizeFilterUpdate(filter.id);
    }
  }
}

// Reset to current preset
async function resetToPreset() {
  if (!currentPreset.value) return;

  if (currentPreset.value.startsWith('default:')) {
    const presetId = currentPreset.value.slice(8);
    const preset = defaultPresets.value.find(p => p.id === presetId);
    if (preset) {
      await applyDefaultPreset(preset);
    }
  } else if (currentPreset.value.startsWith('user:')) {
    const presetName = currentPreset.value.slice(5);
    await dspStore.loadPreset(presetName);
  }
}

// Open save dialog
function openSaveDialog() {
  showSaveDialog.value = true;
}

// Save preset
async function handleSavePreset(name) {
  isSaving.value = true;
  try {
    await dspStore.savePreset(name);
    currentPreset.value = 'user:' + name;
    showSaveDialog.value = false;
  } catch (error) {
    console.error('Error saving preset:', error);
  } finally {
    isSaving.value = false;
  }
}

// Watch for active preset changes from store
watch(() => dspStore.activePreset, (newPreset) => {
  if (newPreset && !currentPreset.value.startsWith('default:')) {
    currentPreset.value = 'user:' + newPreset;
  }
});
</script>

<style scoped>
.zone-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

/* Zone tabs */
.zone-tabs {
  display: flex;
  gap: var(--space-01);
  padding: var(--space-01);
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
  overflow-x: auto;
}

.zone-tab {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-01);
  padding: var(--space-02) var(--space-03);
  border: none;
  border-radius: var(--radius-04);
  background: transparent;
  color: var(--color-text-secondary);
  font-family: 'Space Mono Regular', monospace;
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition-fast), var(--transition-press);
  white-space: nowrap;
}

.zone-tab:hover:not(:disabled) {
  background: var(--color-background-strong);
  color: var(--color-text);
}

.zone-tab.active {
  background: var(--color-brand);
  color: var(--color-text-inverse);
}

.zone-tab.linked .link-badge {
  opacity: 0.8;
}

.zone-tab.unavailable {
  opacity: 0.4;
  cursor: not-allowed;
}

.link-badge {
  display: flex;
  align-items: center;
}

/* Preset row */
.preset-row {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  padding: var(--space-02);
  background: var(--color-background-neutral);
  border-radius: var(--radius-05);
}

.preset-label {
  font-family: 'Space Mono Regular', monospace;
  font-size: 13px;
  color: var(--color-text-secondary);
  white-space: nowrap;
}

.preset-select {
  flex: 1;
  min-width: 0;
  padding: var(--space-02) var(--space-03);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
  color: var(--color-text);
  font-family: 'Space Mono Regular', monospace;
  font-size: 13px;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.preset-select:hover:not(:disabled) {
  border-color: var(--color-text-light);
}

.preset-select:focus {
  outline: none;
  border-color: var(--color-brand);
}

.preset-select:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.preset-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.preset-btn:hover:not(:disabled) {
  background: var(--color-border);
  color: var(--color-text);
}

.preset-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* Scrollbar styling for zone tabs */
.zone-tabs::-webkit-scrollbar {
  height: 4px;
}

.zone-tabs::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: 2px;
}

/* Mobile adjustments */
@media (max-aspect-ratio: 4/3) {
  .zone-tab {
    padding: var(--space-01) var(--space-02);
    font-size: 12px;
  }

  .preset-row {
    padding: var(--space-01);
    gap: var(--space-01);
  }

  .preset-label {
    font-size: 12px;
  }

  .preset-select {
    padding: var(--space-01) var(--space-02);
    font-size: 12px;
  }

  .preset-btn {
    width: 32px;
    height: 32px;
  }
}
</style>
