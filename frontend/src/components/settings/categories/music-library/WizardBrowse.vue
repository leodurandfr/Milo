<!-- frontend/src/components/settings/categories/music-library/WizardBrowse.vue -->
<!--
  Add-share wizard, step 2 — connect and pick the music folder.

  Drives POST /music-library/shares/browse (unprivileged, mount-free) to walk the
  server the way Finder does:
   - SMB: lists shares, then drills into sub-folders; the backend returns
     `auth_required` when the server needs credentials, which reveals the login
     fields inline (validated by a successful re-browse — no blind mount).
   - NFS: lists exports (no drill-down); tapping one selects it.
  Selecting a folder creates the share (persist + mount + rescan) and reports
  whether it actually connected, so the user gets a definite success/failure —
  the thing the plain form couldn't tell them.
-->
<template>
  <SettingsContainer>
    <!-- Breadcrumb: server › share › sub… (each crumb navigates up) -->
    <div class="wb-crumbs text-mono-small">
      <button type="button" class="wb-crumb" :disabled="phase === 'loading'" @click="load('')">
        {{ server.name }}
      </button>
      <template v-for="(seg, i) in crumbs" :key="i">
        <span class="wb-sep">›</span>
        <button type="button" class="wb-crumb" :disabled="phase === 'loading'"
          @click="goToCrumb(i)">{{ seg }}</button>
      </template>
    </div>

    <SettingsSection>
      <!-- Auth step -->
      <template v-if="phase === 'auth'">
        <div class="wb-form">
          <p class="text-mono wb-note">{{ t('musicLibrary.shares.wizard.authPrompt') }}</p>
          <div class="wb-group">
            <label class="text-mono">{{ t('musicLibrary.shares.username') }}</label>
            <InputText v-model="creds.username" :placeholder="t('musicLibrary.shares.usernamePlaceholder')" :maxlength="128" />
          </div>
          <div class="wb-group">
            <label class="text-mono">{{ t('musicLibrary.shares.password') }}</label>
            <InputText v-model="creds.password" type="password" :maxlength="256"
              :placeholder="t('musicLibrary.shares.passwordPlaceholder')" />
          </div>
          <div class="wb-group">
            <label class="text-mono">{{ t('musicLibrary.shares.domain') }}</label>
            <InputText v-model="creds.domain" :placeholder="t('musicLibrary.shares.domainPlaceholder')" :maxlength="128" />
          </div>
          <p v-if="authError" class="wb-error text-mono">{{ authError }}</p>
          <Button variant="brand" size="medium" :loading="connecting" @click="connect">
            {{ t('musicLibrary.shares.wizard.connect') }}
          </Button>
        </div>
      </template>

      <!-- Loading a level -->
      <div v-else-if="phase === 'loading'" class="wb-center"><LoadingSpinner :size="40" /></div>

      <!-- Unreachable / error -->
      <template v-else-if="phase === 'error'">
        <p class="wb-error text-mono">{{ errorMsg }}</p>
        <Button variant="background-strong" size="medium" @click="load(path)">
          {{ t('musicLibrary.shares.wizard.retry') }}
        </Button>
      </template>

      <!-- Saved but the mount didn't come up -->
      <template v-else-if="phase === 'done'">
        <p class="wb-warn text-mono">{{ t('musicLibrary.shares.wizard.savedNotMounted') }}</p>
        <Button variant="background-strong" size="medium" @click="$emit('success')">
          {{ t('musicLibrary.shares.wizard.done') }}
        </Button>
      </template>

      <!-- Browsing entries -->
      <template v-else>
        <div v-if="entries.length" class="wb-list">
          <ListItemButton v-for="entry in entries" :key="entry.path" variant="background"
            :title="entry.name" :action="entry.kind === 'export' ? 'none' : 'caret'"
            @click="onEntry(entry)" />
        </div>
        <!-- Empty: a sub-folder with nothing in it, or a server that lists no
             shares as guest — offer to sign in for private shares. -->
        <template v-else>
          <p class="wb-empty text-mono">
            {{ path ? t('musicLibrary.shares.wizard.emptyFolder') : t('musicLibrary.shares.wizard.noSharesGuest') }}
          </p>
          <Button v-if="canSignIn" variant="background-strong" size="medium" @click="startSignIn">
            {{ t('musicLibrary.shares.wizard.signIn') }}
          </Button>
        </template>

        <p v-if="errorMsg" class="wb-error text-mono">{{ errorMsg }}</p>

        <!-- Pick the current folder (SMB, once inside a share) -->
        <Button v-if="canUseFolder" variant="brand" size="medium" :loading="creating" @click="useThisFolder">
          {{ t('musicLibrary.shares.wizard.useFolder', { name: currentName }) }}
        </Button>
      </template>
    </SettingsSection>
  </SettingsContainer>
