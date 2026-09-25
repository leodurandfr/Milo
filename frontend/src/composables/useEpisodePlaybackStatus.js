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

  // `currentEpisode` is already null unless podcast is the selected source.
  const isCurrentEpisode = computed(() => podcastStore.currentEpisode?.uuid === episodeRef.value?.uuid);
  const phase = computed(() => (isCurrentEpisode.value ? unifiedStore.systemState.session?.phase : null));

  const isCurrentlyPlaying = computed(() => phase.value === 'playing');

  const isCurrentEpisodeBuffering = computed(() =>
    podcastStore.isEpisodePending(episodeRef.value?.uuid) || phase.value === 'loading'
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

  // The current episode's playhead, from the state (ms).
  const live = computed(() => (isCurrentEpisode.value ? podcastStore.currentEpisodeProgress : null));

  const hasProgress = computed(() => {
    // A remaining time needs the duration, which a loading file has not announced yet.
    if (isCurrentEpisode.value) {
      return (live.value?.positionMs || 0) > 0 && !!live.value?.durationMs;
    }
    return (episodeProgress.value?.position || 0) > 0;
  });

  const timeRemaining = computed(() => {
    let remaining;

    // If this is the current episode, use its live playhead (ms → s)
    if (isCurrentEpisode.value) {
      remaining = Math.floor(((live.value?.durationMs || 0) - (live.value?.positionMs || 0)) / 1000);
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
    // If this is the current episode and its file announced a duration, use it
    // (ms → s); a loading one has none yet.
    if (isCurrentEpisode.value && live.value?.durationMs) {
      return formatDuration(Math.floor(live.value.durationMs / 1000));
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
