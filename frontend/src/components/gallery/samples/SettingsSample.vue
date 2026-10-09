<!-- frontend/src/components/gallery/samples/SettingsSample.vue -->
<!--
  Two settings sections, as a settings screen actually composes them.

  SectionStack's whole contribution is the gap *between* sections, so a
  single filler box would show nothing at all — the sample has to be plural to be
  a sample. Everything here is the real components with real props; the only
  thing invented is the copy.
-->
<template>
  <SectionCard title="Volume">
    <SettingItem label="Startup volume">
      <ControlSample />
    </SettingItem>
    <SettingItem label="Restore on boot">
      <Toggle v-model="restoreOnBoot" />
    </SettingItem>
  </SectionCard>

  <SectionCard title="Screen">
    <SettingItem label="Brightness">
      <ControlSample />
    </SettingItem>
  </SectionCard>
</template>

<script setup>
import { ref } from 'vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import Toggle from '@/components/ui/Toggle.vue';
import ControlSample from './ControlSample.vue';

const restoreOnBoot = ref(true);
</script>
