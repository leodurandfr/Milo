<!-- frontend/src/components/settings/categories/music-library/WizardServer.vue -->
<!--
  Add-share wizard, step 1 — pick a server. Auto-discovers SMB/NFS servers on the
  LAN via mDNS on mount; picking one advances to the browse/connect step. A
  "manual" escape hatch falls back to the free-form form for servers that don't
  advertise themselves.
-->
<template>
  <SettingsContainer>
    <SettingsSection>
      <template #header>
        <div class="wiz-header">
          <h2 class="heading-2">{{ t('musicLibrary.shares.wizard.serverTitle') }}</h2>
          <p class="text-mono wiz-header__desc">{{ t('musicLibrary.shares.wizard.serverDesc') }}</p>
        </div>
      </template>

      <div v-if="discovering" class="wiz-center"><LoadingSpinner :size="40" /></div>

      <div v-else-if="servers.length" class="wiz-list">
        <ListItemButton v-for="server in servers" :key="`${server.type}:${server.host}`"
          variant="background" :title="server.name" :subtitle="`${server.address} · ${typeLabel(server.type)}`"
          action="caret" @click="$emit('select', server)">
          <template #icon>
            <span class="wiz-badge text-mono-small">{{ typeLabel(server.type) }}</span>
          </template>
        </ListItemButton>
      </div>

      <p v-else class="wiz-empty text-mono">{{ t('musicLibrary.shares.wizard.noServers') }}</p>

      <Button variant="background-strong" size="small" left-icon="search" :loading="discovering" @click="discover">
        {{ t('musicLibrary.shares.wizard.rescan') }}
      </Button>

      <button type="button" class="wiz-manual text-mono-small" @click="$emit('manual')">
        {{ t('musicLibrary.shares.wizard.manual') }}
      </button>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import Button from '@/components/ui/Button.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';

defineEmits(['select', 'manual']);

const { t } = useI18n();
const store = useMusicLibraryStore();

const servers = ref([]);
const discovering = ref(false);

function typeLabel(type) {
  return type === 'nfs' ? 'NFS' : 'SMB';
}

async function discover() {
  if (discovering.value) return;
  discovering.value = true;
  servers.value = await store.discoverServers();
  discovering.value = false;
}

onMounted(discover);
</script>

<style scoped>
.wiz-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.wiz-header__desc {
  color: var(--color-text-secondary);
}

.wiz-center {
  display: flex;
  justify-content: center;
  padding: var(--space-05);
}

.wiz-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.wiz-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  color: var(--color-text-secondary);
  background: var(--color-background-strong);
}

.wiz-empty {
  padding: var(--space-05);
  text-align: center;
  color: var(--color-text-secondary);
  background: var(--color-background);
  border-radius: var(--radius-04);
  border: 2px dashed var(--color-border);
}

.wiz-manual {
  align-self: center;
  padding: var(--space-02);
  color: var(--color-text-secondary);
  background: none;
  border: none;
  cursor: pointer;
  text-decoration: underline;
}
</style>
