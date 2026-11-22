<!-- frontend/src/components/settings/categories/ThirdPartyLicenses.vue -->
<!-- The repository's THIRD_PARTY_NOTICES.md, as the build put it in dist/:
     every third-party program the unit runs, its license, and the written
     offer for GPL source. Shown inside Settings rather than opened as a page of
     its own, because the kiosk has no way back from a new window. -->
<template>
  <SectionStack>
    <SectionCard>
      <NoticeBox v-if="error !== null" :detail="error">{{ t('settings.readFailed') }}</NoticeBox>
      <pre v-else-if="notices !== null" class="notices text-mono-small">{{ notices }}</pre>
    </SectionCard>
  </SectionStack>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue';
import { useI18n } from '@/services/i18n';
import { apiCall } from '@/services/apiCall';
import { THIRD_PARTY_NOTICES_PATH } from '@/constants/thirdPartyNotices';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';

const { t } = useI18n();

const notices = ref(null);
const error = ref(null);
const controller = new AbortController();

onMounted(async () => {
  // Read as text, never parsed: nginx serves a .md file as octet-stream, which
  // the browser then decodes as UTF-8.
  const result = await apiCall.get(THIRD_PARTY_NOTICES_PATH, {
    category: 'system',
    message: 'Failed to load the third-party notices',
    errorRef: error,
    responseType: 'text',
    signal: controller.signal
  });
  if (result.ok) notices.value = result.data;
});

onBeforeUnmount(() => controller.abort());
</script>

<style scoped>
.notices {
  margin: 0;
  color: var(--color-text-secondary);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
