"""Языковая модель — только на входе и на выходе.

* recognize()   — фото тетради → строки (vision-модель);
* tag_comment() — свободный текст учителя → теги СТРОГО из словаря;
* personalize() — переформулировать готовые советы из словаря под работы.

Ничего не считает и не решает: ответ модели проверяется кодом (теги — из
словаря, цитаты — подстрока исходного текста, ссылки на работы — существуют).
Без ключа всё работает: ввод строк текстом, разметка по словарю основ,
советы — шаблоны.

Провайдеры: Anthropic (Claude) и любой OpenAI-совместимый API
(OpenAI, Gemini через OpenAI-совместимый адрес, OpenRouter и т. п.).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

import requests

from . import tags as T

DEFAULT_MODELS = {"anthropic": "claude-sonnet-5", "openai": "gpt-4o"}
TIMEOUT = 90


@dataclass
class LLMConfig:
    provider: str = ""
    api_key: str = ""
    model: str = ""
    base_url: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.provider and self.api_key)

    def label(self) -> str:
        return f"{self.provider} · {self.model}" if self.ready else "—"


def config_from_env(secrets: Optional[dict] = None) -> LLMConfig:
    """Ключ берём из переменных окружения или st.secrets (на Streamlit Cloud)."""
    src = dict(os.environ)
    if secrets:
        src.update({k: str(v) for k, v in secrets.items() if isinstance(v, (str, int, float))})
    provider = src.get("LLM_PROVIDER", "").lower()
    key = src.get("LLM_API_KEY", "")
    if not provider:
        if src.get("ANTHROPIC_API_KEY"):
            provider, key = "anthropic", src["ANTHROPIC_API_KEY"]
        elif src.get("OPENAI_API_KEY"):
            provider, key = "openai", src["OPENAI_API_KEY"]
    elif not key:
        key = src.get("ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY", "")
    return LLMConfig(
        provider=provider, api_key=key,
        model=src.get("LLM_MODEL") or DEFAULT_MODELS.get(provider, ""),
        base_url=src.get("LLM_BASE_URL", "https://api.openai.com/v1"),
    )


class LLMError(Exception):
    pass


def _call(cfg: LLMConfig, prompt: str, image: Optional[tuple] = None, system: str = "",
          max_tokens: int = 2000) -> str:
    if not cfg.ready:
        raise LLMError("Языковая модель не настроена")
    try:
        if cfg.provider == "anthropic":
            content = []
            if image:
                data, mime = image
                content.append({"type": "image", "source": {"type": "base64", "media_type": mime,
                                                            "data": base64.b64encode(data).decode()}})
            content.append({"type": "text", "text": prompt})
            body = {"model": cfg.model, "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": content}]}
            if system:
                body["system"] = system
            r = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=TIMEOUT,
                              headers={"x-api-key": cfg.api_key, "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"})
            if r.status_code != 200:
                raise LLMError(f"Anthropic API {r.status_code}: {r.text[:300]}")
            return "".join(b.get("text", "") for b in r.json().get("content", []))
        # OpenAI-совместимый
        content = [{"type": "text", "text": prompt}]
        if image:
            data, mime = image
            content.append({"type": "image_url", "image_url": {
                "url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}})
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": content}]
        url = cfg.base_url.rstrip("/") + "/chat/completions"
        r = requests.post(url, json={"model": cfg.model, "messages": msgs}, timeout=TIMEOUT,
                          headers={"Authorization": f"Bearer {cfg.api_key}"})
        if r.status_code != 200:
            raise LLMError(f"API {r.status_code}: {r.text[:300]}")
        return r.json()["choices"][0]["message"]["content"] or ""
    except requests.RequestException as ex:
        raise LLMError(f"Сеть: {ex}") from ex


def _json(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMError("Модель не вернула JSON")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as ex:
        raise LLMError(f"Некорректный JSON: {ex}") from ex


def prepare_image(data: bytes) -> tuple[bytes, str]:
    """Повернуть по EXIF, уменьшить до 1600 px, JPEG. Фото нигде не сохраняется."""
    try:
        from PIL import Image, ImageOps
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((1600, 1600))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001
        return data, "image/jpeg"


OCR_SYSTEM = (
    "Ты аккуратно переписываешь рукописные решения школьников по алгебре. "
    "Никогда не исправляй ошибки ученика и не решай задачу сам: нужна точная копия того, что написано."
)

OCR_PROMPT = """На фото — тетрадь ученика с домашней работой. Задачи:
{problems}

