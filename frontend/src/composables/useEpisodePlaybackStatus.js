import { computed } from 'vue';
import { usePodcastStore } from '@/stores/podcastStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useI18n } from '@/services/i18n';

const LANGUAGE_TO_LOCALE = {
  english: 'en-US',
  french: 'fr-FR',
  spanish: 'es-ES',
  german: 'de-DE',
  italian: 'it-IT',
  portuguese: 'pt-BR',
  chinese: 'zh-CN',
  hindi: 'hi-IN',
};

function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return '0 min';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}min`;
  return `${m} min`;
}

export function useEpisodePlaybackStatus(episodeRef) {
  const { t, currentLanguage } = useI18n();
  const podcastStore = usePodcastStore();
  const unifiedStore = useUnifiedAudioStore();

  const isCurrentEpisode = computed(() => podcastStore.currentEpisode?.uuid === episodeRef.value?.uuid);
  const isPodcastActive = computed(() => unifiedStore.systemState.active_source === 'podcast');

  const isCurrentlyPlaying = computed(() =>
    isCurrentEpisode.value && isPodcastActive.value && (unifiedStore.systemState.metadata?.is_playing || false)
  );

  const isCurrentEpisodeBuffering = computed(() =>
    podcastStore.isEpisodePending(episodeRef.value?.uuid) ||
    (isCurrentEpisode.value && isPodcastActive.value && (unifiedStore.systemState.metadata?.is_buffering || false))
  );

  // Progress for a non-current episode: prefer the live cache entry (kept fresh
  // via WebSocket while something plays), fall back to the API snapshot on the prop.
  const episodeProgress = computed(() =>
    podcastStore.getEpisodeProgress(episodeRef.value?.uuid) || episodeRef.value?.playback_progress || null
  );

  const isCompleted = computed(() => {
    // The current episode is shown as playing/remaining, never "already listened"
    if (isCurrentEpisode.value) return false;
    return episodeProgress.value?.completed === true;
  });

  const hasProgress = computed(() => {
    // If this is the current episode, read live position from unified store (ms)
    if (isCurrentEpisode.value) {
      return (unifiedStore.systemState.metadata?.position || 0) > 0;
    }
    return (episodeProgress.value?.position || 0) > 0;
  });

  const timeRemaining = computed(() => {
    let remaining;

    // If this is the current episode, use live data (unified store, ms → s)
    if (isCurrentEpisode.value) {
      const meta = unifiedStore.systemState.metadata;
      remaining = Math.floor(((meta?.duration || 0) - (meta?.position || 0)) / 1000);
    } else {
      const progress = episodeProgress.value;
      if (!progress) return '';
      remaining = progress.duration - progress.position;
    }

    // Check if episode is completed (less than 5 seconds remaining)
    if (remaining <= 5) {
      return t('podcasts.episodeCompleted');
    }

    return formatDuration(remaining) + ' ' + t('podcasts.remaining');
  });

  const formattedDuration = computed(() => {
    // If this is the current episode, use live duration from unified store (ms → s)
    if (isCurrentEpisode.value) {
      return formatDuration(Math.floor((unifiedStore.systemState.metadata?.duration || 0) / 1000));
    }
    // Otherwise, use episode's static duration
    return formatDuration(episodeRef.value?.duration || 0);
  });

  const statusLabel = computed(() => {
    if (isCurrentlyPlaying.value) return t('podcasts.nowPlaying');
    if (isCompleted.value) return t('podcasts.alreadyListened');
    if (hasProgress.value) return timeRemaining.value;
    return formattedDuration.value;
  });

  function formatRelativeDate(epochSeconds) {
    const date = new Date(epochSeconds * 1000);
    const now = new Date();
    const diff = now - date;
    const days = Math.floor(diff / 86400000);

    if (days === 0) return t('podcasts.today');
    if (days === 1) return t('podcasts.yesterday');

    const locale = LANGUAGE_TO_LOCALE[currentLanguage.value] || 'en-US';
    const day = date.getDate();
    const month = date.toLocaleDateString(locale, { month: 'short' }).replace('.', '');
    const capitalized = month.charAt(0).toUpperCase() + month.slice(1);
    return `${day} ${capitalized}`;
  }

  const formattedDate = computed(() => {
    if (!episodeRef.value?.date_published) return '';
    return formatRelativeDate(episodeRef.value.date_published);
  });

  return {
    isCurrentlyPlaying,
    isCurrentEpisodeBuffering,
    isCompleted,
    hasProgress,
    timeRemaining,
    formattedDuration,
    formattedDate,
    statusLabel,
    pause: podcastStore.pause,
  };
}
