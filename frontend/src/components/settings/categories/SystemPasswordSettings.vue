<!-- frontend/src/components/settings/categories/SystemPasswordSettings.vue -->
<!-- The account password, one level under an SSH panel: the server's (System)
     or, given `macId`, a satellite's (its client panel). The save goes through
     a store, which outlives this view: the panel re-reads the SSH state when it
     mounts again, and that read waits for a save still in flight. Once a save
     is confirmed the form gives way to a message until the view is left, so a
     return finds an empty form again. -->
<template>
  <MessageContent v-if="passwordChanged" icon="checkCircle" :title="t('system.password.changedTitle')"
    :details="t('system.password.changedDetails', { name: sshHost })" :cta-label="t('system.password.done')"
    :cta-click="() => emit('back')" />

  <SectionStack v-else>
    <SectionCard :description="t('system.password.description', { n: PASSWORD_MIN_LENGTH })">
      <InputText v-model="newPassword" type="password" :maxlength="128"
        :placeholder="t('system.password.newPlaceholder')" />
      <InputText v-model="confirmPassword" type="password" :maxlength="128"
        :placeholder="t('system.password.confirmPlaceholder')" @submit="savePassword" />

      <NoticeBox v-if="passwordError">{{ passwordError }}</NoticeBox>

      <Button variant="brand" :loading="savingPassword" :disabled="!canSavePassword || savingPassword"
        @click="savePassword">
        {{ t('system.password.save') }}
      </Button>
    </SectionCard>
  </SectionStack>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useSystemStore } from '@/stores/systemStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useSnapcastStore } from '@/stores/snapcastStore';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const props = defineProps({
  /** A satellite's MAC; absent for the server's own password. */
  macId: { type: String, default: null }
});

const emit = defineEmits(['back']);

const { t } = useI18n();
const systemStore = useSystemStore();
const multiroomStore = useMultiroomStore();
const snapcastStore = useSnapcastStore();

const PASSWORD_MIN_LENGTH = 8;

const newPassword = ref('');
const confirmPassword = ref('');
const savingPassword = ref(false);
const passwordChanged = ref(false);
const passwordError = ref(null);

// The address the SSH panel above logs into, as its `ssh user@host` line
// names it: milo.local for the server, the IP for a satellite.
const sshHost = computed(() => {
  if (!props.macId) return 'milo.local';
  return snapcastStore.clients.find(c => c.mac_id === props.macId)?.ip || props.macId;
});

// Enabled as soon as both fields hold something: the length and the match are
// told by savePassword, since a button greyed out for a rule nothing on screen
// states reads as broken.
const canSavePassword = computed(() =>
  newPassword.value.length > 0 && confirmPassword.value.length > 0
);

async function savePassword() {
  passwordError.value = null;

  if (newPassword.value !== confirmPassword.value) {
    passwordError.value = t('system.password.mismatch');
    return;
  }
  if (newPassword.value.length < PASSWORD_MIN_LENGTH) {
    passwordError.value = t('system.password.tooShort', { n: PASSWORD_MIN_LENGTH });
    return;
  }

  savingPassword.value = true;
  const saved = props.macId
    ? await multiroomStore.setClientPassword(props.macId, newPassword.value, { errorRef: passwordError })
    : await systemStore.setPassword(newPassword.value, { errorRef: passwordError });
  savingPassword.value = false;

  if (!saved) return;

  newPassword.value = '';
  confirmPassword.value = '';
  passwordChanged.value = true;
}
</script>
