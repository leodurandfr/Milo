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
            <RangeSlider v-model="config.step_rotary_db" :min="1" :max="6" :step="1" value-unit=" dB"
              @input="debouncedUpdate('rotary-steps', 'rotary-steps', { step_rotary_db: $event })" />
          </div>
        </div>

        <div class="setting-item-container">
          <div class="volume-item-setting text-mono">
            {{ t('volumeSettings.mobileIncrement') }}
          </div>
          <div class="volume-steps-control">
            <RangeSlider v-model="config.step_mobile_db" :min="1" :max="6" :step="1" value-unit=" dB"
              @input="debouncedUpdate('volume-steps', 'volume-steps', { step_mobile_db: $event })" />
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
            <DoubleRangeSlider v-model="config.limits" :min="-80" :max="0" :step="1" :gap="6" value-unit=" dB"
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
            @click="updateSetting('volume-startup', { startup_volume_db: config.startup_volume_db, restore_last_volume: false })">
            {{ t('volumeSettings.fixedVolume') }}
          </Button>
          <Button :variant="config.restore_last_volume ? 'outline' : 'background-strong'" size="medium"
            @click="updateSetting('volume-startup', { startup_volume_db: config.startup_volume_db, restore_last_volume: true })">
            {{ t('volumeSettings.restoreLast') }}
          </Button>
        </div>

        <div v-if="!config.restore_last_volume" class="setting-item-container">
          <div class="volume-item-setting text-mono">
            {{ t('volumeSettings.fixedStartup') }}
          </div>
          <div class="startup-volume-control">
            <RangeSlider v-model="config.startup_volume_db" :min="config.limits.min" :max="config.limits.max" :step="1" value-unit=" dB"
              @input="debouncedUpdate('volume-startup', 'volume-startup', { startup_volume_db: $event, restore_last_volume: false })" />
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
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

// Local refs for instant responsiveness (all values in dB)
const config = ref({
  step_mobile_db: 3.0,
  step_rotary_db: 2.0,
  limits: { min: -80.0, max: -21.0 },
  restore_last_volume: false,
  startup_volume_db: -30.0
});

// Sync local refs with the store on mount
function syncFromStore() {
  config.value.step_mobile_db = settingsStore.volumeSteps.step_mobile_db;
  config.value.step_rotary_db = settingsStore.volumeSteps.step_rotary_db;
  config.value.limits.min = settingsStore.volumeLimits.min_db;
  config.value.limits.max = settingsStore.volumeLimits.max_db;
  config.value.restore_last_volume = settingsStore.volumeStartup.restore_last_volume;
  config.value.startup_volume_db = settingsStore.volumeStartup.startup_volume_db;
}

function updateVolumeLimits(limits) {
  debouncedUpdate('volume-limits', 'volume-limits', {
    min_db: limits.min,
    max_db: limits.max
  });
}

// WebSocket listeners - update both the store AND local refs
const wsListeners = {
  volume_limits_changed: (msg) => {
    if (msg.data?.limits) {
      const minDb = msg.data.limits.min_db ?? -80.0;
      const maxDb = msg.data.limits.max_db ?? -21.0;
      settingsStore.updateVolumeLimits({ min_db: minDb, max_db: maxDb });
      config.value.limits.min = minDb;
      config.value.limits.max = maxDb;
    }
  },
  volume_startup_changed: (msg) => {
    if (msg.data?.config) {
      const startupDb = msg.data.config.startup_volume_db;
      settingsStore.updateVolumeStartup({
        restore_last_volume: msg.data.config.restore_last_volume,
        startup_volume_db: startupDb
      });
      config.value.restore_last_volume = msg.data.config.restore_last_volume;
      config.value.startup_volume_db = startupDb;
    }
  },
  volume_steps_changed: (msg) => {
    if (msg.data?.config?.step_mobile_db !== undefined) {
      const stepDb = msg.data.config.step_mobile_db;
      settingsStore.updateVolumeSteps({ step_mobile_db: stepDb });
      config.value.step_mobile_db = stepDb;
    }
  },
  rotary_steps_changed: (msg) => {
    if (msg.data?.config?.step_rotary_db !== undefined) {
      const stepDb = msg.data.config.step_rotary_db;
      settingsStore.updateVolumeSteps({ step_rotary_db: stepDb });
      config.value.step_rotary_db = stepDb;
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
    flex-direction: column-reverse;
  }

  .volume-steps-control,
  .startup-volume-control {
    gap: var(--space-05);
  }

}
</style>
