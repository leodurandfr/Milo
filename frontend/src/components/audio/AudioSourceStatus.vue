<!-- AudioSourceStatus.vue -->
<template>
  <div class="source-status" :class="{ 'screensaver-revealing': revealing }">
    <div class="source-status-content">
      <div class="source-status-inner">
        <!-- Device info section -->
        <div class="device-info">
          <div class="device-info-content">
            <div class="device-info-inner">
              <!-- Source icon -->
              <div class="source-icon">
                <LoadingSpinner v-if="SPINNING_STATES.includes(displayState)" :size="26" variant="background" />
                <AppIcon v-else :name="sourceType" :size="32" />
              </div>

              <!-- Text status -->
              <div class="device-status">
                <div v-if="status.lines.length === 1" class="status-single">
                  <h2 class="heading-2">{{ status.lines[0] }}</h2>
                </div>
                <template v-else>
                  <div class="status-line-1" :class="{ 'muted-line': status.nameLine !== 1 }">
                    <h2 class="heading-2">{{ status.lines[0] }}</h2>
                  </div>
                  <div class="status-line-2" :class="{ 'muted-line': status.nameLine !== 2 }">
                    <h2 class="heading-2">{{ status.lines[1] }}</h2>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </div>

        <!-- Bottom action button: retry, Bluetooth disconnect, or Qobuz
             connect-account CTA. The <button> IS the full-width bar so the whole
             surface is clickable. -->
        <button v-if="actionButton" @click="actionButton.onClick" :disabled="actionButton.disabled"
          class="action-button heading-3">{{ actionButton.label }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import AppIcon from '@/components/ui/AppIcon.vue';
import { ALL_AUDIO_SOURCES, AUDIO_SOURCE_LABEL_KEYS } from '@/constants/audioSources';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import { useI18n } from '@/services/i18n';
import { useScreensaverRevealPulse } from '@/composables/useScreensaverReveal';
import { formatDeviceNames } from '@/utils/deviceName';
import { DISPLAY_STATES, UNAVAILABLE_REASONS } from '@/composables/useSourceStatusDisplay';

const { t } = useI18n();

// Replay the card entrance when the screensaver is dismissed.
const revealing = useScreensaverRevealPulse();

// Props
const props = defineProps({
  sourceType: {
    type: String,
    required: true,
    validator: (value) => value === 'none' || ALL_AUDIO_SOURCES.includes(value)
  },
  // The card's own vocabulary, not the backend enum: the four SourceState
  // members plus CD's three metadata-derived screens. Declared in one place —
  // see useSourceStatusDisplay, which is what derives it.
  displayState: {
    type: String,
    required: true,
    validator: (value) => DISPLAY_STATES.includes(value)
  },
  // What stops the source from working, or null when nothing does. Set, it
  // replaces the state's phrase: there is no point saying "Ready to play" on a
  // source whose every command will fail. Derived in useSourceStatusDisplay.
  unavailableReason: {
    type: String,
    default: null,
    validator: (value) => value === null || UNAVAILABLE_REASONS.includes(value)
  },
  deviceName: {
    type: [String, Array],  // Support string or array for ROC multi-clients
    default: ''
  },
  isDisconnecting: {
    type: Boolean,
    default: false
  }
});

// Emits
const emit = defineEmits(['disconnect', 'connect', 'retry', 'open-network-settings']);

// The three states whose icon slot is a spinner instead of the source glyph:
// something is under way that the card is waiting on.
const SPINNING_STATES = ['starting', 'loading_disc', 'ejecting'];

/**
 * The phrase for every state that is not "ready", by display state.
 *
 * Ready is the one that needs the source to answer (see below), so it is not in
 * here. Everything else says the same thing whatever the source is — which is
 * the whole of P4: there is no combination left to fall through, and therefore
 * no terminal "waiting" line to print when one did.
 */
const PHRASE_KEYS = {
  starting: 'status.loading',   // only reached with no source name to append
  active: 'status.playing',
  error: 'status.error',
  loading_disc: 'status.loadingAlbum',
  ejecting: 'status.ejecting'
};

/**
 * The phrase for a missing prerequisite, which outranks the state's own.
 *
 * `no_network` and `no_internet` are two lines rather than one because the
 * actions differ: nothing is reachable at all, versus a router that is up but
 * has no route out. Milō cannot answer a captive portal (no browser), so the
 * backend already folded PORTAL into `no_internet`.
 */
const UNAVAILABLE_PHRASE_KEYS = {
  no_network: 'status.noNetwork',
  no_internet: 'status.noInternet',
  no_account: 'status.accountNotConnected',
  no_drive: 'audioSources.cdSource.noDriveConnected'
};

/**
 * The sources a session is opened *to*, from the other end: a phone picks the
 * speaker (Spotify, Qobuz), a sender casts (AirPlay, DLNA), a device pairs
 * (Bluetooth, Mac). Their idle line invites that connection. The other four —
 * Radio, Podcasts, CD, Music Library — are played from Milō itself and say they
 * are ready to play. Two phrases derived from who starts a session, instead of
 * one written per source.
 */
const SENDER_DRIVEN_SOURCES = ['spotify', 'qobuz', 'airplay', 'dlna', 'bluetooth', 'mac'];

/**
 * "Démarrage de <source>" reads as one sentence broken over two lines, and
 * French agrees the article with the source noun — "du" Bluetooth, "de la"
 * radio, "de" Spotify. That is what the three keys are for; the other seven
 * locales collapse them onto one string. It is also why `starting` is the one
 * state whose phrase leads and whose source name takes the emphasised line.
 */
const STARTING_PHRASE_KEYS = {
  bluetooth: 'status.loadingOfMasculine',
  mac: 'status.loadingOfMasculine',
  cd: 'status.loadingOfMasculine',
  radio: 'status.loadingOfFeminine',
  spotify: 'status.loadingOf',
  podcast: 'status.loadingOf',
  airplay: 'status.loadingOf',
  dlna: 'status.loadingOf',
  qobuz: 'status.loadingOf',
  music_library: 'status.loadingOf'
};

const sourceName = computed(() => {
  const key = AUDIO_SOURCE_LABEL_KEYS[props.sourceType];
  return key ? t(key) : '';
});

const phrase = computed(() => {
  // A missing prerequisite outranks every state: with no link, no account or no
  // drive, neither "Ready" nor "Playing" is true any more.
  if (props.unavailableReason) return t(UNAVAILABLE_PHRASE_KEYS[props.unavailableReason]);

  if (props.displayState !== 'ready') return t(PHRASE_KEYS[props.displayState]);

  return SENDER_DRIVEN_SOURCES.includes(props.sourceType)
    ? t('status.ready')
    : t('status.readyToPlay');
});

/**
 * The two lines, plus which of them carries the identity — the emphasis follows
 * the name wherever it sits, and the other line is muted.
 *
 * Line 1 is the source and line 2 the phrase, except in the two cases where the
 * phrase is the start of a sentence the name completes: "Démarrage de <source>"
 * and "Connecté à <sender>". There the phrase leads and the name takes line 2.
 *
 * `starting` outranks a missing prerequisite — a start genuinely under way is
 * worth showing, and it settles on its own within seconds. Everything else
 * yields to it: "Connecté à <sender>" over a dead link is exactly the kind of
 * stale sentence the two-line builder exists to prevent.
 */
const status = computed(() => {
  if (props.displayState === 'starting' && sourceName.value) {
    return { lines: [t(STARTING_PHRASE_KEYS[props.sourceType]), sourceName.value], nameLine: 2 };
  }

  if (!props.unavailableReason && props.displayState === 'active' && props.deviceName) {
    // ROC is a one-way stream from N senders rather than a link to one device,
    // which is a different sentence, not a different layout.
    const lead = props.sourceType === 'mac' ? t('status.audioReceivedFrom') : t('status.connectedTo');
    return { lines: [lead, formatDeviceNames(props.deviceName)], nameLine: 2 };
  }

  // `none` has no label of its own: the phrase is the whole card.
  return sourceName.value
    ? { lines: [sourceName.value, phrase.value], nameLine: 1 }
    : { lines: [phrase.value], nameLine: 1 };
});

// Single bottom action button on the card, or null to hide it. Mutually
// exclusive by construction: a missing prerequisite is answered first, since
// its fix is what unblocks everything downstream — retrying a start that has no
// network cannot succeed. `no_drive` is the one reason with no button: plugging
// a drive in is not something the UI can offer.
//
// `starting` shows none of them, prerequisites included: the card reads
// "Démarrage de <source>" while the start is under way, and a button offering
// to fix something the start has not yet failed on is an answer to a question
// nobody asked. It appears when the state settles, a few hundred ms later.
const actionButton = computed(() => {
  if (props.displayState === 'starting') return null;

  if (props.unavailableReason) {
    if (props.unavailableReason === 'no_account') {
      return { label: t('status.connect'), disabled: false, onClick: () => emit('connect') };
    }
    if (props.unavailableReason === 'no_drive') return null;
    return {
      label: t('status.networkSettings'),
      disabled: false,
      onClick: () => emit('open-network-settings'),
    };
  }
  if (props.displayState === 'error') {
    return {
      label: t('status.retry'),
      disabled: false,
      onClick: () => emit('retry'),
    };
  }
  if (props.sourceType === 'bluetooth' && props.displayState === 'active') {
    return {
      label: props.isDisconnecting ? t('status.disconnecting') : t('status.disconnect'),
      disabled: props.isDisconnecting,
      onClick: () => emit('disconnect'),
    };
  }
  return null;
});
</script>

<style scoped>
/* === COMPONENT STYLES === */
.source-status {
  background: var(--color-background-neutral);
  border-radius: var(--radius-07);
  box-shadow: var(--shadow-02);
  width: 364px;
  position: relative;
  margin: auto;
}

.source-status-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
  height: 100%;
}

