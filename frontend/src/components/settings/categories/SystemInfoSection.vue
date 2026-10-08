<!-- frontend/src/components/settings/categories/SystemInfoSection.vue -->
<template>
  <SettingsSection>
    <div class="info-content">
      <!-- Header: Icon + Milō OS + Version -->
      <div class="info-header">
        <div class="info-icon">
          <img src="@/assets/app-icons/milo.svg" alt="Milō" />
        </div>
        <span class="heading-2">Milō OS</span>
        <span class="info-version text-mono-medium">
          <Transition name="reveal">
            <span v-if="showVersionSkeleton" class="skeleton-line shimmer" style="width: 96px"></span>
            <span v-else-if="miloVersion !== null">Version {{ miloVersion }}</span>
            <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
          </Transition>
        </span>
      </div>

      <!-- Info grid: CPU + RAM, Temperature + Disk, IP + Network -->
      <div class="info-grid">
        <div class="info-item info-item-bar">
          <div class="info-item-top">
            <span class="info-label text-mono-medium">{{ t('info.cpu') }}</span>
            <span class="info-value text-mono-medium">
              <Transition name="reveal">
                <span v-if="showResourcesSkeleton" class="skeleton-line shimmer" style="width: 36px"></span>
                <span v-else-if="cpuPercent !== null">{{ formatUnit(cpuPercent, '%') }}</span>
                <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
              </Transition>
            </span>
          </div>
          <div class="bar-container">
            <div class="bar-fill" :style="{ width: (cpuPercent ?? 0) + '%' }"></div>
          </div>
        </div>

        <div class="info-item info-item-bar">
          <div class="info-item-top">
            <span class="info-label text-mono-medium">{{ t('info.ram') }}</span>
            <span class="info-value text-mono-medium">
              <Transition name="reveal">
                <span v-if="showResourcesSkeleton" class="skeleton-line shimmer" style="width: 88px"></span>
                <span v-else-if="ram !== null">{{ formatNumber(ram.used_mb) }} / {{ formatUnit(ram.total_mb, 'MB') }}</span>
                <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
              </Transition>
            </span>
          </div>
          <div class="bar-container">
            <div class="bar-fill" :style="{ width: ramPercent + '%' }"></div>
          </div>
        </div>

        <div class="info-item info-item-bar">
          <div class="info-item-top">
            <span class="info-label text-mono-medium">{{ t('info.temperature') }}</span>
            <span class="info-value text-mono-medium">
              <Transition name="reveal">
                <span v-if="showTempSkeleton" class="skeleton-line shimmer" style="width: 48px"></span>
                <span v-else-if="systemTemperature !== null">{{ formatUnit(systemTemperature, '°C', ONE_DECIMAL) }}</span>
                <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
              </Transition>
            </span>
          </div>
          <div class="bar-container">
            <div class="bar-fill" :style="{ width: temperaturePercent + '%' }"></div>
          </div>
        </div>

        <div class="info-item info-item-bar">
          <div class="info-item-top">
            <span class="info-label text-mono-medium">{{ t('info.disk') }}</span>
            <span class="info-value text-mono-medium">
              <Transition name="reveal">
                <span v-if="showResourcesSkeleton" class="skeleton-line shimmer" style="width: 88px"></span>
                <span v-else-if="disk !== null">{{ formatNumber(disk.used_gb) }} / {{ formatUnit(disk.total_gb, 'GB') }}</span>
                <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
              </Transition>
            </span>
          </div>
          <div class="bar-container">
            <div class="bar-fill" :style="{ width: diskPercent + '%' }"></div>
          </div>
        </div>

        <div class="info-item">
          <span class="info-label text-mono-medium">{{ t('info.ipAddress') }}</span>
          <span class="info-value text-mono-medium">
            <Transition name="reveal">
              <span v-if="showIpSkeleton" class="skeleton-line shimmer" style="width: 100px"></span>
              <span v-else-if="ipAddress !== null">{{ ipAddress }}</span>
              <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
            </Transition>
          </span>
        </div>

        <div class="info-item">
          <span class="info-label text-mono-medium">{{ t('info.network') }}</span>
          <span class="info-value text-mono-medium">
            <Transition name="reveal">
              <span v-if="showResourcesSkeleton" class="skeleton-line shimmer" style="width: 140px"></span>
              <span v-else-if="network !== null" class="network-rates">
                <span><span class="rate-arrow">↓</span> {{ formatRate(network.rx_bytes_per_s) }}</span>
                <span class="rate-out"><span class="rate-arrow">↑</span> {{ formatRate(network.tx_bytes_per_s) }}</span>
              </span>
              <span v-else class="text-error">{{ t('updates.notAvailable') }}</span>
            </Transition>
          </span>
        </div>
      </div>

      <!-- Credits -->
      <div class="info-item info-credits">
        <span class="info-label text-mono-medium">{{ t('info.designedBy') }}</span>
        <span class="info-value text-mono-medium">leodurand.com</span>
      </div>
    </div>
  </SettingsSection>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { apiCall } from '@/services/apiCall';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import { useTimer } from '@/composables/useTimer';

const { t, formatNumber, formatUnit } = useI18n();

const ONE_DECIMAL = { minimumFractionDigits: 1, maximumFractionDigits: 1 };
const timer = useTimer();

