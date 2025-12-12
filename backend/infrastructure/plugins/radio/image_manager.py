"""
Image manager for custom radio stations
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
    Manages storage, validation and cleanup of radio station images
    """

    # Image storage directory
    IMAGES_DIR = Path("/var/lib/milo/radio_images")

    # Accepted image formats (SVG not supported - Pillow doesn't handle it natively)
    ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    # Limits
    MAX_FILE_SIZE_MB = 5
    MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
    MAX_DIMENSIONS = (1024, 1024)  # Max resolution for saved images
    WEBP_QUALITY = 80  # WebP compression quality

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        """Creates images directory if it doesn't exist"""
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
            file_content: Binary file content
            filename: Original file name

        Returns:
            Tuple (success, saved_filename, error_message)
            - success: True if save successful
            - saved_filename: Saved file name (e.g.: "abc123.jpg")
            - error_message: Error message if failed
        """
        try:
            # 1. Verify file size
            file_size = len(file_content)
            if file_size > self.MAX_FILE_SIZE_BYTES:
                return False, None, f"Image too large ({file_size / 1024 / 1024:.1f}MB). Maximum: {self.MAX_FILE_SIZE_MB}MB"

            if file_size == 0:
                return False, None, "Empty file"

            # 2. Verify file extension
            original_ext = Path(filename).suffix.lower()
            if original_ext not in self.ALLOWED_EXTENSIONS:
                return False, None, f"Unsupported format. Accepted formats: {', '.join(self.ALLOWED_EXTENSIONS)}"

            # 3. Open and validate image with PIL
            try:
                image = Image.open(io.BytesIO(file_content))
                image.verify()  # Verify it's a real image

                # Reopen after verify() (verify() closes the image)
                image = Image.open(io.BytesIO(file_content))

                # Verify format
                if image.format not in self.ALLOWED_FORMATS:
                    return False, None, f"Unsupported image format: {image.format}"

                # Verify minimum dimensions
                width, height = image.size
                if width < 50 or height < 50:
                    return False, None, f"Image too small ({width}x{height}). Minimum: 50x50px"

            except Exception as e:
                self.logger.warning(f"Image validation failed: {e}")
                return False, None, "Invalid or corrupted file"

            # 4. Process image: resize if needed and convert to WebP
            try:
                # Resize if larger than max dimensions (preserves aspect ratio)
                if width > self.MAX_DIMENSIONS[0] or height > self.MAX_DIMENSIONS[1]:
                    image.thumbnail(self.MAX_DIMENSIONS, Image.Resampling.LANCZOS)
                    self.logger.debug(f"Image resized from {width}x{height} to {image.size[0]}x{image.size[1]}")

                # Convert to WebP, preserving transparency if present
                output_buffer = io.BytesIO()
                if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
                    # Preserve transparency
                    image = image.convert('RGBA')
                    image.save(output_buffer, format='WEBP', quality=self.WEBP_QUALITY, lossless=False)
                else:
                    # No transparency, convert to RGB
                    image = image.convert('RGB')
                    image.save(output_buffer, format='WEBP', quality=self.WEBP_QUALITY)

                webp_content = output_buffer.getvalue()
                final_width, final_height = image.size

            except Exception as e:
                self.logger.error(f"Image processing failed: {e}")
                return False, None, "Error processing image"

            # 5. Generate unique file name (always .webp)
            unique_id = uuid.uuid4().hex[:12]
            saved_filename = f"{unique_id}.webp"
            file_path = self.IMAGES_DIR / saved_filename

            # 6. Save the WebP file
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(webp_content)

            self.logger.info(f"Image saved: {saved_filename} ({final_width}x{final_height}, {len(webp_content) / 1024:.1f}KB)")
            return True, saved_filename, None

        except Exception as e:
            self.logger.error(f"Error saving image: {e}")
            return False, None, f"Error saving file: {str(e)}"

    async def delete_image(self, filename: str) -> bool:
        """
        Deletes an image from storage

        Args:
            filename: File name to delete (e.g.: "abc123.jpg")

        Returns:
            True if deletion successful
        """
        if not filename:
            return False

        try:
            file_path = self.IMAGES_DIR / filename

            # Verify that the file is in our directory (security)
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
        Gets full path of an image

        Args:
            filename: File name

        Returns:
            Full path or None if invalid file
        """
        if not filename:
            return None

        try:
            file_path = self.IMAGES_DIR / filename

            # Security check
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
        Cleans up orphaned images (without associated station)

        Args:
            used_filenames: List of currently used file names

        Returns:
            Number of deleted files
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