.source-status-inner {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  align-items: center;
  justify-content: flex-start;
  padding: var(--space-06) var(--space-04) var(--space-04) var(--space-04);
  position: relative;
  width: 100%;
  height: 100%;
}

.device-info {
  position: relative;
  flex-shrink: 0;
  width: 100%;
}

.device-info-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
  width: 100%;
  height: 100%;
}

.device-info-inner {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  align-items: center;
  justify-content: flex-start;
  padding: 0 var(--space-04) var(--space-04) var(--space-04);
  position: relative;
  width: 100%;
}

/* Source icon */
.source-icon {
  position: relative;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: var(--color-background);
  border-radius: var(--radius-02);
}

/* Text status */
.device-status {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
}

.status-single h2,
.status-line-1 h2,
.status-line-2 h2 {
  color: var(--color-text);
}

/* Line-break support for ROC multi-clients */
.status-line-2 h2 {
  white-space: pre-line;
}

/* The line that does not carry the name — the phrase under the source, or the
   "connected to" above a sender. One rule for both, since which line it lands
   on is the builder's call (see `status.nameLine`), not the stylesheet's. */
.muted-line h2 {
  color: var(--color-text-secondary);
}

/* Bottom action button (disconnect / connect) — the <button> IS the full-width
   bar so the entire surface is clickable, not just the label. */
.action-button {
  box-sizing: border-box;
  width: 100%;
  height: 42px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-02) var(--space-05);
  background: var(--color-background-strong);
  border: none;
  border-radius: var(--radius-04);
  color: var(--color-text-secondary);
  white-space: nowrap;
  cursor: pointer;
}

.action-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .source-status {
    width: 100%;
    max-width: 348px;
  }
}
</style>