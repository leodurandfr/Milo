#!/bin/bash -e
# Stage prerun: ensure EXPORT_IMAGE marker exists
if [ ! -f "${STAGE_DIR}/EXPORT_IMAGE" ]; then
    touch "${STAGE_DIR}/EXPORT_IMAGE"
fi
