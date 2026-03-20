<!-- frontend/src/components/setup/NetworkStep.vue -->
<template>
  <div class="wifi-step">
    <!-- Connection status card -->
    <div class="connection-section">
      <template v-if="loading">
        <!-- Skeleton rows -->
        <div class="connection-row">
          <div class="skeleton-icon shimmer"></div>
          <div class="skeleton-text-line shimmer" style="width: 70px"></div>
          <div class="skeleton-text-line shimmer connection-badge-skeleton"></div>
        </div>
        <div class="connection-row connection-row--wifi">
          <div class="skeleton-icon shimmer"></div>
          <div class="skeleton-text-line shimmer" style="width: 50px"></div>
          <div class="skeleton-text-line shimmer connection-badge-skeleton"></div>
        </div>
      </template>

      <template v-else>
        <!-- Ethernet row -->
        <div class="connection-row">
          <SvgIcon name="network" :size="24" />
          <span class="text-body">{{ t('network.ethernet') }}</span>
          <span class="connection-badge text-mono-small"
            :class="status.ethernet.connected ? 'connection-badge--connected' : 'connection-badge--disconnected'">
            {{ status.ethernet.connected ? t('network.connected') : t('network.notConnected') }}
          </span>
        </div>

        <!-- WiFi row (always visible) -->
        <div class="connection-row connection-row--wifi">
          <WifiSignal :signal="wifiCardSignal" :size="24" />
          <span class="text-body">{{ wifiDisplaySsid || t('setup.summary.wifi') }}</span>
          <span class="connection-badge text-mono-small" :class="wifiBadgeClass">
            {{ wifiBadgeLabel }}
          </span>
        </div>
      </template>
    </div>

    <!-- WiFi country selector -->
    <div class="country-row">
      <span class="country-row__label text-mono">{{ t('network.wifiCountry') }}</span>
      <Dropdown
        :model-value="country"
        :options="countryOptions"
        :placeholder="t('network.selectCountry')"
        @change="onCountryChange"
      />
    </div>

    <!-- Banner: only when no connection at all (hotspot / first boot) -->
    <div v-if="!status.ethernet.connected && !wifiDisplaySsid" class="wifi-banner text-mono-small" :class="{ 'wifi-banner--hotspot': hotspotActive }">
      {{ hotspotActive ? t('setup.wifi.hotspotBanner') : t('setup.wifi.hotspotWarning') }}
    </div>

    <!-- Network list -->
    <div class="wifi-networks">
      <span class="text-mono wifi-networks__label">{{ t('network.wifiNetworks') }}</span>

      <!-- Skeletons -->
      <template v-if="(loading || scanning) && visibleNetworks.length === 0">
        <div v-for="i in 3" :key="'sk-' + i" class="network-skeleton">
          <div class="skeleton-text-line shimmer" :style="{ width: (80 + i * 20) + 'px' }"></div>
          <div class="skeleton-text-line shimmer" style="width: 40px"></div>
        </div>
      </template>

      <!-- Empty state -->
      <div v-else-if="visibleNetworks.length === 0" class="wifi-empty text-mono">
        {{ t('network.noNetworks') }}
      </div>

      <!-- Networks -->
      <div v-for="network in visibleNetworks" :key="network.ssid" class="network-item"
        @click="selectNetwork(network)">
        <div class="network-item__row">
          <div class="network-item__ssid-row">
            <WifiSignal :signal="network.signal" :size="24" />
            <span class="text-body network-item__ssid">{{ network.ssid }}</span>
          </div>
          <SvgIcon name="caretDown" :size="24" color="var(--color-text-light)"
            class="network-item__caret" :class="{ 'network-item__caret--open': selectedSsid === network.ssid }" />
        </div>

        <!-- Expand: password + connect -->
        <div v-if="selectedSsid === network.ssid" class="network-item__expand" @click.stop>
          <InputText v-if="network.security" v-model="password" type="password"
            :placeholder="t('network.password')" @submit="handleConnect(network)" />
          <Button variant="brand" :loading="connecting"
            :disabled="connecting || (network.security && !password)"
            @click="handleConnect(network)">
            {{ connecting
              ? (hotspotActive ? t('network.saving') : t('network.connecting'))
              : (hotspotActive ? t('network.save') : t('network.connect')) }}
          </Button>
          <span v-if="connectError" class="wifi-error text-mono-small">{{ connectError }}</span>
        </div>
      </div>

      <!-- Refresh button -->
      <Button variant="background-strong" size="medium" left-icon="arrowsClockwise"
        :loading="scanning" :disabled="scanning"
        @click="scanNetworks">
        {{ t('network.refresh') }}
      </Button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, watch } from 'vue';
import { useI18n, i18n } from '@/services/i18n';
import { useWifi } from '@/composables/useWifi';
import { wifiCountryOptions, LANGUAGE_TO_COUNTRY } from '@/constants/wifiCountries';
import WifiSignal from '@/components/settings/categories/wifi/WifiSignal.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const { t } = useI18n();

const {
  status,
  networks,
  country,
  loading,
  scanning,
  connecting,
  connectError,
  selectedSsid,
  password,
  selectNetwork,
  scanNetworks,
  connectToNetwork,
  saveNetwork,
  setCountry,
  initialize,
} = useWifi();

const countryOptions = computed(() => wifiCountryOptions(t));

