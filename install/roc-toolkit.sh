#!/bin/bash
# Milo - roc-toolkit Installation (Mac Streaming via ROC)
#
# Builds and installs roc-toolkit from source for Mac audio streaming.
#
# Can be sourced from install.sh or run standalone.

set -e

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

install_roc_toolkit() {
    log_info "Installing roc-toolkit..."

    sudo apt install -y g++ pkg-config scons ragel gengetopt libuv1-dev \
      libspeexdsp-dev libunwind-dev libsox-dev libsndfile1-dev libssl-dev libasound2-dev \
      libtool intltool autoconf automake make cmake avahi-utils libpulse-dev

    local temp_dir
    temp_dir=$(mktemp -d)
    register_temp_dir "$temp_dir"
    cd "$temp_dir"

    git clone https://github.com/roc-streaming/roc-toolkit.git
    cd roc-toolkit
    scons -Q --build-3rdparty=openfec
    sudo scons -Q --build-3rdparty=openfec install
    sudo ldconfig

    cd ~
    rm -rf "$temp_dir"

    roc-recv --version

    log_success "roc-toolkit installed"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_roc_toolkit
fi
