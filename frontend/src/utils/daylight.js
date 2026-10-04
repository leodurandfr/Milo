// frontend/src/utils/daylight.js
/**
 * Sunrise and sunset, computed offline — what the kiosk's `auto` theme follows.
 *
 * The NOAA solar calculator's algorithm (Meeus, as in NOAA's spreadsheet),
 * with the standard -0.833° horizon that accounts for refraction and the sun's
 * radius. Accurate to about a minute at the latitudes people live at, which is
 * well inside what a theme change needs.
 *
 * `isDaytime` asks whether the sun is above that horizon *now*, rather than
 * comparing against one day's sunrise and sunset: that needs no notion of which
 * local day an instant belongs to, and polar day and night fall out of it.
 */

const RAD = Math.PI / 180;
const DEG = 180 / Math.PI;
const MS_PER_DAY = 86400000;
const UNIX_EPOCH_JD = 2440587.5;

/** The sun's apparent altitude at sunrise and sunset. */
const HORIZON_DEG = -0.833;

function julianCentury(date) {
  const jd = date.getTime() / MS_PER_DAY + UNIX_EPOCH_JD;
  return (jd - 2451545) / 36525;
}

/** Declination (deg) and the equation of time (min) at a Julian century. */
function solarPosition(t) {
  const meanLong = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360;
  const meanAnom = 357.52911 + t * (35999.05029 - 0.0001537 * t);
  const eccent = 0.016708634 - t * (0.000042037 + 0.0000001267 * t);
  const center = Math.sin(meanAnom * RAD) * (1.914602 - t * (0.004817 + 0.000014 * t))
    + Math.sin(2 * meanAnom * RAD) * (0.019993 - 0.000101 * t)
    + Math.sin(3 * meanAnom * RAD) * 0.000289;
  const trueLong = meanLong + center;
  const omega = 125.04 - 1934.136 * t;
  const appLong = trueLong - 0.00569 - 0.00478 * Math.sin(omega * RAD);
  const meanObliq = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60;
  const obliq = meanObliq + 0.00256 * Math.cos(omega * RAD);

  const declination = Math.asin(Math.sin(obliq * RAD) * Math.sin(appLong * RAD)) * DEG;

  const y = Math.tan((obliq / 2) * RAD) ** 2;
  const eqTime = 4 * DEG * (
    y * Math.sin(2 * meanLong * RAD)
    - 2 * eccent * Math.sin(meanAnom * RAD)
    + 4 * eccent * y * Math.sin(meanAnom * RAD) * Math.cos(2 * meanLong * RAD)
    - 0.5 * y * y * Math.sin(4 * meanLong * RAD)
    - 1.25 * eccent * eccent * Math.sin(2 * meanAnom * RAD)
  );

  return { declination, eqTime };
}

/** The sun's altitude (deg, no refraction) at `date` seen from latitude/longitude. */
function solarAltitude(date, latitude, longitude) {
  const { declination, eqTime } = solarPosition(julianCentury(date));
  const minutesUtc = (date.getTime() % MS_PER_DAY + MS_PER_DAY) % MS_PER_DAY / 60000;
  const trueSolarTime = minutesUtc + eqTime + 4 * longitude;
  const hourAngle = trueSolarTime / 4 - 180;
  const cosZenith = Math.sin(latitude * RAD) * Math.sin(declination * RAD)
    + Math.cos(latitude * RAD) * Math.cos(declination * RAD) * Math.cos(hourAngle * RAD);
  return 90 - Math.acos(Math.min(1, Math.max(-1, cosZenith))) * DEG;
}

/**
 * Whether the sun is up at `date` — between sunrise and sunset. With no
 * location (`latitude` or `longitude` null: a timezone with no city) it is
 * always day, which keeps the light theme.
 */
export function isDaytime(date, latitude, longitude) {
  if (latitude == null || longitude == null) return true;
  return solarAltitude(date, latitude, longitude) > HORIZON_DEG;
}
