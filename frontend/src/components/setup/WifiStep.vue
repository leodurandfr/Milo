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

      <!-- Ethernet status (minimal) -->
      <div v-if="wifi.status.ethernet.connected" class="ethernet-status">
        <SvgIcon name="network" :size="20" />
        <span class="text-mono">{{ t('network.ethernet') }}</span>
        <span class="ethernet-badge text-mono-small">{{ t('network.connected') }}</span>
      </div>

      <!-- Current WiFi status -->
      <div v-if="wifi.status.wifi.connected" class="wifi-status">
        <div class="wifi-status__info">
          <WifiSignal :signal="wifi.status.wifi.signal" :size="20" />
          <span class="heading-3">{{ wifi.status.wifi.ssid }}</span>
        </div>
        <span class="wifi-status__badge text-mono-small wifi-status__badge--connected">
          {{ t('network.connected') }}
        </span>
      </div>

      <!-- Network list -->
      <div class="wifi-networks">
        <div class="wifi-networks__header">
          <span class="text-mono wifi-networks__label">{{ t('network.otherNetworks') }}</span>
          <Button variant="background-strong" size="small" left-icon="arrowsClockwise"
            :loading="wifi.scanning" :disabled="wifi.scanning"
            @click="wifi.scanNetworks">
            {{ t('network.refresh') }}
          </Button>
        </div>

        <!-- Skeletons -->
        <template v-if="(wifi.loading || wifi.scanning) && visibleNetworks.length === 0">
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
          @click="wifi.selectNetwork(network)">
          <div class="network-item__row">
            <div class="network-item__ssid-row">
              <WifiSignal :signal="network.signal" :size="24" />
              <span class="text-body network-item__ssid">{{ network.ssid }}</span>
            </div>
            <SvgIcon name="caretDown" :size="24" color="var(--color-text-light)"
              class="network-item__caret" :class="{ 'network-item__caret--open': wifi.selectedSsid === network.ssid }" />
          </div>

          <!-- Expand: password + connect -->
          <div v-if="wifi.selectedSsid === network.ssid" class="network-item__expand" @click.stop>
            <InputText v-if="network.security" v-model="wifi.password" type="password"
              :placeholder="t('network.password')" @submit="handleConnect(network)" />
            <Button variant="brand" :loading="wifi.connecting"
              :disabled="wifi.connecting || (network.security && !wifi.password)"
              @click="handleConnect(network)">
              {{ wifi.connecting ? t('network.connecting') : t('network.connect') }}
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
import WifiSignal from '@/components/settings/categories/wifi/WifiSignal.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

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

// Emit network identifier whenever status changes (connected or saved)
watch(() => [wifi.status.wifi.connected, wifi.status.ethernet.connected, wifi.status.wifi.saved_ssid], () => {
  if (wifi.status.wifi.connected) {
    emit('update:modelValue', wifi.status.wifi.ssid || 'wifi');
  } else if (wifi.status.ethernet.connected) {
    emit('update:modelValue', 'ethernet');
  } else {
    emit('update:modelValue', wifi.status.wifi.saved_ssid || null);
  }
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

/* Ethernet status (minimal line) */
.ethernet-status {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-04);
  background: var(--color-background);
  border-radius: var(--radius-04);
}

.ethernet-badge {
  margin-left: auto;
  flex-shrink: 0;
  padding: var(--space-01) var(--space-02);
  border-radius: var(--radius-02);
  background: color-mix(in srgb, var(--color-success) 16%, transparent);
  color: var(--color-success);
}

/* WiFi status card */
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
  align-items: center;
  gap: var(--space-03);
  min-width: 0;
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
