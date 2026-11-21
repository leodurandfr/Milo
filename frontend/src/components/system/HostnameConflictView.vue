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
        :details="deviceDetails"
        :cta-label="t('system.hostnameConflict.recheck')"
        cta-variant="control"
        :cta-click="handleRecheck"
        :cta-loading="systemStore.rechecking"
        :cta-secondary-label="t('system.hostnameConflict.shutdown')"
        cta-secondary-variant="brand"
        :cta-secondary-click="handleShutdown"
      />

      <!-- The other reading of this screen: a speaker powered on before the
           server it should have joined finds no milo.local and stays a
           server. Two servers on one network are not supported, and a server
           never turns into a speaker in place — adding it from Multiroom only
           works on a unit that is not set up. So this says the way there, a
           reset (first boot again, role detection included) or a reflash, and
           offers no button for it. -->
      <p class="text-mono-medium conflict-speaker-hint">
        {{ t('system.hostnameConflict.speakerHint') }}
      </p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useSystemStore } from '@/stores/systemStore';
import { useI18n } from '@/services/i18n';
import { apiCall } from '@/services/apiCall';
import Logo from '@/components/ui/Logo.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const { t } = useI18n();
const systemStore = useSystemStore();

const deviceDetails = computed(() => {
  const name = systemStore.advertisedName;
  const ip = systemStore.localIp;
  if (!name && !ip) return null;
  return t('system.hostnameConflict.thisDevice', {
    name: name || '?',
    ip: ip || '?',
  });
});

function handleRecheck() {
  systemStore.recheckHostname();
}

function handleShutdown() {
  apiCall.post('/api/system/shutdown', null, {
    category: 'system',
    message: 'Error during shutdown',
  });
}
</script>

<style scoped>
.conflict-speaker-hint {
  max-width: 420px;
  margin: var(--space-05) auto 0;
  color: var(--color-text-secondary);
  text-align: center;
}

.hostname-conflict-view {
  position: fixed;
  inset: 0;
  z-index: 10000;
  background: var(--color-ground);
  display: flex;
  align-items: center;
  justify-content: center;
}

.content {
  width: min(560px, 90vw);
  padding: var(--space-04);
}
</style>
