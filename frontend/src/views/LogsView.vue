<!-- frontend/src/views/LogsView.vue -->
<!--
  The logs page, at /logs, opened from Settings › System. Lazily imported, so
  its chunk is fetched the first time someone opens it.

  What it shows is read when it is opened, and again on a filter or on
  Refresh — never in the background (GET /api/system/logs): errors.log and its
  rotations, the server's journal (its milo-* units, and at warning and above
  the system services Milō depends on and the kernel, each listed under its
  own name in the service filter, the kernel as "kernel") and each online
  speaker's own tails, grouped by the backend so a line repeated forty times
  is one row with its count; and at the top, the Pi's throttling state, read
  with it — anything but 0 a warning, each limit named now or since boot.
  The windows offered stop at the journal's retention, which each answer
  carries (`retention_days`, utils/logPeriods.js).
  Every line arrives redacted by the backend (the diagnostic's Redactor): no
  network, share or speaker name, public address or URL query reaches this
  page, which is why it can be shared.

  A row unfolds onto its context — the lines on each side of it, every
  service together (GET /api/system/logs/context) — and "Copy for correction"
  puts that, the release and the source at that moment on the clipboard
  (utils/logCorrection.js). Nothing here is kept in a store: the page is read
  and thrown away, so there is no WebSocket event to follow and nothing to
  resync.
-->
<template>
  <div class="logs">
    <header class="logs__head">
      <IconButton icon="caretLeft" :aria-label="t('common.back')" @click="goBack" />
      <h1 class="heading-1 logs__title">{{ t('system.logs.title') }}</h1>
      <IconButton icon="arrowClockwise" :loading="loading" :aria-label="t('logs.refresh')" @click="load" />
    </header>

    <NoticeBox v-if="page?.throttling?.value" kind="warning">
      <p class="logs__throttling-title">{{ t('logs.throttling.warning', { value: page.throttling.raw }) }}</p>
      <ul class="logs__throttling">
        <li v-for="condition in heldLimits" :key="condition.name">
          {{ limitLabels[condition.name] }} — {{ condition.now ? t('logs.throttling.now') : t('logs.throttling.sinceBoot') }}
        </li>
      </ul>
    </NoticeBox>
    <p v-else-if="page?.throttling" class="logs__note text-body-small">{{ t('logs.throttling.clear') }}</p>

    <div class="logs__filters">
      <Dropdown v-model="level" :options="levelOptions" size="small" />
      <Dropdown v-model="service" :options="serviceOptions" size="small" />
      <Dropdown v-model="since" :options="periodOptions" size="small" />
      <InputText v-model="query" class="logs__search" icon="search" :maxlength="200"
        :placeholder="t('logs.search')" />
    </div>

    <NoticeBox v-if="error !== null" :detail="error">{{ t('logs.readFailed') }}</NoticeBox>

    <NoticeBox v-if="page?.not_collected.length" kind="warning">
      <ul class="logs__missing text-mono-small">
        <li class="logs__missing-title">{{ t('system.diagnostic.notCollected') }}</li>
        <li v-for="item in page.not_collected" :key="`${item.source}:${item.reason}`">{{ item.source }} — {{ item.reason }}</li>
      </ul>
    </NoticeBox>

    <p v-if="page?.truncated" class="logs__note text-body-small">
      {{ t('logs.truncated', { time: formatMoment(page.journal_from) }) }}
      <template v-if="page.warnings_whole">{{ t('logs.truncatedWarningsWhole') }}</template>
    </p>

    <NoticeBox v-if="page && !page.groups.length" kind="empty">{{ t('logs.empty') }}</NoticeBox>

    <ul v-if="page?.groups.length" class="logs__list">
      <li v-for="group in page.groups" :key="keyOf(group)" class="logs__event"
        :class="[`logs__event--${group.level}`, { 'logs__event--open': openKey === keyOf(group) }]">
        <button v-press type="button" class="logs__row" :aria-expanded="openKey === keyOf(group)"
          @click="toggle(group)">
          <span class="logs__level text-mono-small">{{ levelLabels[group.level] ?? group.level }}</span>
          <span class="logs__service text-mono-small">{{ group.service }}</span>
          <span class="logs__message text-mono-medium">{{ firstLine(group.sample) }}</span>
          <span class="logs__when text-mono-small">
            {{ formatMoment(group.last_at) }}
            <span v-if="group.count > 1" class="logs__count">{{ t('logs.count', { count: formatNumber(group.count) }) }}</span>
          </span>
        </button>

        <div v-if="openKey === keyOf(group)" class="logs__detail">
          <p v-if="group.count > 1" class="logs__note text-body-small">
            {{ t('logs.firstSeen', { time: formatMoment(group.first_at) }) }}
          </p>
          <pre v-if="group.sample.includes('\n')" class="logs__sample text-mono-small">{{ group.sample }}</pre>

          <NoticeBox v-if="contextError !== null" :detail="contextError">{{ t('logs.contextFailed') }}</NoticeBox>
          <p v-else-if="!contexts[keyOf(group)]" class="logs__note text-body-small">{{ t('common.loading') }}</p>
          <ol v-else class="logs__context text-mono-small">
            <li v-for="(line, index) in contexts[keyOf(group)].lines" :key="index" class="logs__line"
              :class="{ 'logs__line--event': index === contexts[keyOf(group)].event_index }">
              <span class="logs__line-time">{{ formatClock(line.at) }}</span>
              <span class="logs__line-service">{{ line.service }}</span>
              <span class="logs__line-message">{{ line.message }}</span>
            </li>
          </ol>

          <div class="logs__actions">
            <Button variant="control" size="small" :disabled="!contexts[keyOf(group)]" @click="copy(group)">
              {{ copiedKey === keyOf(group) ? t('logs.copied') : t('logs.copy') }}
            </Button>
          </div>
          <NoticeBox v-if="copyError">{{ copyError }}</NoticeBox>
        </div>
      </li>
    </ul>

    <p v-if="page && page.total_groups > page.groups.length" class="logs__note text-body-small">
      {{ t('logs.more', { shown: formatNumber(page.groups.length), total: formatNumber(page.total_groups) }) }}
    </p>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from '@/services/i18n';
import { apiCall } from '@/services/apiCall';
import { useTimer } from '@/composables/useTimer';
import { useDebounce } from '@/composables/useDebounce';
import { bcp47For } from '@/constants/countries';
import { correctionText } from '@/utils/logCorrection';
import { offeredLogPeriods, periodWithin } from '@/utils/logPeriods';
import { writeToClipboard } from '@/utils/clipboard';
import IconButton from '@/components/ui/IconButton.vue';
import Dropdown from '@/components/ui/Dropdown.vue';
import InputText from '@/components/ui/InputText.vue';
import NoticeBox from '@/components/ui/NoticeBox.vue';
import Button from '@/components/ui/Button.vue';

const { t, formatNumber, formatUnit, currentLanguage } = useI18n();
const router = useRouter();
const timer = useTimer();

// The groups one read returns; the backend caps it at 1000.
const LIMIT = 300;

const level = ref('');
const service = ref('');
const since = ref('24h');
const query = ref('');

const page = ref(null);
const loading = ref(false);
const error = ref(null);

const openKey = ref(null);
const contexts = reactive({});
const contextError = ref(null);
const copiedKey = ref(null);
const copyError = ref(null);

const levelLabels = computed(() => ({
  debug: t('logs.levels.debug'),
  info: t('logs.levels.info'),
  warning: t('logs.levels.warning'),
  error: t('logs.levels.error'),
  critical: t('logs.levels.critical'),
}));

const limitLabels = computed(() => ({
  under_voltage: t('logs.throttling.conditions.under_voltage'),
  frequency_capped: t('logs.throttling.conditions.frequency_capped'),
  throttled: t('logs.throttling.conditions.throttled'),
  soft_temperature_limit: t('logs.throttling.conditions.soft_temperature_limit'),
}));

// The limits the firmware says held, now or at some moment since boot.
const heldLimits = computed(() => (page.value?.throttling?.conditions ?? [])
  .filter(condition => condition.now || condition.since_boot));

const levelOptions = computed(() => [
  { value: '', label: t('logs.severity.all') },
  { value: 'warning', label: t('logs.severity.warning') },
  { value: 'error', label: t('logs.severity.error') },
]);

const serviceOptions = computed(() => [
  { value: '', label: t('logs.allServices') },
  ...(page.value?.services ?? []).map(service => ({ value: service, label: service })),
]);

// Up to the journal's retention, which each answer carries (`retention_days`).
const offeredPeriods = computed(() => offeredLogPeriods(page.value?.retention_days ?? null));

const periodOptions = computed(() => offeredPeriods.value.map(period => ({
  value: period.value,
  label: formatUnit(period.amount, period.unit, { unitDisplay: 'long' }),
})));

const momentFormat = computed(() => new Intl.DateTimeFormat(bcp47For(currentLanguage.value), {
  dateStyle: 'short', timeStyle: 'medium',
}));
const clockFormat = computed(() => new Intl.DateTimeFormat(bcp47For(currentLanguage.value), {
  hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3,
}));

function formatMoment(iso) {
  return iso ? momentFormat.value.format(new Date(iso)) : '';
}

function formatClock(iso) {
  return clockFormat.value.format(new Date(iso));
}

function firstLine(text) {
  return text.split('\n', 1)[0];
}

function keyOf(group) {
  return `${group.service}|${group.level}|${group.template}`;
}

let inFlight = null;

/** One read of the window and filters as they stand; a read still running
 *  is abandoned for it, so the page never shows an answer to older filters. */
async function load() {
  inFlight?.abort();
  const controller = new AbortController();
  inFlight = controller;
  loading.value = true;
  error.value = null;

  const params = { since: since.value, limit: LIMIT };
  if (level.value) params.level = level.value;
  if (service.value) params.service = service.value;
  if (query.value.trim()) params.q = query.value.trim();

  const result = await apiCall.get('/api/system/logs', {
    category: 'system',
    message: 'Failed to read the logs',
    params,
    signal: controller.signal,
    errorRef: error,
    // The journal, errors.log and the speakers are each bounded server-side
    // (8 s, a file read, 6 s); this only has to outlast them.
    timeout: 30000,
  });
  if (inFlight !== controller) return;
  inFlight = null;
  loading.value = false;
  if (!result.ok) return;
  page.value = result.data.data;
  openKey.value = null;
}

async function toggle(group) {
  const key = keyOf(group);
  copyError.value = null;
  if (openKey.value === key) {
    openKey.value = null;
    return;
  }
  openKey.value = key;
  if (contexts[key]) return;
  contextError.value = null;
  const result = await apiCall.get('/api/system/logs/context', {
    category: 'system',
    message: 'Failed to read the context of a log line',
    params: { at: group.last_at, service: group.service },
    errorRef: contextError,
    timeout: 30000,
  });
  if (result.ok) contexts[key] = result.data.data;
}

async function copy(group) {
  const key = keyOf(group);
  copyError.value = null;
  if (await writeToClipboard(correctionText(group, contexts[key]))) {
    copiedKey.value = key;
    timer.setTimeout(() => {
      if (copiedKey.value === key) copiedKey.value = null;
    }, 3000);
  } else {
    copyError.value = t('logs.copyFailed');
  }
}

function goBack() {
  router.push({ name: 'main' });
}

const { debounced: loadSoon } = useDebounce(load, 400);

// A window the retention no longer reaches (shortened on another screen) falls
// back to the longest offered, which reads the page again.
watch(offeredPeriods, (offered) => {
  since.value = periodWithin(since.value, offered);
});
watch([level, service, since], load);
watch(query, loadSoon);
onMounted(load);
</script>

<style scoped>
.logs {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  height: 100%;
  overflow-y: auto;
  padding: var(--space-05);
  background: var(--color-ground);
  color: var(--color-text);
}

.logs__head {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.logs__title {
  flex: 1;
  margin: 0;
}

.logs__filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-02);
}

