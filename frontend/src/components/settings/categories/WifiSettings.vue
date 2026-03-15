<!-- frontend/src/components/settings/categories/WifiSettings.vue -->
<template>
  <SettingsContainer>
    <!-- Current status -->
    <SettingsSection>
      <div class="wifi-status">
        <div class="wifi-status__info">
          <span class="heading-3">{{ status.connected ? status.ssid : t('wifi.disconnected') }}</span>
          <span class="text-mono wifi-status__detail">
            <template v-if="status.connected">
              <SignalDots :signal="status.signal" />
              <span class="wifi-status__ip">{{ status.ip_address }}</span>
            </template>
            <template v-else>
              {{ t('wifi.noConnection') }}
            </template>
          </span>
        </div>
        <span class="wifi-status__badge text-mono-small" :class="status.connected ? 'wifi-status__badge--connected' : 'wifi-status__badge--disconnected'">
          {{ status.connected ? t('wifi.connected') : t('wifi.disconnected') }}
        </span>
      </div>
    </SettingsSection>

    <!-- Known networks -->
    <SettingsSection v-if="knownNetworks.length > 0 || loading">
      <template #header>
        <SectionHeader :title="t('wifi.knownNetworks')" />
      </template>

      <!-- Skeletons -->
      <template v-if="loading && knownNetworks.length === 0">
        <div v-for="i in 2" :key="'sk-known-' + i" class="network-skeleton">
          <div class="skeleton-text-line shimmer" style="width: 120px"></div>
          <div class="skeleton-text-line shimmer" style="width: 40px"></div>
        </div>
      </template>

      <!-- Network list -->
      <div v-for="network in knownNetworks" :key="'known-' + network.ssid" class="network-item"
        :class="{ 'network-item--active': network.in_use }" @click="selectNetwork(network)">
        <div class="network-item__row">
          <span class="heading-3 network-item__ssid">{{ network.ssid }}</span>
          <div class="network-item__meta">
            <SignalDots :signal="network.signal" />
            <button v-if="!network.in_use" v-press type="button" class="network-item__forget text-mono-small"
              @click.stop="forgetNetwork(network.ssid)">
              {{ t('wifi.forget') }}
            </button>
            <span v-else class="network-item__connected text-mono-small">{{ t('wifi.connected') }}</span>
          </div>
        </div>

        <!-- Expand: password + connect (only for non-connected known networks) -->
        <div v-if="selectedSsid === network.ssid && !network.in_use" class="network-item__expand" @click.stop>
          <InputText v-if="network.security" v-model="password" type="password"
            :placeholder="t('wifi.password')" @submit="connectToNetwork(network)" />
          <Button variant="brand" :loading="connecting" :disabled="connecting || (network.security && !password)"
            @click="connectToNetwork(network)">
            {{ connecting ? t('wifi.connecting') : t('wifi.connect') }}
          </Button>
          <span v-if="connectError" class="wifi-error text-mono-small">{{ connectError }}</span>
        </div>
      </div>
    </SettingsSection>

    <!-- Other networks -->
    <SettingsSection>
      <template #header>
        <SectionHeader :title="t('wifi.otherNetworks')">
          <template #actions>
            <Button variant="background-strong" size="small" left-icon="arrowsClockwise"
              :loading="scanning" :disabled="scanning"
              @click="scanNetworks">
              {{ t('wifi.refresh') }}
            </Button>
          </template>
        </SectionHeader>
      </template>

      <!-- Skeletons -->
      <template v-if="loading && otherNetworks.length === 0">
        <div v-for="i in 4" :key="'sk-other-' + i" class="network-skeleton">
          <div class="skeleton-text-line shimmer" :style="{ width: (80 + i * 20) + 'px' }"></div>
          <div class="skeleton-text-line shimmer" style="width: 40px"></div>
        </div>
      </template>

      <!-- Empty state -->
      <div v-if="!loading && otherNetworks.length === 0" class="wifi-empty text-mono">
        {{ t('wifi.noNetworks') }}
      </div>

      <!-- Network list -->
      <div v-for="network in otherNetworks" :key="'other-' + network.ssid" class="network-item"
        @click="selectNetwork(network)">
        <div class="network-item__row">
          <div class="network-item__ssid-row">
            <span class="heading-3 network-item__ssid">{{ network.ssid }}</span>
            <span v-if="network.security" class="network-item__lock text-mono-small">{{ network.security }}</span>
          </div>
          <SignalDots :signal="network.signal" />
        </div>

        <!-- Expand: password + connect -->
        <div v-if="selectedSsid === network.ssid" class="network-item__expand" @click.stop>
          <InputText v-if="network.security" v-model="password" type="password"
            :placeholder="t('wifi.password')" @submit="connectToNetwork(network)" />
          <Button variant="brand" :loading="connecting" :disabled="connecting || (network.security && !password)"
            @click="connectToNetwork(network)">
            {{ connecting ? t('wifi.connecting') : t('wifi.connect') }}
          </Button>
          <span v-if="connectError" class="wifi-error text-mono-small">{{ connectError }}</span>
        </div>
      </div>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { logger } from '@/services/logger';