async function onCountryChange(code) {
  try {
    await setCountry(code);
    scanNetworks();
  } catch {
    // setCountry already logs via logger
  }
}

const emit = defineEmits(['update:modelValue']);

const props = defineProps({
  modelValue: {
    type: String,
    default: null,
  },
  hotspotActive: {
    type: Boolean,
    default: false,
  },
});

// WiFi display: connected SSID or saved SSID (ready when ethernet takes over)
const wifiDisplaySsid = computed(() =>
  status.value.wifi.ssid || status.value.wifi.saved_ssid
);

const wifiCardSignal = computed(() => {
  if (status.value.wifi.connected) return status.value.wifi.signal;
  const ssid = wifiDisplaySsid.value;
  return ssid ? (networks.value.find(n => n.ssid === ssid)?.signal ?? null) : null;
});

const wifiBadgeClass = computed(() => {
  if (status.value.wifi.connected) return 'connection-badge--connected';
  if (wifiDisplaySsid.value) return 'connection-badge--ready';
  return 'connection-badge--disconnected';
});

const wifiBadgeLabel = computed(() => {
  if (status.value.wifi.connected) return t('network.connected');
  if (wifiDisplaySsid.value) return t('network.ready');
  return t('network.notConnected');
});

// Exclude connected/saved SSID from the list
const visibleNetworks = computed(() => {
  const ssid = wifiDisplaySsid.value;
  return networks.value.filter(n => !n.in_use && n.ssid !== ssid);
});

// Emit wifi SSID only (ethernet doesn't count — CTA shows "Skip" when no wifi configured)
watch(() => [status.value.wifi.connected, status.value.wifi.saved_ssid], () => {
  if (status.value.wifi.connected) {
    emit('update:modelValue', status.value.wifi.ssid || null);
  } else {
    emit('update:modelValue', status.value.wifi.saved_ssid || null);
  }
});

async function handleConnect(network) {
  if (props.hotspotActive) {
    // Save credentials without connecting — hotspot stays active
    await saveNetwork(network, t);
  } else {
    await connectToNetwork(network, t);
  }
}

onMounted(async () => {
  await initialize();

  // Pre-select country based on language if no country is set yet
  if (!country.value) {
    const lang = i18n.getCurrentLanguage();
    const mapped = LANGUAGE_TO_COUNTRY[lang];
    if (mapped) {
      setCountry(mapped);
    }
  }
});
</script>

<style scoped>
.wifi-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
}

/* Country selector row (hardware-row pattern) */
.country-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-03);
}

.country-row__label {
  color: var(--color-text-secondary);
  width: 33%;
  flex-shrink: 0;
}

.country-row :deep(.dropdown) {
  flex: 1;
}

.wifi-banner {
  color: var(--color-text-secondary);
  padding: var(--space-03) var(--space-04);
  background: var(--color-background);
  border-radius: var(--radius-04);
}

.wifi-banner--hotspot {
  background: color-mix(in srgb, var(--color-brand) 10%, transparent);
  color: var(--color-text-primary);
}

/* Connection status card (grouped ethernet + wifi) */
.connection-section {
  background: var(--color-background);
  border-radius: var(--radius-04);
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
}

.connection-row {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.connection-row--wifi {
  margin-top: var(--space-03);
  padding-top: var(--space-03);
  border-top: 1px solid var(--color-border);
}

/* Connection row skeletons */
.skeleton-icon {
  width: 24px;
  height: 24px;
  border-radius: var(--radius-02);
  flex-shrink: 0;
}

.connection-badge-skeleton {
  margin-left: auto;
  width: 64px;
  height: 20px;
  border-radius: var(--radius-02);
}

.connection-section .skeleton-text-line,
.connection-section .skeleton-icon {
  --shimmer-base: var(--color-background);
  --shimmer-highlight: var(--color-background-medium-16);
}

.connection-badge {
  margin-left: auto;
  flex-shrink: 0;
  padding: var(--space-01) var(--space-02);
  border-radius: var(--radius-02);
}

.connection-badge--connected {
  background: color-mix(in srgb, var(--color-success) 16%, transparent);
  color: var(--color-success);
}

.connection-badge--ready {
  background: color-mix(in srgb, var(--color-brand) 16%, transparent);
  color: var(--color-brand);
}

.connection-badge--disconnected {
  background: color-mix(in srgb, var(--color-text-light) 16%, transparent);
  color: var(--color-text-secondary);
}

/* Network list */
.wifi-networks {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.wifi-networks__label {
  color: var(--color-text-secondary);
}

.wifi-networks > .btn {
  margin-top: var(--space-02);
}

.network-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-03);
  border-radius: var(--radius-04);
  background: var(--color-background);
  cursor: pointer;
  transition: background-color var(--transition-fast), var(--transition-press);
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
  gap: var(--space-03);
  min-width: 0;
}

.network-item__ssid {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.network-item__caret {
  flex-shrink: 0;
  transform: rotate(-90deg);
  transition: transform var(--transition-fast);
}

.network-item__caret--open {
  transform: rotate(-180deg);
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
  height: 48px;
  padding: 0 var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background);
}

.network-skeleton .skeleton-text-line {
  --shimmer-base: var(--color-background);
  --shimmer-highlight: var(--color-background-medium-16);
}
</style>
