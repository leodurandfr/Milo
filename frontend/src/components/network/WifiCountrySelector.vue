<!-- frontend/src/components/network/WifiCountrySelector.vue -->
<!--
  The WiFi regulatory domain, in one component.

  It used to exist twice with two different behaviours: the setup wizard applied
  it and rescanned, while the settings panel wrapped it in a pending →
  confirm → apply → reboot → poll-for-the-backend sequence of its own. The
  reboot was the odd one out, not the careful one — `milo-set-wifi-country` runs
  `iw reg set` immediately and only writes cmdline.txt for the *next* boot's
  initial domain, which is why the wizard has been getting away without one
  since the first release.

  So: one component, one behaviour, applied on change. The rescan is part of the
  answer rather than a courtesy — the domain decides which channels are legal,
  so the list of networks before and after a change is not the same list.
-->
<template>
  <div class="country-row">
    <span class="country-row__label text-mono-medium">{{ t('network.wifiCountry') }}</span>
    <Dropdown
      :model-value="country"
      :options="countryOptions"
      :placeholder="t('network.selectCountry')"
      @change="onCountryChange"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useNetwork } from '@/composables/useNetwork';
import { wifiCountryOptions } from '@/constants/wifiCountries';
import Dropdown from '@/components/ui/Dropdown.vue';

const { t, getCurrentLanguage } = useI18n();
const { country, scanNetworks, setCountry } = useNetwork();

const countryOptions = computed(() => wifiCountryOptions(getCurrentLanguage()));

async function onCountryChange(code) {
  try {
    await setCountry(code);
    scanNetworks();
  } catch {
    // setCountry already logs via logger
  }
}
</script>

<style scoped>
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

@media (max-aspect-ratio: 4/3) {
  .country-row {
    flex-direction: column;
    align-items: stretch;
  }

  .country-row__label {
    width: auto;
  }
}
</style>
