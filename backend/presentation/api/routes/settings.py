# backend/presentation/api/routes/settings.py - Version corrigée avec codes standardisés
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal
import json
import os

# Modèle pour la requête de changement de langue - CODES STANDARDISÉS
class LanguageRequest(BaseModel):
    language: Literal['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese']

# Fichier de persistance de la langue
LANGUAGE_CONFIG_FILE = os.path.expanduser('~/milo_language.json')

def load_current_language():
    """Charger la langue actuelle depuis le fichier de config"""
    try:
        if os.path.exists(LANGUAGE_CONFIG_FILE):
            with open(LANGUAGE_CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                language = config.get('language', 'french')
                print(f"Loaded language from file: {language}")
                return language
    except Exception as e:
        print(f"Error loading language config: {e}")
    
    print("Using default language: french")
    return 'french'  # Langue par défaut

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
            
            # Diffuser via WebSocket
            if ws_manager and hasattr(ws_manager, 'broadcast_dict'):
                message = {
                    'category': 'settings',
                    'type': 'language_changed',
                    'data': {'language': new_language}
                }
                await ws_manager.broadcast_dict(message)
                print(f"✅ Language broadcasted: {new_language}")
            
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