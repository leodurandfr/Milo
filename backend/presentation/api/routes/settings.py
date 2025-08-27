# backend/presentation/api/routes/settings.py - Version finale avec persistence et WebSocket
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal
import json
import os

# Modèle pour la requête de changement de langue
class LanguageRequest(BaseModel):
    language: Literal['français', 'english', 'español']

# Fichier de persistance de la langue - chemin persistant dans le home directory
LANGUAGE_CONFIG_FILE = os.path.expanduser('~/milo_language.json')

def load_current_language():
    """Charger la langue actuelle depuis le fichier de config"""
    try:
        if os.path.exists(LANGUAGE_CONFIG_FILE):
            with open(LANGUAGE_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                language = config.get('language', 'français')
                print(f"Loaded language from file: {language}")
                return language
    except Exception as e:
        print(f"Error loading language config: {e}")
    
    print("Using default language: français")
    return 'français'  # Langue par défaut

def save_current_language(language: str):
    """Sauvegarder la langue dans le fichier de config"""
    try:
        config_path = os.path.abspath(LANGUAGE_CONFIG_FILE)
        print(f"Saving language to: {config_path}")
        
        config = {'language': language}
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        
        print(f"Language saved successfully: {language}")
        return True
    except Exception as e:
        print(f"Error saving language config: {e}")
        return False

def create_settings_router(ws_manager=None):
    """Créer le router settings avec WebSocket manager"""
    router = APIRouter()
    
    @router.get("/language")
    async def get_current_language():
        """Récupérer la langue actuellement configurée"""
        try:
            language = load_current_language()
            return {
                "status": "success",
                "language": language
            }
        except Exception as e:
            print(f"Error getting language: {e}")
            raise HTTPException(status_code=500, detail=f"Error getting language: {str(e)}")

    @router.post("/language")
    async def set_language(request: LanguageRequest):
        """Changer la langue et notifier tous les clients via WebSocket"""
        try:
            new_language = request.language
            print(f"Received language change request: {new_language}")
            
            # Sauvegarder dans le fichier de config
            if not save_current_language(new_language):
                raise HTTPException(status_code=500, detail="Failed to save language")
            
            # Diffuser via WebSocket - Utiliser broadcast_dict de Milo
            if ws_manager:
                try:
                    print(f"WebSocket manager type: {type(ws_manager)}")
                    available_methods = [method for method in dir(ws_manager) if not method.startswith('_') and callable(getattr(ws_manager, method))]
                    print(f"Available WebSocket methods: {available_methods}")
                    
                    # Message au format standard Milo
                    message = {
                        'category': 'settings',
                        'type': 'language_changed',
                        'data': {'language': new_language}
                    }
                    
                    success = False
                    
                    # Utiliser broadcast_dict spécifique à l'architecture Milo
                    if hasattr(ws_manager, 'broadcast_dict'):
                        await ws_manager.broadcast_dict(message)
                        print(f"✅ Language broadcasted via broadcast_dict: {new_language}")
                        success = True
                    # Autres méthodes en fallback
                    elif hasattr(ws_manager, 'broadcast_message'):
                        await ws_manager.broadcast_message(message)
                        print(f"✅ Language broadcasted via broadcast_message: {new_language}")
                        success = True
                    elif hasattr(ws_manager, 'broadcast'):
                        await ws_manager.broadcast(message)
                        print(f"✅ Language broadcasted via broadcast: {new_language}")
                        success = True
                    else:
                        print("❌ No compatible WebSocket broadcast method found")
                        print(f"Available methods: {available_methods}")
                    
                    if not success:
                        print("❌ WebSocket broadcast failed - language change saved but not broadcasted")
                
                except Exception as ws_error:
                    print(f"❌ WebSocket broadcast error: {ws_error}")
            else:
                print("❌ WebSocket manager not available")
            
            print(f"Language successfully changed to: {new_language}")
            
            return {
                "status": "success",
                "message": f"Language changed to {new_language}",
                "language": new_language
            }
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"Error setting language: {e}")
            raise HTTPException(status_code=500, detail=f"Error setting language: {str(e)}")
    
    return router