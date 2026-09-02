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
    <!-- Indexing settled — how many tracks this share contributed. A standalone
         card (like the rest of the app's MessageContent screens), not nested in
         a SettingsSection, so no breadcrumb above it either. -->
    <MessageContent v-if="phase === 'indexed'" icon="check"
      :title="t('musicLibrary.shares.wizard.indexedTitle')"
      :subtitle="indexEmpty
        ? t('musicLibrary.shares.wizard.indexedEmpty')
        : (indexTotalMode
          ? t('musicLibrary.shares.wizard.indexedTotal', { count: finalFound })
          : t('musicLibrary.shares.wizard.indexedShare', { count: finalFound }))"
      :cta-label="t('musicLibrary.shares.wizard.done')" :cta-variant="indexEmpty ? 'background-strong' : 'brand'"
      :cta-click="leave" />

    <SettingsSection v-else>
      <!-- Auth step: no folder path to show yet — just the NAS being signed into. -->
      <h2 v-if="phase === 'auth'" class="heading-2">{{ server.name }}</h2>

      <!-- Breadcrumb: server › share › sub… (each crumb navigates up) -->
      <div v-else class="wb-crumbs">
        <button type="button" class="wb-crumb text-mono-small" :disabled="crumbsLocked" @click="load('')">
          {{ server.name }}
        </button>
        <template v-for="(seg, i) in crumbs" :key="i">
          <SvgIcon name="caretRight" :size="16" class="wb-sep" />
          <button type="button" class="wb-crumb text-mono-small" :disabled="crumbsLocked"
            @click="goToCrumb(i)">{{ seg }}</button>
        </template>
      </div>

      <!-- Auth step -->
      <template v-if="phase === 'auth'">
        <div class="wb-form">
          <p class="text-mono-medium wb-note">{{ t('musicLibrary.shares.wizard.authPrompt') }}</p>
          <div class="wb-group">
            <label class="text-mono-medium">{{ t('musicLibrary.shares.username') }}</label>
            <InputText v-model="creds.username" :placeholder="t('musicLibrary.shares.usernamePlaceholder')" :maxlength="128" />
          </div>
          <div class="wb-group">
            <label class="text-mono-medium">{{ t('musicLibrary.shares.password') }}</label>
            <InputText v-model="creds.password" type="password" :maxlength="256"
              :placeholder="t('musicLibrary.shares.passwordPlaceholder')" />
          </div>
          <p v-if="authError" class="wb-error text-mono-medium">{{ authError }}</p>
          <Button variant="brand" size="medium" :loading="connecting" @click="connect">
            {{ t('musicLibrary.shares.wizard.connect') }}
          </Button>
        </div>
      </template>

      <!-- Loading a level -->
      <div v-else-if="phase === 'loading'" class="wb-center"><LoadingSpinner :size="40" /></div>

      <!-- Unreachable / error -->
      <template v-else-if="phase === 'error'">
        <p class="wb-error text-mono-medium">{{ errorMsg }}</p>
        <Button variant="background-strong" size="medium" @click="load(path)">
          {{ t('musicLibrary.shares.wizard.retry') }}
        </Button>
      </template>

      <!-- Saved but the mount didn't come up -->
      <template v-else-if="phase === 'done'">
        <p class="wb-warn text-mono-medium">{{ t('musicLibrary.shares.wizard.savedNotMounted') }}</p>
        <Button variant="background-strong" size="medium" @click="$emit('success')">
          {{ t('musicLibrary.shares.wizard.done') }}
        </Button>
      </template>

      <!-- Mounted — indexing runs; the live count validates there's music here.
           No CTA: it settles into 'indexed' on its own once the scan finishes. -->
      <template v-else-if="phase === 'indexing'">
        <h2 class="heading-2">{{ t('musicLibrary.shares.wizard.indexing') }}</h2>
        <ScanProgress open
          :label="t('musicLibrary.shares.wizard.indexingCount', { count: liveFound })" />
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
          <p class="wb-empty text-mono-medium">
            {{ path ? t('musicLibrary.shares.wizard.emptyFolder') : t('musicLibrary.shares.wizard.noSharesGuest') }}
          </p>
          <Button v-if="canSignIn" variant="background-strong" size="medium" @click="startSignIn">
            {{ t('musicLibrary.shares.wizard.signIn') }}
          </Button>
        </template>

        <p v-if="errorMsg" class="wb-error text-mono-medium">{{ errorMsg }}</p>
      </template>
    </SettingsSection>

    <!-- Pick the current folder (SMB, once inside a share) — sticky, so it's
         reachable from anywhere in the entries list. -->
    <Button v-if="phase === 'browsing' && canUseFolder" variant="brand" size="medium" class="apply-button-sticky"
      :loading="creating" @click="useThisFolder">
      {{ t('musicLibrary.shares.wizard.useFolder', { name: currentName }) }}
    </Button>
  </SettingsContainer>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue';
import { useI18n } from '@/services/i18n';
import { useTimer } from '@/composables/useTimer';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import ScanProgress from '@/components/settings/categories/music-library/ScanProgress.vue';
import ListItemButton from '@/components/ui/ListItemButton.vue';
import InputText from '@/components/ui/InputText.vue';
import Button from '@/components/ui/Button.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import MessageContent from '@/components/ui/MessageContent.vue';

const props = defineProps({
  server: { type: Object, required: true }, // { name, host, type }
});
const emit = defineEmits(['success', 'phase-change']);

const { t } = useI18n();
const store = useMusicLibraryStore();
const timer = useTimer();

// phase: loading | auth | browsing | error | done | indexing | indexed
const phase = ref('loading');
// Lets the parent's NavigationHeader name the current action (sign-in vs
// folder pick) instead of just repeating the NAS name throughout.
watch(phase, (p) => emit('phase-change', p), { immediate: true });
const path = ref('');          // last successfully-loaded path ('' = top level)
const pendingPath = ref('');   // path currently being loaded (kept across the auth prompt)
const entries = ref([]);
const creds = reactive({ username: '', password: '' });
const authError = ref('');
const errorMsg = ref('');
const connecting = ref(false);
const creating = ref(false);

// Post-mount indexing / validation. The share gets its own Navidrome library, so
// its track count IS what this share contributed — no baseline to snapshot and
// no delta to attribute, which is what the whole-library counter used to force
// (and got wrong whenever a scan was already running).
const INDEX_GRACE_MS = 9000;   // how long to wait for the scan to visibly start
const INDEX_MAX_MS = 120000;   // hard cap; the scan continues server-side
const newShareId = ref(null);  // the share being indexed
const finalFound = ref(0);     // settled count
const indexEmpty = ref(false);
let sawScanning = false;
let indexingActive = false;

// Tracks indexed in this share's own storage space, live.
const liveFound = computed(() =>
  store.storages.find((s) => s.id === newShareId.value)?.track_count || 0
);

const isNfs = computed(() => props.server.type === 'nfs');
const crumbs = computed(() => (path.value ? path.value.split('/') : []));
// The share exists once indexing starts — freeze the breadcrumb so a tap can't
// wander back into the browser mid-validation.
const crumbsLocked = computed(() =>
  phase.value === 'loading' || phase.value === 'indexing' || phase.value === 'indexed'
);
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
    createShare(entry.path);
  } else {
    load(entry.path);
  }
}

