<!-- frontend/src/components/settings/categories/SshSection.vue -->
<!-- The SSH switch and the way to the account password, for the server
     (System) and for a satellite (its client panel). The factory password is
     identical on every Milō and published with the source, so an open SSH door
     is worth a word — but as a fact, and only while the door is open. Not as a
     precondition: nothing on the API authenticates, so refusing to open it would
     have stopped the owner and nobody else. -->
<template>
  <SectionCard>
    <template #header>
      <div class="ssh-header">
        <h2 class="heading-2">{{ t('system.ssh.title') }}</h2>
        <Toggle :model-value="switchPosition" :disabled="busy" @change="request" />
      </div>
    </template>

    <!-- The description is drawn here rather than by SectionCard so the button
         can sit beside it; on a portrait screen it drops under the text. It
         stays with SSH closed too: the same password guards sudo on the unit
         itself, and it is better changed before the door opens. -->
    <div class="ssh-intro">
      <p class="ssh-description text-body-medium">{{ description }}</p>
      <Button size="small" variant="tinted" class="ssh-password" @click="$emit('open-password')">
        {{ t('system.password.change') }}
      </Button>
    </div>
    <div v-if="state.enabled && user" class="ssh-command text-mono-medium">
      ssh {{ user }}@{{ host }}
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
import { ref, computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import SectionCard from '@/components/ui/SectionCard.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import Button from '@/components/ui/Button.vue';
import Toggle from '@/components/ui/Toggle.vue';

const props = defineProps({
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

const emit = defineEmits(['toggle', 'open-password']);

const { t } = useI18n();

// Both parents hold `busy` until the unit has opened or closed the door (a
// systemctl on the server, a round trip to a satellite) and only then pass the
// new state, so the switch shows the request for that wait and the state again
// once it ends: a refused request is back where it was.
const requested = ref(null);
const switchPosition = computed(() =>
  props.busy && requested.value !== null ? requested.value : props.state.enabled
);
watch(() => props.busy, (busy) => {
  if (!busy) requested.value = null;
});

function request(enabled) {
  requested.value = enabled;
  emit('toggle', enabled);
}
</script>

<style scoped>
.ssh-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-03);
}

.ssh-intro {
  display: flex;
  align-items: center;
  gap: var(--space-04);
}

/* Same look as SectionCard's own description, which this one stands in for. */
.ssh-description {
  flex: 1;
  margin: 0;
  color: var(--color-text-secondary);
}

.ssh-password {
  flex-shrink: 0;
}

.ssh-command {
  padding: var(--space-03);
  border-radius: var(--radius-04);
  background: var(--color-inset);
  color: var(--color-text-secondary);
  overflow-x: auto;
  white-space: nowrap;
}

@media (max-aspect-ratio: 4/3) {
  .ssh-intro {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
