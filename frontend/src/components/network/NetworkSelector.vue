<!-- frontend/src/components/network/NetworkSelector.vue -->
<!-- The one WiFi network list: country selector + scan list + password + the action. -->
<!-- Owns its selectedSsid/password state via useNetwork(). The wizard, Settings › Network
     and the satellite adoption all draw it; none redraws a row. -->
<template>
  <div class="network-selector">
    <!-- WiFi country selector -->
    <WifiCountrySelector v-if="showCountry" />

    <!-- Network list -->
    <div class="wifi-networks">
      <span v-if="showLabel" class="text-mono-medium wifi-networks__label">{{ t('network.wifiNetworks') }}</span>

      <!-- Skeletons -->
      <template v-if="(loading || scanning) && visibleNetworks.length === 0">
        <div v-for="i in 3" :key="'sk-' + i" class="network-skeleton">
          <div class="skeleton-text-line shimmer" :style="{ width: (80 + i * 20) + 'px' }"></div>
          <div class="skeleton-text-line shimmer" style="width: 40px"></div>
        </div>
      </template>

      <!-- Empty state -->
      <NoticeBox v-else-if="visibleNetworks.length === 0" kind="empty">
        {{ t('network.noNetworks') }}
      </NoticeBox>

      <!-- Networks -->
      <div v-for="network in visibleNetworks" :key="network.ssid" class="network-item"
        @click="selectNetwork(network)">
        <div class="network-item__row">
          <div class="network-item__ssid-row">
            <WifiSignal :signal="network.signal" :size="24" />
            <span class="text-body-medium network-item__ssid">{{ network.ssid }}</span>
          </div>
          <SvgIcon name="caretDown" :size="24" color="var(--color-text-tertiary)"
            class="network-item__caret" :class="{ 'network-item__caret--open': selectedSsid === network.ssid }" />
        </div>

        <!-- Expand: password, then the action and its error -->
        <div v-if="selectedSsid === network.ssid" class="network-item__expand" @click.stop>
          <InputText v-if="network.security" v-model="password" type="password"
            :placeholder="t('network.password')" @submit="act(network)" />
          <template v-if="action">
            <Button variant="brand" :loading="connecting"
              :disabled="connecting || (network.security && !password)"
              @click="act(network)">
              {{ connecting
                ? (action === 'save' ? t('network.saving') : t('network.connecting'))
                : (action === 'save' ? t('network.save') : t('network.connect')) }}
            </Button>
            <NoticeBox v-if="connectError" :detail="connectDetail">{{ connectError }}</NoticeBox>
          </template>
        </div>
      </div>

      <!-- Refresh -->
      <Button variant="control" size="medium" left-icon="arrowClockwise"
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
import { useNetwork } from '@/composables/useNetwork';
import { LANGUAGE_TO_COUNTRY } from '@/constants/wifiCountries';
import WifiSignal from '@/components/settings/categories/wifi/WifiSignal.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import WifiCountrySelector from '@/components/network/WifiCountrySelector.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';

const { t } = useI18n();

const props = defineProps({
  showCountry: { type: Boolean, default: true },
  showLabel: { type: Boolean, default: true },
  // A network the parent draws itself, left out of the list.
  excludeSsid: { type: String, default: null },
  // What the selected network's button (and Enter in its password) does:
  // 'connect' joins it now, 'save' only stores the profile (over the recovery
  // AP, where joining would take the radio from under the browser). null draws
  // no button: the parent acts on `update:wifi` with its own.
  action: {
    type: String,
    default: null,
    validator: (v) => v === null || v === 'connect' || v === 'save',
  },
});

// `saved`: a network was saved here (its SSID), the key typed with it untried.
const emit = defineEmits(['update:wifi', 'saved']);

const {
  networks,
  country,
  loading,
  scanning,
  connecting,
  connectError,
  connectDetail,
  selectedSsid,
  password,
  selectNetwork,
  scanNetworks,
  connectToNetwork,
  saveNetwork,
  setCountry,
  initialize,
} = useNetwork();

// Every network but the one in use: a network only saved stays, so its key can
// be typed again — unless the parent draws it itself.
const visibleNetworks = computed(() =>
  networks.value.filter(n => !n.in_use && n.ssid !== props.excludeSsid)
);

async function act(network) {
  if (props.action === 'connect') {
    connectToNetwork(network, t);
  } else if (props.action === 'save' && await saveNetwork(network, t)) {
    emit('saved', network.ssid);
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

  // Pre-select the country from the language, where the picker shows the
  // guess (the wizard). A list drawn without it — Settings › Network, which
  // has its own row, and the satellite adoption — never writes the regulatory
  // domain behind the reader's back.
  if (props.showCountry && !country.value) {
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
  background: var(--color-inset);
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

/* Skeleton */
.network-skeleton {
  display: flex;
  justify-content: space-between;
  align-items: center;
  height: 48px;
  padding: 0 var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-inset);
}
</style>
