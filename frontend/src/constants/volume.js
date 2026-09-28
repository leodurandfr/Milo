/**
 * The level a client is drawn at before the server has sent one: the
 * backend's `DEFAULT_VOLUME_DB` (backend/config/constants.py).
 */
export const DEFAULT_VOLUME_DB = -45;

/**
 * The largest delta one `POST /api/volume/adjust` accepts, either way
 * (`VolumeAdjustRequest.delta_db`, backend/api/models.py).
 *
 * Both are held equal to the backend by tests/architecture/volumeContract.test.js.
 */
export const MAX_ADJUST_DB = 60;
