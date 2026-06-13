// frontend/src/stores/fanStore.js
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { apiCall } from '@/services/apiCall';

/**
 * Cooling fan control store.
 *
 * Mirrors the backend FanController (backend/hardware/fan.py): persisted config
 * (enabled/mode/manual_percent/curve) plus live telemetry (temp/rpm/pwm).
 * Config changes arrive via the `settings.fan_config_changed` WS event; the
 * `settings.fan_status_changed` telemetry tick updates ONLY telemetry so it
 * never clobbers an edit in progress on the page.
 */
export const useFanStore = defineStore('fan', () => {
  // null until the first status load tells us whether a fan exists (hides the
  // settings entry off-Pi / on boards without the cooling_fan device).
  const available = ref(null);

  const config = ref({
    enabled: false,
    mode: 'auto', // 'auto' | 'manual' | 'target' (disabled is enabled=false, not a mode)
    manual_percent: 50,
    target_temp_c: 65,
    curve: [],
  });

  const status = ref({
    temp_c: 0,
    rpm: 0,
    pwm_percent: 0,
  });

  function applyConfig(data) {
    if (!data) return;
    if (data.available !== undefined) available.value = data.available;
    config.value = {
      enabled: data.enabled,
      mode: data.mode,
      manual_percent: data.manual_percent,
      target_temp_c: data.target_temp_c,
      curve: data.curve ?? [],
    };
  }

  function applyTelemetry(data) {
    if (!data) return;
    // Telemetry ticks must NOT drive `available` — only loadStatus/applyConfig do.
    // Otherwise a heartbeat could hide the page mid-edit.
    status.value = {
      temp_c: data.temp_c,
      rpm: data.rpm,
      pwm_percent: data.pwm_percent,
    };
  }

  // Initial load: hydrate both config and telemetry from the backend.
  async function loadStatus() {
    const result = await apiCall.get('/api/fan/status', {
      category: 'fan',
      message: 'Error loading fan status',
      logLevel: 'debug',
    });
    if (result.ok) {
      applyConfig(result.data);
      applyTelemetry(result.data);
    }
  }

  // Page poll: refresh telemetry only, so it never clobbers an edit in progress.
  async function refreshTelemetry() {
    const result = await apiCall.get('/api/fan/status', {
      category: 'fan',
      message: 'Error refreshing fan telemetry',
      logLevel: 'debug',
    });
    if (result.ok) {
      applyTelemetry(result.data);
    }
  }

  async function updateConfig(newConfig) {
    const payload = { ...config.value, ...newConfig };
    const prev = config.value;
    config.value = payload; // optimistic
    const result = await apiCall.put('/api/fan/config', payload, {
      category: 'fan',
      message: 'Error updating fan config',
    });
    if (!result.ok) {
      config.value = prev; // revert
      return false;
    }
    return true;
  }

  async function testSpeed(percent) {
    await apiCall.post('/api/fan/test', { percent }, {
      category: 'fan',
      message: 'Error testing fan speed',
    });
  }

  async function resync() {
    return loadStatus();
  }

  return {
    resync,
    available,
    config,
    status,
    applyConfig,
    applyTelemetry,
    loadStatus,
    refreshTelemetry,
    updateConfig,
    testSpeed,
  };
});
