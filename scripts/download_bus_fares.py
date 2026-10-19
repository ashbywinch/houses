"""BODS NeTEx fare data downloading and checkpoint management."""

from __future__ import annotations

import io
import logging
import re
import zipfile
from collections.abc import Generator
from pathlib import Path
from typing import Any, TypedDict

import httpx

logger = logging.getLogger(__name__)

class _BodsFares:
    """The BODS fares API endpoints: the dataset listing and the download host."""

    BODS_BASE = "https://data.bus-data.dft.gov.uk/api/v1/"
    DOWNLOAD_BASE = "https://data.bus-data.dft.gov.uk"
    CACHE_DIR = Path("data/bods_cache")
    CHECKPOINT_DIR = Path("data/.bus_fares_checkpoints")


type BodsDataset = dict[str, Any]
"""One dataset listing from the BODS fares API: the API's own JSON payload,
from which this repo reads ``id`` and ``description``."""


type BodsDatasets = list[BodsDataset]
"""The dataset listings one BODS fares query returns."""



def _dataset_cache_path(dataset_id: int, filename: str) -> Path:
    name = filename.removesuffix(".xml").replace(" ", "_").replace("(", "").replace(")", "")
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", name)[:200]
    return _BodsFares.CACHE_DIR / f"dataset_{dataset_id}_{safe}.xml"


class _DatasetListParams(TypedDict):
    """The BODS fares dataset-list query parameters."""

    noc: str
    limit: int
    api_key: str


def get_bods_datasets(noc: str, api_key: str) -> BodsDatasets:
    url = f"{_BodsFares.BODS_BASE}fares/dataset/"
    params = _DatasetListParams(noc=noc, limit=50, api_key=api_key)

    resp = httpx.get(url, params=httpx.QueryParams(**params), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    results: BodsDatasets = data.get("results", [])
    logger.info("NOC %s: %d fare datasets found", noc, len(results))
    return results


def _cached_dataset_xml(dataset_id: int) -> Generator[str, None, None]:
    """Yield the cached XML texts for a dataset; warns when the cache is empty."""
    cached_paths = sorted(_BodsFares.CACHE_DIR.glob(f"dataset_{dataset_id}.xml"))
    cached_paths.extend(sorted(_BodsFares.CACHE_DIR.glob(f"dataset_{dataset_id}_*.xml")))
    if not cached_paths:
        logger.warning("No cached files found for dataset %d", dataset_id)
        return
    logger.info("Reading %d cached files for dataset %d", len(cached_paths), dataset_id)
    for path in cached_paths:
        yield path.read_text(encoding="utf-8")


def download_dataset(dataset_id: int, api_key: str, cached_only: bool = False) -> Generator[str, None, None]:
    _BodsFares.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if cached_only:
        yield from _cached_dataset_xml(dataset_id)
        return

    url = f"{_BodsFares.DOWNLOAD_BASE}/fares/dataset/{dataset_id}/download/"
    headers: dict[str, str] = {"Authorization": f"Token {api_key}"}

    resp = httpx.get(url, headers=headers, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    logger.info("Downloaded dataset %d (%d bytes)", dataset_id, len(resp.content))

    content = resp.content
    content_type = resp.headers.get("content-type", "")

    if "zip" in content_type or content[:2] == b"PK":

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            xml_names = sorted([n for n in zf.namelist() if n.endswith(".xml")])
            if not xml_names:
                logger.warning("No XML files found in zip for dataset %d", dataset_id)
                return
            logger.info("Zip contains %d XML files for dataset %d", len(xml_names), dataset_id)
            for xml_name in xml_names:
                xml_str = zf.read(xml_name).decode("utf-8")
                cache_path = _dataset_cache_path(dataset_id, Path(xml_name).stem)
                if not cache_path.is_file():
                    cache_path.write_text(xml_str, encoding="utf-8")
                yield xml_str
    else:
        yield resp.text


def checkpoint_path(display_name: str) -> Path:
    safe_name = display_name.replace(" ", "_").replace("/", "_")
    return _BodsFares.CHECKPOINT_DIR / f"{safe_name}.json"
