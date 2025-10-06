<!-- frontend/src/components/settings/categories/VolumeSettings.vue -->
<template>
  <div class="settings-container">
    <!-- Contrôles du volume -->
    <section class="settings-section">
      <div class="volume-group">
        <h2 class="heading-2 text-body">{{ t('Contrôles du volume') }}</h2>

        <div class="setting-item-container">
          <div class="volume-item-setting text-mono">
            {{ t('Incrémentation du rotary encoder') }}
          </div>
          <div class="volume-steps-control">
            <RangeSlider v-model="config.rotary_volume_steps" :min="1" :max="10" :step="1" value-unit="%"
              @input="debouncedUpdate('rotary-steps', 'rotary-steps', { rotary_volume_steps: $event })" />
          </div>
        </div>

        <div class="setting-item-container">
          <div class="volume-item-setting text-mono">
            {{ t('Incrémentation des boutons volume en mobile') }}
          </div>
          <div class="volume-steps-control">
            <RangeSlider v-model="config.mobile_volume_steps" :min="1" :max="10" :step="1" value-unit="%"
              @input="debouncedUpdate('volume-steps', 'volume-steps', { mobile_volume_steps: $event })" />
          </div>
        </div>
      </div>
    </section>

    <!-- Limites du volume -->
    <section class="settings-section">
      <div class="volume-group">
        <h2 class="heading-2 text-body">{{ t('Limites du volume') }}</h2>
        <div class="setting-item-container">
          <div class="volume-item-setting text-mono">
            {{ t('Volume minimal et maximal') }}
          </div>
          <div class="volume-limits-control">
            <DoubleRangeSlider v-model="config.limits" :min="0" :max="100" :step="1" :gap="10" value-unit="%"
              @input="updateVolumeLimits" />
          </div>
        </div>
      </div>
    </section>

    <!-- Volume au démarrage -->
    <section class="settings-section">
      <div class="volume-group">
        <h2 class="heading-2 text-body">{{ t('Volume au démarrage') }}</h2>

        <div class="startup-mode-buttons">
          <Button variant="toggle" :active="!config.restore_last_volume"
            @click="updateSetting('volume-startup', { startup_volume: config.startup_volume, restore_last_volume: false })">
            {{ t('Volume fixe') }}
          </Button>
          <Button variant="toggle" :active="config.restore_last_volume"
            @click="updateSetting('volume-startup', { startup_volume: config.startup_volume, restore_last_volume: true })">
            {{ t('Restaurer le dernier') }}
          </Button>
        </div>

        <div v-if="!config.restore_last_volume" class="setting-item-container">
          <div class="volume-item-setting text-mono">
            {{ t('Volume fixe au démarrage') }}
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
import { ref, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import useWebSocket from '@/services/websocket';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import Button from '@/components/ui/Button.vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import DoubleRangeSlider from '@/components/ui/DoubleRangeSlider.vue';

const { t } = useI18n();
const { on } = useWebSocket();
const { updateSetting, debouncedUpdate, loadConfig, clearAllTimers } = useSettingsAPI();

const config = ref({
  mobile_volume_steps: 5,
  rotary_volume_steps: 2,
  limits: { min: 0, max: 65 },
  restore_last_volume: false,
  startup_volume: 37
});

function updateVolumeLimits(limits) {
  debouncedUpdate('volume-limits', 'volume-limits', {
    alsa_min: limits.min,
    alsa_max: limits.max
  });
}

// WebSocket listeners
const wsListeners = {
  volume_limits_changed: (msg) => {
    if (msg.data?.limits) {
      config.value.limits = {
        min: msg.data.limits.alsa_min || 0,
        max: msg.data.limits.alsa_max || 65
      };
    }
  },
  volume_startup_changed: (msg) => {
    if (msg.data?.config) {
      config.value.restore_last_volume = msg.data.config.restore_last_volume;
      config.value.startup_volume = msg.data.config.startup_volume;
    }
  },
  volume_steps_changed: (msg) => {
    if (msg.data?.config?.mobile_volume_steps) {
      config.value.mobile_volume_steps = msg.data.config.mobile_volume_steps;
    }
  },
  rotary_steps_changed: (msg) => {
    if (msg.data?.config?.rotary_volume_steps) {
      config.value.rotary_volume_steps = msg.data.config.rotary_volume_steps;
    }
  }
};

onMounted(async () => {
  // Load configs in parallel
  const [volumeLimits, volumeStartup, volumeSteps, rotarySteps] = await Promise.all([
    loadConfig('volume-limits'),
    loadConfig('volume-startup'),
    loadConfig('volume-steps'),
    loadConfig('rotary-steps')
  ]);

  if (volumeLimits.status === 'success') {
    config.value.limits = {
      min: volumeLimits.limits.alsa_min || 0,
      max: volumeLimits.limits.alsa_max || 65
    };
  }

  if (volumeStartup.status === 'success') {
    config.value.restore_last_volume = volumeStartup.config.restore_last_volume || false;
    config.value.startup_volume = volumeStartup.config.startup_volume || 37;
  }

  if (volumeSteps.status === 'success') {
    config.value.mobile_volume_steps = volumeSteps.config.mobile_volume_steps || 5;
  }

  if (rotarySteps.status === 'success') {
    config.value.rotary_volume_steps = rotarySteps.config.rotary_volume_steps || 2;
  }

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
  border-radius: var(--radius-04);
  padding: var(--space-06) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05);
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
  .startup-mode-buttons {
    flex-direction: column;
  }

  .volume-steps-control,
  .startup-volume-control {
    gap: var(--space-05);
  }
}
</style>
