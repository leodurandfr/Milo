// frontend/tests/composables/dropdownOptions.test.js
/**
 * A Dropdown option may be `disabled`, as a ButtonGroup option may: shown, not
 * pickable. Radio's search lists "Loading countries…" that way under its
 * "Country" heading, both with the value '' (none), for as long as the country
 * list is not there — indefinitely if it failed. If this fails, the loading
 * line is a choice like any other.
 *
 * Dropdown is mounted as a host, its menu kept in place (Teleport stubbed), and
 * driven by clicks on its options; what is asserted is what it emits to its
 * v-model, never its DOM.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { mount } from '@vue/test-utils';
import Dropdown from '@/components/ui/Dropdown.vue';

const OPTIONS = [
  { label: 'Country', value: '' },
  { label: 'Loading countries…', value: '', disabled: true },
  { label: 'France', value: 'FR' },
];

describe('Dropdown — options', () => {
  let wrapper;
  afterEach(() => wrapper?.unmount());

  async function openMenu() {
    wrapper = mount(Dropdown, {
      props: { modelValue: '', options: OPTIONS },
      attachTo: document.body,
      global: { stubs: { teleport: true }, directives: { press: {} } }
    });
    await wrapper.find('button').trigger('click');
  }

  function option(label) {
    const found = wrapper.findAll('[class~="dropdown-item"]').find((el) => el.text() === label);
    if (!found) throw new Error(`no option "${label}" in the open menu`);
    return found;
  }

  it('a disabled option cannot be picked', async () => {
    await openMenu();

    await option('Loading countries…').trigger('click');

    expect(wrapper.emitted('update:modelValue')).toBeUndefined();
    expect(wrapper.emitted('change')).toBeUndefined();
  });

  it('an enabled option beside it still can', async () => {
    await openMenu();

    await option('France').trigger('click');

    expect(wrapper.emitted('update:modelValue')).toEqual([['FR']]);
  });
});
