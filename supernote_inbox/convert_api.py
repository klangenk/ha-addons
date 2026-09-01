#!/usr/bin/env python3
"""Read-only conversion service for Supernote .note files.

POST a notebook, get its pages back as JSON. Nothing else: no polling, no
credentials, no state. n8n already owns "which files are new" for the capture
pipeline, and answering that question in a second place would mean two
systems disagreeing about it.

Text comes from the device's own handwriting recognition, embedded in the
.note file by firmware. No OCR and no model runs here, which is what keeps
this on the Pi instead of on a GPU machine. Notebooks must be created as
"Real-Time Recognition" notes; ordinary notes carry no text and come back as
sketch images only.

    GET  /health
    POST /convert?images=true          body: raw .note bytes

Both need `Authorization: Bearer <api_token>`.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import secrets
import tempfile
from pathlib import Path

import supernotelib as sn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

MAX_UPLOAD_BYTES = 256 * 1024 * 1024  # a very long notebook, with room to spare
OPTIONS_FILE = Path("/data/options.json")

log = logging.getLogger("supernote-convert")
app = FastAPI(title="Supernote Convert", docs_url=None, redoc_url=None)


def read_options() -> dict:
    """Add-on options, with environment overrides for running outside HA."""
    options = {"api_token": "", "port": 8099, "log_level": "info"}
    if OPTIONS_FILE.exists():
        options.update(json.loads(OPTIONS_FILE.read_text("utf-8")))
    for key, env in (("api_token", "API_TOKEN"), ("port", "PORT"), ("log_level", "LOG_LEVEL")):
        if os.environ.get(env):
            options[key] = os.environ[env]
    options["port"] = int(options["port"])
    return options


OPTIONS = read_options()
API_TOKEN = str(OPTIONS["api_token"])


def require_token(authorization: str = Header(default="")) -> None:
    expected = f"Bearer {API_TOKEN}"
    # compare_digest so a wrong token cannot be found one character at a time
    if not API_TOKEN or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "supernotelib": sn.__version__}


def page_id_of(page, index: int) -> str:
    """Stable identity for a page across edits.

    Supernote gives each page an id that survives editing and reordering,
    which is what lets a caller tell "new page" from "same page, more written
    on it". Fall back to the index if a firmware stops providing one - then
    page identity degrades to position, which is the best available guess.
    """
    try:
        pageid = page.get_pageid()
    except Exception:  # noqa: BLE001 - defensive against format changes
        pageid = None
    return str(pageid) if pageid else f"index-{index}"


def ink_digest(page) -> str:
    """Hash of the raw stroke data - changes only when the page was drawn on."""
    digest = hashlib.sha1()
    chunks = 0
    try:
        layers = page.get_layers() if page.is_layer_supported() else []
    except Exception:  # noqa: BLE001
        layers = []
    for layer in layers or []:
        getter = getattr(layer, "get_content", None)
        content = getter() if callable(getter) else None
        if content:
            digest.update(content)
            chunks += 1
    content = page.get_content()
    if content:
        digest.update(content)
        chunks += 1
    return digest.hexdigest()[:16] if chunks else ""


@app.post("/convert", dependencies=[Depends(require_token)])
async def convert(
    request: Request,
    images: bool = Query(default=True, description="render pages without text as PNG"),
) -> JSONResponse:
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="empty body, expected .note bytes")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="notebook too large")

    handle = tempfile.NamedTemporaryFile(suffix=".note", delete=False)
    try:
        handle.write(raw)
        handle.close()
        # loose: a firmware bump changes the file signature, and refusing to
        # read the notebook at all is a worse outcome than a best-effort parse
        notebook = sn.load_notebook(handle.name, policy="loose")
    except Exception as exc:  # noqa: BLE001
        log.exception("could not parse notebook (%d bytes)", len(raw))
        raise HTTPException(status_code=422, detail=f"not a readable .note file: {exc}") from exc
    finally:
        handle.close()
        Path(handle.name).unlink(missing_ok=True)

    realtime = notebook.is_realtime_recognition()
    if not realtime:
        log.warning("notebook has no real-time recognition; text extraction will be empty")

    text_converter = sn.converter.TextConverter(notebook)
    image_converter = None
    pages = []

    for index in range(notebook.get_total_pages()):
        page = notebook.get_page(index)
        entry = {"page": index + 1, "page_id": page_id_of(page, index)}

        try:
            text = text_converter.convert(index)
        except Exception:  # noqa: BLE001 - one bad page must not fail the notebook
            log.exception("page %d: recognised text could not be read", index + 1)
            text = None

        if text and text.strip():
            text = text.strip()
            entry.update(
                kind="text",
                digest=hashlib.sha1(text.encode("utf-8")).hexdigest()[:16],
                text=text,
            )
            pages.append(entry)
            continue

        # No recognised text: a sketch, a diagram, or handwriting the device
        # could not read. Hand over the pixels and let the caller decide.
        digest = ink_digest(page)
        if not digest:
            entry.update(kind="empty", digest="")
            pages.append(entry)
            continue
        entry.update(kind="sketch", digest=digest)
        if images:
            if image_converter is None:
                image_converter = sn.converter.ImageConverter(notebook)
            buffer = io.BytesIO()
            image_converter.convert(index).save(buffer, format="PNG")
            entry["png_base64"] = base64.b64encode(buffer.getvalue()).decode("ascii")
        pages.append(entry)

    log.info(
        "converted %d page(s): %d text, %d sketch",
        len(pages),
        sum(1 for p in pages if p["kind"] == "text"),
        sum(1 for p in pages if p["kind"] == "sketch"),
    )
    return JSONResponse(
        {
            "realtime_recognition": realtime,
            "total_pages": notebook.get_total_pages(),
            "pages": pages,
        }
    )


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=getattr(logging, str(OPTIONS["log_level"]).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not API_TOKEN:
        raise SystemExit("api_token is not set - refusing to start without authentication")
    log.info("listening on port %d", OPTIONS["port"])
    uvicorn.run(app, host="0.0.0.0", port=OPTIONS["port"], log_level="warning")


if __name__ == "__main__":
    main()
