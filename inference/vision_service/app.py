from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, ValidationError
from transformers import AutoModelForImageTextToText, AutoProcessor

app = FastAPI(title="PNG Local Vision Service", version="1.0.0")
_inference_gate = asyncio.Semaphore(max(int(os.getenv("VISION_CONCURRENCY", "1")), 1))
_max_image_bytes = int(os.getenv("VISION_MAX_IMAGE_BYTES", str(20 * 1024 * 1024)))


class Finding(BaseModel):
    title: str
    category: str
    severity: str
    description: str
    evidence: str
    confidence: float = Field(ge=0, le=1)


class FindingList(BaseModel):
    findings: list[Finding]


@lru_cache(maxsize=1)
def get_runtime():
    model_id = os.getenv("VISION_MODEL", "openbmb/MiniCPM-V-4.6")
    processor = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForImageTextToText.from_pretrained(
        model_id,
        torch_dtype="auto",
        device_map="auto",
    )
    return processor, model


def _json_object(text: str) -> dict:
    candidate = text.strip()
    if candidate.startswith("```"):
        first_newline = candidate.find("\n")
        candidate = candidate[first_newline + 1 : candidate.rfind("```")].strip()
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model response does not contain a JSON object")
    value = json.loads(candidate[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model response is not a JSON object")
    return value


def analyze_image(path: Path, context: str) -> dict:
    processor, model = get_runtime()
    prompt = (
        "你是油气站场巡检图片辅助分析模型。只描述图片中直接可见的安全隐患候选，"
        "不得推断不可见的泄漏、压力或设备内部状态。输出一个 JSON 对象，格式为："
        '{"findings":[{"title":"", "category":"", "severity":"low|medium|high|critical", '
        '"description":"", "evidence":"", "confidence":0.0}]}。'
        f"现场上下文：{context or '未提供'}"
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "url": path.resolve().as_uri()},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    inputs = processor.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        return_dict=True,
        return_tensors="pt",
        downsample_mode=os.getenv("VISION_DOWNSAMPLE_MODE", "4x"),
        max_slice_nums=int(os.getenv("VISION_MAX_SLICE_NUMS", "16")),
    ).to(model.device)
    generated = model.generate(
        **inputs,
        downsample_mode=os.getenv("VISION_DOWNSAMPLE_MODE", "4x"),
        max_new_tokens=int(os.getenv("VISION_MAX_NEW_TOKENS", "1024")),
        do_sample=False,
    )
    trimmed = [
        output_ids[len(input_ids) :]
        for input_ids, output_ids in zip(inputs.input_ids, generated, strict=True)
    ]
    text = processor.batch_decode(
        trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )[0]
    return FindingList.model_validate(_json_object(text)).model_dump()


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/vision/analyze")
async def analyze(
    file: UploadFile,
    model: str = Form(default="openbmb/MiniCPM-V-4.6"),
    context: str = Form(default=""),
) -> dict:
    configured_model = os.getenv("VISION_MODEL", "openbmb/MiniCPM-V-4.6")
    if model != configured_model:
        raise HTTPException(status_code=409, detail=f"service is loaded with {configured_model}")
    suffix = Path(file.filename or "image.bin").suffix[:16]
    temporary = tempfile.NamedTemporaryFile(prefix="pipechina-vision-", suffix=suffix, delete=False)
    path = Path(temporary.name)
    try:
        await asyncio.to_thread(shutil.copyfileobj, file.file, temporary)
        await asyncio.to_thread(temporary.close)
        file_size = await asyncio.to_thread(lambda: path.stat().st_size)
        if file_size <= 0 or file_size > _max_image_bytes:
            raise HTTPException(status_code=413, detail="image size is outside the allowed range")
        async with _inference_gate:
            return await asyncio.to_thread(analyze_image, path, context)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=502,
            detail="vision output failed schema validation",
        ) from exc
    finally:
        if not temporary.closed:
            await asyncio.to_thread(temporary.close)
        await asyncio.to_thread(path.unlink, missing_ok=True)
        await file.close()
