"""
Scryfall Card Service.

Provides card name lookup and metadata from Scryfall's bulk data.
Downloads and indexes bulk JSON for efficient local lookups.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Set

import requests

logger = logging.getLogger(__name__)


class ScryfallBulkService:
    """
    Service for looking up card information from Scryfall bulk data.

    Downloads bulk JSON once and builds a local index for fast lookups.
    """

    BULK_DATA_URL = "https://api.scryfall.com/bulk-data"

    def __init__(self, cache_dir: Optional[str] = None):
        """
        Initialize the Scryfall service.

        Args:
            cache_dir: Directory to store cached data. If None, uses default location.
        """
        if cache_dir is None:
            project_root = Path(__file__).parent.parent.parent
            cache_dir = project_root / "data" / "cache"

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._arena_id_index: Dict[int, Dict] = {}
        self._index_loaded = False
        self._bulk_file_path = self.cache_dir / "scryfall_default_cards.json"
        self._index_file_path = self.cache_dir / "arena_id_index.json"

    def ensure_bulk_data(self, force_download: bool = False) -> bool:
        """
        Ensure bulk data is downloaded and indexed.

        Args:
            force_download: Force re-download even if file exists

        Returns:
            True if data is ready, False otherwise
        """
        # Check if we already have the index loaded
        if self._index_loaded and not force_download:
            return True

        # Try to load existing index
        if self._load_index() and not force_download:
            return True

        # Download bulk data if needed
        if not self._bulk_file_path.exists() or force_download:
            if not self._download_bulk_data():
                return False

        # Build index from bulk data
        return self._build_index()

    def _download_bulk_data(self) -> bool:
        """
        Download Scryfall bulk data file.

        Returns:
            True if download successful, False otherwise

        Note:
            Logs errors but doesn't raise exceptions to allow graceful degradation.
        """
        try:
            logger.info("Fetching Scryfall bulk data info...")
            response = requests.get(self.BULK_DATA_URL, timeout=30)
            response.raise_for_status()
            bulk_info = response.json()

            # Find default_cards data
            download_url = None
            for item in bulk_info.get("data", []):
                if item.get("type") == "default_cards":
                    download_url = item.get("download_uri")
                    break

            if not download_url:
                logger.error("Could not find default_cards bulk data in Scryfall response")
                return False

            logger.info(f"Downloading bulk data from {download_url}...")
            logger.info("This may take a few minutes (~350MB)...")

            # Download with progress and timeout handling
            try:
                with requests.get(download_url, stream=True, timeout=600) as r:
                    r.raise_for_status()
                    total_size = int(r.headers.get("content-length", 0))
                    downloaded = 0

                    with open(self._bulk_file_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=8192 * 16):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                pct = downloaded / total_size * 100
                                if downloaded % (8192 * 1000) == 0:
                                    logger.info(
                                        f"Downloaded {downloaded / 1024 / 1024:.1f}MB ({pct:.1f}%)"
                                    )
            except requests.Timeout:
                logger.error("Download timed out - Scryfall server may be slow")
                # Clean up partial download
                if self._bulk_file_path.exists():
                    self._bulk_file_path.unlink()
                return False

            # Verify download completed
            if self._bulk_file_path.exists():
                size_mb = self._bulk_file_path.stat().st_size / 1024 / 1024
                if size_mb < 100:  # Bulk data should be > 100MB
                    logger.error(f"Downloaded file seems too small ({size_mb:.1f}MB)")
                    return False
                logger.info(f"Downloaded to {self._bulk_file_path} ({size_mb:.1f}MB)")
                return True
            return False

        except requests.RequestException as e:
            logger.error(f"Failed to download bulk data: {e}")
            return False
        except IOError as e:
            logger.error(f"Failed to write bulk data file: {e}")
            return False

    def _build_index(self) -> bool:
        """Build Arena ID index from bulk data."""
        try:
            logger.info(f"Building Arena ID index from {self._bulk_file_path}...")

            with open(self._bulk_file_path, "r", encoding="utf-8") as f:
                cards = json.load(f)

            self._arena_id_index = {}
            count = 0

            for card in cards:
                arena_id = card.get("arena_id")
                if arena_id:
                    self._arena_id_index[arena_id] = self._simplify_card_data(card)
                    count += 1

            logger.info(f"Indexed {count} Arena cards")

            # Save index for faster future loads
            self._save_index()
            self._index_loaded = True
            return True

        except Exception as e:
            logger.error(f"Failed to build index: {e}")
            return False

    def _save_index(self):
        """Save index to disk for faster future loads."""
        try:
            logger.info("Saving Arena ID index...")
            with open(self._index_file_path, "w") as f:
                json.dump(self._arena_id_index, f)
            logger.info(f"Index saved to {self._index_file_path}")
        except Exception as e:
            logger.warning(f"Failed to save index: {e}")

    def _load_index(self) -> bool:
        """Load index from disk if available."""
        if not self._index_file_path.exists():
            return False

        try:
            logger.info("Loading Arena ID index from cache...")
            with open(self._index_file_path, "r") as f:
                data = json.load(f)

            # Convert string keys back to int
            self._arena_id_index = {int(k): v for k, v in data.items()}
            self._index_loaded = True
            logger.info(f"Loaded {len(self._arena_id_index)} cards from index")
            return True

        except Exception as e:
            logger.warning(f"Failed to load index: {e}")
            return False

    def _simplify_card_data(self, card: Dict) -> Dict[str, Any]:
        """Simplify Scryfall card data to essential fields."""
        # Handle double-faced cards
        if "card_faces" in card and len(card["card_faces"]) > 0:
            front_face = card["card_faces"][0]
            mana_cost = front_face.get("mana_cost", card.get("mana_cost", ""))
            type_line = front_face.get("type_line", card.get("type_line", ""))
            oracle_text = front_face.get("oracle_text", card.get("oracle_text", ""))
            power = front_face.get("power")
            toughness = front_face.get("toughness")
            image_uri = front_face.get("image_uris", {}).get("normal")
        else:
            mana_cost = card.get("mana_cost", "")
            type_line = card.get("type_line", "")
            oracle_text = card.get("oracle_text", "")
            power = card.get("power")
            toughness = card.get("toughness")
            image_uri = card.get("image_uris", {}).get("normal")

        return {
            "name": card.get("name"),
            "mana_cost": mana_cost,
            "cmc": card.get("cmc", 0),
            "type_line": type_line,
            "colors": card.get("colors", []),
            "color_identity": card.get("color_identity", []),
            "set_code": card.get("set"),
            "rarity": card.get("rarity"),
            "oracle_text": oracle_text,
            "power": power,
            "toughness": toughness,
            "scryfall_id": card.get("id"),
            "arena_id": card.get("arena_id"),
            "image_uri": image_uri,
        }

    def get_card_by_arena_id(self, arena_id: int) -> Optional[Dict[str, Any]]:
        """
        Look up a card by its MTG Arena ID (grpId).

        Args:
            arena_id: The Arena card ID

        Returns:
            Card data dictionary or None if not found
        """
        if not self._index_loaded:
            self.ensure_bulk_data()

        return self._arena_id_index.get(arena_id)

    def lookup_cards_batch(self, arena_ids: Set[int]) -> Dict[int, Optional[Dict]]:
        """
        Look up multiple cards by Arena ID.

        Args:
            arena_ids: Set of Arena IDs to look up

        Returns:
            Dictionary mapping Arena ID to card data
        """
        if not self._index_loaded:
            self.ensure_bulk_data()

        return {aid: self._arena_id_index.get(aid) for aid in arena_ids}

    def get_all_arena_ids(self) -> Set[int]:
        """Get all known Arena IDs."""
        if not self._index_loaded:
            self.ensure_bulk_data()
        return set(self._arena_id_index.keys())

    def stats(self) -> Dict[str, Any]:
        """Get statistics about the card database."""
        if not self._index_loaded:
            self.ensure_bulk_data()

        return {
            "total_cards": len(self._arena_id_index),
            "index_loaded": self._index_loaded,
            "bulk_file_exists": self._bulk_file_path.exists(),
            "bulk_file_size_mb": (
                self._bulk_file_path.stat().st_size / 1024 / 1024
                if self._bulk_file_path.exists()
                else 0
            ),
        }

    def download_card_image(self, card_grp_id: int) -> Optional[Path]:
        """
        Download and cache a card image from Scryfall.

        Args:
            card_grp_id: Arena card group ID

        Returns:
            Path to cached image file, or None if download failed
        """
        card_data = self.get_card_by_arena_id(card_grp_id)
        if not card_data or not card_data.get("image_uri"):
            logger.warning(f"No image URI found for card {card_grp_id}")
            return None

        # Create images cache directory
        images_dir = self.cache_dir / "card_images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # Use grp_id as filename
        image_path = images_dir / f"{card_grp_id}.jpg"

        # Return cached image if exists
        if image_path.exists():
            return image_path

        # Download image
        try:
            image_uri = card_data["image_uri"]
            response = requests.get(image_uri, timeout=10)
            response.raise_for_status()

            # Save to cache
            with open(image_path, "wb") as f:
                f.write(response.content)

            logger.info(f"Downloaded image for card {card_grp_id}: {card_data.get('name')}")
            return image_path

        except Exception as e:
            logger.error(f"Failed to download image for card {card_grp_id}: {e}")
            return None

    def get_cached_image_path(self, card_grp_id: int) -> Optional[Path]:
        """
        Get path to cached image if it exists.

        Args:
            card_grp_id: Arena card group ID

        Returns:
            Path to cached image, or None if not cached
        """
        image_path = self.cache_dir / "card_images" / f"{card_grp_id}.jpg"
        return image_path if image_path.exists() else None


# Singleton instance
_scryfall_service: Optional[ScryfallBulkService] = None


def get_scryfall() -> ScryfallBulkService:
    """Get the global Scryfall service instance."""
    global _scryfall_service
    if _scryfall_service is None:
        _scryfall_service = ScryfallBulkService()
    return _scryfall_service
