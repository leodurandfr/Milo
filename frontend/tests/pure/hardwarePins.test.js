// frontend/tests/pure/hardwarePins.test.js
/**
 * What breaks when these fail: the Hardware page offers a GPIO that
 * HardwareConfigRequest refuses (two encoder lines on one pin, the IR data
 * line on an encoder pin, the power LED on the IR pin), and Apply & Reboot
 * answers a 422 after two confirmations. Or it offers too few, and a wiring
 * the unit accepts cannot be chosen.
 */
import { describe, it, expect } from 'vitest';
import { pinOptionsByField, unsetPinFields } from '@/utils/hardwarePins';

const FREE = [5, 6, 13, 17, 22, 23, 26, 27].map(value => ({ value, label: `GPIO ${value}` }));
const values = options => options.map(pin => pin.value);
const pinOptions = (config, free, field) => pinOptionsByField(config, free)[field];

const CONFIG = {
  rotary_enabled: true, clk_pin: 22, dt_pin: 27, sw_pin: 23,
  ir_enabled: true, ir_gpio_pin: 17,
  power_button_enabled: true, power_led_gpio_pin: 26,
};

describe('pinOptionsByField', () => {
  it('offers a field its own pin and every free pin no other enabled peripheral holds', () => {
    expect(values(pinOptions(CONFIG, FREE, 'clk_pin'))).toEqual([5, 6, 13, 22]);
    expect(values(pinOptions(CONFIG, FREE, 'ir_gpio_pin'))).toEqual([5, 6, 13, 17]);
    expect(values(pinOptions(CONFIG, FREE, 'power_led_gpio_pin'))).toEqual([5, 6, 13, 26]);
  });

  it('frees the pins of a peripheral switched off', () => {
    const config = { ...CONFIG, rotary_enabled: false, power_button_enabled: false };

    expect(values(pinOptions(config, FREE, 'ir_gpio_pin'))).toEqual(values(FREE));
  });

  it('never offers a pin the card holds', () => {
    const free = FREE.filter(pin => pin.value !== 5);

    expect(values(pinOptions(CONFIG, free, 'sw_pin'))).not.toContain(5);
  });
});

describe('unsetPinFields', () => {
  it('names nothing for a wiring the unit accepts', () => {
    expect(unsetPinFields(CONFIG, FREE)).toEqual([]);
  });

  it('names a pin the new card holds, on an enabled peripheral only', () => {
    const free = FREE.filter(pin => pin.value !== 17 && pin.value !== 22);

    expect(unsetPinFields(CONFIG, free)).toEqual(['clk_pin', 'ir_gpio_pin']);
    expect(unsetPinFields({ ...CONFIG, ir_enabled: false }, free)).toEqual(['clk_pin']);
  });

  it('names both sides of a collision a switch brought back', () => {
    // Stored while the IR receiver was off: its line on the encoder's SW pin.
    const config = { ...CONFIG, ir_gpio_pin: 23 };

    expect(unsetPinFields(config, FREE)).toEqual(['sw_pin', 'ir_gpio_pin']);
  });
});
