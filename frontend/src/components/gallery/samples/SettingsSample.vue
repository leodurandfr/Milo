<!-- frontend/src/components/gallery/samples/SettingsSample.vue -->
<!--
  Two settings sections, as a settings screen actually composes them.

  SettingsContainer's whole contribution is the gap *between* sections, so a
  single filler box would show nothing at all — the sample has to be plural to be
  a sample. Everything here is the real components with real props; the only
  thing invented is the copy.
-->
<template>
  <SettingsSection title="Volume">
    <SettingItem label="Startup volume">
      <ControlSample />
    </SettingItem>
    <SettingItem label="Restore on boot">
      <Toggle v-model="restoreOnBoot" />
    </SettingItem>
  </SettingsSection>

  <SettingsSection title="Screen">
    <SettingItem label="Brightness">
      <ControlSample />
    </SettingItem>
  </SettingsSection>
</template>

<script setup>
import { ref } from 'vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import Toggle from '@/components/ui/Toggle.vue';
import ControlSample from './ControlSample.vue';

const restoreOnBoot = ref(true);
</script>
