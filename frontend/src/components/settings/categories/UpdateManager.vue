<!-- frontend/src/components/settings/categories/UpdateManager.vue -->
<template>
  <SettingsContainer>
    <!-- Error state -->
    <template v-if="localProgramsError">
      <div class="error-state">
        <div class="error-message text-mono-medium">
          {{ t('updates.error') }}
        </div>
        <Button size="small" variant="background-strong" @click="loadLocalPrograms">
          {{ t('updates.retry') }}
        </Button>
      </div>
    </template>

    <template v-else>
      <!-- The operating system and the individual programs share one card:
           a divider tells the two groups apart. -->
      <SettingsSection>
        <div class="update-groups">
          <template v-if="localProgramsLoading || localPrograms.milo">
            <div class="update-group">
              <h2 class="heading-2">{{ t('updates.osTitle') }}</h2>
              <!-- Shown only alongside the button, because it describes what pressing
                   it does: one Milō update carries the whole validated set, and the
                   satellites with it — but only say so where there are any. -->
              <p v-if="!localProgramsLoading && localPrograms.milo?.update_available && !isLocalUpdateCompleted('milo')"
                class="text-mono-medium section-note">
                {{ t('updates.dependenciesHint') }}
                <template v-if="anticipatedSatellites.length"> {{ t('updates.clientsHint') }}</template>
              </p>
              <div class="crossfade-wrapper">
                <Transition name="crossfade">
                  <div v-if="localProgramsLoading" key="skeleton" class="programs-list">
                    <div class="program-item-skeleton">
                      <div class="skeleton-icon shimmer"></div>
                      <div class="skeleton-text shimmer skeleton-name"></div>
                      <div class="skeleton-text shimmer skeleton-version"></div>
                      <div class="skeleton-button shimmer"></div>
                    </div>
                  </div>

                  <div v-else key="content" class="programs-list">
                    <div class="program-item">
                      <div class="program-info">
                        <AppIcon :name="getProgramIcon('milo')" :size="48" class="program-icon" />
                        <span class="program-name heading-4">{{ localPrograms.milo.name }}</span>
                        <span class="program-version text-mono-medium">
                          milo {{ miloVersionLabel }}
                          <template
                            v-if="localPrograms.milo.update_available && !isLocalUpdateCompleted('milo')">
                            <span class="version-new">> {{ localPrograms.milo.latest?.tag_name }}</span>
                          </template>
                        </span>
                      </div>

                      <div class="program-actions">
                        <Button
                          v-if="isLocalUpdating('milo') || debugForceUpdating || (localPrograms.milo.update_available && canUpdateLocal('milo') && !isLocalUpdateCompleted('milo'))"
                          size="small" variant="brand" class="program-button"
                          :loading="isLocalUpdating('milo') || debugForceUpdating"
                          @click="startLocalUpdate('milo')"
                          :disabled="debugForceUpdating || isLocalUpdateBusy() || isAnySatelliteUpdating()">
                          {{ miloButtonLabel }}
                        </Button>
                        <Button v-else size="small" variant="background-strong" class="program-button btn-up-to-date" disabled>
                          {{ t('updates.upToDate') }}
                        </Button>
                      </div>
                    </div>
                  </div>
                </Transition>
              </div>
            </div>

            <div class="section-divider"></div>
          </template>

          <!-- The individual programs — a maintainer surface. -->
          <div class="update-group">
            <h2 class="heading-2">{{ t('updates.programsTitle') }}</h2>
            <div class="crossfade-wrapper">
              <Transition name="crossfade">
                <div v-if="localProgramsLoading" key="skeleton" class="programs-list">
                  <div v-for="n in enabledProgramCount" :key="n" class="program-item-skeleton">
                    <div class="skeleton-icon shimmer"></div>
                    <div class="skeleton-text shimmer skeleton-name"></div>
                    <div class="skeleton-text shimmer skeleton-version"></div>
                    <div class="skeleton-button shimmer"></div>
                  </div>
                </div>

                <div v-else key="content" class="programs-list">
                  <template v-for="(program, key) in localPrograms" :key="key">
                    <div v-if="key !== 'milo' && isProgramEnabled(key)" class="program-item">
                      <div class="program-info">
                        <AppIcon :name="getProgramIcon(key)" :size="48" class="program-icon" />
                        <span class="program-name heading-4">{{ getProgramDisplayName(program, key) }}</span>
                        <span class="program-version text-mono-medium">
                          {{ getVersionLabel(key) }} {{ getLocalInstalledVersion(program) || t('updates.notAvailable') }}
                          <span v-if="rows[key].update" class="version-new">> {{ rows[key].update.version }}</span>
                        </span>
                      </div>

                      <div class="program-actions">
                        <!-- While an install runs there is one button, and it is the
                             one that was pressed: it keeps its own label and colour
                             rather than being joined by a second, still-clickable
                             one the backend would refuse. -->
                        <Button v-if="isLocalUpdating(key) || debugForceUpdating"
                          size="small" :variant="isReverting(key) ? 'background-strong' : 'brand'"
                          class="program-button" loading disabled>
                          {{ isReverting(key)
                            ? t('updates.revertingTo', { version: rows[key].revertTo })
                            : t('updates.updating') }}
                        </Button>
                        <template v-else>
                          <!-- Only on a unit deliberately moved past the manifest —
                               and then it is the only thing saying so. First, so
                               that the row's rightmost button is the one that moves
                               forward, wherever the two appear together. -->
                          <Button v-if="rows[key].revertTo" size="small" variant="background-strong"
                            class="program-button"
                            @click="startLocalUpdate(key, 'validated')"
                            :disabled="isLocalUpdateBusy()">
                            {{ t('updates.revertTo', { version: rows[key].revertTo }) }}
                          </Button>
                          <!-- One button for both kinds of update: the manifest's
                               version, and what upstream published past it. Which
                               one it is comes from the backend, never from a
                               comparison here. -->
                          <Button v-if="rows[key].update && canUpdateLocal(key)"
                            size="small" variant="brand" class="program-button"
                            @click="startLocalUpdate(key, rows[key].update.target)"
                            :disabled="isLocalUpdateBusy()">
                            {{ t('updates.update') }}
                          </Button>
                          <Button v-if="!rows[key].update && !rows[key].revertTo"
                            size="small" variant="background-strong" class="program-button btn-up-to-date" disabled>
                            {{ t('updates.upToDate') }}
                          </Button>
                        </template>
                      </div>
                    </div>
                  </template>
                </div>
              </Transition>
            </div>
          </div>
        </div>
      </SettingsSection>

      <!-- Section 3: Satellite Programs (error) -->
      <SettingsSection v-if="isMultiroomEnabled && satellitesError"
        :title="t('updates.satelliteProgramsTitle')">
        <div class="error-state">
          <div class="error-message text-mono-medium">
            {{ t('updates.errorDetectingSatellites') }}
          </div>
          <Button size="small" variant="background-strong" @click="loadSatellites">
            {{ t('updates.retry') }}
          </Button>
        </div>
      </SettingsSection>

      <!-- Section 3: Satellite Programs (one section per anticipated client, crossfade skeleton → content) -->
      <template v-if="isMultiroomEnabled && !satellitesError">
        <SettingsSection v-for="client in anticipatedSatellites" :key="client.mac_id">
          <template #header>
            <h2 class="heading-2">{{ t('updates.satelliteSectionTitle') }} <span class="satellite-name">·&nbsp;{{
              client.name || client.mac_id }}</span></h2>
          </template>
          <div class="crossfade-wrapper">
            <Transition name="crossfade">
              <div v-if="!satelliteByMacId[client.mac_id] && isSatelliteLoading(client.mac_id)" key="skeleton"
                class="programs-list">
                <div class="program-item-skeleton">
                  <div class="skeleton-icon shimmer"></div>
                  <div class="skeleton-text shimmer skeleton-name"></div>
                  <div class="skeleton-text shimmer skeleton-version"></div>
                  <div class="skeleton-button shimmer"></div>
                </div>
                <div class="program-item-skeleton">
                  <div class="skeleton-icon shimmer"></div>
                  <div class="skeleton-text shimmer skeleton-name"></div>
                  <div class="skeleton-text shimmer skeleton-version"></div>
                  <div class="skeleton-button shimmer"></div>
                </div>
                <div class="program-item-skeleton">
                  <div class="skeleton-icon shimmer"></div>
                  <div class="skeleton-text shimmer skeleton-name"></div>
                  <div class="skeleton-text shimmer skeleton-version"></div>
                  <div class="skeleton-button shimmer"></div>
                </div>
              </div>

              <div v-else-if="satelliteByMacId[client.mac_id]" key="content" class="programs-list">
                <!-- Milo Client row -->
                <div class="program-item">
                  <div class="program-info">
                    <AppIcon name="milo-client" :size="48" class="program-icon" />
                    <span class="program-name heading-4">Milō Client</span>
                    <!-- A satellite reports the release it was deployed from, which is the
                         server's own: both halves ship from one commit, so there is no third
                         thing it could be. That is why this prints under `milo` and not
                         `milo-client`, and why it needs no formatting — it is the same tag the
                         server's row shows. What decides whether the button lights is a value
                         the row never prints: the payload fingerprint, compared on the backend. -->
                    <span class="program-version text-mono-medium">
                      milo {{ satelliteByMacId[client.mac_id].app_release || t('updates.notAvailable') }}
                      <template
                        v-if="satelliteByMacId[client.mac_id].app_update_available && !isSatelliteAppUpdateCompleted(client.mac_id)">
                        <span class="version-new">> {{ satelliteByMacId[client.mac_id].server_release }}</span>
                      </template>
                    </span>
                  </div>

                  <div class="program-actions">
                    <Button
                      v-if="isSatelliteAppUpdating(client.mac_id) || debugForceUpdating || (satelliteByMacId[client.mac_id].app_update_available && satelliteByMacId[client.mac_id].online && !isSatelliteAppUpdateCompleted(client.mac_id))"
                      size="small" variant="brand" class="program-button"
                      :loading="isSatelliteAppUpdating(client.mac_id) || debugForceUpdating"
                      @click="startSatelliteAppUpdate(client.mac_id)"
                      :disabled="debugForceUpdating || isMiloUpdating() || isSatelliteBusy(client.mac_id)">
                      {{ (isSatelliteAppUpdating(client.mac_id) || debugForceUpdating) ? t('updates.updating') : t('updates.update') }}
                    </Button>
                    <Button v-else size="small" variant="background-strong" class="program-button btn-up-to-date" disabled>
                      {{ t('updates.upToDate') }}
                    </Button>
                  </div>
                </div>

                <!-- Snapclient row -->
                <div class="program-item">
                  <div class="program-info">
                    <AppIcon name="multiroom" :size="48" class="program-icon" />
                    <span class="program-name heading-4">Multiroom Client</span>
                    <span class="program-version text-mono-medium">
                      snapclient {{ satelliteByMacId[client.mac_id].snapclient_version || t('updates.notAvailable') }}
                      <template
                        v-if="satelliteByMacId[client.mac_id].update_available && !isSatelliteUpdateCompleted(client.mac_id)">
                        <span class="version-new">> {{ satelliteByMacId[client.mac_id].latest_version }}</span>
                      </template>
                    </span>
                  </div>

                  <div class="program-actions">
                    <Button
                      v-if="isSatelliteUpdating(client.mac_id) || debugForceUpdating || (satelliteByMacId[client.mac_id].update_available && satelliteByMacId[client.mac_id].online && !isSatelliteUpdateCompleted(client.mac_id))"
                      size="small" variant="brand" class="program-button"
                      :loading="isSatelliteUpdating(client.mac_id) || debugForceUpdating"
                      @click="startSatelliteUpdate(client.mac_id)"
                      :disabled="debugForceUpdating || isMiloUpdating() || isSatelliteBusy(client.mac_id)">
                      {{ (isSatelliteUpdating(client.mac_id) || debugForceUpdating) ? t('updates.updating') : t('updates.update') }}
                    </Button>
                    <Button v-else size="small" variant="background-strong" class="program-button btn-up-to-date" disabled>
                      {{ t('updates.upToDate') }}
                    </Button>
                  </div>
                </div>

                <!-- CamillaDSP row -->
                <div class="program-item">
                  <div class="program-info">
                    <AppIcon name="equalizer" :size="48" class="program-icon" />
                    <span class="program-name heading-4">{{ t('equalizer.title') }}</span>
                    <span class="program-version text-mono-medium">
                      camilladsp {{ satelliteByMacId[client.mac_id].camilladsp_version || t('updates.notAvailable') }}
                      <template
                        v-if="satelliteByMacId[client.mac_id].camilladsp_update_available && !isSatelliteCamillaUpdateCompleted(client.mac_id)">
                        <span class="version-new">> {{ satelliteByMacId[client.mac_id].camilladsp_latest_version }}</span>
                      </template>
                    </span>
                  </div>

                  <div class="program-actions">
                    <Button
                      v-if="isSatelliteCamillaUpdating(client.mac_id) || debugForceUpdating || (satelliteByMacId[client.mac_id].camilladsp_update_available && satelliteByMacId[client.mac_id].online && !isSatelliteCamillaUpdateCompleted(client.mac_id))"
                      size="small" variant="brand" class="program-button"
                      :loading="isSatelliteCamillaUpdating(client.mac_id) || debugForceUpdating"
                      @click="startSatelliteCamillaUpdate(client.mac_id)"
                      :disabled="debugForceUpdating || isMiloUpdating() || isSatelliteBusy(client.mac_id)">
                      {{ (isSatelliteCamillaUpdating(client.mac_id) || debugForceUpdating) ? t('updates.updating') : t('updates.update') }}
                    </Button>
                    <Button v-else size="small" variant="background-strong" class="program-button btn-up-to-date" disabled>
                      {{ t('updates.upToDate') }}
                    </Button>
                  </div>
                </div>
              </div>

              <!-- Snapcast still sees it, its own API does not answer. Said in
                   words: a skeleton here waits for a fetch nobody will make. -->
              <div v-else key="unreachable" class="programs-list">
                <p class="text-mono-medium section-note">{{ t('multiroom.offline') }}</p>
              </div>
            </Transition>
          </div>
        </SettingsSection>
      </template>
    </template>
  </SettingsContainer>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import Button from '@/components/ui/Button.vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import { useI18n } from '@/services/i18n';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useUpdatesStore } from '@/stores/updatesStore';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';

