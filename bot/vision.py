"""Extract Tech360 job fields from screenshots via OpenAI Vision."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

from bot.config import OPENAI_API_KEY, OPENAI_VISION_MODEL

logger = logging.getLogger("itg.vision")

NO_API_KEY_MSG = "no_api_key"
RATE_LIMIT_MSG = "rate_limit"

EXTRACTION_PROMPT = """You extract data from Xfinity/Comcast Tech360 mobile app screenshots for a field technician.

Return ONLY valid JSON (no markdown) with this schema:
{
  "job_number": "string or null",
  "customer_name": "string or null",
  "address": "string or null",
  "account_number": "string or null",
  "work_type": "Trouble Call | Service Change | New Install | Special Request | Self Install | null",
  "subtype_codes": ["array of codes like HSD OUT, VID OUT, 01:TV ALL OUT, TECH RECOVERY, VID UP, HSD NC, HSD RC"],
  "hookup_type": "Aerial | Underground | null",
  "dwelling_type": "string or null"
}

Rules:
- job_number is from header "Job# ######"
- work_type is the large heading near bottom (Trouble Call, Service Change, New Install, etc.)
- subtype_codes are lines under work_type (HSD OUT, VID OUT, 01:TV ALL OUT, TECH RECOVERY, HSD NC, HSD RC, VID UP, etc.)
- address is the full street + city + state + zip when visible on screen
- account_number is the long number under Account #
- If multiple screenshots belong to same job, merge fields — prefer non-null values
"""


def _compress_image(path: Path, max_side: int = 1600) -> bytes:
    try:
        from PIL import Image

        with Image.open(path) as img:
            img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > max_side:
                scale = max_side / max(w, h)
                img = img.resize((int(w * scale), int(h * scale)))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            return buf.getvalue()
    except Exception:
        return path.read_bytes()

def _merge_extractions(items: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "job_number": None,
        "customer_name": None,
        "address": None,
        "account_number": None,
        "work_type": None,
        "subtype_codes": [],
        "hookup_type": None,
        "dwelling_type": None,
    }
    subtypes: set[str] = set()

    for item in items:
        for key in ("job_number", "customer_name", "address", "account_number", "work_type", "hookup_type", "dwelling_type"):
            if item.get(key) and not merged.get(key):
                merged[key] = item[key]
        for code in item.get("subtype_codes") or []:
            if code:
                subtypes.add(str(code).strip())

    merged["subtype_codes"] = sorted(subtypes)
    return merged


def _parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


async def extract_from_images(image_paths: list[Path]) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError(NO_API_KEY_MSG)

    from openai import AsyncOpenAI, RateLimitError

    client = AsyncOpenAI(api_key=OPENAI_API_KEY, max_retries=0)
    content: list[dict[str, Any]] = [{"type": "text", "text": EXTRACTION_PROMPT}]

    for path in image_paths:
        data = base64.b64encode(_compress_image(path)).decode("utf-8")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{data}"},
            }
        )

    last_error: Exception | None = None
    for attempt in range(5):
        try:
            response = await client.chat.completions.create(
                model=OPENAI_VISION_MODEL,
                messages=[{"role": "user", "content": content}],
                max_tokens=800,
                temperature=0,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content or "{}"
            parsed = _parse_json_response(raw)
            if len(image_paths) > 1:
                return _merge_extractions([parsed])
            return parsed
        except RateLimitError as exc:
            last_error = exc
            wait = 2 ** attempt
            logger.warning("OpenAI rate limit, retry %s in %ss", attempt + 1, wait)
            await asyncio.sleep(wait)
        except Exception as exc:
            if "429" in str(exc):
                last_error = exc
                wait = 2 ** attempt
                logger.warning("OpenAI 429, retry %s in %ss", attempt + 1, wait)
                await asyncio.sleep(wait)
            else:
                raise

    raise RuntimeError(RATE_LIMIT_MSG) from last_error


def empty_extraction() -> dict[str, Any]:
    return {
        "job_number": None,
        "customer_name": None,
        "address": None,
        "account_number": None,
        "work_type": None,
        "subtype_codes": [],
        "hookup_type": None,
        "dwelling_type": None,
    }
