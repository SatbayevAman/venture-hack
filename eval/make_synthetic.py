"""Синтетический набор для дымового теста распознавания (этап B4).

Рисует строки решений из синтетической истории (core/seed.py) рукописным шрифтом
Caveat (лицензия SIL OFL 1.1, поддерживает кириллицу и казахские буквы) на листе
«в клетку» и пишет разметку в eval/synthetic/labels.csv в формате eval/labels.csv.

ЭТО СИНТЕТИКА: печатный «почерк» читается гораздо легче настоящего. Набор нужен,
чтобы проверить, что путь «фото → строки → проверка» работает целиком, и отладить
промпт. Цифры для слайда — только по реальным фото (eval/labels.csv).

Запуск (из корня репозитория):
    python eval/make_synthetic.py                   # 30 фото с одной задачей + 1 фото с ДЗ №7 целиком
    python eval/make_synthetic.py --n 60 --seed 3
    python eval/make_synthetic.py --font путь/к/Caveat.ttf
    ANTHROPIC_API_KEY=... python evaluate.py --ocr --labels eval/synthetic/labels.csv

Шрифт: если не указан --font, берётся из кэша ~/.cache/portret/Caveat.ttf, а при
первом запуске скачивается из репозитория Google Fonts.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402
from PIL.PngImagePlugin import PngInfo  # noqa: E402

from core import db, pipeline, seed  # noqa: E402
from core.checker import check_problem  # noqa: E402

OUT = ROOT / "synthetic"
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/caveat/Caveat%5Bwght%5D.ttf"
FONT_CACHE = Path.home() / ".cache" / "portret" / "Caveat.ttf"
FALLBACK_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf", "Arial.ttf")
NO_GLYPH = set("√✓")  # в Caveat нет — рисуем запасным шрифтом
INKS = [(24, 44, 120), (20, 30, 90), (35, 35, 45), (30, 60, 150)]
W, H = 1100, 1400
CELL = 38


def load_font(path: str | None) -> Path:
    if path:
        return Path(path)
    if not FONT_CACHE.exists():
        FONT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        print(f"Скачиваю Caveat (OFL) → {FONT_CACHE}")
        urllib.request.urlretrieve(FONT_URL, FONT_CACHE)
    return FONT_CACHE


def fallback_font(size: int):
    for f in FALLBACK_FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def paper(rng: random.Random) -> Image.Image:
    """Лист в клетку, чуть неровный по цвету."""
    base = rng.randint(242, 252)
    img = Image.new("RGB", (W, H), (base, base, base - rng.randint(2, 8)))
    d = ImageDraw.Draw(img)
    grid = (rng.randint(185, 205), rng.randint(205, 220), rng.randint(225, 240))
    off = rng.randint(0, CELL - 1)
    for x in range(off, W, CELL):
        d.line([(x, 0), (x, H)], fill=grid, width=1)
    for y in range(off, H, CELL):
        d.line([(0, y), (W, y)], fill=grid, width=1)
    d.line([(W - 90, 0), (W - 90, H)], fill=(220, 120, 120), width=2)  # поля
    return img


def draw_line(img: Image.Image, text: str, x: int, y: int, font_path: Path, size: int,
              ink: tuple, rng: random.Random) -> None:
    """Строка «от руки»: посимвольная дрожь по высоте и размеру, наклон всей строки."""
    layer = Image.new("L", (W, size * 2), 0)
    d = ImageDraw.Draw(layer)
    cx = 4
    for ch in text:
        s = max(12, int(size * rng.uniform(0.93, 1.07)))
        f = fallback_font(int(s * 0.8)) if ch in NO_GLYPH else ImageFont.truetype(str(font_path), s)
        if ch not in NO_GLYPH:
            try:
                f.set_variation_by_axes([rng.uniform(420, 600)])
            except Exception:  # noqa: BLE001 — статичный шрифт без осей
                pass
        dy = int(size * 0.35) + rng.randint(-2, 2)
        d.text((cx, dy), ch, font=f, fill=255)
        cx += int(d.textlength(ch, font=f) * rng.uniform(0.92, 1.05)) + (rng.randint(2, 6) if ch == " " else 0)
    layer = layer.rotate(rng.uniform(-1.3, 1.3), resample=Image.BICUBIC, expand=False)
    layer = layer.transform(layer.size, Image.AFFINE, (1, rng.uniform(0.05, 0.18), 0, 0, 1, 0), Image.BICUBIC)
    layer = layer.filter(ImageFilter.GaussianBlur(rng.uniform(0.2, 0.6)))
    img.paste(Image.new("RGB", layer.size, ink), (x, y - size), layer)


def render(problems: list, font_path: Path, rng: random.Random, path: Path) -> None:
    """problems: [(номер, [строки])] → PNG. Условие не пишем: в тетради его обычно нет."""
    img = paper(rng)
    ink = rng.choice(INKS)
    size = rng.randint(40, 50)
    y = CELL * 2 + rng.randint(0, 10)
    x0 = 60 + rng.randint(-10, 25)
    for num, lines in problems:
        draw_line(img, f"№{num}", x0 - 20, y, font_path, size, ink, rng)
        y += int(CELL * 1.6)
        for line in lines:
            draw_line(img, line, x0 + rng.randint(-6, 10), y, font_path, size, ink, rng)
            y += int(CELL * rng.uniform(1.45, 1.75))
        y += CELL // 2
    img = img.convert("L")  # оттенки серого: файлы меньше, почерк читается так же
    meta = PngInfo()
    meta.add_text("Comment", "SYNTHETIC — сгенерировано eval/make_synthetic.py, не реальная работа")
    img.save(path, optimize=True, pnginfo=meta)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="фото с одной задачей")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--font", help="путь к Caveat.ttf (или другому рукописному шрифту с кириллицей)")
    args = ap.parse_args()
    font_path = load_font(args.font)
    rng = random.Random(args.seed)
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("syn_*.png"):
        old.unlink()

    conn = db.connect(":memory:")
    seed.build(conn)
    works = db.q(conn, """SELECT st.submission_id, st.problem_id, p.kind, p.statement,
                                 group_concat(st.raw_text, char(10)) AS lines
                          FROM (SELECT * FROM steps ORDER BY submission_id, problem_id, line_no) st
                          JOIN problems p ON p.id = st.problem_id
                          GROUP BY st.submission_id, st.problem_id""")
    # половина — с ошибкой, половина — без: иначе набор перекошен в сторону верных решений
    with_err = [w for w in works if db.q1(conn, "SELECT first_error_line FROM attempts WHERE submission_id=? AND problem_id=?",
                                          (w["submission_id"], w["problem_id"]))["first_error_line"]]
    no_err = [w for w in works if w not in with_err]
    pick = rng.sample(with_err, min(len(with_err), args.n // 2)) + rng.sample(no_err, min(len(no_err), args.n - args.n // 2))
    rng.shuffle(pick)

    rows = []

    def label(photo: str, num: int, kind: str, statement: str, lines: list) -> dict:
        res = check_problem(kind, statement, lines, None)
        fe = res.first_error
        return {"photo": photo, "problem": num, "kind": kind, "statement": statement, "lines": " | ".join(lines),
                "error_line": fe["line"] if fe else 0, "error_tag": fe["tag"] if fe else "", "status": "synthetic"}

    for k, w in enumerate(pick, 1):
        lines = w["lines"].split("\n")
        name = f"syn_{k:03d}.png"
        render([(1, lines)], font_path, rng, OUT / name)
        rows.append(label(f"synthetic/{name}", 1, w["kind"], w["statement"], lines))

    # одно фото с несколькими задачами — живая демо-работа ДЗ №7
    aid = seed.live_assignment_id(conn)
    probs = pipeline.problems_of(conn, aid)
    page = [(p["idx"], seed.LIVE_DEMO_TEXT[p["idx"]].split("\n")) for p in probs]
    name = "syn_live_hw7.png"
    render(page, font_path, rng, OUT / name)
    for p in probs:
        rows.append(label(f"synthetic/{name}", p["idx"], p["kind"], p["statement"], seed.LIVE_DEMO_TEXT[p["idx"]].split("\n")))

    fields = ["photo", "problem", "kind", "statement", "lines", "error_line", "error_tag", "status"]
    with open(OUT / "labels.csv", "w", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        wr.writerows(rows)
    (OUT / "manifest.json").write_text(json.dumps({
        "synthetic": True, "generator": "eval/make_synthetic.py", "seed": args.seed, "font": "Caveat (SIL OFL 1.1)",
        "photos": len(pick) + 1, "problems": len(rows)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Готово: {len(pick) + 1} синтетических фото, {len(rows)} задач → {OUT}")


if __name__ == "__main__":
    main()