function getProgramIcon(programKey) {
  const iconMap = {
    'milo': 'milo',
    'go-librespot': 'spotify',
    'multiroom': 'multiroom',
    'shairport-sync': 'airplay',
    'camilladsp': 'equalizer',
    'qobuz-proxy': 'qobuz',
    'navidrome': 'music_library'
  };
  return iconMap[programKey] || 'settings';
}

function getProgramDisplayName(program, key) {
  const nameOverrides = {
    'go-librespot': t('audioSources.spotify'),
    'multiroom': t('audioSources.multiroom'),
    'shairport-sync': t('audioSources.airplay'),
    'camilladsp': t('equalizer.title'),
    'qobuz-proxy': t('audioSources.qobuz'),
    'navidrome': t('audioSources.musicLibrary')
  };
  return nameOverrides[key] || program.name;
}

function getVersionLabel(key) {
  const labelOverrides = {
    'multiroom': 'snapcast',
    'shairport-sync': 'shairport-sync',
    'camilladsp': 'camilladsp',
    'qobuz-proxy': 'qobuz-proxy'
  };
  return labelOverrides[key] || key;
}

const { t } = useI18n();
const unifiedStore = useUnifiedAudioStore();
const multiroomStore = useMultiroomStore();
const settingsStore = useSettingsStore();
const updatesStore = useUpdatesStore();

