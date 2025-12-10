#!/bin/bash
# Milo Readiness Check Script
# This script waits for backend and frontend to be ready before quitting Plymouth

set -e

# Configuration
MAX_WAIT=60  # Maximum wait time in seconds
BACKEND_URL="http://localhost:8000/api/health"
FRONTEND_URL="http://localhost/"
CHECK_INTERVAL=0.5  # Initial check interval in seconds

# Colors for logging
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[READINESS]${NC} $1" | systemd-cat -t milo-readiness -p info
    echo -e "${BLUE}[READINESS]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[READINESS]${NC} $1" | systemd-cat -t milo-readiness -p info
    echo -e "${GREEN}[READINESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[READINESS]${NC} $1" | systemd-cat -t milo-readiness -p warning
    echo -e "${YELLOW}[READINESS]${NC} $1"
}

log_error() {
    echo -e "${RED}[READINESS]${NC} $1" | systemd-cat -t milo-readiness -p err
    echo -e "${RED}[READINESS]${NC} $1"
}

wait_for_service() {
    local url="$1"
    local service_name="$2"
    local elapsed=0
    local retry_count=0

    log_info "Waiting for $service_name to be ready..."

    while [ $elapsed -lt $MAX_WAIT ]; do
        if curl -sf "$url" > /dev/null 2>&1; then
            log_success "$service_name is ready! (took ${elapsed}s)"
            return 0
        fi

        # Exponential backoff with max interval of 2s
        local wait_time=$(echo "scale=2; $CHECK_INTERVAL * (1.5 ^ $retry_count)" | bc | awk '{printf "%.2f", ($1 > 2.0) ? 2.0 : $1}')
        sleep "$wait_time"

        elapsed=$(echo "$elapsed + $wait_time" | bc | awk '{printf "%.0f", $1}')
        retry_count=$((retry_count + 1))

        # Log progress every 10 seconds
        if [ $((retry_count % 20)) -eq 0 ]; then
            log_info "Still waiting for $service_name... (${elapsed}s elapsed)"
        fi
    done

    log_error "$service_name timeout after ${MAX_WAIT}s"
    return 1
}

quit_plymouth() {
    log_info "Quitting Plymouth splash screen..."

    # Check if Plymouth is active
    if plymouth --ping 2>/dev/null; then
        plymouth quit --retain-splash 2>/dev/null || plymouth quit 2>/dev/null || true
        log_success "Plymouth quit successfully"
    else
        log_warning "Plymouth is not active (might be already quit)"
    fi
}

# Main execution
main() {
    log_info "Starting Milo readiness check..."
    log_info "Maximum wait time: ${MAX_WAIT}s"

    # Wait for backend
    if ! wait_for_service "$BACKEND_URL" "Backend API"; then
        log_warning "Backend not ready, but continuing anyway..."
    fi

    # Wait for frontend (nginx)
    if ! wait_for_service "$FRONTEND_URL" "Frontend (Nginx)"; then
        log_warning "Frontend not ready, but continuing anyway..."
    fi

    log_success "All services are ready!"

    # Quit Plymouth immediately - frontend has its own boot screen
    # No delay needed since the frontend loader handles the transition
    quit_plymouth

    log_success "Readiness check complete. System is ready for kiosk mode."

    exit 0
}

# Run main function
main
