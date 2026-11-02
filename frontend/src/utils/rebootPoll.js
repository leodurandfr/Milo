/**
 * One step of the wait after an apply-and-reboot, decided from the ping just
 * made. The page reloads only on an answer that follows a failed ping: a
 * backend that answers before it ever went down has not rebooted, and a page
 * reloaded on it shows a config that never reached the boot config as the one
 * in service.
 *
 * `polls` counts the pings made so far, this one included; `maxPolls` of them
 * without the backend coming back is a timeout.
 *
 * @returns {'wait' | 'down' | 'reload' | 'timeout'}
 */
export function nextRebootStep({ pingOk, wentDown, polls, maxPolls }) {
  if (pingOk && wentDown) return 'reload';
  if (polls >= maxPolls) return 'timeout';
  return pingOk ? 'wait' : 'down';
}
