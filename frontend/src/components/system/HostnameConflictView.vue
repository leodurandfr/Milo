<!-- frontend/src/components/system/HostnameConflictView.vue -->
<!--
  Full-screen takeover shown when another Milō server has claimed `milo.local`
  on the local network. Blocks the rest of the UI until the conflict is gone:
  either the user shuts down this device, or the periodic re-check (every 5
  minutes) detects that the other Milō has been turned off.
-->
<template>
  <div class="hostname-conflict-view">
    <Logo position="top" :visible="true" />

    <div class="content">
      <MessageContent
        icon="network"
        :title="t('system.hostnameConflict.title')"
        :subtitle="t('system.hostnameConflict.subtitle')"
        :cta-label="t('system.hostnameConflict.recheck')"
        cta-variant="background-strong"
        :cta-click="handleRecheck"
        :cta-loading="systemStore.rechecking"
        :cta-secondary-label="t('system.hostnameConflict.shutdown')"
        cta-secondary-variant="brand"
        :cta-secondary-click="handleShutdown"
      />
    </div>
  </div>
</template>

<script setup>
import axios from 'axios';
import { useSystemStore } from '@/stores/systemStore';
import { useI18n } from '@/services/i18n';
import { apiCall } from '@/services/apiCall';
import Logo from '@/components/ui/Logo.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const { t } = useI18n();
const systemStore = useSystemStore();

function handleRecheck() {
  systemStore.recheckHostname();
}

function handleShutdown() {
  apiCall('system', 'Error during shutdown', async () => {
    await axios.post('/api/system/shutdown');
  });
}
</script>

<style scoped>
.hostname-conflict-view {
  position: fixed;
  inset: 0;
  z-index: 10000;
  background: var(--color-background);
  display: flex;
  align-items: center;
  justify-content: center;
}

.content {
  width: min(560px, 90vw);
  padding: var(--space-04);
}
</style>
