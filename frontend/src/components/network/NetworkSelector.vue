<!-- frontend/src/components/network/NetworkSelector.vue -->
<!-- Reusable WiFi network picker: country selector + scan list + password input. -->
<!-- Owns its selectedSsid/password state via useWifi(); exposes connect/save closures via the `action` slot. -->
<template>
  <div class="network-selector">
    <!-- WiFi country selector -->
    <div v-if="showCountry" class="country-row">
      <span class="country-row__label text-mono">{{ t('network.wifiCountry') }}</span>
      <Dropdown
        :model-value="country"
        :options="countryOptions"
        :placeholder="t('network.selectCountry')"
        @change="onCountryChange"
      />
    </div>

    <!-- Network list -->
    <div class="wifi-networks">
      <span v-if="showLabel" class="text-mono wifi-networks__label">{{ t('network.wifiNetworks') }}</span>

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

        <!-- Expand: password + action slot + error -->
        <div v-if="selectedSsid === network.ssid" class="network-item__expand" @click.stop>
          <InputText v-if="network.security" v-model="password" type="password"
            :placeholder="t('network.password')" @submit="onPasswordSubmit(network)" />
          <slot
            name="action"
            :network="network"
            :password="password"
            :security="network.security"
            :connecting="connecting"
            :connect-error="connectError"
            :connect="() => connectToNetwork(network, t)"
            :save="() => saveNetwork(network, t)"
          />
          <span v-if="showConnectError && connectError" class="wifi-error text-mono-small">{{ connectError }}</span>
        </div>
      </div>

      <!-- Refresh -->
      <Button variant="background-strong" size="medium" left-icon="arrowClockwise"
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

const { t, getCurrentLanguage } = useI18n();

const props = defineProps({
  // Hide a specific SSID from the list (e.g., the currently-connected one).
  excludeSsid: { type: String, default: null },
  showCountry: { type: Boolean, default: true },
  showLabel: { type: Boolean, default: true },
  // Action triggered when user presses Enter in the password field.
  // 'connect' → live connect to wifi, 'save' → just persist the profile, null → emit `submit` to parent.
  submitAction: {
    type: String,
    default: null,
    validator: (v) => v === null || v === 'connect' || v === 'save',
  },
  // Render the connect error inline. Set to false in flows where the parent
  // never invokes the slot's `connect`/`save` closures (the ref can't fill,
  // so the conditional DOM is dead and confusing).
  showConnectError: { type: Boolean, default: true },
});

const emit = defineEmits(['update:wifi', 'submit']);

const {
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

const countryOptions = computed(() => wifiCountryOptions(getCurrentLanguage()));

const visibleNetworks = computed(() =>
  networks.value.filter(n => !n.in_use && n.ssid !== props.excludeSsid)
);

async function onCountryChange(code) {
  try {
    await setCountry(code);
    scanNetworks();
  } catch {
    // setCountry already logs via logger
  }
}

function onPasswordSubmit(network) {
  if (props.submitAction === 'connect') {
    connectToNetwork(network, t);
  } else if (props.submitAction === 'save') {
    saveNetwork(network, t);
  } else {
    emit('submit', network);
  }
}

watch([selectedSsid, password], () => {
  if (!selectedSsid.value) {
    emit('update:wifi', null);
    return;
  }
  const network = networks.value.find(n => n.ssid === selectedSsid.value);
  emit('update:wifi', {
    ssid: selectedSsid.value,
    password: password.value,
    security: network?.security || '',
  });
});

onMounted(async () => {
  await initialize();

  // Pre-select country based on language if not already set.
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
.network-selector {
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
  min-width: 0;
}

.network-item__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-03);
  min-width: 0;
}

.network-item__ssid-row {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  min-width: 0;
  flex: 1;
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