// Update state and `programs/*` WS handling live in updatesStore (handlers
// registered centrally in App.vue); this component only renders store state.
const {
  localPrograms, localProgramsLoading, localProgramsError,
  satellites, satellitesError, satelliteByMacId,
} = storeToRefs(updatesStore);
const {
  loadLocalPrograms, loadSatellites,
  canUpdateLocal, startLocalUpdate,
  startSatelliteUpdate, startSatelliteAppUpdate, startSatelliteCamillaUpdate,
  isLocalUpdating, isLocalUpdateCompleted,
  isSatelliteUpdating, isSatelliteUpdateCompleted,
  isSatelliteAppUpdating, isSatelliteAppUpdateCompleted,
  isSatelliteCamillaUpdating, isSatelliteCamillaUpdateCompleted,
  isSatelliteAwaitingReturn, localUpdateTarget,
  isMiloUpdating, isLocalUpdateBusy, isAnySatelliteUpdating, isSatelliteBusy,
} = updatesStore;

const isMultiroomEnabled = computed(() => unifiedStore.systemState.multiroom_enabled);

// A unit runs a release, so the row prints its TAG — not the "X.Y.Z" parsed out
// of it. The two differ exactly where it matters: `v0.2.0-rc1` parses to
// "0.2.0", which would draw a pre-release under test and the stable release it
// is a candidate for as the same string. On a tree the backend reports as
// outside the channel — a development checkout — there is no tag, and the raw
// `git describe` is the only honest answer.
const miloVersionLabel = computed(() => {
  const milo = localPrograms.value.milo;
  if (!milo) return t('updates.notAvailable');
  return milo.installed?.release_tag
    || milo.installed?.raw_version
    || t('updates.notAvailable');
});

