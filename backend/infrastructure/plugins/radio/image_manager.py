"""
Manager d'images pour les stations radio personnalisées
"""
import os
import io
import uuid
import logging
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image
import aiofiles


class ImageManager:
    """
    Manages le stockage, validation et nettoyage des images de stations radio
    """

    # Répertoire de stockage des images
    IMAGES_DIR = Path("/var/lib/milo/radio_images")

    # Formats d'images acceptés (SVG non supporté - Pillow ne le gère pas nativement)
    ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    # Limites
    MAX_FILE_SIZE_MB = 5
    MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
    MAX_DIMENSIONS = (1500, 1500)  # Résolution max augmentée à 1500x1500

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        """Creates directory d'images s'il n'existe pas"""
        try:
            self.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            self.logger.debug(f"Images directory ready: {self.IMAGES_DIR}")
        except Exception as e:
            self.logger.error(f"Error creating images directory: {e}")

    async def validate_and_save_image(
        self,
        file_content: bytes,
        filename: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Validates and saves an image

        Args:
            file_content: Contenu binaire du fichier
            filename: Nom original du fichier

        Returns:
            Tuple (success, saved_filename, error_message)
            - success: True si sauvegarde réussie
            - saved_filename: Nom du fichier sauvegardé (ex: "abc123.jpg")
            - error_message: Message d'erreur si échec
        """
        try:
            # 1. Verify la taille du fichier
            file_size = len(file_content)
            if file_size > self.MAX_FILE_SIZE_BYTES:
                return False, None, f"Image trop volumineuse ({file_size / 1024 / 1024:.1f}MB). Maximum: {self.MAX_FILE_SIZE_MB}MB"

            if file_size == 0:
                return False, None, "Fichier vide"

            # 2. Verify l'extension du fichier
            original_ext = Path(filename).suffix.lower()
            if original_ext not in self.ALLOWED_EXTENSIONS:
                return False, None, f"Format non supporté. Formats acceptés: {', '.join(self.ALLOWED_EXTENSIONS)}"

            # 3. Open and validate l'image avec PIL
            try:
                image = Image.open(io.BytesIO(file_content))
                image.verify()  # Vérifie que c'est une vraie image

                # Rouvrir après verify() (verify() ferme l'image)
                image = Image.open(io.BytesIO(file_content))

                # Verify le format
                if image.format not in self.ALLOWED_FORMATS:
                    return False, None, f"Format d'image non supporté: {image.format}"

                # Verify les dimensions
                width, height = image.size
                if width > self.MAX_DIMENSIONS[0] or height > self.MAX_DIMENSIONS[1]:
                    return False, None, f"Image trop grande ({width}x{height}). Maximum: {self.MAX_DIMENSIONS[0]}x{self.MAX_DIMENSIONS[1]}px"

                if width < 50 or height < 50:
                    return False, None, f"Image trop petite ({width}x{height}). Minimum: 50x50px"

            except Exception as e:
                self.logger.warning(f"Image validation failed: {e}")
                return False, None, "Fichier invalide ou corrompu"

            # 4. Generate name de fichier unique
            unique_id = uuid.uuid4().hex[:12]
            saved_filename = f"{unique_id}{original_ext}"
            file_path = self.IMAGES_DIR / saved_filename

            # 5. Save le fichier
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(file_content)

            self.logger.info(f"Image saved: {saved_filename} ({width}x{height}, {file_size / 1024:.1f}KB)")
            return True, saved_filename, None

        except Exception as e:
            self.logger.error(f"Error saving image: {e}")
            return False, None, f"Error lors de la sauvegarde: {str(e)}"

    async def delete_image(self, filename: str) -> bool:
        """
        Deletes an image du stockage

        Args:
            filename: Nom du fichier à supprimer (ex: "abc123.jpg")

        Returns:
            True si suppression réussie
        """
        if not filename:
            return False

        try:
            file_path = self.IMAGES_DIR / filename

            # Verify que le fichier est bien dans notre répertoire (sécurité)
            if not file_path.resolve().is_relative_to(self.IMAGES_DIR.resolve()):
                self.logger.warning(f"Attempted path traversal: {filename}")
                return False

            if file_path.exists():
                file_path.unlink()
                self.logger.info(f"Image deleted: {filename}")
                return True
            else:
                self.logger.warning(f"Image not found for deletion: {filename}")
                return False

        except Exception as e:
            self.logger.error(f"Error deleting image {filename}: {e}")
            return False

    def get_image_path(self, filename: str) -> Optional[Path]:
        """
        Gets path complet d'une image

        Args:
            filename: Nom du fichier

        Returns:
            Path complet ou None si fichier invalide
        """
        if not filename:
            return None

        try:
            file_path = self.IMAGES_DIR / filename

            # Verify sécurité
            if not file_path.resolve().is_relative_to(self.IMAGES_DIR.resolve()):
                self.logger.warning(f"Attempted path traversal: {filename}")
                return None

            if file_path.exists():
                return file_path
            return None

        except Exception as e:
            self.logger.error(f"Error getting image path {filename}: {e}")
            return None

    async def cleanup_orphaned_images(self, used_filenames: list[str]) -> int:
        """
        Cleans up orphaned images (sans station associée)

        Args:
            used_filenames: Liste des noms de fichiers actuellement utilisés

        Returns:
            Nombre de fichiers supprimés
        """
        try:
            used_set = set(used_filenames)
            deleted_count = 0

            for file_path in self.IMAGES_DIR.iterdir():
                if file_path.is_file() and file_path.name not in used_set:
                    file_path.unlink()
                    deleted_count += 1
                    self.logger.info(f"Orphaned image deleted: {file_path.name}")

            if deleted_count > 0:
                self.logger.info(f"Cleaned up {deleted_count} orphaned images")

            return deleted_count

        except Exception as e:
            self.logger.error(f"Error during orphaned images cleanup: {e}")
            return 0