Перепиши решение каждой задачи построчно, строка тетради = строка в ответе.
Правила записи:
- степень: x² или x^2; корень: √; умножение: ·; дробь: a/b (скобки, если нужно); минус: −
- индексы корней: x₁, x₂
- слова (Ответ, Проверка, ОДЗ, или, не подходит, Жауабы, Тексеру и т. п.) сохраняй как есть
- не переписывай условие, если его нет в тетради; не добавляй своих строк; ошибки не исправляй
- если символ неразборчив, поставь «?»

Верни только JSON без пояснений:
{{"problems": [{{"number": 1, "lines": ["...", "..."]}}, {{"number": 2, "lines": ["..."]}}]}}"""


def recognize(cfg: LLMConfig, image_bytes: bytes, problems: list) -> dict:
    """problems: [(idx, statement)] → {idx: [строки]}"""
    data, mime = prepare_image(image_bytes)
    plist = "\n".join(f"№{i}. {st}" for i, st in problems)
    out = _json(_call(cfg, OCR_PROMPT.format(problems=plist), image=(data, mime), system=OCR_SYSTEM))
    res = {}
    for p in out.get("problems", []):
        try:
            idx = int(p.get("number"))
        except (TypeError, ValueError):
            continue
        lines = [str(l).strip() for l in p.get("lines", []) if str(l).strip()]
        res[idx] = lines
    return res


def _tag_catalog() -> str:
    rows = []
    for code, t in T.TAGS.items():
        if t["kind"] in ("error", "habit", "teacher") and code not in ("domain_noted", "check_done", "other"):
            rows.append(f"- {code}: {t['ru']} / {t['kk']}")
    return "\n".join(rows)


TAG_PROMPT = """Комментарий учителя к домашней работе ученика (может быть на русском или казахском):
«{text}»

Сопоставь его с тегами из словаря. Используй ТОЛЬКО коды из списка:
{catalog}

Для каждого подходящего тега дай точную цитату — фрагмент комментария, дословно.
Если ничего не подходит — верни тег teacher_other с цитатой всего комментария.
Верни только JSON: {{"tags": [{{"tag": "код", "quote": "цитата"}}]}}"""


def tag_comment(cfg: LLMConfig, text: str) -> list[dict]:
    out = _json(_call(cfg, TAG_PROMPT.format(text=text, catalog=_tag_catalog()), max_tokens=600))
    res = []
    for item in out.get("tags", []):
        tag = str(item.get("tag", ""))
        quote = str(item.get("quote", "")).strip()
        if tag not in T.TAGS:  # модель не может придумать новый вывод
            tag = "teacher_other"
        if not quote or quote.lower() not in text.lower():  # цитата должна быть из текста
            quote = text.strip()
        res.append({"tag": tag, "quote": quote})
    return res or [{"tag": "teacher_other", "quote": text.strip()}]


PERSONALIZE_PROMPT = """Ты помогаешь учителю математики. Ниже — готовые советы из методического словаря
и доказательства из работ ученика «{alias}». Перепиши каждый совет для учителя в 1–2 предложения,
привязав к конкретным работам (ДЗ №…), на языке: {language}.
Не добавляй новых выводов о ученике, не оценивай характер, описывай только действия.
Упоминай только те номера ДЗ, что есть в доказательствах.

{items}

Верни только JSON: {{"advice": ["совет 1", "совет 2", ...]}} — ровно {n} строк в том же порядке."""


def personalize(cfg: LLMConfig, recs: list, alias: str, lang: str) -> list[str]:
    items = []
    for i, r in enumerate(recs, 1):
        refs = "; ".join(f"ДЗ №{x['work_no']}, задача {x['problem_idx']}, строка {x['line_no']}: {x['evidence']}"
                         for x in r["refs"] if x.get("work_no"))
        items.append(f"{i}. {r['title']} ({r['why']}). Совет из словаря: {r['teacher']}\n   Доказательства: {refs or 'нет'}")
    language = "казахский" if lang == "kk" else "русский"
    out = _json(_call(cfg, PERSONALIZE_PROMPT.format(alias=alias, language=language,
                                                     items="\n".join(items), n=len(recs)), max_tokens=1200))
    adv = [str(a).strip() for a in out.get("advice", [])]
    if len(adv) != len(recs):
        raise LLMError("Модель вернула другое число советов")
    # проверка: упомянутые работы существуют в доказательствах
    for a, r in zip(adv, recs):
        allowed = {str(x["work_no"]) for x in r["refs"] if x.get("work_no")}
        for num in re.findall(r"№\s*(\d+)", a):
            if num not in allowed:
                raise LLMError(f"Модель сослалась на несуществующую работу №{num}")
    return adv
