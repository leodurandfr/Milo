<!-- frontend/src/components/settings/categories/InfoSettings.vue -->
<template>
  <SettingsSection>
    <div class="info-content">
      <!-- Header: Icon + Milō OS + Version -->
      <div class="info-header">
        <div class="info-icon">
          <img src="@/assets/app-icons/milo.svg" alt="Milō" />
        </div>
        <span class="heading-2">Milō OS</span>
        <span class="info-version text-mono">
          <span v-if="showVersionSkeleton" class="skeleton-line shimmer" style="width: 96px"></span>
          <span v-else-if="miloVersion !== null">Version {{ miloVersion }}</span>
          <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
        </span>
      </div>

      <!-- Info grid: IP + Temperature, CPU + RAM -->
      <div class="info-grid">
        <div class="info-item">
          <span class="info-label text-mono">{{ t('info.ipAddress') }}</span>
          <span class="info-value text-mono">
            <span v-if="showIpSkeleton" class="skeleton-line shimmer" style="width: 100px"></span>
            <span v-else-if="ipAddress !== null">{{ ipAddress }}</span>
            <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
          </span>
        </div>

        <div class="info-item">
          <span class="info-label text-mono">{{ t('info.temperature') }}</span>
          <span class="info-value text-mono">
            <span v-if="showTempSkeleton" class="skeleton-line shimmer" style="width: 48px"></span>
            <span v-else-if="systemTemperature !== null">{{ systemTemperature.toFixed(1) }}°C</span>
            <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
          </span>
        </div>

        <div class="info-item info-item-bar">
          <div class="info-item-top">
            <span class="info-label text-mono">{{ t('info.cpu') }}</span>
            <span class="info-value text-mono">
              <span v-if="showResourcesSkeleton" class="skeleton-line shimmer" style="width: 36px"></span>
              <span v-else-if="cpuPercent !== null">{{ cpuPercent }}%</span>
              <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
            </span>
          </div>
          <div class="bar-container">
            <div class="bar-fill" :style="{ width: (cpuPercent ?? 0) + '%' }"></div>
          </div>
        </div>

        <div class="info-item info-item-bar">
          <div class="info-item-top">
            <span class="info-label text-mono">{{ t('info.ram') }}</span>
            <span class="info-value text-mono">
              <span v-if="showResourcesSkeleton" class="skeleton-line shimmer" style="width: 88px"></span>
              <span v-else-if="ram !== null">{{ ram.used_mb }} / {{ ram.total_mb }} MB</span>
              <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
            </span>
          </div>
          <div class="bar-container">
            <div class="bar-fill" :style="{ width: ramPercent + '%' }"></div>
          </div>
        </div>
      </div>

      <!-- Credits -->
      <div class="info-item info-credits">
        <span class="info-label text-mono">{{ t('info.designedBy') }}</span>
        <span class="info-value text-mono">leodurand.com</span>
      </div>
    </div>
  </SettingsSection>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { apiCall } from '@/services/apiCall';
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

const showVersionSkeleton = computed(() => versionLoading.value && miloVersion.value === null);
const showIpSkeleton = computed(() => ipLoading.value && ipAddress.value === null);
const showTempSkeleton = computed(() => temperatureLoading.value && systemTemperature.value === null);
const showResourcesSkeleton = computed(() => resourcesLoading.value && cpuPercent.value === null);

const ramPercent = computed(() => {
  if (!ram.value) return 0;
  return Math.round((ram.value.used_mb / ram.value.total_mb) * 100);
});

async function loadMiloVersion() {
  if (versionLoading.value) return;
  versionLoading.value = true;
  const result = await apiCall.get('/api/programs/milo/installed', {
    category: 'system',
    message: 'Error loading Milo version'
  });
  miloVersion.value = result.ok ? (result.data.installed?.versions?.main || null) : null;
  versionLoading.value = false;
}

async function loadSystemTemperature() {
  if (temperatureLoading.value) return;
  temperatureLoading.value = true;
  const result = await apiCall.get('/api/settings/system-temperature', {
    category: 'system',
    message: 'Error loading temperature',
    checkStatus: true
  });
  systemTemperature.value = (result.ok && result.data.temperature !== null) ? result.data.temperature : null;
  temperatureLoading.value = false;
}

async function loadNetworkInfo() {
  if (ipLoading.value) return;
  ipLoading.value = true;
  const result = await apiCall.get('/api/settings/network-info', {
    category: 'system',
    message: 'Error loading network info',
    checkStatus: true
  });
  ipAddress.value = (result.ok && result.data.ip !== null) ? result.data.ip : null;
  ipLoading.value = false;
}

async function loadSystemResources() {
  if (resourcesLoading.value) return;
  resourcesLoading.value = true;
  const result = await apiCall.get('/api/settings/system-resources', {
    category: 'system',
    message: 'Error loading system resources',
    checkStatus: true
  });
  if (result.ok) {
    cpuPercent.value = result.data.cpu_percent;
    ram.value = result.data.ram;
  }
  resourcesLoading.value = false;
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
.info-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-07);
}

.info-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-02);
  padding-top: var(--space-05);
}

.info-icon {
  width: 72px;
  height: 72px;
  margin-bottom: var(--space-02);
  /* 16px total between icon and title: 8px (gap) + 8px (margin) */
}

.info-icon img {
  width: 100%;
  height: 100%;
}

.info-version {
  color: var(--color-text-secondary);
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

.info-item-bar {
  flex-direction: column;
  gap: var(--space-02);
}

.info-item-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  width: 100%;
}

.info-label {
  color: var(--color-text-secondary);
}

.info-value {
  color: var(--color-text);
  text-align: right;
}



.bar-container {
  width: 100%;
  height: 6px;
  background: var(--color-background-medium-16);
  border-radius: 3px;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  background: var(--color-background-contrast-32);
  border-radius: 3px;
  transition: width var(--transition-normal);
}

.skeleton-line {
  --shimmer-base: var(--color-background-strong);
  --shimmer-highlight: var(--color-background-medium-16);
  display: inline-block;
  height: var(--line-height-mono);
  border-radius: var(--radius-02);
  vertical-align: top;
}

.text-secondary {
  color: var(--color-text-secondary);
}

.text-error {
  color: var(--color-error);
}

@media (max-aspect-ratio: 4/3) {
  .info-grid {
    grid-template-columns: 1fr;
  }

  .info-credits {
    flex-direction: column;
    gap: var(--space-01)
  }

  .info-icon {
    width: 56px;
    height: 56px;
  }
}
</style>
