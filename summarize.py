#!/usr/bin/env python3
"""Step 2 of the ELA study-guide pipeline: rule-based summarisation.

Reads ``work/manifest.json`` and ``work/*.txt`` written by ``extract.py`` and
generates ``latex/sections/*.tex``.

Rules:
* Only text extracted from the source PDFs is used. Nothing is paraphrased
  into new facts: every value, formula, step and answer printed in the guide
  is a (cleaned) sentence/line taken from the extracted text.
* A field that cannot be found in the text is printed as
  "Not in the source PDFs".
* Every source file also gets a "Detailed explanation" section that walks
  through its complete content (all headings, paragraphs, lists, equations,
  tables) so nothing in the PDFs is left out.
* No LLM or online service is used: only open-source tools (Poppler,
  PyMuPDF, Tesseract/OCRmyPDF, LaTeX), so the build is deterministic and runs
  entirely inside GitHub Actions.
"""
from __future__ import annotations

import difflib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
SECTIONS = ROOT / "latex" / "sections"
FIGS = ROOT / "latex" / "figs"
MISSING = "Not in the source PDFs"

# --------------------------------------------------------------------------
# Experiment catalogue (keywords decide which text belongs where)
# --------------------------------------------------------------------------
KNOWN = [
    {
        "key": "ester-hydrolysis",
        "title": "Ester Hydrolysis",
        "title_re": r"ester|hydrolys",
        "body_re": [r"\bester", r"hydrolys", r"ethyl\s+acetate|methyl\s+acetate", r"pseudo|first[- ]order"],
    },
    {
        "key": "emf-thermodynamics",
        "title": "Thermodynamic Functions from EMF Measurements (Zn--Cu System)",
        "title_re": r"thermodynamic|\bemf\b|e\.m\.f|electromotive|zn[\s\-]*cu|daniel",
        "body_re": [r"\bemf\b|e\.m\.f|electromotive", r"thermodynamic", r"daniel|zinc|copper|\bzn\b|\bcu\b",
                    r"entropy|enthalpy|gibbs|temperature coefficient"],
    },
]

FIELDS = [  # (field, heading regex) — order matters (more specific first)
    ("apparatus_chem", r"apparatus\s*(?:and|&)\s*(?:chemicals|reagents|materials)(?:\s+required)?|materials?\s+required|requirements?"),
    ("chemicals", r"chemicals?(?:\s+required)?|reagents?(?:\s+required)?|solutions?\s+(?:required|provided)"),
    ("apparatus", r"apparatus(?:\s+required)?|equipments?|instruments?(?:\s+required)?|glassware"),
    ("aim", r"aim(?:\s+of\s+the\s+experiment)?|objectives?|purpose"),
    ("mechanism", r"mechanism(?:\s+of\s+[\w\s]{0,30})?"),
    ("principle", r"principle|theory|introduction|background|basic\s+concepts?"),
    ("procedure", r"(?:experimental\s+)?procedure|methodology|method|steps"),
    ("observation", r"observations?(?:\s+tables?)?|tabulations?|readings|observations?\s+and\s+calculations?"),
    ("calculation", r"calculations?|formulae|formulas?|computations?"),
    ("graph", r"graphs?|plots?|graphical\s+representation"),
    ("error", r"sources?\s+of\s+errors?|possible\s+errors?|errors?"),
    ("precaution", r"precautions?|safety(?:\s+precautions?)?"),
    ("result", r"results?|inference|conclusions?"),
    ("viva", r"viva(?:[\s\-]*voce)?(?:\s+questions)?|review\s+questions|questions"),
]
HEAD_RE = [(f, re.compile(
    r"^\s*(?:[0-9]{1,2}[.)]\s*|[IVX]{1,4}[.)]\s*|[•\-*]\s*)?(?:" + rx + r")\s*(?:[:\-–—]+\s*(?P<rest>.*))?$",
    re.I)) for f, rx in FIELDS]

EXP_START = re.compile(
    r"^\s*(?:experiment|expt|exp)\s*\.?\s*(?:no\.?|number|#)?\s*[:\-.]?\s*(\d{1,2}|[IVX]{1,4})\b\s*[:\-.–]?\s*(.*)$", re.I)
AIM_LINE = re.compile(r"^\s*(?:aim|objective)\s*[:\-–]", re.I)

APPARATUS_WORDS = [
    "burette", "pipette", "conical flask", "volumetric flask", "standard flask", "measuring cylinder",
    "beaker", "test tube", "funnel", "glass rod", "stop watch", "stopwatch", "stop clock", "thermostat",
    "water bath", "thermometer", "potentiometer", "voltmeter", "digital voltmeter", "multimeter",
    "salt bridge", "electrode", "hot plate", "heater", "ice bath", "reagent bottle", "wash bottle",
    "watch glass", "weighing balance", "electronic balance", "dropper", "magnetic stirrer", "u-tube",
    "conductometer", "ph meter", "colorimeter", "spectrophotometer", "cell", "wire",
]
CHEMICAL_WORDS = [
    "ethyl acetate", "methyl acetate", "ester", "hydrochloric acid", "hcl", "sodium hydroxide", "naoh",
    "phenolphthalein", "oxalic acid", "ice", "distilled water", "zinc sulphate", "zinc sulfate", "znso4",
    "copper sulphate", "copper sulfate", "cuso4", "zinc rod", "zinc strip", "copper rod", "copper strip",
    "kcl", "potassium chloride", "agar", "kno3", "potassium nitrate", "sulphuric acid", "sulfuric acid",
    "h2so4", "acetic acid", "ethanol", "methanol", "methyl orange", "sand paper", "emery paper",
]
UNIT_RE = re.compile(
    r"(?<![A-Za-z])(mol\s*(?:dm|L|l)\s*-?\s*[⁻\-]?\s*[3³1¹]|mol\s*/\s*(?:L|dm3)|kJ\s*mol\s*[⁻\-]?\s*[1¹]|"
    r"J\s*K\s*[⁻\-]?\s*[1¹]\s*mol\s*[⁻\-]?\s*[1¹]|J\s*mol\s*[⁻\-]?\s*[1¹]\s*K\s*[⁻\-]?\s*[1¹]|"
    r"J\s*mol\s*[⁻\-]?\s*[1¹]|V\s*K\s*[⁻\-]?\s*[1¹]|min\s*[⁻\-]\s*[1¹]|s\s*[⁻\-]\s*[1¹]|"
    r"kJ|mV|mL|ml|cm\s*[3³]|dm\s*[3³]|°\s*C|K|V|J|min|mins|sec|s|M|N|C\s*mol\s*[⁻\-]?\s*[1¹])(?![A-Za-z])")