.logs__search {
  flex: 1 1 240px;
}

.logs__throttling-title {
  margin: 0;
}

.logs__throttling {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  margin: var(--space-02) 0 0;
  padding: 0;
  list-style: none;
}

.logs__missing {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  margin: 0;
  padding: 0;
  list-style: none;
}

.logs__missing-title,
.logs__note {
  margin: 0;
  color: var(--color-text-secondary);
}

.logs__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  margin: 0;
  padding: 0;
  list-style: none;
}

.logs__event {
  border-radius: var(--radius-04);
  background: var(--color-surface);
  overflow: hidden;
}

.logs__row {
  display: grid;
  grid-template-columns: 88px minmax(0, 220px) minmax(0, 1fr) auto;
  align-items: baseline;
  gap: var(--space-03);
  width: 100%;
  padding: var(--space-03) var(--space-04);
  border: none;
  background: none;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.logs__level {
  color: var(--color-text-secondary);
}

.logs__event--warning .logs__level {
  color: var(--color-warning);
}

.logs__event--error .logs__level,
.logs__event--critical .logs__level {
  color: var(--color-error);
}

.logs__service,
.logs__when {
  color: var(--color-text-secondary);
}

.logs__service,
.logs__message {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logs__event--open .logs__message {
  white-space: normal;
  overflow-wrap: anywhere;
}

.logs__count {
  margin-left: var(--space-02);
  padding: 0 var(--space-02);
  border-radius: var(--radius-02);
  background: var(--color-fill-faint);
  color: var(--color-text);
}

.logs__detail {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: 0 var(--space-04) var(--space-04);
}

.logs__sample,
.logs__context {
  margin: 0;
  padding: var(--space-03);
  border-radius: var(--radius-03);
  background: var(--color-inset);
  color: var(--color-text-secondary);
  overflow-x: auto;
}

.logs__sample {
  white-space: pre;
}

.logs__context {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  list-style: none;
}

.logs__line {
  display: grid;
  grid-template-columns: auto minmax(0, 200px) minmax(0, 1fr);
  gap: var(--space-03);
}

.logs__line--event {
  border-radius: var(--radius-01);
  background: var(--color-fill-faint);
  color: var(--color-text);
}

.logs__line-service {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.logs__line-message {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.logs__actions {
  display: flex;
  gap: var(--space-03);
}

@media (max-aspect-ratio: 4/3) and (max-width: 1023px) {
  .logs {
    padding: var(--space-04);
  }

  .logs__row {
    grid-template-columns: auto minmax(0, 1fr);
    row-gap: var(--space-01);
  }

  .logs__message {
    grid-column: 1 / -1;
  }

  .logs__line {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .logs__line-message {
    grid-column: 1 / -1;
  }
}
</style>
