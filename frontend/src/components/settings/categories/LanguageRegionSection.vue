<!-- frontend/src/components/settings/categories/LanguageRegionSection.vue -->
<!-- Language and timezone, one card of Settings › System: the two answers to
     "where am I, how do I read things", set once at setup and rarely again.
     The timezone is normally already right — the first browser to open the UI
     reports its own, or the kiosk its WiFi country's, and the backend adopts it
     while nothing has been chosen (composables/useTimezone.js::adoptTimezone) —
     so this is the correction, not the question. Each language is named in
     itself, so someone who picked one they cannot read still finds their own. -->
<template>
  <SectionCard :title="t('settings.languageRegion')">
    <SettingItem :label="t('settings.language')" inline>
      <Dropdown :model-value="currentLanguage" :options="languageOptions"
        :disabled="languageSaving" @change="selectLanguage" />
    </SettingItem>

    <!-- A language the backend refused: the one in use stays selected. -->
    <NoticeBox v-if="languageError !== null" :detail="languageError">{{ t('settings.writeRefused') }}</NoticeBox>

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

    <NoticeBox v-if="timezoneError !== null" :detail="timezoneError">{{ t('settings.writeRefused') }}</NoticeBox>
  </SectionCard>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { apiCall } from '@/services/apiCall';
import { bcp47For } from '@/constants/countries';
import Dropdown from '@/components/ui/Dropdown.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import SettingItem from '@/components/settings/SettingItem.vue';

const { t, getAvailableLanguages, getCurrentLanguage, setLanguage } = useI18n();

const availableLanguages = computed(() => getAvailableLanguages());
const currentLanguage = computed(() => getCurrentLanguage());
const languageOptions = computed(() =>
  availableLanguages.value.map(language => ({ label: language.name, value: language.code }))
);

const languageSaving = ref(false);
const languageError = ref(null);

// One language in flight at a time: a refusal landing after a later tap was
// applied would say "not saved" under the language that was.
async function selectLanguage(languageCode) {
  languageError.value = null;
  languageSaving.value = true;
  await setLanguage(languageCode, { errorRef: languageError });
  languageSaving.value = false;
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

const collationLocale = computed(() => bcp47For(currentLanguage.value));

// tzdata files islands under their ocean (`Indian/Reunion`, `Pacific/Tahiti`),
// in English: translated, so someone on Réunion finds "Océan Indien".
const areaOptions = computed(() =>
  [...new Set(zones.value.map(zone => split(zone)[0]))]
    .map(area => ({ label: t(`timezone.areas.${area}`), value: area }))
    .sort((a, b) => a.label.localeCompare(b.label, collationLocale.value))
);

const locationOptions = computed(() =>
  zones.value
    .filter(zone => split(zone)[0] === selectedArea.value)
    .map(zone => ({ label: split(zone)[1].replace(/_/g, ' '), value: split(zone)[1] }))
    .sort((a, b) => a.label.localeCompare(b.label, collationLocale.value))
);

async function loadTimezone() {
  const result = await apiCall.get('/api/system/timezone', {
    category: 'system',
    message: 'Failed to load timezone'
  });
  if (!result.ok) return;
  zones.value = result.data.data.available || [];
  const current = result.data.data.timezone;
  // A zone the list does not offer — the shipped `Etc/UTC`, or one set by hand
  // outside Milō — leaves the placeholders up rather than an Area with no
  // Location under it.
  if (zones.value.includes(current)) {
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