# --------------------------------------------------------------------------
# LaTeX helpers
# --------------------------------------------------------------------------
TEXT_MAP = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
    "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    "<": r"\textless{}", ">": r"\textgreater{}", "|": r"\textbar{}",
    "Δ": r"\ensuremath{\Delta}", "∆": r"\ensuremath{\Delta}", "°": r"\textdegree{}",
    "±": r"\ensuremath{\pm}", "×": r"\ensuremath{\times}", "÷": r"\ensuremath{\div}",
    "−": "-", "–": "--", "—": "---", "‐": "-", "‑": "-", "‘": "`", "’": "'", "“": "``", "”": "''",
    "′": "'", "″": "''", "…": r"\ldots{}", "•": r"\textbullet{}", "·": r"\ensuremath{\cdot}",
    "μ": r"\ensuremath{\mu}", "µ": r"\ensuremath{\mu}", "Ω": r"\ensuremath{\Omega}", "∞": r"\ensuremath{\infty}",
    "≈": r"\ensuremath{\approx}", "≤": r"\ensuremath{\leq}", "≥": r"\ensuremath{\geq}", "≠": r"\ensuremath{\neq}",
    "→": r"\ensuremath{\rightarrow}", "⟶": r"\ensuremath{\rightarrow}", "←": r"\ensuremath{\leftarrow}",
    "⇌": r"\ensuremath{\rightleftharpoons}", "↔": r"\ensuremath{\leftrightarrow}", "⇒": r"\ensuremath{\Rightarrow}",
    "∝": r"\ensuremath{\propto}", "√": r"\ensuremath{\surd}", "∑": r"\ensuremath{\sum}", "∂": r"\ensuremath{\partial}",
    "α": r"\ensuremath{\alpha}", "β": r"\ensuremath{\beta}", "γ": r"\ensuremath{\gamma}", "δ": r"\ensuremath{\delta}",
    "ε": r"\ensuremath{\varepsilon}", "θ": r"\ensuremath{\theta}", "κ": r"\ensuremath{\kappa}", "λ": r"\ensuremath{\lambda}",
    "ν": r"\ensuremath{\nu}", "π": r"\ensuremath{\pi}", "ρ": r"\ensuremath{\rho}", "σ": r"\ensuremath{\sigma}",
    "τ": r"\ensuremath{\tau}", "φ": r"\ensuremath{\phi}", "ω": r"\ensuremath{\omega}", "Σ": r"\ensuremath{\Sigma}",
    "½": r"\ensuremath{\frac{1}{2}}", "ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "\u00a0": " ", "\u200b": "", "\ufeff": "",
}
SUB = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋₍₎", "0123456789+-()")
SUP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾", "0123456789+-()")
SUB_CH, SUP_CH = "₀₁₂₃₄₅₆₇₈₉₊₋₍₎", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾"


def tex(s: str) -> str:
    """Escape plain text for pdfLaTeX (unknown glyphs are transliterated or dropped)."""
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch in SUB_CH or ch in SUP_CH:
            j = i
            kind = SUB_CH if ch in SUB_CH else SUP_CH
            while j < len(s) and s[j] in kind:
                j += 1
            run = s[i:j].translate(SUB if kind is SUB_CH else SUP)
            out.append((r"\textsubscript{%s}" if kind is SUB_CH else r"\textsuperscript{%s}") % run)
            i = j
            continue
        if ch in TEXT_MAP:
            out.append(TEXT_MAP[ch])
        elif ord(ch) < 128:
            out.append(ch if ch.isprintable() or ch == " " else " ")
        else:
            norm = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append("".join(TEXT_MAP.get(c, c) for c in norm))
        i += 1
    return re.sub(r"[ \t]+", " ", "".join(out)).strip()


MATH_MAP = {
    "Δ": r"\Delta ", "∆": r"\Delta ", "α": r"\alpha ", "β": r"\beta ", "γ": r"\gamma ", "δ": r"\delta ",
    "θ": r"\theta ", "λ": r"\lambda ", "κ": r"\kappa ", "μ": r"\mu ", "µ": r"\mu ", "π": r"\pi ",
    "ε": r"\varepsilon ", "Ω": r"\Omega ", "σ": r"\sigma ", "∞": r"\infty ", "×": r"\times ", "·": r"\cdot ",
    "−": "-", "–": "-", "—": "-", "≈": r"\approx ", "∝": r"\propto ", "≤": r"\leq ", "≥": r"\geq ",
    "°": r"^{\circ}", "→": r"\rightarrow ", "⇌": r"\rightleftharpoons ", "∂": r"\partial ", "÷": r"\div ",
    "’": "'", "′": "'", "√": r"\sqrt ", "½": r"\tfrac{1}{2}", "\u00a0": " ",
}
MATH_SAFE = re.compile(r"^[A-Za-z0-9\s+\-*/=().,\[\]{}^_\\|<>':]+$")


def to_math(line: str) -> str | None:
    """Convert a formula line from the source into LaTeX math, or None if unsafe."""
    s = line.strip().rstrip(".,;")
    s = re.sub(r"^\(?\d{1,2}[.)]\s+", "", s)          # leading list number
    s = re.sub(r"\s*\(\s*\d{1,2}\s*\)\s*$", "", s)     # trailing equation number "(3)"
    s = re.sub(r"[" + SUB_CH + r"]+", lambda m: "_{" + m.group(0).translate(SUB) + "}", s)
    s = re.sub(r"[" + SUP_CH + r"]+", lambda m: "^{" + m.group(0).translate(SUP) + "}", s)
    for k, v in MATH_MAP.items():
        s = s.replace(k, v)
    if "__" in s or re.search(r"_(?![A-Za-z0-9{])", s):
        return None   # fill-in blanks ("____") are not maths
    if not MATH_SAFE.match(s) or s.count("(") != s.count(")") or s.count("[") != s.count("]"):
        return None
    s = re.sub(r"\^\s*(-?\s*\d+(?:\.\d+)?)", lambda m: "^{" + m.group(1).replace(" ", "") + "}", s)
    s = re.sub(r"_\s*([A-Za-z0-9]+)", r"_{\1}", s)
    s = re.sub(r"\blog\s*10\b|\blog_\{10\}", r"\\log_{10}", s)
    s = re.sub(r"(?<!\\)\b(log|ln|exp)\b", r"\\\1", s)
    # multi-letter words (not commands) upright
    s = re.sub(r"(?<![\\A-Za-z])([A-Za-z]{3,})", lambda m: r"\mathrm{" + m.group(1) + "}", s)
    s = s.replace("%", r"\%")
    if s.count("{") != s.count("}") or s.strip().startswith("=") or s.strip().endswith("="):
        return None
    return s.strip()


def to_ce(line: str) -> str | None:
    """Chemical reaction line -> \\ce{...} or None."""
    s = line.strip().rstrip(".,;")
    s = s.translate(SUB).translate(SUP)
    s = s.replace("⇌", " <=> ").replace("→", " -> ").replace("⟶", " -> ").replace("−", "-").replace("–", "-")
    s = re.sub(r"\s*-{1,2}>\s*", " -> ", s)
    if not re.match(r"^[A-Za-z0-9\s+\-()<>=\[\].]+$", s) or s.count("(") != s.count(")"):
        return None
    return r"\ce{" + re.sub(r"\s+", " ", s).strip() + "}"


