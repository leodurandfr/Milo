<!-- frontend/src/components/settings/categories/LanguageSettings.vue -->
<!-- Language and timezone: the two answers to "where am I, how do I read
     things". The timezone is normally already right — the first browser to
     open the UI reports its own and the backend adopts it while nothing has
     been chosen (App.vue::adoptBrowserTimezone) — so this is the correction,
     not the question. -->
<template>
  <SectionStack>
    <SectionCard>
      <div class="language-grid">
        <ListItemButton
          v-for="language in availableLanguages"
          :key="language.code"
          :title="language.name"
          action="radio"
          :model-value="currentLanguage === language.code"
          @click="selectLanguage(language.code)"
        >
          <template #icon>
            <img :src="getFlagIcon(language.code)" :alt="language.name" />
          </template>
        </ListItemButton>
      </div>
    </SectionCard>

    <SectionCard :title="t('timezone.title')">
      <!-- Area then Location: ~490 zones in one dropdown is unusable with a
           finger, and every zone the backend returns has both halves. -->
      <SettingItem :label="t('timezone.area')" inline>
        <Dropdown :model-value="selectedArea" :options="areaOptions"
          :placeholder="t('timezone.selectArea')" :disabled="saving" @change="selectArea" />
      </SettingItem>

      <SettingItem :label="t('timezone.location')" inline>
        <Dropdown :model-value="selectedLocation" :options="locationOptions"
          :placeholder="t('timezone.selectLocation')" :disabled="saving || !selectedArea"
          @change="selectLocation" />
      </SettingItem>

      <span v-if="timezoneError" class="timezone-error text-mono-small">{{ timezoneError }}</span>
    </SectionCard>
  </SectionStack>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSettingsAPI } from '@/composables/useSettingsAPI';
import { apiCall } from '@/services/apiCall';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import SettingItem from '@/components/settings/SettingItem.vue';

import franceIcon from '@/assets/flags-icons/france.svg';
import unitedKingdomIcon from '@/assets/flags-icons/united-kingdom.svg';
import spainIcon from '@/assets/flags-icons/spain.svg';
import indiaIcon from '@/assets/flags-icons/india.svg';
import chinaIcon from '@/assets/flags-icons/china.svg';
import portugalIcon from '@/assets/flags-icons/portugal.svg';
import italyIcon from '@/assets/flags-icons/italy.svg';
import germanyIcon from '@/assets/flags-icons/germany.svg';

const flagIcons = {
  french: franceIcon,
  english: unitedKingdomIcon,
  spanish: spainIcon,
  hindi: indiaIcon,
  chinese: chinaIcon,
  portuguese: portugalIcon,
  italian: italyIcon,
  german: germanyIcon
};

const { t, getAvailableLanguages, getCurrentLanguage } = useI18n();
const { updateSetting } = useSettingsAPI();

const availableLanguages = computed(() => getAvailableLanguages());
const currentLanguage = computed(() => getCurrentLanguage());

function getFlagIcon(languageCode) {
  return flagIcons[languageCode] || '';
}

async function selectLanguage(languageCode) {
  await updateSetting('language', { language: languageCode });
}

// === Timezone ===
const zones = ref([]);
const selectedArea = ref('');
const selectedLocation = ref('');
const saving = ref(false);
const timezoneError = ref(null);

/** `Europe/Paris` → `['Europe', 'Paris']`; `America/Argentina/Salta` keeps its
 *  tail whole so the second dropdown stays a single choice. */
function split(zone) {
  const cut = zone.indexOf('/');
  return [zone.slice(0, cut), zone.slice(cut + 1)];
}

const areaOptions = computed(() => {
  const areas = [...new Set(zones.value.map(zone => split(zone)[0]))].sort();
  return areas.map(area => ({ label: area.replace(/_/g, ' '), value: area }));
});

const locationOptions = computed(() =>
  zones.value
    .filter(zone => split(zone)[0] === selectedArea.value)
    .map(zone => ({ label: split(zone)[1].replace(/_/g, ' '), value: split(zone)[1] }))
    .sort((a, b) => a.label.localeCompare(b.label))
);

async function loadTimezone() {
  const result = await apiCall.get('/api/system/timezone', {
    category: 'system',
    message: 'Failed to load timezone'
  });
  if (!result.ok) return;
  zones.value = result.data.data.available || [];
  const current = result.data.data.timezone;
  if (current && current.includes('/')) {
    [selectedArea.value, selectedLocation.value] = split(current);
  }
}

function selectArea(area) {
  selectedArea.value = area;
  // A location from the previous area names nothing in this one; blanking it
  // is what keeps the pair from ever reading as a zone that does not exist.
  selectedLocation.value = '';
}

async function selectLocation(location) {
  const previous = selectedLocation.value;
  selectedLocation.value = location;
  timezoneError.value = null;
  saving.value = true;

  const result = await apiCall.put('/api/system/timezone',
    { timezone: `${selectedArea.value}/${location}` }, {
      category: 'system',
      message: 'Failed to set timezone',
      errorRef: timezoneError
    });

  saving.value = false;
  if (!result.ok) selectedLocation.value = previous;
}

onMounted(loadTimezone);
</script>

<style scoped>
.language-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-01);
}

.timezone-error {
  color: var(--color-error);
}

@media (max-aspect-ratio: 4/3) {
  .language-grid {
    grid-template-columns: 1fr;
  }
}
</style>
