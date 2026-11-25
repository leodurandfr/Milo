// frontend/src/constants/multiroomWrites.js
/**
 * The status a multiroom write answers when the server recorded the value and
 * a speaker did not take it yet: a zone's crossover pin (applied at the
 * member's next reconnection), a member's EQ independence and a zone's new
 * member ("saved, the speaker did not take its EQ"). The screen then says the
 * change is saved (`settings.writeDeferred`), never that it is not.
 *
 * Read per call site, never for every 502: the level trim and the delay are
 * pushed before they are recorded, so their 502 recorded nothing.
 */
export const RECORDED_NOT_APPLIED = 502;
