"""
file: backend/main.py
Point d'entrée principal de l'application oakOS.
"""
import asyncio
import logging
import uvicorn
import json
import subprocess
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Dict, Any

from backend.config.container import container
from backend.presentation.websockets.manager import WebSocketManager
from backend.presentation.websockets.events import WebSocketEventHandler
from backend.domain.audio import AudioState
from backend.presentation.api.routes.librespot import setup_librespot_routes
from backend.presentation.api.routes.snapclient import setup_snapclient_routes

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Création de l'application
app = FastAPI(title="oakOS Backend")

# Configuration CORS pour le développement
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En production, limitez aux origines spécifiques
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialisation des services
event_bus = container.event_bus()
audio_state_machine = container.audio_state_machine()

# Initialisation du gestionnaire WebSocket
ws_manager = WebSocketManager()
ws_event_handler = WebSocketEventHandler(event_bus, ws_manager)

# Ajout des routes pour les plugins
librespot_router = setup_librespot_routes(lambda: container.librespot_plugin())
app.include_router(librespot_router, prefix="/api")

snapclient_router = setup_snapclient_routes(lambda: container.snapclient_plugin())
app.include_router(snapclient_router, prefix="/api")

@app.on_event("startup")
async def startup_event():
    """Initialisation de l'application au démarrage"""
    logging.info("Starting oakOS backend...")
    
    # Initialisation des plugins
    try:
        # Initialiser le plugin librespot
        librespot_plugin = container.librespot_plugin()
        if await librespot_plugin.initialize():
            # Enregistrer le plugin dans la machine à états
            audio_state_machine.register_plugin(AudioState.LIBRESPOT, librespot_plugin)
            logging.info("Plugin librespot enregistré avec succès")
        else:
            logging.error("Échec de l'initialisation du plugin librespot")
            
        # Initialiser le plugin snapclient
        snapclient_plugin = container.snapclient_plugin()
        if await snapclient_plugin.initialize():
            # Enregistrer le plugin dans la machine à états
            audio_state_machine.register_plugin(AudioState.MACOS, snapclient_plugin)
            logging.info("Plugin snapclient enregistré avec succès")
        else:
            logging.error("Échec de l'initialisation du plugin snapclient")
            
    except Exception as e:
        logging.error(f"Erreur lors de l'initialisation des plugins: {str(e)}")

@app.get("/api/status")
async def status():
    """Endpoint de statut simple pour vérifier que l'API fonctionne."""
    return {"status": "running", "version": "0.1.0"}

@app.get("/api/audio/state")
async def get_audio_state():
    """Récupère l'état actuel du système audio"""
    return await audio_state_machine.get_current_state()

@app.post("/api/audio/source/{source}")
async def change_audio_source(source: str):
    """Change la source audio active"""
    try:
        target_state = AudioState(source)
        success = await audio_state_machine.transition_to(target_state)
        if success:
            return {"status": "success", "message": f"Switched to {source}"}
        else:
            return {"status": "error", "message": f"Failed to switch to {source}"}
    except ValueError:
        return {"status": "error", "message": f"Invalid source: {source}"}
    
@app.post("/api/audio/control/{source}")
async def control_audio_source(source: str, command_data: Dict[str, Any]):
    """Contrôle une source audio spécifique"""
    try:
        # Vérifier si la source existe
        if source not in [state.value for state in AudioState if state != AudioState.NONE and state != AudioState.TRANSITIONING]:
            raise HTTPException(status_code=400, detail=f"Source audio invalide: {source}")
        
        # Récupérer la commande et les données
        command = command_data.get("command")
        data = command_data.get("data", {})
        
        if not command:
            raise HTTPException(status_code=400, detail="Commande manquante")
        
        # Récupérer le plugin associé à la source
        source_state = AudioState(source)
        plugin = audio_state_machine.plugins.get(source_state)
        
        if not plugin:
            raise HTTPException(status_code=404, detail=f"Plugin non trouvé pour la source: {source}")
        
        # Exécuter la commande
        result = await plugin.handle_command(command, data)
        
        return {"status": "success", "result": result}
        
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": f"Erreur lors du contrôle de la source: {str(e)}"}
        
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Endpoint WebSocket pour la communication en temps réel"""
    await ws_manager.connect(websocket)
    try:
        # Envoyer un message de confirmation de connexion
        await websocket.send_json({
            "type": "connection_established",
            "data": {"message": "Connected to oakOS backend"}
        })
        
        # Boucle de réception des messages
        while True:
            try:
                # Attendre un message avec un timeout
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                
                # Traiter le message
                logging.info(f"Received WebSocket message: {data[:100]}...")
                
                # Pour l'instant, simplement accuser réception
                try:
                    message = json.loads(data)
                    await websocket.send_json({
                        "type": "message_ack",
                        "data": {"received_type": message.get("type", "unknown")}
                    })
                except json.JSONDecodeError:
                    logging.error(f"Invalid JSON received: {data}")
                    
            except asyncio.TimeoutError:
                # Envoyer un ping pour maintenir la connexion active
                await websocket.send_json({"type": "ping"})
                
    except WebSocketDisconnect:
        logging.info("WebSocket disconnected normally")
        ws_manager.disconnect(websocket)
    except Exception as e:
        logging.error(f"WebSocket error: {str(e)}")
        ws_manager.disconnect(websocket)

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)