// The offer points both ways. A release GitHub stops publishing — withdrawn
// because it turned out bad — is offered back to every unit that took it, and
// the button has to say so rather than calling a return an update. Which
// direction it is comes from the backend, like every other decision here.
const miloButtonLabel = computed(() => {
  const version = localPrograms.value.milo?.latest?.tag_name;
  const withdrawn = localPrograms.value.milo?.latest?.withdrawn;
  if (isLocalUpdating('milo') || debugForceUpdating.value) {
    return withdrawn ? t('updates.revertingTo', { version }) : t('updates.updating');
  }
  return withdrawn ? t('updates.revertTo', { version }) : t('updates.update');
});

// Non-local satellites: online clients + clients with an active update (anticipates snapclient restart during update)
const anticipatedSatellites = computed(() =>
  multiroomStore.clientList.filter(c => {
    if (c.is_local) return false;
    if (c.online) return true;
    return isSatelliteUpdating(c.mac_id) || isSatelliteAppUpdating(c.mac_id) || isSatelliteCamillaUpdating(c.mac_id);
  })
);

// The inventory is a live probe of each satellite's own API, so a client
// missing from it is one that did not answer. Expected before the first fetch
// and across the restart an update puts it through — anywhere else a skeleton
// would claim to be loading something nobody is fetching.
function isSatelliteLoading(macId) {
  return satellites.value === null
    || isSatelliteAwaitingReturn(macId)
    || isSatelliteUpdating(macId)
    || isSatelliteAppUpdating(macId)
    || isSatelliteCamillaUpdating(macId);
}

