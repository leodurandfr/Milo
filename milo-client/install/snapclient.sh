#!/bin/bash
# Milo Client - Snapclient Installation (Multiroom Audio)
#
# Installs Snapclient from GitHub releases or Debian repos.
# Only the client is needed (server runs on the main Milo).
#
# Can be sourced from install-client.sh or run standalone.

set -e

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/../../install/common.sh"
fi

install_snapclient() {
    install_snapcast_packages snapclient
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_snapclient
fi
