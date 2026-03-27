#!/bin/bash
# Milo Client - Base System Setup
#
# Installs base dependencies, configures hostname, creates the milo-client user,
# discovers the main Milo server, clones the repo, and sets up the Python app.
#
# Can be sourced from install-client.sh or run standalone.

set -e

MILO_CLIENT_USER="${MILO_CLIENT_USER:-milo-client}"
MILO_CLIENT_HOME="${MILO_CLIENT_HOME:-/home/$MILO_CLIENT_USER}"
MILO_CLIENT_REPO_DIR="${MILO_CLIENT_REPO_DIR:-$MILO_CLIENT_HOME/repo}"
MILO_CLIENT_APP_DIR="${MILO_CLIENT_APP_DIR:-$MILO_CLIENT_REPO_DIR/milo-client/app}"
MILO_CLIENT_VENV_DIR="${MILO_CLIENT_VENV_DIR:-$MILO_CLIENT_HOME/venv}"
MILO_CLIENT_DATA_DIR="${MILO_CLIENT_DATA_DIR:-/var/lib/milo-client}"
MILO_CLIENT_REPO_URL="${MILO_CLIENT_REPO_URL:-https://github.com/leodurandfr/Milo.git}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/../../install/common.sh"
fi

install_dependencies() {
    log_info "Updating system..."

    export DEBIAN_FRONTEND=noninteractive
    export DEBCONF_NONINTERACTIVE_SEEN=true

    echo 'Dpkg::Options {
       "--force-confdef";
       "--force-confnew";
    }' | sudo tee /etc/apt/apt.conf.d/local >/dev/null

    sudo apt update
    sudo apt upgrade -y

    log_info "Installing minimal dependencies..."
    sudo apt install -y \
        git \
        python3-pip \
        python3-venv \
        python3-dev \
        libasound2-dev \
        avahi-daemon \
        avahi-utils

    log_success "Dependencies installed"
}

suppress_pulseaudio() {
    log_info "Removing PulseAudio/PipeWire..."
    sudo apt remove -y pulseaudio pipewire || true
    sudo apt autoremove -y
    log_success "PulseAudio/PipeWire removed"
}

discover_milo_principal() {
    # Use --server if provided (static IP for environments without mDNS)
    if [[ -n "${ARG_SERVER_IP:-}" ]]; then
        MILO_PRINCIPAL_IP="$ARG_SERVER_IP"
        log_success "Main Milo server: $MILO_PRINCIPAL_IP (from --server)"
        return 0
    fi

    log_info "Searching for main Milo on the network..."

    # Verify milo.local is reachable, but store the hostname instead of the
    # resolved IP so the client stays connected after network interface changes
    # (e.g. main Milo switching from Ethernet to WiFi).
    local resolved_ip
    if resolved_ip=$(getent hosts milo.local 2>/dev/null | awk '{print $1}' | head -1) && [[ -n "$resolved_ip" ]]; then
        MILO_PRINCIPAL_IP="milo.local"
        log_success "Main Milo found at: $resolved_ip (milo.local)"
        return 0
    fi

    log_error "Unable to find main Milo on the network."
    log_error "Make sure the main Milo is running, or use --server <ip> to specify its IP address."
    exit 1
}

setup_hostname() {
    local new_hostname="milo-client"
    local current_hostname
    current_hostname=$(hostname)

    if [ "$current_hostname" != "$new_hostname" ]; then
        log_info "Configuring hostname '$new_hostname'..."
        echo "$new_hostname" | sudo tee /etc/hostname > /dev/null
        sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$new_hostname/" /etc/hosts
        sudo hostnamectl set-hostname "$new_hostname"
        log_success "Hostname configured"
    else
        log_success "Hostname '$new_hostname' already configured"
    fi
}

