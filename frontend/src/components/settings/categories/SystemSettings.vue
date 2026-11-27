<!-- frontend/src/components/settings/categories/SystemSettings.vue -->
<!-- The unit itself: what it is, the language and timezone it was set up
     with, then two rows down to what is set at install or reached for when
     something goes wrong — the hardware, and the advanced settings (SSH,
     troubleshooting, reset). -->
<template>
  <SectionStack>
    <SystemInfoSection />

    <LanguageRegionSection />

    <SectionCard>
      <div class="system-rows">
        <ListItemButton :title="t('settings.hardware')" action="caret" @click="$emit('open-hardware')" />
        <ListItemButton :title="t('settings.advanced')" action="caret" @click="$emit('open-advanced')" />
      </div>
    </SectionCard>
  </SectionStack>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import SystemInfoSection from '@/components/settings/categories/SystemInfoSection.vue';
import LanguageRegionSection from '@/components/settings/categories/LanguageRegionSection.vue';

defineEmits(['open-hardware', 'open-advanced']);

const { t } = useI18n();
</script>

<style scoped>
.system-rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}
</style>