// Debug: toggle via console with window.__miloDebugUpdating(true/false)
const debugForceUpdating = ref(false);
if (typeof window !== 'undefined') {
  window.__miloDebugUpdating = (val) => { debugForceUpdating.value = val; };
}

const programToDockApp = {
  'go-librespot': 'spotify',
  'multiroom': 'multiroom',
  'shairport-sync': 'airplay',
  'qobuz-proxy': 'qobuz',
  'navidrome': 'music_library'
};

function isProgramEnabled(programKey) {
  const dockKey = programToDockApp[programKey];
  if (!dockKey) return true;
  return settingsStore.dockApps[dockKey] !== false;
}

// +1 for CamillaDSP which is always visible (not dock-gated)
const enabledProgramCount = computed(() =>
  Object.keys(programToDockApp).filter(isProgramEnabled).length + 1
);

// === DISPLAY HELPERS ===

function getLocalInstalledVersion(program) {
  const versions = program.installed?.versions || {};
  const versionValues = Object.values(versions);
  return versionValues.length > 0 ? versionValues[0] : null;
}

function getLocalLatestVersion(program) {
  return program.latest?.version || null;
}

// Which of the two gestures the running install is. The row still carries
// `revertTo` while it runs — the list is refetched only once it ends — so the
// button keeps saying what it was pressed to do.
function isReverting(programKey) {
  return localUpdateTarget(programKey) === 'validated' && !!rows.value[programKey]?.revertTo;
}

