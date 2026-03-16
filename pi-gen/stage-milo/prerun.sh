#!/bin/bash -e

echo "ROOTFS_DIR=${ROOTFS_DIR}"
echo "PREV_ROOTFS_DIR=${PREV_ROOTFS_DIR}"

if [ ! -d "${ROOTFS_DIR}" ]; then
	echo "Copying rootfs from previous stage..."
	copy_previous
fi

if [ ! -d "${ROOTFS_DIR}/proc" ]; then
	echo "ERROR: rootfs copy failed — ${ROOTFS_DIR}/proc does not exist"
	echo "Contents of ROOTFS_DIR:"
	ls -la "${ROOTFS_DIR}/" 2>&1 || true
	echo "Contents of PREV_ROOTFS_DIR:"
	ls -la "${PREV_ROOTFS_DIR}/" 2>&1 || true
	exit 1
fi

echo "rootfs OK"
