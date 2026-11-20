<!-- frontend/src/components/settings/SettingsRefusal.vue -->
<!-- A write the server refused, said the same way on every settings screen:
     what happened in the reader's language, the server's detail folded under
     it. Pinned to the bottom of the panel, as an Apply button is, so it is
     seen wherever the control that moved back sits on a long page. Nothing is
     drawn while `detail` is null. A screen that already pins a button there
     puts both in one sticky footer: two sticky boxes at bottom 0 overlap. -->
<template>
  <div v-if="detail !== null" class="settings-refusal">
    <NoticeBox :detail="detail">
      <slot>{{ t('settings.writeRefused') }}</slot>
    </NoticeBox>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import NoticeBox from '@/components/ui/NoticeBox.vue';

defineProps({
  // The server's detail (useSettingsAPI's `refusal`, or an apiCall errorRef).
  detail: {
    type: String,
    default: null
  }
});

const { t } = useI18n();
</script>

<style scoped>
/* The notice's tint is translucent: a solid surface under it keeps the
   content scrolling beneath from showing through. */
.settings-refusal {
  position: sticky;
  bottom: 0;
  z-index: 10;
  border-radius: var(--radius-04);
  background: var(--color-surface-solid);
}
</style>
