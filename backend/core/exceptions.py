# backend/domain/exceptions.py
"""
Custom exception hierarchy for Milo - enables unified error handling.
"""

class MiloException(Exception):
    """Base exception for all Milo errors."""
    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


# ============================================================================
# RECOVERABLE ERRORS
# ============================================================================

class MiloRecoverableException(MiloException):
    """Recoverable error - system can continue operating."""
    pass


class ServiceUnavailableException(MiloRecoverableException):
    """External service is unavailable (snapcast, systemd, etc.)."""
    pass


class DeviceNotFoundException(MiloRecoverableException):
    """Audio device not found."""
    pass


class PluginNotReadyException(MiloRecoverableException):
    """Plugin not yet initialized."""
    pass


class TransitionInProgressException(MiloRecoverableException):
    """A transition is already in progress."""
    pass


# ============================================================================
# CRITICAL ERRORS
# ============================================================================

class MiloCriticalException(MiloException):
    """Critical error - requires intervention or restart."""
    pass


class StateCorruptionException(MiloCriticalException):
    """System state is corrupted or inconsistent."""
    pass


class InitializationFailedException(MiloCriticalException):
    """Critical component initialization failed."""
    pass


class DependencyInjectionException(MiloCriticalException):
    """Dependency injection error (circular resolution, etc.)."""
    pass


# ============================================================================
# CONFIGURATION ERRORS
# ============================================================================

class MiloConfigurationException(MiloException):
    """Configuration error."""
    pass


class InvalidSettingsException(MiloConfigurationException):
    """Invalid settings."""
    pass


class InvalidCommandException(MiloConfigurationException):
    """Invalid command or incorrect parameters."""
    pass


# ============================================================================
# TRANSITION ERRORS
# ============================================================================

class TransitionException(MiloException):
    """Error during audio source transition."""
    pass


class TransitionTimeoutException(TransitionException):
    """Timeout during transition."""
    pass


class SourceStartFailedException(TransitionException):
    """Audio source failed to start."""
    pass


class SourceStopFailedException(TransitionException):
    """Audio source failed to stop."""
    pass


# ============================================================================
# ROUTING ERRORS
# ============================================================================

class RoutingException(MiloException):
    """Audio routing error."""
    pass


class MultiroomActivationException(RoutingException):
    """Multiroom activation failed."""
    pass


class EqualizerActivationException(RoutingException):
    """Equalizer activation failed."""
    pass