// The two decisions a program row can offer, taken once per program.
//
// `update` is the release the brand button installs and the target it asks the
// backend for: what upstream published past the manifest when there is such a
// release, otherwise the manifest's own version. `revertTo` is the version the
// return button goes back to, and it exists only while the unit runs something
// past the manifest — `latest.validated` is present for that case alone.
//
// Every one of those is decided by the backend. Nothing here compares two
// versions: doing it on both sides is how the two answers come to disagree.
const rows = computed(() => {
  const out = {};
  for (const [key, program] of Object.entries(localPrograms.value)) {
    const upstream = program.latest?.upstream;
    const revertTo = program.latest?.validated?.version || null;
    let update = null;
    if (!isLocalUpdateCompleted(key)) {
      if (program.update_available && !revertTo) {
        // Behind the manifest: catching up comes before trying something newer,
        // and `upstream.ahead` is measured against the pin rather than against
        // what is installed — so it stays true on a unit that never reached it.
        update = { target: 'validated', version: getLocalLatestVersion(program) };
      } else if (upstream?.ahead) {
        update = { target: 'upstream', version: upstream.version };
      }
    }
    // No "validated" update while a trial is recorded: that target is the
    // return, and it installs the manifest's version — which is not the one
    // this row would print beside it.
    out[key] = { update, revertTo };
  }
  return out;
});

// === LIFECYCLE ===

onMounted(async () => {
  // Both inventories resync centrally in App.vue::resyncStores — a reconnect
  // handler here would fetch the satellites a second time.
  const tasks = [loadLocalPrograms()];
  if (isMultiroomEnabled.value) {
    tasks.push(loadSatellites());
  }
  await Promise.all(tasks);
});
</script>

<style scoped>
.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-05);
  text-align: center;
}

.error-message {
  color: var(--color-text-secondary);
}

/* The two groups and the line between them carry their own gap, so a single
   token spells the space above the line and below it. The section's own
   `--space-04` no longer reaches them — it sees one child — and it stays the
   gap *inside* each group, between a title and what it introduces. `--space-06`
   is the one token that stays above that fixed 16 px at both sizes while still
   shrinking on mobile: `--space-05` lands *on* it there, and the card flattens
   to a single rhythm in which the two groups stop reading as two. */
.update-groups {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
}

.update-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.section-divider {
  height: 1px;
  background: var(--color-border);
}