const miloVersion = ref(null);
const versionLoading = ref(false);
const systemTemperature = ref(null);
const temperatureLoading = ref(false);
const ipAddress = ref(null);
const ipLoading = ref(false);
const cpuPercent = ref(null);
const ram = ref(null);
const disk = ref(null);
const network = ref(null);
const resourcesLoading = ref(false);

// A skeleton until the first read answers, never again: a value that fails
// stays "not available" across the polls instead of pulsing through it.
const versionRead = ref(false);
const ipRead = ref(false);
const temperatureRead = ref(false);
const resourcesRead = ref(false);
const showVersionSkeleton = computed(() => !versionRead.value);
const showIpSkeleton = computed(() => !ipRead.value);
const showTempSkeleton = computed(() => !temperatureRead.value);
const showResourcesSkeleton = computed(() => !resourcesRead.value);

const ramPercent = computed(() => {
  if (!ram.value) return 0;
  return Math.round((ram.value.used_mb / ram.value.total_mb) * 100);
});

// A Pi 5 with its fan idles well above 35 °C (the fan's setpoint defaults to
// 65 °C). The firmware throttles from 80 °C and holds the SoC near 85 °C, so
// the last tenth of the bar is only reached while throttling.
const TEMP_MIN_C = 35;
const TEMP_MAX_C = 90;

const temperaturePercent = computed(() => {
  if (systemTemperature.value === null) return 0;
  const ratio = (systemTemperature.value - TEMP_MIN_C) / (TEMP_MAX_C - TEMP_MIN_C);
  return Math.round(Math.min(1, Math.max(0, ratio)) * 100);
});

const diskPercent = computed(() => {
  if (!disk.value) return 0;
  return Math.round((disk.value.used_gb / disk.value.total_gb) * 100);
});

// Decimal prefixes, as the unit names say (a kilobyte is 1000 bytes), so each
// unit hands over at 1000 and none ever shows four digits.
function formatRate(bytesPerSec) {
  if (bytesPerSec < 1000) return formatUnit(Math.round(bytesPerSec), 'B/s');
  const kb = Math.round(bytesPerSec / 1000);
  if (kb < 1000) return formatUnit(kb, 'kB/s');
  return formatUnit(bytesPerSec / 1e6, 'MB/s', ONE_DECIMAL);
}

async function loadMiloVersion() {
  if (versionLoading.value) return;
  versionLoading.value = true;
  const result = await apiCall.get('/api/programs/milo/installed', {
    category: 'system',
    message: 'Error loading Milo version'
  });
  miloVersion.value = result.ok ? (result.data.installed?.versions?.main || null) : null;
  versionLoading.value = false;
  versionRead.value = true;
}

async function loadSystemTemperature() {
  if (temperatureLoading.value) return;
  temperatureLoading.value = true;
  const result = await apiCall.get('/api/system/temperature', {
    category: 'system',
    message: 'Error loading temperature',
    checkStatus: true
  });
  systemTemperature.value = (result.ok && result.data.temperature !== null) ? result.data.temperature : null;
  temperatureLoading.value = false;
  temperatureRead.value = true;
}

async function loadNetworkInfo() {
  if (ipLoading.value) return;
  ipLoading.value = true;
  const result = await apiCall.get('/api/system/network-info', {
    category: 'system',
    message: 'Error loading network info',
    checkStatus: true
  });
  ipAddress.value = (result.ok && result.data.ip !== null) ? result.data.ip : null;
  ipLoading.value = false;
  ipRead.value = true;
}

async function loadSystemResources() {
  if (resourcesLoading.value) return;
  resourcesLoading.value = true;
  const result = await apiCall.get('/api/system/resources', {
    category: 'system',
    message: 'Error loading system resources',
    checkStatus: true
  });
  if (result.ok) {
    cpuPercent.value = result.data.cpu_percent;
    ram.value = result.data.ram;
    disk.value = result.data.disk;
    network.value = result.data.network;
  }
  resourcesLoading.value = false;
  resourcesRead.value = true;
}

async function pollDynamicData() {
  await Promise.all([loadSystemTemperature(), loadSystemResources()]);
}

// Asked during setup, so the first frame already draws the skeletons: asked
// from onMounted, it drew "not available" first, which then faded out.
const firstLoad = Promise.all([loadMiloVersion(), loadNetworkInfo(), pollDynamicData()]);

onMounted(async () => {
  await firstLoad;
  timer.setInterval(pollDynamicData, 5000); // auto-cleared on unmount
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
  background: var(--color-inset);
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

/* One cell for a skeleton and the value it reveals: they crossfade in place. */
.info-version,
.info-value {
  display: inline-grid;
  justify-items: end;
}

.info-version {
  justify-items: start;
}

.info-version > *,
.info-value > * {
  grid-area: 1 / 1;
}



.network-rates {
  display: inline-flex;
  gap: var(--space-03);
}

.rate-arrow {
  color: var(--color-text-secondary);
}

.rate-out {
  color: var(--color-text-secondary);
}

.bar-container {
  width: 100%;
  height: 6px;
  background: var(--color-fill-faint);
  border-radius: 3px;
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  background: var(--color-text-secondary);
  border-radius: 3px;
  transition: width var(--transition-normal);
}

.skeleton-line {
  display: inline-block;
  height: var(--line-height-mono-medium);
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
