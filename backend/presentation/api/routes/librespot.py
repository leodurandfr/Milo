"""
Routes API spécifiques pour le plugin librespot.
À placer dans backend/presentation/api/routes/librespot.py
"""
from fastapi import APIRouter, HTTPException, Query, Depends
import subprocess
import asyncio
from typing import Dict, Any, Optional

# Créer un router dédié pour librespot
router = APIRouter(
    prefix="/librespot",
    tags=["librespot"],
    responses={404: {"description": "Not found"}},
)

# Référence au plugin librespot (sera injectée via l'injection de dépendances)
librespot_plugin_dependency = None

def setup_librespot_routes(plugin_provider):
    """
    Configure les routes librespot avec une référence au plugin.
    
    Args:
        plugin_provider: Fonction qui retourne une instance du plugin librespot
    """
    global librespot_plugin_dependency
    librespot_plugin_dependency = plugin_provider
    return router

def get_librespot_plugin():
    """Dépendance pour obtenir le plugin librespot"""
    if librespot_plugin_dependency is None:
        raise HTTPException(
            status_code=500, 
            detail="Librespot plugin not initialized. Call setup_librespot_routes first."
        )
    return librespot_plugin_dependency()

@router.get("/status")
async def get_librespot_status(plugin = Depends(get_librespot_plugin)):
    """Récupère le statut actuel de go-librespot pour débogage"""
    try:
        # Vérifier si l'API de go-librespot est accessible
        try:
            # Vérifier d'abord si un appareil est connecté
            is_connected = await plugin.is_device_connected()
            
            # Récupérer le statut brut de l'API
            status = await plugin._fetch_status()
            
            # Essayer d'extraire les métadonnées
            try:
                metadata = await plugin._fetch_metadata() if is_connected else {}
            except Exception as metadata_error:
                metadata = {"error": str(metadata_error)}
            
            # Informations sur la connexion WebSocket
            ws_info = {
                "url": plugin.ws_url,
                "task_running": plugin.ws_task is not None and not plugin.ws_task.done()
            }
            
            # Informations sur le processus go-librespot
            process_info = None
            if plugin.process:
                process_info = {
                    "pid": plugin.process.pid,
                    "running": plugin.process.poll() is None
                }
            
            # Vérifier explicitement l'état de connexion et les métadonnées
            # pour fournir un indicateur clair au frontend
            device_connected = is_connected and (
                status.get("username") is not None or 
                bool(plugin.metadata) or
                bool(metadata)
            )
            
            return {
                "status": "ok",
                "api_accessible": True,
                "api_url": plugin.api_url,
                "device_connected": device_connected,  # Indicateur clair de connexion d'appareil
                "ws_info": ws_info,
                "process_info": process_info,
                "is_active": plugin.is_active,
                "raw_status": status,
                "metadata": metadata
            }
        except Exception as e:
            return {
                "status": "error",
                "api_accessible": False,
                "error": str(e),
                "message": "Impossible de communiquer avec l'API go-librespot",
                "api_url": plugin.api_url,
                "ws_url": plugin.ws_url,
                "device_connected": False,  # Explicitement non connecté en cas d'erreur
                "process_info": {
                    "pid": plugin.process.pid if plugin.process else None,
                    "running": plugin.process and plugin.process.poll() is None
                } if plugin.process else None
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors de la récupération du plugin librespot: {str(e)}",
            "device_connected": False  # Explicitement non connecté en cas d'erreur
        }

