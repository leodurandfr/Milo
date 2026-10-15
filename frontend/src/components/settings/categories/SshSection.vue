<!-- frontend/src/components/settings/categories/SshSection.vue -->
<!-- The SSH switch and the way to the account password, for the server
     (System) and for a satellite (its client panel). The factory password is
     identical on every Milō and published with the source, so an open SSH door
     is worth a word — but as a fact, and only while the door is open. Not as a
     precondition: nothing on the API authenticates, so refusing to open it would
     have stopped the owner and nobody else. -->
<template>
  <SectionCard :description="description">
    <template #header>
      <div class="ssh-header">
        <h2 class="heading-2">{{ t('system.ssh.title') }}</h2>
        <Toggle :model-value="state.enabled" :disabled="busy" @change="$emit('toggle', $event)" />
      </div>
    </template>

    <!-- Wraps: the button drops under the command when the row is too
         narrow for both, rather than squeezing the command into a scroll.
         It stays with SSH closed too: the same password guards sudo on the
         unit itself, and it is better changed before the door opens. -->
    <div class="ssh-command-row">
      <div v-if="state.enabled && user" class="ssh-command text-mono-medium">
        ssh {{ user }}@{{ host }}
      </div>
      <Button size="small" variant="tinted" @click="$emit('open-password')">
        {{ t('system.password.change') }}
      </Button>
    </div>
    <!-- The marker speaks for the `milo` account only; with no such account
         there is nothing it can say. -->
    <NoticeBox v-if="state.enabled && user && state.passwordIsDefault" kind="warning">
      {{ factoryPasswordNotice }}
    </NoticeBox>

    <NoticeBox v-if="error">{{ error }}</NoticeBox>
  </SectionCard>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import SectionCard from '@/components/ui/SectionCard.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import Button from '@/components/ui/Button.vue';
import Toggle from '@/components/ui/Toggle.vue';

defineProps({
  /** { enabled, passwordIsDefault } */
  state: { type: Object, required: true },
  host: { type: String, required: true },
  /** The account SSH logs into; null hides the command (a unit with none to offer). */
  user: { type: String, default: null },
  description: { type: String, required: true },
  factoryPasswordNotice: { type: String, required: true },
  busy: { type: Boolean, default: false },
  error: { type: String, default: null },
});

defineEmits(['toggle', 'open-password']);

const { t } = useI18n();
</script>

<style scoped>
.ssh-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
}

.ssh-command-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-03);
}

.ssh-command {
  flex: 1 0 auto;
  padding: var(--space-03);
  border-radius: var(--radius-04);
  background: var(--color-inset);
  color: var(--color-text-secondary);
  overflow-x: auto;
  white-space: nowrap;
}
</style>
