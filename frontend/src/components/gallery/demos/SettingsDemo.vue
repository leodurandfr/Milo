<!-- frontend/src/components/gallery/demos/SettingsDemo.vue -->
<template>
  <GalleryItem id="SettingsContainer">
    <GalleryVariant label="its whole contribution: one gap between sections" stacked>
      <SettingsContainer>
        <SettingsSection title="Volume">
          <SettingItem label="Startup volume">
            <ControlSample />
          </SettingItem>
        </SettingsSection>
        <SettingsSection title="Screen">
          <SettingItem label="Brightness">
            <ControlSample />
          </SettingItem>
        </SettingsSection>
      </SettingsContainer>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="SettingsSection">
    <GalleryVariant label=":title — the built-in heading" stacked>
      <SettingsSection title="Volume">
        <SettingItem label="Startup volume">
          <ControlSample />
        </SettingItem>
      </SettingsSection>
    </GalleryVariant>
    <GalleryVariant label="header slot — replaces the title, which is why both are never passed" stacked>
      <SettingsSection title="Ignored while the slot is filled">
        <template #header>
          <SectionHeader title="Volume" subtitle="Startup level and limits" />
        </template>
        <SettingItem label="Startup volume">
          <ControlSample />
        </SettingItem>
      </SettingsSection>
    </GalleryVariant>
    <GalleryVariant label="no title at all — a card with only its content" stacked>
      <SettingsSection>
        <ControlSample />
      </SettingsSection>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="SettingItem">
    <GalleryVariant label=":label — mono, secondary, above the control" stacked>
      <SettingItem label="Startup volume">
        <ControlSample />
      </SettingItem>
    </GalleryVariant>
    <GalleryVariant label="no label — the wrapper alone, for a full-width control" stacked>
      <SettingItem>
        <ControlSample />
      </SettingItem>
    </GalleryVariant>
  </GalleryItem>

  <GalleryItem id="SectionHeader">
    <GalleryVariant label=":title" stacked>
      <SectionHeader title="Stations" />
    </GalleryVariant>
    <GalleryVariant label=":title + :subtitle" stacked>
      <SectionHeader title="Stations" subtitle="24 saved" />
    </GalleryVariant>
    <GalleryVariant label="actions slot — right-aligned, and it stacks below 4:3" stacked>
      <SectionHeader title="Stations" subtitle="24 saved">
        <template #actions>
          <Button size="small" variant="brand">Add</Button>
        </template>
      </SectionHeader>
    </GalleryVariant>
  </GalleryItem>
</template>

<script setup>
import GalleryItem from '../GalleryItem.vue';
import GalleryVariant from '../GalleryVariant.vue';
import ControlSample from '../samples/ControlSample.vue';
import SettingsContainer from '@/components/settings/SettingsContainer.vue';
import SettingsSection from '@/components/settings/SettingsSection.vue';
import SettingItem from '@/components/settings/SettingItem.vue';
import SectionHeader from '@/components/settings/SectionHeader.vue';
import Button from '@/components/ui/Button.vue';
</script>