@router.post("/connect")
async def restart_librespot_connection(plugin = Depends(get_librespot_plugin)):
    """Redémarre la connexion avec go-librespot"""
    try:
        # Forcer un redémarrage de la connexion WebSocket
        await plugin._stop_websocket_connection()
        await plugin._start_websocket_connection()
        
        # Vérifier si le processus go-librespot fonctionne
        if plugin.process and plugin.process.poll() is not None:
            # Processus terminé, le redémarrer
            plugin.process = subprocess.Popen(
                [plugin.executable_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            await asyncio.sleep(5)  # Attendre que le processus démarre
        
        # Vérifier le statut après redémarrage
        try:
            status = await plugin._fetch_status()
            return {
                "status": "success",
                "message": "Connexion à go-librespot redémarrée avec succès",
                "api_status": status
            }
        except Exception as e:
            return {
                "status": "warning",
                "message": f"Connexion redémarrée mais API inaccessible: {str(e)}"
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors du redémarrage de la connexion: {str(e)}"
        }

@router.post("/restart")
async def restart_go_librespot(plugin = Depends(get_librespot_plugin)):
    """Redémarre complètement le processus go-librespot"""
    try:
        # D'abord, arrêter les connexions et le processus existants
        await plugin._stop_websocket_connection()
        
        # Tuer le processus existant si nécessaire
        if plugin.process:
            try:
                plugin.process.terminate()
                try:
                    plugin.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    plugin.process.kill()
            except Exception as kill_error:
                return {
                    "status": "warning",
                    "message": f"Erreur lors de l'arrêt du processus: {str(kill_error)}"
                }
        
        # Lancer un nouveau processus
        try:
            plugin.process = subprocess.Popen(
                [plugin.executable_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Attendre que le processus démarre
            await asyncio.sleep(5)
            
            # Vérifier si le processus fonctionne
            if plugin.process.poll() is not None:
                stdout, stderr = plugin.process.communicate()
                return {
                    "status": "error",
                    "message": "go-librespot s'est arrêté immédiatement après le démarrage",
                    "returncode": plugin.process.returncode,
                    "stdout": stdout.decode('utf-8', errors='replace'),
                    "stderr": stderr.decode('utf-8', errors='replace')
                }
            
            # Redémarrer la connexion WebSocket
            await plugin._start_websocket_connection()
            
            return {
                "status": "success",
                "message": "go-librespot redémarré avec succès",
                "pid": plugin.process.pid
            }
            
        except Exception as start_error:
            return {
                "status": "error",
                "message": f"Erreur lors du démarrage du processus: {str(start_error)}"
            }
    
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur inattendue: {str(e)}"
        }


@router.post("/force-disconnect")
async def force_librespot_disconnect(plugin = Depends(get_librespot_plugin)):
    """Force l'envoi d'un événement de déconnexion pour librespot"""
    try:
        # Publier un événement de déconnexion explicite
        await plugin.publish_status("disconnected", {
            "connected": False,
            "deviceConnected": False,
            "is_playing": False,
            "forced_disconnect": True  # Indiquer que c'est une déconnexion forcée
        })
        
        return {
            "status": "success",
            "message": "Événement de déconnexion Librespot envoyé"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors de l'envoi de l'événement de déconnexion: {str(e)}"
        }
        
        
@router.get("/logs")
async def get_librespot_logs(
    lines: int = Query(20, gt=0, le=200),
    plugin = Depends(get_librespot_plugin)
):
    """Récupère les dernières lignes des logs de go-librespot"""
    try:
        # Vérifier si le processus est en cours d'exécution
        if not plugin.process or plugin.process.poll() is not None:
            return {
                "status": "error",
                "message": "Le processus go-librespot n'est pas en cours d'exécution"
            }
        
        # Essayer de récupérer la sortie du processus
        try:
            # Vérifier si nous pouvons obtenir une sortie non bloquante
            import fcntl
            import os
            
            flags = fcntl.fcntl(plugin.process.stdout, fcntl.F_GETFL)
            fcntl.fcntl(plugin.process.stdout, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            
            flags = fcntl.fcntl(plugin.process.stderr, fcntl.F_GETFL)
            fcntl.fcntl(plugin.process.stderr, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            
            # Lire tout ce qui est disponible
            stdout_data = b""
            stderr_data = b""
            
            try:
                stdout_data = plugin.process.stdout.read()
            except (BlockingIOError, ValueError):
                pass
                
            try:
                stderr_data = plugin.process.stderr.read()
            except (BlockingIOError, ValueError):
                pass
                
            return {
                "status": "success",
                "pid": plugin.process.pid,
                "stdout": stdout_data.decode('utf-8', errors='replace') if stdout_data else "",
                "stderr": stderr_data.decode('utf-8', errors='replace') if stderr_data else ""
            }
            
        except Exception as output_error:
            return {
                "status": "warning",
                "message": f"Impossible de récupérer la sortie du processus: {str(output_error)}",
                "pid": plugin.process.pid
            }
    
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur inattendue: {str(e)}"
        }