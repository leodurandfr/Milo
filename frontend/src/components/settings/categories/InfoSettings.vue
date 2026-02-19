<!-- frontend/src/components/settings/categories/InfoSettings.vue -->
<template>
  <SettingsSection>
    <div class="info-grid">
      <div class="info-item">
        <span class="info-label text-mono">{{ t('info.miloVersion') }}</span>
        <span class="info-value text-mono">
          <span v-if="versionLoading && miloVersion === null">...</span>
          <span v-else-if="miloVersion !== null">{{ miloVersion }}</span>
          <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
        </span>
      </div>

      <div class="info-item">
        <span class="info-label text-mono">{{ t('info.temperature') }}</span>
        <span class="info-value text-mono">
          <span v-if="temperatureLoading && systemTemperature === null">...</span>
          <span v-else-if="systemTemperature !== null">{{ systemTemperature.toFixed(1) }}°C</span>
          <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
        </span>
      </div>

      <div class="info-item">
        <span class="info-label text-mono">{{ t('info.ipAddress') }}</span>
        <span class="info-value text-mono">
          <span v-if="ipLoading && ipAddress === null">...</span>
          <span v-else-if="ipAddress !== null">{{ ipAddress }}</span>
          <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
        </span>
      </div>

      <div class="info-item">
        <span class="info-label text-mono">{{ t('info.cpu') }}</span>
        <span class="info-value text-mono">
          <span v-if="resourcesLoading && cpuPercent === null">...</span>
          <span v-else-if="cpuPercent !== null">{{ cpuPercent }}%</span>
          <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
        </span>
      </div>

      <div class="info-item">
        <span class="info-label text-mono">{{ t('info.ram') }}</span>
        <span class="info-value text-mono">
          <span v-if="resourcesLoading && ram === null">...</span>
          <span v-else-if="ram !== null">{{ ram.used_mb }} / {{ ram.total_mb }} MB</span>
          <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
        </span>
      </div>
    </div>
  </SettingsSection>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import axios from 'axios';
import SettingsSection from '@/components/settings/SettingsSection.vue';

const { t } = useI18n();

const miloVersion = ref(null);
const versionLoading = ref(false);
const systemTemperature = ref(null);
const temperatureLoading = ref(false);
const ipAddress = ref(null);
const ipLoading = ref(false);
const cpuPercent = ref(null);
const ram = ref(null);
const resourcesLoading = ref(false);

async function loadMiloVersion() {
  if (versionLoading.value) return;

  try {
    versionLoading.value = true;
    const response = await axios.get('/api/programs/milo/installed');
    miloVersion.value = response.data.installed?.versions?.main || null;
  } catch (error) {
    console.error('Error loading Milo version:', error);
    miloVersion.value = null;
  } finally {
    versionLoading.value = false;
  }
}

async function loadSystemTemperature() {
  if (temperatureLoading.value) return;

  try {
    temperatureLoading.value = true;
    const response = await axios.get('/api/settings/system-temperature');

    if (response.data.status === 'success' && response.data.temperature !== null) {
      systemTemperature.value = response.data.temperature;
    } else {
      systemTemperature.value = null;
    }
  } catch (error) {
    console.error('Error loading temperature:', error);
    systemTemperature.value = null;
  } finally {
    temperatureLoading.value = false;
  }
}

async function loadNetworkInfo() {
  if (ipLoading.value) return;

  try {
    ipLoading.value = true;
    const response = await axios.get('/api/settings/network-info');

    if (response.data.status === 'success' && response.data.ip !== null) {
      ipAddress.value = response.data.ip;
    } else {
      ipAddress.value = null;
    }
  } catch (error) {
    console.error('Error loading network info:', error);
    ipAddress.value = null;
  } finally {
    ipLoading.value = false;
  }
}

async function loadSystemResources() {
  if (resourcesLoading.value) return;

  try {
    resourcesLoading.value = true;
    const response = await axios.get('/api/settings/system-resources');

    if (response.data.status === 'success') {
      cpuPercent.value = response.data.cpu_percent;
      ram.value = response.data.ram;
    }
  } catch (error) {
    console.error('Error loading system resources:', error);
  } finally {
    resourcesLoading.value = false;
  }
}

let pollingInterval = null;

async function pollDynamicData() {
  await Promise.all([loadSystemTemperature(), loadSystemResources()]);
}

onMounted(async () => {
  await Promise.all([loadMiloVersion(), loadNetworkInfo(), pollDynamicData()]);
  pollingInterval = setInterval(pollDynamicData, 5000);
});

onUnmounted(() => {
  if (pollingInterval) {
    clearInterval(pollingInterval);
  }
});
</script>

<style scoped>
.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-02);
}

.info-item {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
}

.info-label {
  color: var(--color-text-secondary);
}

.info-value {
  color: var(--color-text);
  text-align: right;
}

.text-error {
  color: var(--color-error);
}

@media (max-aspect-ratio: 4/3) {
  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