.programs-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.program-item {
  display: grid;
  /* The actions hug their label; everything left over goes to the version line,
     which is the part that grows — a satellite's reads
     "milo 0.1.0 (c6247d94) > 0.1.0 (2e21c957)". Two equal columns wrapped it
     while half the row sat empty. */
  grid-template-columns: 1fr auto;
  align-items: center;
  gap: var(--space-03);
}

.program-item:first-child {
  padding-top: var(--space-02);
}

.program-item:not(:last-child) {
  border-bottom: 1px solid var(--color-border);
  padding-bottom: var(--space-03);
}

.program-info {
  display: grid;
  grid-template-columns: auto 1fr;
  grid-template-areas:
    "icon name"
    "icon version";
  gap: var(--space-01) var(--space-03);
}

.program-icon {
  grid-area: icon;
}

.program-name {
  grid-area: name;
  color: var(--color-text);
  align-self: end;
}

.program-version {
  grid-area: version;
  color: var(--color-text-secondary);
  align-self: start;
}

.version-new {
  color: var(--color-brand);
}

.section-note {
  color: var(--color-text-secondary);
}

.satellite-name {
  color: var(--color-text-secondary);
}

.program-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: var(--space-02);
}

/* Crossfade transition */
.crossfade-wrapper {
  display: grid;
}

.crossfade-wrapper>* {
  grid-area: 1 / 1;
}

.crossfade-enter-active {
  transition: opacity var(--transition-normal);
}

.crossfade-leave-active {
  transition: opacity var(--transition-normal-leave);
}

.crossfade-enter-from,
.crossfade-leave-to {
  opacity: 0;
}

.program-item-skeleton {
  display: grid;
  grid-template-columns: auto 1fr auto;
  grid-template-areas:
    "icon name button"
    "icon version button";
  align-items: center;
  gap: var(--space-01) var(--space-04);
}

.program-item-skeleton:first-child {
  padding-top: var(--space-02);
}

.program-item-skeleton:not(:last-child) {
  border-bottom: 1px solid var(--color-border);
  padding-bottom: var(--space-03);
}

.skeleton-icon,
.skeleton-text,
.skeleton-button {
  --shimmer-base: var(--color-background-strong);
  --shimmer-highlight: var(--color-background);
}

.skeleton-icon {
  grid-area: icon;
  width: 48px;
  height: 48px;
  border-radius: var(--radius-03);
}

.skeleton-name {
  grid-area: name;
  width: 120px;
  height: calc(var(--font-size-h4) * 1.2);
  border-radius: var(--radius-02);
}

.skeleton-version {
  grid-area: version;
  width: 180px;
  height: calc(var(--font-size-mono-medium) * 1.4);
  border-radius: var(--radius-02);
}

.skeleton-button {
  grid-area: button;
  width: 100px;
  height: 36px;
  border-radius: var(--radius-02);
  justify-self: end;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .program-item {
    grid-template-columns: 1fr;
    gap: var(--space-02);
  }

  .program-item:first-child {
    padding-top: 0;
  }

  .program-info {
    gap: var(--space-01) var(--space-02);
  }

  /* AppIcon renders `:size` as an inline style — nothing else outranks it. */
  .program-icon {
    width: 44px !important;
    height: 44px !important;
  }

  .program-actions {
    width: 100%;
  }

  .program-button {
    flex: 1;
  }

  /* Hide "Up to date" button and skeleton button on mobile */
  .btn-up-to-date,
  .skeleton-button {
    display: none;
  }

  /* Skeleton responsive */
  .program-item-skeleton {
    grid-template-columns: auto 1fr;
    grid-template-areas:
      "icon name"
      "icon version";
    gap: var(--space-01) var(--space-02);
  }

  .program-item-skeleton:first-child {
    padding-top: 0;
  }

  .skeleton-icon {
    width: 44px;
    height: 44px;
  }

  .skeleton-name {
    width: 100px;
  }

  .skeleton-version {
    width: 160px;
  }
}
</style>
