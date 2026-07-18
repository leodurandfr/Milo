<!-- frontend/src/components/settings/categories/music-library/ManageShare.vue -->
<!--
  Add / edit a network share (SMB or NFS). Submits to the /music-library/shares
  routes via the store, which persists the config, mounts it read-only under
  /media/milo through milo-mount, and rescans Navidrome.

  - SMB (cifs) accepts optional credentials; NFS (AUTH_SYS) takes none, so the
    credential fields are hidden and never sent for NFS.
  - The password is write-only: on edit, an empty field keeps the saved password
    (the backend treats an omitted password as "keep the existing cred file").
  - Both add and edit use an explicit Submit — a save remounts and rescans, so it
    is a deliberate action, not an auto-saved keystroke.
-->
<template>
  <SettingsSection>
    <form class="share-form" @submit.prevent="handleSubmit">
      <!-- Type: SMB / NFS -->
      <div class="form-group">
        <label class="text-mono">{{ t('musicLibrary.shares.type') }}</label>
        <ButtonGroup v-model="form.type" :options="typeOptions" @change="applyType" />
      </div>

      <div class="form-group">
        <label class="text-mono">{{ t('musicLibrary.shares.name') }} *</label>
        <InputText v-model="form.name" :placeholder="t('musicLibrary.shares.namePlaceholder')" :maxlength="128" />
        <span class="text-mono share-form__hint">{{ t('musicLibrary.shares.nameHint') }}</span>
      </div>

      <div class="form-group">
        <label class="text-mono">{{ t('musicLibrary.shares.host') }} *</label>
        <InputText v-model="form.host" :placeholder="t('musicLibrary.shares.hostPlaceholder')" :maxlength="255" />
      </div>

      <div class="form-group">
        <label class="text-mono">{{ pathLabel }} *</label>
        <InputText v-model="form.path" :placeholder="pathPlaceholder" :maxlength="1024" />
        <span class="text-mono share-form__hint">{{ pathHint }}</span>
      </div>

      <!-- Credentials (SMB only) -->
      <template v-if="form.type === 'cifs'">
        <div class="form-row">
          <div class="form-group">
            <label class="text-mono">{{ t('musicLibrary.shares.username') }}</label>
            <InputText v-model="form.username" :placeholder="t('musicLibrary.shares.usernamePlaceholder')" :maxlength="128" />
          </div>

          <div class="form-group">
            <label class="text-mono">{{ t('musicLibrary.shares.domain') }}</label>
            <InputText v-model="form.domain" :placeholder="t('musicLibrary.shares.domainPlaceholder')" :maxlength="128" />
          </div>
        </div>

        <div class="form-group">
          <label class="text-mono">{{ t('musicLibrary.shares.password') }}</label>
          <InputText v-model="form.password" type="password" :maxlength="256"
            :placeholder="passwordPlaceholder" />
          <span v-if="isEditMode && share?.has_credentials" class="text-mono share-form__hint">
            {{ t('musicLibrary.shares.passwordKeepHint') }}
          </span>
        </div>
      </template>

      <!-- NFS help note -->
      <p v-else class="text-mono share-form__note">{{ t('musicLibrary.shares.nfsNoCredentials') }}</p>

      <!-- Error -->
      <div v-if="errorMessage" class="share-form__error text-mono">{{ errorMessage }}</div>

      <Button variant="brand" size="medium" type="submit" class="share-form__submit"
        :loading="isSubmitting" :disabled="isSubmitting || !isValid">
        {{ isEditMode ? t('musicLibrary.shares.save') : t('musicLibrary.shares.add') }}
      </Button>

      <!-- Remove (edit only) — two-tap inline confirm, like the power menu. -->
      <Button v-if="isEditMode" variant="important" size="medium" type="button"
        :loading="isRemoving" :disabled="isSubmitting || isRemoving" @click="handleRemove">
        {{ confirmRemove ? t('musicLibrary.shares.confirmRemove') : t('musicLibrary.shares.remove') }}
      </Button>
    </form>
  </SettingsSection>
</template>

