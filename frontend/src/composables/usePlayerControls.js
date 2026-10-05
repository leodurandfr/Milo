// frontend/src/composables/usePlayerControls.js
// What a source's player offers now, read from the wire: the controls
// utils/playerControls.js derives from `controls` and `details`, held steady
// while the source leaves, and the one way to send them. Read once per player
// through usePlayerState, so the full player and the navigation's playing bar
// derive their buttons by one rule, and the parts of one player from one copy.
import { computed, ref, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useDelayedFlag } from '@/composables/useDelayedFlag';
import { playerControls } from '@/utils/playerControls';

export function usePlayerControls(source) {
  const unifiedStore = useUnifiedAudioStore();

  // This source's slice of the state: another source's session, controls and
  // phase are not ours (the player is still on screen while it leaves).
  const isSelected = computed(() => unifiedStore.systemState.source === source);
  const session = computed(() => (isSelected.value ? unifiedStore.systemState.session : null));
  const controls = computed(() => (isSelected.value ? unifiedStore.systemState.controls : []));
  const details = computed(() => (isSelected.value ? unifiedStore.systemState.details : null));
  const phase = computed(() => session.value?.phase ?? null);

  // Which buttons are drawn is read from a settled state: while switching away
  // `controls` is empty, and a player leaving must not trade its transport for
  // nothing mid-fade. Whether each one is enabled, and a toggle's state, is the
  // live list's — a button the source would refuse now shows disabled.
  const settled = ref({ controls: [], details: null });
  watch(
    () => {
      const { switching, service } = unifiedStore.systemState;
      return isSelected.value && !switching && service === 'running'
        ? { controls: controls.value, details: details.value }
        : null;
    },
    (state) => {
      if (state) settled.value = state;
    },
    { immediate: true }
  );

  const liveControls = computed(() => playerControls({ controls: controls.value, details: details.value, phase: phase.value }));
  const shownControls = computed(() => {
    const live = new Map(liveControls.value.map(control => [control.id, control]));
    return playerControls({ ...settled.value, phase: phase.value })
      .map(control => live.get(control.id) ?? { ...control, enabled: false });
  });

  const isBuffering = useDelayedFlag(() => phase.value === 'loading');

  // sendCommand swallows + logs errors via the store, and answers whether the
  // source took the command. One the source does not list now would be
  // refused, so it is not sent (false).
  async function sendSourceCommand(command, data) {
    if (!controls.value.includes(command)) return false;
    return unifiedStore.sendCommand(source, command, data);
  }

  return {
    isSelected, session, controls, details, phase,
    liveControls, shownControls, isBuffering, sendSourceCommand
  };
}
