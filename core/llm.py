"""Языковая модель — только на входе и на выходе.

* recognize()   — фото тетради → строки (vision-модель);
  recognize_detailed() — то же, плюс неразборчивые фрагменты каждой строки
  (уверенность строк считает код: core/ocr.py);
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


try:  # фото с iPhone (HEIC) — если установлен необязательный pillow-heif
    from pillow_heif import register_heif_opener as _register_heif
    HEIC_OK = True
except Exception:  # noqa: BLE001
    _register_heif, HEIC_OK = None, False

PHOTO_TYPES = ["jpg", "jpeg", "png", "webp"] + (["heic", "heif"] if HEIC_OK else [])


def prepare_image(data: bytes) -> tuple[bytes, str]:
    """Повернуть по EXIF, уменьшить до 1600 px, JPEG. Фото нигде не сохраняется."""
    try:
        from PIL import Image, ImageOps
        if _register_heif is not None:
            _register_heif()
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


_RULES = """Правила записи:
- степень: x² или x^2; корень: √; умножение: ·; дробь: a/b (скобки, если нужно); минус: −
- индексы корней: x₁, x₂
- неравенства и промежутки — как в тетради: знаки < > ≤ ≥, ∞, ∪, скобки промежутков ( ) [ ] и «;» внутри них,
  например x ∈ [−2; 3] или (−∞; −1) ∪ [3; +∞)
- строку знаков метода интервалов переписывай как есть: + − +
- систему уравнений пиши двумя строками, по уравнению в строке; большую фигурную скобку системы не переписывай
- без LaTeX: не пиши \\frac, \\sqrt, \\cdot, \\infty, \\cup, $ и фигурные скобки LaTeX
- слова (Ответ, Проверка, ОДЗ, или, не подходит, Жауабы, Тексеру и т. п.) сохраняй как есть
- не переписывай условие, если его нет в тетради; не добавляй своих строк; ошибки не исправляй
- не решай задачу и не дописывай пропущенные шаги
- если символ неразборчив, поставь в text «?» вместо него, а в unsure — фрагмент строки,
  в котором сомневаешься (как ты его прочитал); если всё разборчиво — unsure: []"""

OCR_DETAILED_PROMPT = """На фото — тетрадь ученика с домашней работой. Задачи:
{problems}

Перепиши решение каждой задачи построчно, строка тетради = строка в ответе.
""" + _RULES + """
- строки, которые не удалось отнести ни к одной задаче, положи в unassigned

Верни только JSON без пояснений:
{{"problems": [{{"number": 1, "lines": [{{"text": "x² = 7x", "unsure": []}}]}}], "unassigned": []}}"""

# Второе прочтение (passes=2): другая формулировка, чтобы ошибки прочтений были независимее.
OCR_SECOND_PROMPT = """Ты видишь фото страницы школьной тетради по алгебре. В ней решены задачи:
{problems}

Сделай посимвольную расшифровку рукописи: иди по тетради сверху вниз и для каждой задачи выпиши
каждую рукописную строку ровно так, как она написана, — даже если в ней ошибка.
Внимательно различай похожие знаки: + и −, x и ×, 1 и 7, 5 и S, 0 и 6, ² и 2, скобки и индексы.
""" + _RULES + """
- строки, которые не относятся ни к одной задаче, положи в unassigned

Ответ — только JSON по схеме:
{{"problems": [{{"number": 1, "lines": [{{"text": "...", "unsure": []}}]}}], "unassigned": [{{"text": "...", "unsure": []}}]}}"""

JSON_REPAIR_PROMPT = """Твой прошлый ответ не удалось разобрать как JSON. Вот он:
<<<
{previous}
>>>
Верни только JSON по схеме, без пояснений и без markdown:
{{"problems": [{{"number": 1, "lines": [{{"text": "строка", "unsure": []}}]}}], "unassigned": []}}"""

UNASSIGNED = 0   # ключ для строк, которые модель не отнесла ни к одной задаче (номера задач — с 1)


def _json_ocr(text: str) -> dict:
    """Разобрать ответ распознавания: JSON (возможно, в ```-блоке) с ключом problems."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    out = _json(m.group(1) if m else text)
    if not isinstance(out, dict) or not isinstance(out.get("problems", []), list):
        raise LLMError("JSON не по схеме: нет списка problems")
    return out


def _line_item(l) -> Optional[dict]:
    if isinstance(l, dict):
        text = str(l.get("text") or "").strip()
        unsure = l.get("unsure") or []
        if isinstance(unsure, str):
            unsure = [unsure]
        unsure = [str(u).strip() for u in unsure if str(u).strip()]
    else:
        text, unsure = str(l if l is not None else "").strip(), []
    return {"text": text, "unsure": unsure} if text else None


def _parse_detailed(out: dict) -> dict:
    res: dict = {}
    for p in out.get("problems") or []:
        if not isinstance(p, dict):
            continue
        m = re.search(r"\d+", str(p.get("number", "")))
        if not m:
            continue
        lines = [x for x in (_line_item(l) for l in (p.get("lines") or [])) if x]
        res.setdefault(int(m.group(0)), []).extend(lines)
    loose = [x for x in (_line_item(l) for l in (out.get("unassigned") or [])) if x]
    if loose:
        res.setdefault(UNASSIGNED, []).extend(loose)
    return res


def _read_once(cfg: LLMConfig, prompt: str, image: tuple) -> dict:
    first = _call(cfg, prompt, image=image, system=OCR_SYSTEM, max_tokens=4000)
    try:
        return _parse_detailed(_json_ocr(first))
    except LLMError:
        pass
    # одна попытка починить формат — без фото, только текст прошлого ответа
    second = _call(cfg, JSON_REPAIR_PROMPT.format(previous=first[:6000]), system=OCR_SYSTEM, max_tokens=4000)
    try:
        return _parse_detailed(_json_ocr(second))
    except LLMError as ex:
        raise LLMError("Модель дважды вернула ответ не в формате JSON — распознать строки не удалось. "
                       "Попробуйте ещё раз или введите строки текстом.") from ex


def recognize_detailed(cfg: LLMConfig, image_bytes: bytes, problems: list, passes: int = 1) -> dict:
    """problems: [(idx, statement)] → {idx: [{"text": str, "unsure": [str]}]} — сырой ответ модели.

    Строки без задачи — под ключом UNASSIGNED (0). При passes=2 второй запрос идёт с другой
    формулировкой, строки выравниваются (ocr.merge_readings); у разошедшихся строк появляется
    поле alternatives = [первое прочтение, второе]. Уверенность здесь не считается — это ocr.score_lines."""
    data, mime = prepare_image(image_bytes)
    plist = "\n".join(f"№{i}. {st}" for i, st in problems)
    res = _read_once(cfg, OCR_DETAILED_PROMPT.format(problems=plist), (data, mime))
    if passes >= 2:
        from .ocr import merge_readings  # ocr → checker; импорт здесь, чтобы llm оставался лёгким
        res2 = _read_once(cfg, OCR_SECOND_PROMPT.format(problems=plist), (data, mime))
        for idx in sorted(set(res) | set(res2)):
            res[idx] = merge_readings(res.get(idx, []), res2.get(idx, []))
    return res


def recognize(cfg: LLMConfig, image_bytes: bytes, problems: list) -> dict:
    """problems: [(idx, statement)] → {idx: [строки]}"""
    det = recognize_detailed(cfg, image_bytes, problems)
    return {idx: [l["text"] for l in lines] for idx, lines in det.items() if idx != UNASSIGNED}


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