create_milo_client_user() {
    if id "$MILO_CLIENT_USER" &>/dev/null; then
        log_info "User '$MILO_CLIENT_USER' already exists"
    else
        log_info "Creating user '$MILO_CLIENT_USER'..."
        sudo useradd -m -s /bin/bash -G audio,sudo "$MILO_CLIENT_USER"
        log_success "User '$MILO_CLIENT_USER' created"
    fi

    sudo mkdir -p "$MILO_CLIENT_DATA_DIR"
    sudo chown -R "$MILO_CLIENT_USER:audio" "$MILO_CLIENT_DATA_DIR"
}

clone_milo_client_repo() {
    log_info "Cloning Milo repository (sparse checkout)..."

    # If repo already exists, update it instead of cloning
    if [[ -d "$MILO_CLIENT_REPO_DIR/.git" ]]; then
        log_info "Repository already exists, updating..."
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" fetch --depth 1 origin main
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" reset --hard origin/main
        log_success "Repository updated"
    elif [[ -d "$MILO_CLIENT_REPO_DIR" ]]; then
        # Directory exists but not a git repo - remove and clone fresh
        log_warning "Removing incomplete repository directory..."
        sudo rm -rf "$MILO_CLIENT_REPO_DIR"
        sudo -u "$MILO_CLIENT_USER" git clone --no-checkout --depth 1 "$MILO_CLIENT_REPO_URL" "$MILO_CLIENT_REPO_DIR"
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" sparse-checkout init --cone
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" sparse-checkout set milo-client
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" checkout
        log_success "Repository cloned (sparse checkout: milo-client/)"
    else
        # Fresh clone
        sudo -u "$MILO_CLIENT_USER" git clone --no-checkout --depth 1 "$MILO_CLIENT_REPO_URL" "$MILO_CLIENT_REPO_DIR"
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" sparse-checkout init --cone
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" sparse-checkout set milo-client
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" checkout
        log_success "Repository cloned (sparse checkout: milo-client/)"
    fi

    # Unshallow and fetch tags so git describe can resolve the version
    # (shallow --depth 1 clone cannot trace back to tags)
    if ! sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" fetch --unshallow --tags origin 2>/dev/null; then
        log_warning "Could not fetch full tag history — version will show as a commit hash"
    fi

    # Write initial app version (same format as deploy_update writes)
    local app_version
    app_version=$(sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" describe --tags --always 2>/dev/null || echo "unknown")
    sudo tee "$MILO_CLIENT_DATA_DIR/app-version" > /dev/null <<< "$app_version"
    sudo chown "$MILO_CLIENT_USER:audio" "$MILO_CLIENT_DATA_DIR/app-version"
    log_success "App version recorded: $app_version"
}

install_milo_client_application() {
    log_info "Configuring Python environment for Milo Client..."

    sudo -u "$MILO_CLIENT_USER" python3 -m venv "$MILO_CLIENT_VENV_DIR"
    sudo -u "$MILO_CLIENT_USER" bash -c 'source "$1/bin/activate" && pip install --upgrade pip' -- "$MILO_CLIENT_VENV_DIR"

    # Install packages from piwheels (faster for ARM)
    sudo -u "$MILO_CLIENT_USER" bash -c 'source "$1/bin/activate" && pip install -r "$2/requirements.txt"' -- "$MILO_CLIENT_VENV_DIR" "$MILO_CLIENT_APP_DIR"

    # Install camilladsp from GitHub (not available on PyPI/piwheels)
    log_info "Installing camilladsp from GitHub..."
    sudo -u "$MILO_CLIENT_USER" bash -c 'source "$1/bin/activate" && pip install git+https://github.com/HEnquist/pycamilladsp.git' -- "$MILO_CLIENT_VENV_DIR"

    log_success "Milo Client application installed"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_dependencies
    suppress_pulseaudio
    discover_milo_principal
    export MILO_PRINCIPAL_IP
    setup_hostname
    create_milo_client_user
    clone_milo_client_repo
    install_milo_client_application
    sudo rm -f /etc/apt/apt.conf.d/local
    log_success "Base system setup complete"
fi
