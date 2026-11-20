// frontend/src/utils/hardwarePins.js

/**
 * The GPIO fields of the Hardware page, each with the switch that puts its
 * peripheral on the header. HardwareConfigRequest refuses two enabled
 * peripherals on one GPIO (the encoder's three lines among themselves, the IR
 * data line, the power button's LED), so a pin counts only while its switch
 * is on.
 */
const PIN_FIELDS = {
  clk_pin: 'rotary_enabled',
  dt_pin: 'rotary_enabled',
  sw_pin: 'rotary_enabled',
  ir_gpio_pin: 'ir_enabled',
  power_led_gpio_pin: 'power_button_enabled',
};

/**
 * The pins a field's dropdown offers: those the card leaves free, less those
 * another enabled peripheral holds — exactly what the PUT accepts for it.
 *
 * @param {object} config - the page's config (pins and switches)
 * @param {Array<{value: number}>} freePins - the pins the selected card leaves free
 * @param {string} field - a key of PIN_FIELDS
 */
function pinOptions(config, freePins, field) {
  const taken = new Set(
    Object.entries(PIN_FIELDS)
      .filter(([other, enabledBy]) => other !== field && config[enabledBy])
      .map(([other]) => config[other])
  );
  return freePins.filter(pin => !taken.has(pin.value));
}

/**
 * Every GPIO field of the page, with the pins its dropdown offers (pinOptions).
 *
 * @param {object} config - the page's config (pins and switches)
 * @param {Array<{value: number}>} freePins - the pins the selected card leaves free
 * @returns {Record<string, Array<{value: number}>>}
 */
export function pinOptionsByField(config, freePins) {
  return Object.fromEntries(
    Object.keys(PIN_FIELDS).map(field => [field, pinOptions(config, freePins, field)])
  );
}

/**
 * The fields of an enabled peripheral whose pin is not among its options: a
 * pin the new card holds, or one another peripheral took meanwhile. Each
 * dropdown shows it unset, and the PUT would refuse it.
 */
export function unsetPinFields(config, freePins) {
  return Object.entries(PIN_FIELDS)
    .filter(([field, enabledBy]) => config[enabledBy]
      && !pinOptions(config, freePins, field).some(pin => pin.value === config[field]))
    .map(([field]) => field);
}
