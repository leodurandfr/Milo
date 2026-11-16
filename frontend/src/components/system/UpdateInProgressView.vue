<!-- frontend/src/components/system/UpdateInProgressView.vue -->
<!--
  Full-screen takeover while Milō replaces itself: from the download of the new
  release to the moment it has held after its reboot. Nothing behind it can be
  used meanwhile, and the one thing asked of the reader is to leave the power on.
  Dark in both themes, as the boot screen it hands over to and back from.
-->
<template>
  <div class="update-in-progress-view">
    <Logo position="top" :visible="true" />

    <div class="content">
      <div class="card">
        <div class="card-head">
          <h1 class="heading-2 card-title">{{ t('miloUpdate.title') }}</h1>
          <p v-if="versions" class="text-mono-medium card-versions">{{ versions }}</p>
        </div>
        <div class="card-progress">
          <!-- No count and no percentage: nothing here can stall at a number. -->
          <div class="indeterminate" role="progressbar" :aria-label="stepLabel">
            <span class="indeterminate-bar"></span>
          </div>
          <p class="text-body-medium card-step">{{ stepLabel }}</p>
        </div>
      </div>
    </div>

    <!-- Uppercase by the stylesheet alone: the locale keeps the sentence's own
         case, which is what a screen reader reads out. -->
    <p class="text-mono-medium update-footer">{{ t('miloUpdate.doNotUnplug') }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { stripTagPrefix } from '@/utils/miloRelease';
import Logo from '@/components/ui/Logo.vue';

const props = defineProps({
  phase: {
    type: String,
    required: true,
    validator: (value) => ['download', 'install', 'restart', 'verify'].includes(value)
  },
  fromVersion: {
    type: String,
    default: null
  },
  toVersion: {
    type: String,
    default: null
  }
});

const { t } = useI18n();

const STEP_KEYS = {
  download: 'miloUpdate.stepDownload',
  install: 'miloUpdate.stepInstall',
  restart: 'miloUpdate.stepRestart',
  verify: 'miloUpdate.stepVerify'
};

const stepLabel = computed(() => t(STEP_KEYS[props.phase]));

// The tags as the release channel names them, whatever spelling arrives.
const tag = (version) => (version ? `v${stripTagPrefix(version)}` : null);

const versions = computed(() =>
  [tag(props.fromVersion), tag(props.toVersion)].filter(Boolean).join(' → ') || null);
</script>

<style scoped>
/* A region dark in both themes is a color-scheme scope, not a second set of
   roles (design-system.css). */
.update-in-progress-view {
  color-scheme: dark;
  position: fixed;
  inset: 0;
  z-index: 10000;
  background: var(--color-ground);
  display: flex;
  align-items: center;
  justify-content: center;
}

.content {
  width: min(560px, 90vw);
  padding: var(--space-04);
}

.card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-06);
  padding: var(--space-07) var(--space-06);
  text-align: center;
  background: var(--color-surface);
  border-radius: var(--radius-06);
}

.card-head {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-02);
}

.card-title {
  color: var(--color-text);
}

.card-versions {
  color: var(--color-brand);
}

.card-progress {
  width: min(320px, 100%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-03);
}

.card-step {
  color: var(--color-text-secondary);
}

/* ProgressBar's track (8px, round, fill-faint), with a segment sliding across
   it for as long as the update runs. */
.indeterminate {
  position: relative;
  width: 100%;
  height: 8px;
  border-radius: var(--radius-full);
  background-color: var(--color-fill-faint);
  overflow: hidden;
}

.indeterminate-bar {
  position: absolute;
  inset: 0 auto 0 0;
  width: 40%;
  border-radius: var(--radius-full);
  background-color: var(--color-text-secondary);
  animation: indeterminate-slide 1.6s ease-in-out infinite;
}

@keyframes indeterminate-slide {
  from { transform: translateX(-100%); }
  to { transform: translateX(250%); }
}

@media (prefers-reduced-motion: reduce) {
  .indeterminate-bar {
    width: 100%;
    animation: indeterminate-pulse 2s ease-in-out infinite;
  }
}

@keyframes indeterminate-pulse {
  0%, 100% { opacity: 0.25; }
  50% { opacity: 0.6; }
}

/* Where the boot screen writes its message. */
.update-footer {
  position: absolute;
  bottom: calc(var(--space-07) + env(safe-area-inset-bottom, 0px) + var(--screen-inset));
  left: 0;
  right: 0;
  text-align: center;
  text-transform: uppercase;
  color: var(--color-text-secondary);
}
</style>
