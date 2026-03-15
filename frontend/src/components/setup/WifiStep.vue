<!-- frontend/src/components/setup/WifiStep.vue -->
<template>
  <div class="wifi-step">
    <!-- Post-connection message (hotspot mode: connection will drop) -->
    <div v-if="wifiConfigured" class="wifi-configured">
      <span class="heading-3 wifi-configured__title">{{ t('setup.wifi.hotspotConfigured') }}</span>
      <span class="text-mono-small wifi-configured__detail">{{ t('setup.wifi.hotspotConfiguredDetail') }}</span>
    </div>

    <template v-else>
      <!-- Contextual banner -->
      <div class="wifi-banner text-mono-small" :class="{ 'wifi-banner--hotspot': hotspotActive }">
        {{ hotspotActive ? t('setup.wifi.hotspotBanner') : t('setup.wifi.hotspotWarning') }}
      </div>

      <!-- Current status -->
      <div v-if="wifi.status.connected" class="wifi-status">
        <div class="wifi-status__info">
          <span class="heading-3">{{ wifi.status.ssid }}</span>
          <span class="text-mono wifi-status__detail">
            <SignalDots :signal="wifi.status.signal" />
            <span class="wifi-status__ip">{{ wifi.status.ip_address }}</span>
          </span>
        </div>
        <span class="wifi-status__badge text-mono-small">{{ t('wifi.connected') }}</span>
      </div>

      <!-- Network list -->
      <div class="wifi-networks">
        <div class="wifi-networks__header">
          <span class="text-mono wifi-networks__label">{{ t('wifi.otherNetworks') }}</span>
          <Button variant="background-strong" size="small" left-icon="arrowsClockwise"
            :loading="wifi.scanning" :disabled="wifi.scanning"
            @click="wifi.scanNetworks">
            {{ t('wifi.refresh') }}
          </Button>
        </div>

        <!-- Skeletons -->
        <template v-if="wifi.loading">
          <div v-for="i in 4" :key="'sk-' + i" class="network-skeleton">
            <div class="skeleton-text-line shimmer" :style="{ width: (80 + i * 20) + 'px' }"></div>
            <div class="skeleton-text-line shimmer" style="width: 40px"></div>
          </div>
        </template>

        <!-- Empty state -->
        <div v-else-if="visibleNetworks.length === 0" class="wifi-empty text-mono">
          {{ t('wifi.noNetworks') }}
        </div>

        <!-- Networks -->
        <div v-for="network in visibleNetworks" :key="network.ssid" class="network-item"
          @click="wifi.selectNetwork(network)">
          <div class="network-item__row">
            <div class="network-item__ssid-row">
              <span class="heading-3 network-item__ssid">{{ network.ssid }}</span>
              <span v-if="network.security" class="network-item__lock text-mono-small">{{ network.security }}</span>
            </div>
            <SignalDots :signal="network.signal" />
          </div>

          <!-- Expand: password + connect -->
          <div v-if="wifi.selectedSsid === network.ssid" class="network-item__expand" @click.stop>
            <InputText v-if="network.security" v-model="wifi.password" type="password"
              :placeholder="t('wifi.password')" @submit="handleConnect(network)" />
            <Button variant="brand" :loading="wifi.connecting"
              :disabled="wifi.connecting || (network.security && !wifi.password)"
              @click="handleConnect(network)">
              {{ wifi.connecting ? t('wifi.connecting') : t('wifi.connect') }}
            </Button>
            <span v-if="wifi.connectError" class="wifi-error text-mono-small">{{ wifi.connectError }}</span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { reactive, ref, computed, onMounted, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useWifi } from '@/composables/useWifi';
import SignalDots from '@/components/settings/categories/wifi/SignalDots.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';

const { t } = useI18n();
const wifi = reactive(useWifi());

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

const wifiConfigured = ref(false);

// Show all networks except the currently connected one
const visibleNetworks = computed(() =>
  wifi.networks.filter(n => !n.in_use)
);

// Emit connected SSID whenever status changes
watch(() => wifi.status.connected, (connected) => {
  emit('update:modelValue', connected ? wifi.status.ssid : null);
});

async function handleConnect(network) {
  if (props.hotspotActive) {
    // On hotspot: the connect call will likely error because wlan0 switches
    // from AP to STA mode, dropping our connection. Treat any outcome as success.
    try {
      await wifi.connectToNetwork(network, t);
    } catch {
      // Expected: connection dropped during AP→STA switch
    }
    wifiConfigured.value = true;
    emit('update:modelValue', network.ssid);
  } else {
    await wifi.connectToNetwork(network, t);
  }
}

onMounted(() => {
  wifi.initialize();
});
</script>

<style scoped>
.wifi-step {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.wifi-banner {
  color: var(--color-text-secondary);
  padding: var(--space-03) var(--space-04);
  background: var(--color-background);
  border-radius: var(--radius-04);
  line-height: 1.4;
}

.wifi-banner--hotspot {
  background: color-mix(in srgb, var(--color-brand) 10%, transparent);
  color: var(--color-text-primary);
}

/* Post-connection confirmation */
.wifi-configured {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-06) var(--space-04);
  text-align: center;
}

.wifi-configured__title {
  color: var(--color-success);
}

.wifi-configured__detail {
  color: var(--color-text-secondary);
  line-height: 1.4;
}

/* Status card */
.wifi-status {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-04);
  background: var(--color-background);
  border-radius: var(--radius-04);
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
  background: color-mix(in srgb, var(--color-success) 16%, transparent);
  color: var(--color-success);
}

/* Network list */
.wifi-networks {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.wifi-networks__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.wifi-networks__label {
  color: var(--color-text-secondary);
}

.network-item {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-04);
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
  background: var(--color-background);
}

.network-skeleton .skeleton-text-line {
  --shimmer-base: var(--color-background);
  --shimmer-highlight: var(--color-background-medium-16);
}
</style>