function useThisFolder() {
  createShare(path.value);
}

async function createShare(targetPath) {
  if (creating.value) return;
  creating.value = true;
  errorMsg.value = '';
  const payload = {
    type: props.server.type,
    host: props.server.host,
    path: targetPath,
    // The discovered server name (e.g. "NAS-Leo") is the share's friendly label;
    // the folder it points at rides `path`, and the list row renders the two as
    // "<name> / <path>". The manual form lets the user edit the label later.
    name: props.server.name,
  };
  if (props.server.type === 'cifs' && creds.password) {
    payload.username = creds.username || undefined;
    payload.password = creds.password;
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
    startIndexing(result.shareId);
  } else {
    phase.value = 'done'; // saved, but the mount didn't come up
  }
}

// Watch the scan through after mounting, showing this share's live count.
// Non-blocking: the "Done" button leaves at any point and the scan finishes
// server-side. Nothing is polled — the backend pushes the flag and the counts.
function startIndexing(shareId) {
  sawScanning = false;
  newShareId.value = shareId;
  indexingActive = true;
  phase.value = 'indexing';
  // The scan may be over before it was ever observed (an empty share indexes in
  // milliseconds), and it may also never visibly start. Both settle on a timer;
  // the cap only bounds the wizard, never the scan.
  timer.setTimeout(() => { if (!sawScanning) finishIndexing(); }, INDEX_GRACE_MS);
  timer.setTimeout(finishIndexing, INDEX_MAX_MS);
}

watch(() => store.isScanning, (scanning) => {
  if (!indexingActive) return;
  if (scanning) sawScanning = true;
  else if (sawScanning) finishIndexing();
});

function finishIndexing() {
  if (!indexingActive) return;
  indexingActive = false;
  finalFound.value = liveFound.value;
  indexEmpty.value = finalFound.value === 0;
  phase.value = 'indexed';
}

function leave() {
  indexingActive = false;
  emit('success');
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
  color: var(--color-text-secondary);
}

.wb-crumb {
  padding: var(--space-01) 0;
  color: var(--color-text-secondary);
  background: none;
  border: none;
  cursor: pointer;
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

/* Pinned to the bottom of the scroll area (mirrors ManageShare's save button). */
.apply-button-sticky {
  position: sticky;
  bottom: 0;
  width: 100%;
  z-index: 10;
}
</style>