import axios from 'axios';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import SignalDots from '@/components/settings/categories/wifi/SignalDots.vue';

const { t } = useI18n();

// State
const status = ref({ connected: false, ssid: null, ip_address: null, signal: null });
const networks = ref([]);
const savedSsids = ref(new Set());
const loading = ref(true);
const scanning = ref(false);
const connecting = ref(false);
const connectError = ref('');
const selectedSsid = ref(null);
const password = ref('');

// Split networks into known and other
const knownNetworks = computed(() =>
  networks.value.filter(n => n.in_use || savedSsids.value.has(n.ssid))
);

const otherNetworks = computed(() =>
  networks.value.filter(n => !n.in_use && !savedSsids.value.has(n.ssid))
);

// Actions
function selectNetwork(network) {
  if (network.in_use) return;
  if (selectedSsid.value === network.ssid) {
    selectedSsid.value = null;
    password.value = '';
    connectError.value = '';
  } else {
    selectedSsid.value = network.ssid;
    password.value = '';
    connectError.value = '';
  }
}

async function loadStatus() {
  try {
    const res = await axios.get('/api/wifi/status');
    status.value = res.data.data;
  } catch (error) {
    logger.error('wifi', 'Failed to load WiFi status', error);
  }
}

async function loadSavedNetworks() {
  try {
    const res = await axios.get('/api/wifi/saved');
    savedSsids.value = new Set(res.data.data.map(n => n.ssid));
  } catch (error) {
    logger.error('wifi', 'Failed to load saved networks', error);
  }
}

async function scanNetworks() {
  scanning.value = true;
  try {
    const res = await axios.get('/api/wifi/networks');
    networks.value = res.data.data;
  } catch (error) {
    logger.error('wifi', 'Failed to scan networks', error);
  } finally {
    scanning.value = false;
  }
}

async function connectToNetwork(network) {
  connecting.value = true;
  connectError.value = '';
  try {
    const payload = { ssid: network.ssid, password: network.security ? password.value : null };
    const res = await axios.post('/api/wifi/connect', payload);
    status.value = res.data.data;
    selectedSsid.value = null;
    password.value = '';
    // Refresh lists to update in_use flags and saved
    await Promise.all([scanNetworks(), loadSavedNetworks()]);
  } catch (error) {
    const detail = error.response?.data?.detail;
    connectError.value = detail || t('wifi.connectFailed');
    logger.error('wifi', 'WiFi connection failed', error);
  } finally {
    connecting.value = false;
  }
}

async function forgetNetwork(ssid) {
  try {
    await axios.delete('/api/wifi/saved', { params: { ssid } });
    savedSsids.value.delete(ssid);
    // Trigger reactivity
    savedSsids.value = new Set(savedSsids.value);
  } catch (error) {
    logger.error('wifi', 'Failed to forget network', error);
  }
}

onMounted(async () => {
  await Promise.all([loadStatus(), loadSavedNetworks(), scanNetworks()]);
  loading.value = false;
});

</script>

<style scoped>
/* Status card */
.wifi-status {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-03);
}

.wifi-status__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.wifi-status__detail {
  color: var(--color-text-secondary);
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.wifi-status__ip {
  color: var(--color-text-secondary);
}

.wifi-status__badge {
  flex-shrink: 0;
  padding: var(--space-01) var(--space-02);
  border-radius: var(--radius-02);
}

.wifi-status__badge--connected {
  background: color-mix(in srgb, var(--color-success) 16%, transparent);
  color: var(--color-success);
}

.wifi-status__badge--disconnected {
  background: color-mix(in srgb, var(--color-error) 16%, transparent);
  color: var(--color-error);
}

/* Network items */
.network-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
  cursor: pointer;
  transition: background-color var(--transition-fast), var(--transition-press);
}

.network-item--active {
  cursor: default;
}

.network-item__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-03);
}

.network-item__ssid-row {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  min-width: 0;
}

.network-item__ssid {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.network-item__lock {
  color: var(--color-text-secondary);
  flex-shrink: 0;
}

.network-item__meta {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  flex-shrink: 0;
}

.network-item__forget {
  color: var(--color-error);
  background: none;
  border: none;
  cursor: pointer;
  padding: var(--space-01) var(--space-02);
  border-radius: var(--radius-02);
  transition: background-color var(--transition-fast), var(--transition-press);
}

.network-item__forget:hover {
  background: color-mix(in srgb, var(--color-error) 8%, transparent);
}

.network-item__connected {
  color: var(--color-success);
}

/* Expanded connect form */
.network-item__expand {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding-top: var(--space-02);
}

.wifi-error {
  color: var(--color-error);
}

/* Empty state */
.wifi-empty {
  color: var(--color-text-secondary);
  text-align: center;
  padding: var(--space-04);
}

/* Skeleton */
.network-skeleton {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-strong);
}

.network-skeleton .skeleton-text-line {
  --shimmer-base: var(--color-background-strong);
  --shimmer-highlight: var(--color-background-medium-16);
}
</style>
