<!-- frontend/src/components/settings/categories/InfoSettings.vue -->
<template>
  <section class="settings-section">
    <div class="info-grid">
      <div class="info-item">
        <span class="info-label text-mono">{{ t('info.miloVersion') }}</span>
        <span class="info-value text-mono">0.1.0</span>
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
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import axios from 'axios';

const { t } = useI18n();

const systemTemperature = ref(null);
const temperatureLoading = ref(false);
const ipAddress = ref(null);
const ipLoading = ref(false);

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

let temperatureInterval = null;

onMounted(async () => {
  await loadSystemTemperature();
  await loadNetworkInfo();
  temperatureInterval = setInterval(loadSystemTemperature, 5000);
});

onUnmounted(() => {
  if (temperatureInterval) {
    clearInterval(temperatureInterval);
  }
});
</script>

<style scoped>
.settings-section {
  background: var(--color-background-neutral);
  border-radius: var(--radius-06);
  padding: var(--space-05-fixed) var(--space-05);
  display: flex;
  flex-direction: column;
  gap: var(--space-05-fixed);
}

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

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .settings-section {
    border-radius: var(--radius-05);
  }

  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
