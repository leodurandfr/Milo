<!-- frontend/src/components/settings/categories/VolumeSettings.vue -->
<template>
  <div class="settings-container">
    <!-- Volume controls -->
    <section class="settings-section">
      <div class="volume-group">
        <h2 class="heading-2">{{ t('volumeSettings.controls') }}</h2>

        <div class="setting-item-container">
          <div class="volume-item-setting text-mono">
            {{ t('volumeSettings.rotaryIncrement') }}
          </div>
          <div class="volume-steps-control">
            <RangeSlider v-model="config.rotary_volume_steps" :min="1" :max="10" :step="1" value-unit="%"
              @input="debouncedUpdate('rotary-steps', 'rotary-steps', { rotary_volume_steps: $event })" />
          </div>
        </div>

        <div class="setting-item-container">
          <div class="volume-item-setting text-mono">
            {{ t('volumeSettings.mobileIncrement') }}
          </div>
          <div class="volume-steps-control">
            <RangeSlider v-model="config.mobile_volume_steps" :min="1" :max="10" :step="1" value-unit="%"
              @input="debouncedUpdate('volume-steps', 'volume-steps', { mobile_volume_steps: $event })" />
          </div>
        </div>
      </div>
    </section>

    <!-- Volume limits -->
    <section class="settings-section">
      <div class="volume-group">
        <h2 class="heading-2">{{ t('volumeSettings.limits') }}</h2>
        <div class="setting-item-container">
          <div class="volume-item-setting text-mono">
            {{ t('volumeSettings.minMax') }}
          </div>
          <div class="volume-limits-control">
            <DoubleRangeSlider v-model="config.limits" :min="0" :max="100" :step="1" :gap="10" value-unit="%"
              @input="updateVolumeLimits" />
          </div>
        </div>
      </div>
    </section>

    <!-- Startup volume -->
    <section class="settings-section">
      <div class="volume-group">
        <h2 class="heading-2">{{ t('volumeSettings.startup') }}</h2>

        <div class="startup-mode-buttons">
          <Button :variant="!config.restore_last_volume ? 'outline' : 'background-strong'" size="medium"
            @click="updateSetting('volume-startup', { startup_volume: config.startup_volume, restore_last_volume: false })">
            {{ t('volumeSettings.fixedVolume') }}
          </Button>
          <Button :variant="config.restore_last_volume ? 'outline' : 'background-strong'" size="medium"
            @click="updateSetting('volume-startup', { startup_volume: config.startup_volume, restore_last_volume: true })">
            {{ t('volumeSettings.restoreLast') }}
          </Button>
        </div>

        <div v-if="!config.restore_last_volume" class="setting-item-container">
          <div class="volume-item-setting text-mono">
            {{ t('volumeSettings.fixedStartup') }}
          </div>
          <div class="startup-volume-control">
            <RangeSlider v-model="config.startup_volume" :min="0" :max="100" :step="1" value-unit="%"
              @input="debouncedUpdate('volume-startup', 'volume-startup', { startup_volume: $event, restore_last_volume: false })" />
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { useSettingsStore } from '@/stores/settingsStore';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import DoubleRangeSlider from '@/components/ui/DoubleRangeSlider.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { updateSetting, debouncedUpdate, clearAllTimers } = useSettingsAPI();
const settingsStore = useSettingsStore();

// Local refs for instant responsiveness
const config = ref({
  mobile_volume_steps: 5,
  rotary_volume_steps: 5,
  limits: { min: 0, max: 65 },
  restore_last_volume: false,
  startup_volume: 50
});

// Sync local refs with the store on mount
function syncFromStore() {
  config.value.mobile_volume_steps = settingsStore.volumeSteps.mobile_volume_steps;
  config.value.rotary_volume_steps = settingsStore.volumeSteps.rotary_volume_steps;
  config.value.limits.min = settingsStore.volumeLimits.alsa_min;
  config.value.limits.max = settingsStore.volumeLimits.alsa_max;
  config.value.restore_last_volume = settingsStore.volumeStartup.restore_last_volume;
  config.value.startup_volume = settingsStore.volumeStartup.startup_volume;
}

function updateVolumeLimits(limits) {
  debouncedUpdate('volume-limits', 'volume-limits', {
    alsa_min: limits.min,
    alsa_max: limits.max
  });
}

// WebSocket listeners - update both the store AND local refs
const wsListeners = {
  volume_limits_changed: (msg) => {
    if (msg.data?.limits) {
      settingsStore.updateVolumeLimits({
        alsa_min: msg.data.limits.alsa_min || 0,
        alsa_max: msg.data.limits.alsa_max || 65
      });
      config.value.limits.min = msg.data.limits.alsa_min || 0;
      config.value.limits.max = msg.data.limits.alsa_max || 65;
    }
  },
  volume_startup_changed: (msg) => {
    if (msg.data?.config) {
      settingsStore.updateVolumeStartup({
        restore_last_volume: msg.data.config.restore_last_volume,
        startup_volume: msg.data.config.startup_volume
      });
      config.value.restore_last_volume = msg.data.config.restore_last_volume;
      config.value.startup_volume = msg.data.config.startup_volume;
    }
  },
  volume_steps_changed: (msg) => {
    if (msg.data?.config?.mobile_volume_steps !== undefined) {
      settingsStore.updateVolumeSteps({
        mobile_volume_steps: msg.data.config.mobile_volume_steps
      });
      config.value.mobile_volume_steps = msg.data.config.mobile_volume_steps;
    }
  },
  rotary_steps_changed: (msg) => {
    if (msg.data?.config?.rotary_volume_steps !== undefined) {
      settingsStore.updateVolumeSteps({
        rotary_volume_steps: msg.data.config.rotary_volume_steps
      });
      config.value.rotary_volume_steps = msg.data.config.rotary_volume_steps;
    }
  }
};

onMounted(() => {
  // Sync with the store on mount
  syncFromStore();

  // Register WebSocket listeners
  Object.entries(wsListeners).forEach(([eventType, handler]) => {
    on('settings', eventType, handler);
  });
});

onUnmounted(() => {
  clearAllTimers();
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

.volume-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.setting-item-container {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.volume-item-setting {
  color: var(--color-text-secondary);
}

.volume-steps-control,
.startup-volume-control {
  display: flex;
  align-items: center;
}

.volume-limits-control {
  display: flex;
  flex-direction: column;
}

.startup-mode-buttons {
  display: flex;
  gap: var(--space-02);
}

.startup-mode-buttons .btn {
  flex: 1;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
    .settings-section {
    border-radius: var(--radius-05);
  }
  .startup-mode-buttons {
    flex-direction: column;
  }

  .volume-steps-control,
  .startup-volume-control {
    gap: var(--space-05);
  }

}
</style>