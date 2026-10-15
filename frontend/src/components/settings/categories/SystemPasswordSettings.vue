<!-- frontend/src/components/settings/categories/SystemPasswordSettings.vue -->
<!-- The account password, one level under an SSH panel: the server's (System)
     or, given `macId`, a satellite's (its client panel). The save goes through
     a store, which outlives this view: the panel re-reads the SSH state when it
     mounts again, and that read waits for a save still in flight. -->
<template>
  <SectionStack>
    <SectionCard :description="t('system.password.description')">
      <InputText v-model="newPassword" type="password" :maxlength="128"
        :placeholder="t('system.password.newPlaceholder')" />
      <InputText v-model="confirmPassword" type="password" :maxlength="128"
        :placeholder="t('system.password.confirmPlaceholder')" @submit="savePassword" />

      <NoticeBox v-if="passwordError">{{ passwordError }}</NoticeBox>

      <Button variant="brand" :loading="savingPassword" :disabled="!canSavePassword || savingPassword"
        @click="savePassword">
        {{ passwordSaved ? t('system.password.saved') : t('system.password.save') }}
      </Button>
    </SectionCard>
  </SectionStack>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useTimer } from '@/composables/useTimer';
import { useSystemStore } from '@/stores/systemStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import SectionStack from '@/components/ui/SectionStack.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';

const props = defineProps({
  /** A satellite's MAC; absent for the server's own password. */
  macId: { type: String, default: null }
});

const { t } = useI18n();
const timer = useTimer();
const systemStore = useSystemStore();
const multiroomStore = useMultiroomStore();

const PASSWORD_MIN_LENGTH = 8;

const newPassword = ref('');
const confirmPassword = ref('');
const savingPassword = ref(false);
const passwordSaved = ref(false);
const passwordError = ref(null);

const canSavePassword = computed(() =>
  newPassword.value.length >= PASSWORD_MIN_LENGTH && confirmPassword.value.length > 0
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
  passwordSaved.value = true;
  timer.setTimeout(() => { passwordSaved.value = false; }, 3000);
}
</script>