<script setup>
import { reactive, ref, computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import InputText from '@/components/ui/InputText.vue';
import ButtonGroup from '@/components/ui/ButtonGroup.vue';
import Button from '@/components/ui/Button.vue';

const props = defineProps({
  mode: {
    type: String,
    default: 'add',
    validator: (v) => ['add', 'edit'].includes(v),
  },
  share: {
    type: Object,
    default: null,
  },
});

const emit = defineEmits(['back', 'success']);

const { t } = useI18n();
const store = useMusicLibraryStore();

const isEditMode = computed(() => props.mode === 'edit');

const typeOptions = computed(() => [
  { label: 'SMB', value: 'cifs' },
  { label: 'NFS', value: 'nfs' },
]);

const form = reactive({
  type: 'cifs',
  name: '',
  host: '',
  path: '',
  username: '',
  password: '',
  domain: '',
});

const isSubmitting = ref(false);
const isRemoving = ref(false);
const confirmRemove = ref(false);
const errorMessage = ref('');

// Path is an SMB share name vs an NFS export path — label/placeholder adapt.
const pathLabel = computed(() =>
  form.type === 'nfs' ? t('musicLibrary.shares.exportPath') : t('musicLibrary.shares.shareName')
);
const pathPlaceholder = computed(() =>
  form.type === 'nfs'
    ? t('musicLibrary.shares.exportPlaceholder')
    : t('musicLibrary.shares.sharePlaceholder')
);
const pathHint = computed(() =>
  form.type === 'nfs' ? t('musicLibrary.shares.exportHint') : t('musicLibrary.shares.shareHint')
);

// On edit, a stored password stays server-side (write-only); show dots as a
// placeholder to signal one is saved, and an empty field keeps it.
const passwordPlaceholder = computed(() =>
  isEditMode.value && props.share?.has_credentials
    ? '••••••'
    : t('musicLibrary.shares.passwordPlaceholder')
);

const isValid = computed(
  () => form.name.trim() && form.host.trim() && form.path.trim()
);

function initForm() {
  errorMessage.value = '';
  confirmRemove.value = false;
  if (isEditMode.value && props.share) {
    form.type = props.share.type || 'cifs';
    form.name = props.share.name || '';
    form.host = props.share.host || '';
    form.path = props.share.path || '';
    // username/domain are non-secret metadata — prefill so the login is visible.
    form.username = props.share.username || '';
    form.domain = props.share.domain || '';
  } else {
    form.type = 'cifs';
    form.name = '';
    form.host = '';
    form.path = '';
    form.username = '';
    form.domain = '';
  }
  // The password is write-only (never returned by the API) — always start blank.
  form.password = '';
}

watch(() => props.share, initForm, { immediate: true });

// Switching to NFS clears the credential fields so they can't leak into the
// payload (the backend rejects credentials on an NFS share outright). Shared by
// the protocol toggle and by picking a discovered server.
function applyType(type) {
  form.type = type;
  if (type === 'nfs') {
    form.username = '';
    form.password = '';
    form.domain = '';
  }
  confirmRemove.value = false;
}

function buildPayload() {
  const payload = {
    type: form.type,
    name: form.name.trim(),
    host: form.host.trim(),
    path: form.path.trim(),
  };
  if (form.type === 'cifs') {
    if (form.username.trim()) payload.username = form.username.trim();
    if (form.domain.trim()) payload.domain = form.domain.trim();
    if (form.password) payload.password = form.password; // omitted → keep existing (edit) / guest (add)
  }
  return payload;
}

async function handleSubmit() {
  if (isSubmitting.value || !isValid.value) return;
  errorMessage.value = '';
  isSubmitting.value = true;
  const payload = buildPayload();
  const result = isEditMode.value
    ? await store.updateShare(props.share.id, payload)
    : await store.addShare(payload);
  isSubmitting.value = false;
  if (result.ok) {
    emit('success');
  } else {
    errorMessage.value =
      typeof result.error === 'string' && result.error
        ? result.error
        : t('musicLibrary.shares.errorGeneric');
  }
}

async function handleRemove() {
  if (isRemoving.value) return;
  if (!confirmRemove.value) {
    confirmRemove.value = true;
    return;
  }
  isRemoving.value = true;
  const ok = await store.removeShare(props.share.id);
  isRemoving.value = false;
  if (ok) {
    emit('success');
  } else {
    confirmRemove.value = false;
    errorMessage.value = t('musicLibrary.shares.errorGeneric');
  }
}
</script>

<style scoped>
.share-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.form-group label {
  color: var(--color-text-secondary);
}

.share-form__hint {
  color: var(--color-text-light);
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-03);
}

.share-form__note {
  color: var(--color-text-secondary);
}

.share-form__error {
  padding: var(--space-03);
  background: var(--color-error-subtle);
  border-radius: var(--radius-04);
  color: var(--color-error);
}

.share-form__submit {
  margin-top: var(--space-02);
}

@media (max-aspect-ratio: 4/3) {
  .form-row {
    grid-template-columns: 1fr;
  }
}
</style>
