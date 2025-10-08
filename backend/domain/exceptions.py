# backend/domain/exceptions.py
"""
Hiérarchie d'exceptions custom pour Milo - Permet une gestion d'erreurs uniforme
"""

class MiloException(Exception):
    """Exception de base pour toutes les erreurs Milo"""
    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


# === Erreurs récupérables ===

class MiloRecoverableException(MiloException):
    """Erreur récupérable - le système peut continuer à fonctionner"""
    pass


class ServiceUnavailableException(MiloRecoverableException):
    """Un service externe n'est pas disponible (snapcast, systemd, etc.)"""
    pass


class DeviceNotFoundException(MiloRecoverableException):
    """Device audio introuvable"""
    pass


class PluginNotReadyException(MiloRecoverableException):
    """Plugin pas encore initialisé"""
    pass


class TransitionInProgressException(MiloRecoverableException):
    """Une transition est déjà en cours"""
    pass


# === Erreurs critiques ===

class MiloCriticalException(MiloException):
    """Erreur critique - nécessite intervention ou redémarrage"""
    pass


class StateCorruptionException(MiloCriticalException):
    """État système corrompu ou incohérent"""
    pass


class InitializationFailedException(MiloCriticalException):
    """Échec d'initialisation d'un composant critique"""
    pass


class DependencyInjectionException(MiloCriticalException):
    """Erreur dans l'injection de dépendances (résolution circulaire, etc.)"""
    pass


# === Erreurs de configuration ===

class MiloConfigurationException(MiloException):
    """Erreur de configuration"""
    pass


class InvalidSettingsException(MiloConfigurationException):
    """Settings invalides"""
    pass


class InvalidCommandException(MiloConfigurationException):
    """Commande invalide ou paramètres incorrects"""
    pass


# === Erreurs de transition ===

class TransitionException(MiloException):
    """Erreur durant une transition de source audio"""
    pass


class TransitionTimeoutException(TransitionException):
    """Timeout durant une transition"""
    pass


class SourceStartFailedException(TransitionException):
    """Échec du démarrage d'une source"""
    pass


class SourceStopFailedException(TransitionException):
    """Échec de l'arrêt d'une source"""
    pass


# === Erreurs de routage ===

class RoutingException(MiloException):
    """Erreur de routage audio"""
    pass


class MultiroomActivationException(RoutingException):
    """Échec d'activation du multiroom"""
    pass


class EqualizerActivationException(RoutingException):
    """Échec d'activation de l'equalizer"""
    pass