# --------------------------------------------------------------------------
# Text utilities
# --------------------------------------------------------------------------
def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


BULLET = re.compile(r"^\s*(?:[•●▪■◦○➢➤►✓✔\-*]|\uf0b7|\uf0a7|\uf076|\uf0d8|o(?=\s))\s+")
NUMBERED = re.compile(r"^\s*(?:\(?\d{1,2}[.)]|\(?[ivx]{1,4}\)|\(?[a-h]\)|step\s*\d+\s*[:.\-])\s+", re.I)


def items(text: str) -> list[str]:
    """Split text into list items using bullets/numbering; fall back to paragraphs/sentences."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    marked = sum(1 for ln in lines if BULLET.match(ln) or NUMBERED.match(ln))
    out: list[str] = []
    if marked >= 2:
        cur = ""
        for ln in lines:
            if BULLET.match(ln) or NUMBERED.match(ln):
                if squash(cur):
                    out.append(squash(cur))
                cur = NUMBERED.sub("", BULLET.sub("", ln))
            elif not ln.strip():
                continue
            else:
                cur += " " + ln.strip()
        if squash(cur):
            out.append(squash(cur))
        return [o for o in out if re.search(r"[A-Za-z0-9]", o)]
    paras = paragraphs(text)
    if len(paras) >= 2:
        return paras
    return sentences(" ".join(paras))


def paragraphs(text: str) -> list[str]:
    paras, cur = [], []
    for ln in text.splitlines():
        if ln.strip():
            cur.append(ln.strip())
        elif cur:
            paras.append(squash(" ".join(cur)))
            cur = []
    if cur:
        paras.append(squash(" ".join(cur)))
    return [p for p in paras if re.search(r"[A-Za-z0-9]", p)]


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", squash(text))
    return [p.strip() for p in parts if len(p.strip()) > 2]


PROSE_WORD = re.compile(r"^[A-Za-z]{2,}[.,:;]?$")
MATH_WORDS = {"log", "ln", "exp", "sin", "cos", "dE", "dT", "nF", "nFE", "RT", "Vt", "V0", "Vinf"}


def split_formula(line: str) -> tuple[str, str]:
    """'The rate constant is k = ...' -> ('The rate constant is', 'k = ...')."""
    s = line.strip()
    if "=" not in s:
        return s, ""
    lhs, rhs = s.split("=", 1)
    toks = lhs.split()
    i = len(toks)
    while i > 0:
        t = toks[i - 1]
        if PROSE_WORD.match(t) and t.rstrip(".,:;") not in MATH_WORDS and \
                (len(t.rstrip(".,:;")) > 3 or t[-1] in ".,:;" or t.lower() in {"is", "as", "by", "of", "the", "and", "to", "be", "are"}):
            break
        i -= 1
    prefix = " ".join(toks[:i])
    formula = (" ".join(toks[i:]) + " =" + rhs).strip()
    if not toks[i:]:
        return s, ""
    return prefix, formula


def is_formula(line: str) -> bool:
    s = line.strip()
    if "=" not in s or len(s) > 160 or len(s) < 3:
        return False
    _, f = split_formula(s)
    if not f or len(f) > 110:
        return False
    words = [w for w in re.findall(r"[A-Za-z]{4,}", f) if w not in MATH_WORDS]
    return len(words) <= 3 and bool(re.search(r"[A-Za-zΔ∆]", f))


def is_reaction(line: str) -> bool:
    s = line.strip()
    return bool(re.search(r"→|⟶|⇌|->", s)) and len(s) < 120 and \
        len(re.findall(r"[A-Z][a-z]?[0-9₀-₉]", s)) >= 1 and len(re.findall(r"[a-z]{5,}", s)) <= 3


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass
class Chunk:
    source: str               # file name
    title: str
    pages: tuple[int, int]
    text: str
    fields: dict[str, str] = field(default_factory=dict)
    structured: int = 0       # number of recognised headings
    ocr: bool = False         # text came from OCR (less reliable)


@dataclass
class Experiment:
    key: str
    title: str
    chunks: list[Chunk] = field(default_factory=list)
    figures: list[dict] = field(default_factory=list)


def clean_pages(raw: str) -> list[str]:
    """Split into pages and drop running headers/footers and page numbers."""
    pages = raw.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    norm = lambda ln: re.sub(r"\d+", "#", squash(ln)).lower()

    def edges(pg: str) -> set[str]:
        ls = [x for x in pg.splitlines() if x.strip()]
        return {norm(x) for x in ls[:3] + ls[-3:]}

    counts: dict[str, int] = {}
    for pg in pages:
        for ln in edges(pg):
            counts[ln] = counts.get(ln, 0) + 1
    n = len(pages)
    repeated = {k for k, v in counts.items()
                if len(k) < 120 and ((n >= 4 and v >= 0.6 * n) or (n in (2, 3) and v == n))}
    out = []
    for pg in pages:
        keep, ls = [], pg.splitlines()
        nonempty = [i for i, x in enumerate(ls) if x.strip()]
        edge_idx = set(nonempty[:3] + nonempty[-3:])
        for i, ln in enumerate(ls):
            n = norm(ln)
            if i in edge_idx and (n in repeated or re.fullmatch(r"(page\s*)?#(\s*(of|/)\s*#)?|-\s*#\s*-", n)):
                continue
            keep.append(ln.rstrip())
        out.append("\n".join(keep))
    return out


def parse_fields(text: str) -> tuple[dict[str, str], int]:
    fields: dict[str, list[str]] = {}
    cur = "_intro"
    found = 0
    for ln in text.splitlines():
        hit = None
        stripped = ln.strip()
        if 0 < len(stripped) <= 120:
            for f, rx in HEAD_RE:
                m = rx.match(stripped)
                if m:
                    rest = (m.group("rest") or "").strip()
                    # a bare heading must be short; "Heading: text" may be longer
                    if not rest and len(stripped) > 45:
                        continue
                    if not rest and not re.search(r"[:\-–]\s*$", stripped) and \
                            stripped.split()[0][0].islower():
                        continue  # a sentence that starts with e.g. "method ..." is not a heading
                    hit = (f, rest)
                    break
        if hit:
            cur = hit[0]
            found += 1
            fields.setdefault(cur, [])
            if hit[1]:
                fields[cur].append(hit[1])
        else:
            fields.setdefault(cur, []).append(ln)
    joined = {k: "\n".join(v).strip("\n") for k, v in fields.items()}
    return {k: v for k, v in joined.items() if re.search(r"[A-Za-z0-9]", v)}, found


def split_manual(pages: list[str]) -> list[tuple[str, tuple[int, int], str]]:
    """Split lab-manual text into (title, page-range, text) chunks, one per experiment."""
    lines: list[tuple[int, str]] = [(pno, ln) for pno, pg in enumerate(pages, 1) for ln in pg.splitlines()]
    starts = []
    for i, (_, ln) in enumerate(lines):
        m = EXP_START.match(ln)
        if m and len(ln.strip()) < 140:
            starts.append(i)
    if len(starts) < 2:   # fall back to "Aim:" anchors, title = nearest non-empty line above
        starts = []
        for i, (_, ln) in enumerate(lines):
            if AIM_LINE.match(ln):
                j = i - 1
                while j > 0 and not lines[j][1].strip() and i - j < 4:
                    j -= 1
                starts.append(max(j, 0) if i - j < 4 else i)
    # drop entries of an index / list of experiments (starts too close together)
    filtered = []
    for k, s in enumerate(starts):
        nxt = starts[k + 1] if k + 1 < len(starts) else len(lines)
        body = [l for _, l in lines[s:nxt] if l.strip()]
        if len(body) >= 8 or any(AIM_LINE.match(l) for l in body):
            filtered.append(s)
    starts = filtered
    chunks = []
    if not starts:
        return [("", (1, len(pages)), "\n".join(pages))]
    if starts[0] > 0:
        chunks.append(("_front", (1, lines[starts[0]][0]), "\n".join(l for _, l in lines[:starts[0]])))
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        body = [l for _, l in lines[s:e]]
        m = EXP_START.match(body[0])
        title = (m.group(2) if m else body[0]).strip(" :-–.")
        rest_i = 1
        if not title or AIM_LINE.match(title):
            for t in body[1:6]:
                rest_i += 1
                if t.strip() and not AIM_LINE.match(t):
                    title = t.strip(" :-–.")
                    break
        chunks.append((squash(title), (lines[s][0], lines[e - 1][0]), "\n".join(body)))
    return chunks


def file_title(name: str) -> tuple[str, str]:
    m = re.search(r"ELA_(\d{4}-\d{2}-\d{2})_(.+)\.pdf$", name, re.I)
    if m:
        return m.group(1), m.group(2).replace("_", " ").strip()
    return "", Path(name).stem.replace("_", " ")


def classify(title: str, text: str) -> str | None:
    for k in KNOWN:
        if title and re.search(k["title_re"], title, re.I):
            return k["key"]
    head = text[:3000]
    best, score = None, 0
    for k in KNOWN:
        sc = sum(1 for rx in k["body_re"] if re.search(rx, head, re.I))
        if sc > score:
            best, score = k["key"], sc
    return best if score >= 2 else None


# --------------------------------------------------------------------------
# Field selection / fallbacks
# --------------------------------------------------------------------------
def best(exp: Experiment, name: str) -> tuple[str, str]:
    """Longest version of a field across all chunks of the experiment -> (text, source)."""
    cands = [(c.fields.get(name, ""), c.source) for c in exp.chunks]
    cands = [c for c in cands if c[0].strip()]
    if not cands:
        return "", ""
    ocr = {c.source for c in exp.chunks if c.ocr}
    return max(cands, key=lambda c: len(c[0]) * (0.6 if c[1] in ocr else 1.0))


def all_text(exp: Experiment) -> str:
    return "\n".join(c.text for c in exp.chunks)


def find_sentences(text: str, rx: str, limit: int = 4) -> list[str]:
    out = []
    for s in sentences(text):
        if re.search(rx, s, re.I) and s not in out and len(s) < 400:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def find_words(text: str, words: list[str]) -> list[str]:
    """Words from the list that occur in the text, spelt as in the text."""
    flat = squash(text)
    found: list[str] = []
    for w in words:
        m = re.search(r"(?<![A-Za-z])" + re.escape(w) + r"s?(?![A-Za-z])", flat, re.I)
        if m and not any(m.group(0).lower() in f.lower() for f in found):
            found.append(m.group(0))
    return found


def formulas(text: str, limit: int = 8) -> list[tuple[str, str]]:
    """[(kind, latex)] for formula / reaction lines found in text."""
    out, seen = [], set()
    for ln in text.splitlines():
        s = squash(ln)
        if not s or s in seen:
            continue
        if is_reaction(s):
            ce = to_ce(s)
            out.append(("ce", ce) if ce else ("raw", s))
            seen.add(s)
        elif is_formula(s):
            f = split_formula(s)[1]
            m = to_math(f)
            out.append(("math", m) if m else ("raw", f))
            seen.add(s)
        if len(out) >= limit:
            break
    return out


def units(text: str) -> list[str]:
    found = []
    for m in re.finditer(r"(?:\d\s*|\(\s*|\bin\s+)" + UNIT_RE.pattern + r"(?=\s*\)|\b|\s|$)", text):
        u = squash(m.group(1))
        if u not in found:
            found.append(u)
    return found[:10]


def duration(text: str) -> str:
    m = re.search(r"(?:duration|time\s+(?:required|allotted|allowed|needed))\s*[:\-]?\s*([^\n]{1,30})", text, re.I)
    if m:
        return squash(m.group(1))
    m = re.search(r"\b(\d+(?:\.\d+)?\s*(?:-\s*\d+\s*)?(?:hours?|hrs?)\b)", text, re.I)
    return squash(m.group(1)) if m else MISSING


def table_header(text: str) -> list[str]:
    for ln in text.splitlines():
        cells = [c.strip() for c in re.split(r"\s{2,}|\t|\|", ln.strip()) if c.strip()]
        if len(cells) < 3 and ln.count("(") >= 3:
            cells = [c.strip() for c in re.split(r"(?<=\))\s+(?=[A-Za-z])", ln.strip()) if c.strip()]
        if 3 <= len(cells) <= 9:
            numeric = sum(1 for c in cells if re.fullmatch(r"[\d.\-+\s]+", c))
            if numeric <= len(cells) // 3:
                return cells
    return []


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def render_items(lst: list[str], env: str = "itemize") -> str:
    if not lst:
        return MISSING + ".\n"
    return "\\begin{%s}[leftmargin=*,itemsep=2pt]\n%s\\end{%s}\n" % (
        env, "".join(f"  \\item {tex(x)}\n" for x in lst), env)


def render_text(text: str) -> str:
    """Paragraphs; formula / reaction lines become display maths."""
    if not text.strip():
        return MISSING + ".\n"
    out, buf = [], []

    def flush():
        if buf:
            for p in paragraphs("\n".join(buf)):
                out.append(tex(p) + "\n")
            buf.clear()

    for ln in text.splitlines():
        s = squash(ln)
        if s and is_reaction(s) and to_ce(s):
            flush()
            out.append("\\[ " + to_ce(s) + " \\]\n")
        elif s and is_formula(s) and to_math(split_formula(s)[1]):
            flush()
            pre, f = split_formula(s)
            if pre:
                out.append(tex(pre) + "\n")
            out.append("\\begin{equation*}\n  " + to_math(f) + "\n\\end{equation*}\n")
        else:
            buf.append(ln)
    flush()
    return "\n".join(out)


def render_formulas(fs: list[tuple[str, str]]) -> str:
    if not fs:
        return MISSING + ".\n"
    out = []
    for kind, val in fs:
        if kind == "math":
            out.append("\\begin{equation*}\n  " + val + "\n\\end{equation*}\n")
        elif kind == "ce":
            out.append("\\[ " + val + " \\]\n")
        else:
            out.append("\\begin{center}\\texttt{" + tex(val) + "}\\end{center}\n")
    return "".join(out)


def limit_chars(text: str, n: int) -> str:
    """Kept for call sites; the guide never truncates source content."""
    return text


def viva(exp: Experiment, aim: list[str], principle: str, chem: list[str], app: list[str],
         graphs: list[str], fs: list[tuple[str, str]], prec: list[str], result: str) -> list[tuple[str, str]]:
    qa: list[tuple[str, str]] = []
    vtext, _ = best(exp, "viva")
    if vtext:  # questions printed in the source
        q, a = None, []
        for ln in vtext.splitlines():
            s = squash(NUMBERED.sub("", ln))
            if not s:
                continue
            if s.endswith("?") or re.match(r"^q\s*\d*[.:)]", s, re.I):
                if q:
                    qa.append((q, squash(" ".join(a)) or MISSING))
                q, a = re.sub(r"^q\s*\d*[.:)]\s*", "", s, flags=re.I), []
            elif q:
                if re.match(r"^ans(wer)?\s*[.:\-]", s, re.I):
                    a.append(re.sub(r"^ans(wer)?\s*[.:\-]\s*", "", s, flags=re.I))
                elif a and not re.match(r"^[A-Z][\w ]{2,30}:\s", s):
                    a.append(s)
                elif not a and not re.match(r"^[A-Z][\w ]{2,30}:\s", s):
                    a.append(s)
                else:
                    if q:
                        qa.append((q, squash(" ".join(a)) or MISSING))
                    q, a = None, []
        if q:
            qa.append((q, squash(" ".join(a)) or MISSING))
    if aim:
        qa.append(("What is the aim of this experiment?", aim[0]))
    psent = sentences(squash(principle))
    if psent:
        qa.append(("State the principle of the experiment.", " ".join(psent[:2])))
    defs = 0
    for s in psent + sentences(squash(best(exp, "mechanism")[0])):
        m = re.match(r"^((?:The\s+)?[A-Z][\w\-]*(?:\s+[\w\-]+){0,4}?)\s+(?:is|are)\s+(?:defined\s+as|called|known\s+as|a|an|the)\b", s)
        if m and len(s) < 300 and defs < 4:
            qa.append((f"What is {m.group(1).strip()[0].lower() + m.group(1).strip()[1:]}?", s))
            defs += 1
    if chem:
        qa.append(("Which chemicals are used?", ", ".join(chem) + "."))
    if app:
        qa.append(("Which apparatus is used?", ", ".join(app) + "."))
    for kind, val in fs[:1]:
        qa.append(("Write the key equation used.",
                   "$" + val + "$" if kind == "math" else val if kind == "ce" else tex(val)))
    if graphs:
        qa.append(("Which graph is plotted?", graphs[0]))
    if prec:
        qa.append(("Mention one precaution.", prec[0]))
    if result.strip():
        qa.append(("What is the expected result?", sentences(squash(result))[0] if sentences(squash(result)) else squash(result)))
    return qa


FIELD_TITLES = {
    "_intro": "Introduction", "aim": "Aim", "principle": "Principle / theory", "mechanism": "Mechanism",
    "apparatus_chem": "Apparatus and chemicals", "apparatus": "Apparatus", "chemicals": "Chemicals",
    "procedure": "Procedure", "observation": "Observations", "calculation": "Calculations",
    "graph": "Graph", "result": "Result", "precaution": "Precautions", "error": "Sources of error",
    "viva": "Viva questions",
}
LIST_FIELDS = {"apparatus_chem", "apparatus", "chemicals", "procedure", "precaution", "error"}


def render_block(text: str, field_name: str) -> str:
    """Full rendering of one block of source text: lists stay lists, tables stay tables."""
    if field_name in LIST_FIELDS:
        its = items(text)
        if len(its) >= 2:
            return render_items(its, "enumerate" if field_name == "procedure" else "itemize")
    rows = [ln for ln in text.splitlines() if len([c for c in re.split(r"\s{2,}|\t", ln.strip()) if c]) >= 3]
    if len(rows) >= 2 and len(rows) >= 0.5 * len([ln for ln in text.splitlines() if ln.strip()]):
        return render_table(text)
    return render_text(text)


def render_table(text: str) -> str:
    """Source table (layout text with column gaps) -> booktabs table; other lines as text."""
    out, rows = [], []
    def flush():
        if rows:
            n = max(len(r) for r in rows)
            if n > 9:
                out.append(render_text("\n".join("  ".join(r) for r in rows)))
            else:
                col = r"p{\dimexpr(\linewidth-%d\tabcolsep)/%d\relax}" % (2 * n, n)
                body = "".join(" & ".join(tex(c) for c in r + [""] * (n - len(r))) + " \\\\\n" for r in rows[1:])
                out.append("\\begin{center}\\small\n\\begin{tabular}{@{}" + col * n + "@{}}\n\\toprule\n" +
                           " & ".join(tex(c) for c in rows[0] + [""] * (n - len(rows[0]))) + " \\\\\n\\midrule\n" +
                           body + "\\bottomrule\n\\end{tabular}\n\\end{center}\n")
            rows.clear()
    buf = []
    for ln in text.splitlines():
        cells = [c.strip() for c in re.split(r"\s{2,}|\t", ln.strip()) if c.strip()]
        if len(cells) >= 2 and (rows or len(cells) >= 3):
            if buf:
                out.append(render_text("\n".join(buf)))
                buf = []
            rows.append(cells)
        else:
            flush()
            buf.append(ln)
    flush()
    if buf:
        out.append(render_text("\n".join(buf)))
    return "".join(out)


def render_detailed(exp: Experiment) -> str:
    """Section-by-section walk-through of the complete content of every source used."""
    L = ["\\subsection{Detailed explanation from the source PDFs}\n",
         "The complete content of each source file for this experiment, in the order it appears in the file, "
         "organised under its own headings. Near-identical copies are shown once.\n"]
    kept: list[str] = []
    for c in sorted(exp.chunks, key=lambda c: (c.ocr, c.source)):
        body = squash(c.text)
        dup = next((k for k in kept if difflib.SequenceMatcher(None, k[:6000], body[:6000]).quick_ratio() > 0.95 and
                    difflib.SequenceMatcher(None, k[:6000], body[:6000]).ratio() > 0.9), None)
        title = "\\texttt{" + fname(c.source) + "}" + (f" (pp.~{c.pages[0]}--{c.pages[1]})" if c.pages[1] > c.pages[0] else "")
        if dup is None and c.ocr:
            dup = next((k for k in kept if difflib.SequenceMatcher(None, k[:6000], body[:6000]).ratio() > 0.8), None)
        if dup is not None:
            L.append("\\subsubsection*{" + title + "}\n" + ("Scanned copy (OCR) of" if c.ocr else "Same content as") +
                     " a file shown above.\n")
            continue
        kept.append(body)
        L.append("\\subsubsection*{" + title + "}\n")
        if c.ocr:
            L.append("{\\footnotesize\\textit{Text recovered by OCR from a scanned PDF; minor recognition errors are possible.}}\n\n")
        for name, text in ordered_fields(c.text):
            if not text.strip():
                continue
            L.append("\\paragraph{" + tex(FIELD_TITLES.get(name, name.title())) + ".}\n" + render_block(text, name))
    return "\n".join(L)


def ordered_fields(text: str) -> list[tuple[str, str]]:
    """Like parse_fields, but keeps every block in document order (repeated headings kept)."""
    blocks: list[tuple[str, list[str]]] = [("_intro", [])]
    for ln in text.splitlines():
        stripped = ln.strip()
        hit = None
        if 0 < len(stripped) <= 120:
            for f, rx in HEAD_RE:
                m = rx.match(stripped)
                if m:
                    rest = (m.group("rest") or "").strip()
                    if not rest and len(stripped) > 45:
                        continue
                    if not rest and not re.search(r"[:\-–]\s*$", stripped) and stripped.split()[0][0].islower():
                        continue
                    hit = (f, rest)
                    break
        if hit:
            blocks.append((hit[0], [hit[1]] if hit[1] else []))
        else:
            blocks[-1][1].append(ln)
    return [(k, "\n".join(v).strip("\n")) for k, v in blocks if re.search(r"[A-Za-z0-9]", "\n".join(v))]


def fname(name: str) -> str:
    """File name for \\texttt with break points after underscores and hyphens."""
    return tex(name).replace(r"\_", r"\_\allowbreak{}").replace("-", r"-\allowbreak{}")


def render_experiment(exp: Experiment) -> tuple[str, dict]:
    T = all_text(exp)
    aim_t, _ = best(exp, "aim")
    aim = sentences(squash(aim_t))[:2] if aim_t else find_sentences(T, r"\baim\b|\bobjective\b|\bto determine\b|\bto study\b", 1)
    principle, _ = best(exp, "principle")
    if not principle:
        intro = max((c.fields.get("_intro", "") for c in exp.chunks), key=len, default="")
        principle = intro if len(intro) > 200 else ""
    mechanism, _ = best(exp, "mechanism")
    appchem, _ = best(exp, "apparatus_chem")
    app_t, _ = best(exp, "apparatus")
    chem_t, _ = best(exp, "chemicals")
    app_list = items(app_t) if app_t else []
    chem_list = items(chem_t) if chem_t else []
    combined = items(appchem) if appchem else []
    app_words = find_words(app_t or appchem or T, APPARATUS_WORDS)
    chem_words = find_words(chem_t or appchem or T, CHEMICAL_WORDS)
    proc_t, _ = best(exp, "procedure")
    obs_t, _ = best(exp, "observation")
    calc_t, _ = best(exp, "calculation")
    graph_t, _ = best(exp, "graph")
    res_t, _ = best(exp, "result")
    prec_t, _ = best(exp, "precaution")
    err_t, _ = best(exp, "error")

    graphs = items(graph_t) if graph_t else find_sentences(T, r"\bplot|\bgraph|\bversus\b|\bvs\.?\b|\bslope\b|\bintercept\b", 4)
    prec = items(prec_t) if prec_t else []
    errs = items(err_t) if err_t else []
    fs = formulas("\n".join([calc_t, principle, mechanism, obs_t, res_t])) or formulas(T)
    unit_list = units("\n".join([calc_t, obs_t, res_t, principle])) or units(T)
    header = table_header(obs_t) or table_header(calc_t)

    L = []
    L.append(f"\\section{{{tex(exp.title)}}}\\label{{sec:{exp.key}}}\n")
    srcs = sorted({c.source for c in exp.chunks})
    L.append("\\noindent\\textit{Sources:} " + "; ".join(r"\texttt{" + fname(s) + "}" for s in srcs) + ".\n")

    L.append("\\subsection{Aim and principle}\n\\paragraph{Aim.} " +
             (tex(" ".join(aim)) if aim else MISSING + ".") + "\n")
    L.append("\\paragraph{Principle / theory.}\n" + render_text(limit_chars(principle, 6000)))
    if mechanism:
        L.append("\\paragraph{Mechanism.}\n" + render_text(limit_chars(mechanism, 4000)))
    L.append("\\paragraph{Key equations (as given in the source).}\n" + render_formulas(fs))

    L.append("\\subsection{Apparatus and chemicals}\n")
    if combined:
        L.append("\\paragraph{Apparatus and chemicals (source list).}\n" + render_items(combined))
    L.append("\\paragraph{Apparatus.}\n" + (render_items(app_list) if app_list else
             (("Items named in the source text: " + tex(", ".join(app_words)) + ".\n") if app_words else MISSING + ".\n")))
    L.append("\\paragraph{Chemicals.}\n" + (render_items(chem_list) if chem_list else
             (("Chemicals named in the source text: " + tex(", ".join(chem_words)) + ".\n") if chem_words else MISSING + ".\n")))

    L.append("\\subsection{Procedure}\n" + render_items(items(proc_t), "enumerate") if proc_t
             else "\\subsection{Procedure}\n" + MISSING + ".\n")

    L.append("\\subsection{Observation table template and calculations}\n")
    if header:
        n = len(header)
        col = r"p{\dimexpr(\linewidth-%d\tabcolsep)/%d\relax}" % (2 * n, n)
        L.append("\\begin{center}\\small\n\\begin{tabular}{@{}" + col * n + "@{}}\n\\toprule\n" +
                 " & ".join(tex(h) for h in header) + " \\\\\n\\midrule\n" +
                 ("&" * (n - 1) + " \\\\[4pt]\n") * 6 + "\\bottomrule\n\\end{tabular}\n\\end{center}\n"
                 "{\\footnotesize Column headings taken from the source table; rows left blank for your readings.}\n")
    else:
        L.append("\\paragraph{Table template.} Table layout: " + MISSING + ".\n")
    if obs_t:
        L.append("\\paragraph{Observation notes (source).}\n" + render_text(limit_chars(obs_t, 3000)))
    L.append("\\paragraph{Calculations.}\n" + render_text(limit_chars(calc_t, 4000)) if calc_t
             else "\\paragraph{Calculations.} " + MISSING + ".\n")
    L.append("\\paragraph{Units found in the source.} " +
             (", ".join(tex(u) for u in unit_list) + "." if unit_list else MISSING + ".") + "\n")

    L.append("\\subsection{Graphs to plot and expected result}\n\\paragraph{Graphs.}\n" + render_items(graphs))
    L.append("\\paragraph{Expected result.}\n" + render_text(limit_chars(res_t, 2000)))
    figs = [f for f in exp.figures if (FIGS / f["file"]).exists()][:4]
    for f in figs:
        L.append("\\begin{figure}[htbp]\\centering\n\\includegraphics[width=0.6\\linewidth,height=0.35\\textheight,"
                 "keepaspectratio]{figs/" + f["file"] + "}\n\\caption*{\\footnotesize Figure from \\texttt{" +
                 fname(f["source"]) + "}, page " + str(f["page"]) + ".}\n\\end{figure}\n")

    L.append("\\subsection{Precautions and sources of error}\n\\paragraph{Precautions.}\n" + render_items(prec) +
             "\\paragraph{Sources of error.}\n" + render_items(errs))

    qa = viva(exp, aim, principle, chem_list or chem_words, app_list or app_words, graphs, fs, prec, res_t)
    L.append("\\subsection{Likely viva questions}\n")
    if qa:
        L.append("\\begin{enumerate}[leftmargin=*,itemsep=4pt]\n")
        for q, a in qa:
            a_tex = a if a.startswith("$") or a.startswith("\\ce") else tex(a)
            L.append(f"  \\item \\textbf{{{tex(q)}}}\\\\ {a_tex}\n")
        L.append("\\end{enumerate}\n")
    else:
        L.append(MISSING + ".\n")

    L.append(render_detailed(exp))

    def first(x):
        return x[0] if x else MISSING
    eq0 = next(((k, v) for k, v in fs if k != "raw"), fs[0] if fs else None)
    eq_tex = ("$" + eq0[1] + "$" if eq0[0] == "math" else eq0[1] if eq0[0] == "ce" else tex(eq0[1])) if eq0 else MISSING
    psent = sentences(squash(principle))
    revision = [
        ("Aim", tex(first(aim))),
        ("Principle", tex(first(psent))),
        ("Key equation", eq_tex),
        ("Graph", tex(first(graphs))),
        ("Remember", tex(first(prec) if prec else first(sentences(squash(res_t))))),
    ]
    L.append("\\begin{tcolorbox}[colback=blue!3,colframe=blue!50!black,title=Quick revision: " + tex(exp.title) + "]\n"
             "\\begin{enumerate}[leftmargin=*,itemsep=1pt]\n" +
             "".join(f"  \\item \\textbf{{{k}:}} {v}\n" for k, v in revision) + "\\end{enumerate}\n\\end{tcolorbox}\n")

    short_aim = first(aim)
    if len(short_aim) > 160:
        short_aim = short_aim[:157].rsplit(" ", 1)[0] + "..."
    glance = {"title": exp.title, "key": exp.key, "aim": short_aim, "formula": eq_tex,
              "units": ", ".join(unit_list[:5]) or MISSING, "time": duration(T)}
    return "\n".join(L), glance


def render_file_summaries(manifest: dict, by_file: dict[str, list[Chunk]], usage: dict[str, list[str]]) -> str:
    """One extractive summary per uploaded (unique) PDF: what it contains, its key points and equations."""
    L = ["\\section{Summary of each uploaded PDF}\\label{sec:file-summaries}\n",
         "One summary per unique PDF. Every sentence and equation below is taken from that file; "
         "the full content is in the \\emph{Detailed explanation} subsection of the experiment it belongs to.\n"]
    for f in manifest["files"]:
        chunks = by_file.get(f["name"], [])
        date, title = file_title(f["name"])
        L.append("\\subsection{" + tex(title or f["stem"]) + (f" ({date})" if date else "") + "}\n")
        dups = f.get("duplicates", [])
        meta = [f"\\textbf{{File:}} \\texttt{{{fname(f['name'])}}}",
                f"\\textbf{{Pages:}} {f['pages']}",
                f"\\textbf{{Text extracted with:}} {tex(f['method'])}",
                f"\\textbf{{Figures extracted:}} {len(f.get('figures', []))}",
                "\\textbf{Covered in:} " + tex("; ".join(dict.fromkeys(usage.get(f["name"], ["(no text)"]))))]
        if dups:
            meta.append("\\textbf{Identical copies:} " + "; ".join("\\texttt{" + fname(d) + "}" for d in dups))
        L.append("\\begin{itemize}[leftmargin=*,itemsep=0pt]\n" + "".join(f"  \\item {m}\n" for m in meta) + "\\end{itemize}\n")
        if not chunks:
            L.append("No text could be extracted from this file.\n")
            continue
        text = "\n".join(c.text for c in chunks)
        heads = []
        points = []
        for c in chunks:
            if c.title and c.title not in ("_front",) and len(chunks) > 1:
                heads.append(c.title)
            for name, block in ordered_fields(c.text):
                label = FIELD_TITLES.get(name, name.title())
                if name != "_intro" and label not in heads:
                    heads.append(label)
                pool = items(block) if name in LIST_FIELDS or name == "viva" else sentences(squash(block))
                sent = [x for x in pool if len(x) > 15 and not is_formula(x)]
                if name == "_intro" and sent and len(sent[0].split()) < 6:
                    sent = sent[1:]
                if sent:
                    pt = sent[0] if len(sent[0]) < 320 else sent[0][:317].rsplit(" ", 1)[0] + "..."
                    points.append((label, pt))
        if heads:
            L.append("\\paragraph{Contents.} " + tex(", ".join(dict.fromkeys(heads))) + ".\n")
        if points:
            L.append("\\paragraph{Key points.}\n\\begin{itemize}[leftmargin=*,itemsep=1pt]\n" +
                     "".join(f"  \\item \\textit{{{tex(k)}:}} {tex(v)}\n" for k, v in points[:14]) + "\\end{itemize}\n")
        fs = formulas(text, limit=6)
        if fs:
            L.append("\\paragraph{Equations in this file.}\n" + render_formulas(fs))
    return "\n".join(L)


def render_front(chunks: list[Chunk]) -> str:
    L = ["\\section{Lab manual: general information}\\label{sec:manual-general}\n",
         "Text that appears in the lab manuals before the first experiment "
         "(instructions, index, general information), reproduced from the source.\n"]
    kept: list[str] = []
    for c in chunks:
        body = squash(c.text)
        if any(difflib.SequenceMatcher(None, k[:4000], body[:4000]).quick_ratio() > 0.95 for k in kept):
            L.append("\\paragraph{\\texttt{" + fname(c.source) + "}.} Same content as above.\n")
            continue
        kept.append(body)
        L.append("\\subsection*{From \\texttt{" + fname(c.source) + "} (pp.~" + f"{c.pages[0]}--{c.pages[1]}" + ")}\n" +
                 render_text(limit_chars(c.text, 8000)))
    return "\n".join(L)


# --------------------------------------------------------------------------
def main() -> int:
    mpath = WORK / "manifest.json"
    if not mpath.exists():
        print("ERROR: run extract.py first")
        return 1
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    SECTIONS.mkdir(parents=True, exist_ok=True)
    for old in SECTIONS.glob("*.tex"):
        old.unlink()

    exps: dict[str, Experiment] = {k["key"]: Experiment(k["key"], k["title"]) for k in KNOWN}
    front: list[Chunk] = []
    usage: dict[str, list[str]] = {}
    by_file: dict[str, list[Chunk]] = {}

    def add(key_hint: str | None, title: str, chunk: Chunk):
        key = key_hint
        if key is None:  # new experiment, merge by similar title
            norm = re.sub(r"[^a-z0-9 ]", "", title.lower()).strip() or chunk.source.lower()
            for k, e in exps.items():
                if difflib.SequenceMatcher(None, norm, re.sub(r"[^a-z0-9 ]", "", e.title.lower())).ratio() > 0.8:
                    key = k
                    break
            if key is None:
                key = re.sub(r"[^a-z0-9]+", "-", norm)[:40].strip("-") or f"exp-{len(exps) + 1}"
                exps[key] = Experiment(key, title.title() if title.isupper() else title)
        exps[key].chunks.append(chunk)
        usage.setdefault(chunk.source, []).append(exps[key].title)
        return exps[key]

    for f in manifest["files"]:
        raw = (ROOT / f["text"]).read_text(encoding="utf-8", errors="replace")
        pages = clean_pages(raw)
        _, ftitle = file_title(f["name"])
        # any PDF holding several experiments (lab manuals, record books, combined notes) is split
        parts = split_manual(pages)
        is_manual = bool(re.search(r"man[u]?al|lan[- ]?manual", ftitle, re.I)) or \
            len([p for p in parts if p[0] != "_front"]) >= 2
        if not is_manual:
            parts = [(ftitle, (1, len(pages)), "\n".join(pages))]
        if not "".join(p[2] for p in parts).strip():
            usage.setdefault(f["name"], []).append("(no text could be extracted)")
            continue
        for title, prange, text in parts:
            if not text.strip():
                continue
            flds, n = parse_fields(text)
            ch = Chunk(f["name"], title, prange, text, flds, n, ocr=(f.get("method") == "OCR"))
            by_file.setdefault(f["name"], []).append(ch)
            if title == "_front":
                if len(squash(text)) > 80:
                    front.append(ch)
                    usage.setdefault(f["name"], []).append("Lab manual: general information")
                continue
            key = classify(title if not is_manual else title, text if is_manual or not title else text)
            if not is_manual and key is None:
                key = classify("", text)
            exp = add(key, title or ftitle, ch)
            for fig in f.get("figures", []):
                if prange[0] <= fig["page"] <= prange[1]:
                    exp.figures.append({**fig, "source": f["name"]})

    order = [k["key"] for k in KNOWN] + sorted(k for k in exps if k not in {x["key"] for x in KNOWN})
    inputs, glance = [], []
    for key in order:
        e = exps[key]
        if not e.chunks:
            continue
        body, g = render_experiment(e)
        (SECTIONS / f"{key}.tex").write_text(body, encoding="utf-8")
        inputs.append(key)
        glance.append(g)
        print(f"  {key}: {len(e.chunks)} chunk(s) from {len({c.source for c in e.chunks})} file(s)")
    if front:
        (SECTIONS / "manual-general.tex").write_text(render_front(front), encoding="utf-8")
        inputs.append("manual-general")

    (SECTIONS / "file-summaries.tex").write_text(render_file_summaries(manifest, by_file, usage), encoding="utf-8")
    inputs.insert(0, "file-summaries")
    (SECTIONS / "experiments.tex").write_text(
        "".join(f"\\clearpage\n\\input{{sections/{k}}}\n" for k in inputs), encoding="utf-8")

    G = ["\\section*{All experiments at a glance}\\addcontentsline{toc}{section}{All experiments at a glance}\n",
         "{\\small\\renewcommand{\\arraystretch}{1.25}\n\\noindent\\begin{tabular}{@{}>{\\raggedright\\arraybackslash}p{0.17\\linewidth}"
         ">{\\raggedright\\arraybackslash}p{0.27\\linewidth}>{\\raggedright\\arraybackslash}p{0.25\\linewidth}"
         ">{\\raggedright\\arraybackslash}p{0.12\\linewidth}>{\\raggedright\\arraybackslash}p{0.11\\linewidth}@{}}\n\\toprule\n"
         "\\textbf{Experiment} & \\textbf{Aim} & \\textbf{Key formula} & \\textbf{Units} & \\textbf{Time} \\\\\n\\midrule\n"]
    for g in glance:
        G.append(f"\\hyperref[sec:{g['key']}]{{{tex(g['title'])}}} & {tex(g['aim'])} & "
                 f"\\begin{{minipage}}[t]{{\\linewidth}}\\raggedright\\footnotesize {g['formula']}\\end{{minipage}} & "
                 f"{tex(g['units'])} & {tex(g['time'])} \\\\\n\\addlinespace\n")
    G.append("\\bottomrule\n\\end{tabular}}\n")
    (SECTIONS / "glance.tex").write_text("".join(G), encoding="utf-8")

    A = ["\\section{Source files}\\label{app:sources}\n",
         f"{len(manifest['files'])} unique PDF(s) were processed "
         f"({len(manifest['files']) + len(manifest['duplicates'])} files received). "
         "Byte-identical duplicates were detected with SHA-256 and processed once.\n",
         "\\subsection*{Unique files}\n{\\footnotesize\n\\begin{longtable}{@{}p{0.52\\linewidth}p{0.06\\linewidth}"
         "p{0.13\\linewidth}p{0.23\\linewidth}@{}}\n\\toprule\nFile & Pages & Text method & Used in \\\\\n\\midrule\\endhead\n"]
    for f in manifest["files"]:
        used = "; ".join(dict.fromkeys(usage.get(f["name"], ["(not classified)"])))
        A.append(f"\\texttt{{{fname(f['name'])}}}\\newline SHA-256: \\texttt{{{f['sha256'][:16]}\\ldots}} & "
                 f"{f['pages']} & {tex(f['method'])} & {tex(used)} \\\\\n\\addlinespace\n")
    A.append("\\bottomrule\n\\end{longtable}}\n\\subsection*{Duplicates (not processed again)}\n")
    if manifest["duplicates"]:
        A.append("{\\footnotesize\n\\begin{longtable}{@{}p{0.48\\linewidth}p{0.48\\linewidth}@{}}\n\\toprule\n"
                 "Duplicate file & Identical to \\\\\n\\midrule\\endhead\n")
        for d in manifest["duplicates"]:
            A.append(f"\\texttt{{{fname(d['name'])}}} & \\texttt{{{fname(d['same_as'])}}} \\\\\n\\addlinespace\n")
        A.append("\\bottomrule\n\\end{longtable}}\n")
    else:
        A.append("No byte-identical duplicates were found.\n")
    (SECTIONS / "sources.tex").write_text("".join(A), encoding="utf-8")

    missing = [f["name"] for f in manifest["files"] if f["name"] not in usage]
    if missing:
        print("WARNING: files not used:", missing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