</template>

<script setup>
import { ref, reactive, computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';

const props = defineProps({
  server: { type: Object, required: true }, // { name, host, type }
});
const emit = defineEmits(['success']);

const { t } = useI18n();
const store = useMusicLibraryStore();

// phase: loading | auth | browsing | error | done
const phase = ref('loading');
const path = ref('');          // last successfully-loaded path ('' = top level)
const pendingPath = ref('');   // path currently being loaded (kept across the auth prompt)
const entries = ref([]);
const creds = reactive({ username: '', password: '', domain: '' });
const authError = ref('');
const errorMsg = ref('');
const connecting = ref(false);
const creating = ref(false);

const isNfs = computed(() => props.server.type === 'nfs');
const crumbs = computed(() => (path.value ? path.value.split('/') : []));
const currentName = computed(() => crumbs.value[crumbs.value.length - 1] || props.server.name);
// A whole share or any sub-folder is selectable (SMB only, once past the root).
const canUseFolder = computed(() => !isNfs.value && path.value !== '');
// Offer a sign-in when a SMB server listed nothing as guest and no credentials
// have been tried yet (e.g. a box that only exposes IPC$ anonymously).
const canSignIn = computed(() => !isNfs.value && !creds.username && !creds.password);

function hasCreds() {
  return !!(creds.username || creds.password);
}

function startSignIn() {
  authError.value = '';
  phase.value = 'auth';
}

async function load(target) {
  pendingPath.value = target;
  phase.value = 'loading';
  errorMsg.value = '';
  const r = await store.browseShare({
    type: props.server.type,
    host: props.server.host,
    path: target,
    username: creds.username,
    password: creds.password,
    domain: creds.domain,
  });
  if (r.status === 'ok') {
    path.value = target;
    entries.value = r.entries;
    authError.value = '';
    phase.value = 'browsing';
  } else if (r.status === 'auth_required') {
    // Guest listing was refused — collect credentials and retry pendingPath.
    if (hasCreds()) authError.value = t('musicLibrary.shares.wizard.wrongCredentials');
    phase.value = 'auth';
  } else {
    errorMsg.value = r.status === 'unreachable'
      ? t('musicLibrary.shares.wizard.unreachable')
      : t('musicLibrary.shares.wizard.browseError');
    phase.value = 'error';
  }
}

async function connect() {
  if (connecting.value) return;
  connecting.value = true;
  await load(pendingPath.value);
  connecting.value = false;
}

function goToCrumb(index) {
  load(crumbs.value.slice(0, index + 1).join('/'));
}

function onEntry(entry) {
  if (entry.kind === 'export') {
    createShare(entry.path, entry.name);
  } else {
    load(entry.path);
  }
}

function useThisFolder() {
  createShare(path.value, currentName.value);
}

async function createShare(targetPath, displayName) {
  if (creating.value) return;
  creating.value = true;
  errorMsg.value = '';
  const payload = {
    type: props.server.type,
    host: props.server.host,
    path: targetPath,
    name: displayName,
  };
  if (props.server.type === 'cifs' && creds.password) {
    payload.username = creds.username || undefined;
    payload.password = creds.password;
    payload.domain = creds.domain || undefined;
  }
  const result = await store.addShare(payload);
  creating.value = false;
  if (!result.ok) {
    errorMsg.value = typeof result.error === 'string' && result.error
      ? result.error
      : t('musicLibrary.shares.errorGeneric');
    return;
  }
  if (result.mounted) {
    emit('success');
  } else {
    phase.value = 'done'; // saved, but the mount didn't come up
  }
}

// Kick off at the top level (guest); auth is requested only if refused.
load('');
</script>

<style scoped>
.wb-crumbs {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-01);
  padding: 0 var(--space-02);
  color: var(--color-text-secondary);
}

.wb-crumb {
  padding: var(--space-01) 0;
  color: var(--color-text-secondary);
  background: none;
  border: none;
  cursor: pointer;
}

.wb-crumb:last-of-type {
  color: var(--color-text);
}

.wb-crumb:disabled {
  cursor: default;
}

.wb-sep {
  color: var(--color-text-light);
}

.wb-center {
  display: flex;
  justify-content: center;
  padding: var(--space-05);
}

.wb-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.wb-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.wb-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.wb-group label {
  color: var(--color-text-secondary);
}

.wb-note {
  color: var(--color-text-secondary);
}

.wb-empty {
  padding: var(--space-05);
  text-align: center;
  color: var(--color-text-secondary);
}

.wb-error {
  padding: var(--space-03);
  background: var(--color-error-subtle);
  border-radius: var(--radius-04);
  color: var(--color-error);
}

.wb-warn {
  padding: var(--space-03);
  background: var(--color-warning-subtle);
  border-radius: var(--radius-04);
  color: var(--color-warning);
}
</style>
