// frontend/tests/pure/rebootPoll.test.js
/**
 * The wait after "Apply and reboot", shared by the Hardware page and the setup
 * wizard. A ping answered before the backend ever went down is the backend
 * that never rebooted; reloading on it shows a config that was never applied
 * as the one in service.
 */
import { describe, it, expect } from 'vitest';
import { nextRebootStep } from '@/utils/rebootPoll';

/** Drives the steps the way both callers do, one ping result per poll. */
function run(pings, maxPolls = 60) {
  let wentDown = false;
  const steps = [];
  for (let polls = 1; polls <= pings.length; polls += 1) {
    const step = nextRebootStep({ pingOk: pings[polls - 1], wentDown, polls, maxPolls });
    steps.push(step);
    if (step === 'down') wentDown = true;
    if (step === 'reload' || step === 'timeout') break;
  }
  return steps;
}

describe('nextRebootStep', () => {
  it('a ping that answers before the backend ever went down never reloads', () => {
    expect(run([true, true, true])).toEqual(['wait', 'wait', 'wait']);
  });

  it('the first answer after a failed ping reloads', () => {
    expect(run([true, false, false, true, true])).toEqual(['wait', 'down', 'down', 'reload']);
  });

  it('past maxPolls it times out instead of reloading', () => {
    expect(run([true, true, true, true], 3)).toEqual(['wait', 'wait', 'timeout']);
    expect(run([false, false, false, true], 3)).toEqual(['down', 'down', 'timeout']);
  });
});
