import base64
import binascii
import json
import pickle
import re
import sqlite3
import unicodedata
from contextlib import closing
from functools import lru_cache
from hashlib import md5, sha1
from io import BytesIO
from pathlib import Path

from fontTools.ttLib import TTFont, TTLibError


FONT_TABLES = ("HanSansCN_glyfHashedTables.pkl", "HanSansCN_CmapTables.pkl")
FONT_DATA = re.compile(r"data:[^,\s'\"()]*?;base64,([A-Za-z0-9+/=]+)", re.I)
OPTION = re.compile(r"^([A-Z])\s*[、.．:：)）]\s*(.+)$", re.S)
QUESTION_PREFIX = re.compile(r"^(?:\s*(?:\d+[、.．)）]|[【\[(（](?:单选题|多选题|判断题)[】\])）]))+\s*")
JUDGMENTS = {
    "√": "对", "✓": "对", "对": "对", "正确": "对",
    "x": "错", "×": "错", "✗": "错", "错": "错", "错误": "错",
}


class CourseError(Exception):
    pass


class UnusedGlyph:
    pass


class TableReader(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) == ("fontTools.ttLib.tables._g_l_y_f", "Glyph"):
            return UnusedGlyph
        raise pickle.UnpicklingError("字体映射表不应包含可执行对象")


@lru_cache(maxsize=2)
def font_tables(directory):
    try:
        with (directory / FONT_TABLES[0]).open("rb") as file:
            hashes = {digest: glyph for glyph, digest in TableReader(file).load()[0]}
        with (directory / FONT_TABLES[1]).open("rb") as file:
            codepoints = {
                glyph: code for code, glyph in TableReader(file).load()
                if 0x4E00 <= code <= 0x9FA5
            }
    except (OSError, EOFError, pickle.UnpicklingError, TypeError, ValueError, IndexError) as error:
        raise CourseError("字体映射表缺失或无法读取") from error
    return hashes, codepoints


def font_decoder(font_css, font_dir):
    encoded = FONT_DATA.search(font_css)
    if not encoded:
        return {}
    hashes, codepoints = font_tables(font_dir)
    try:
        with TTFont(BytesIO(base64.b64decode(encoded[1], validate=True))) as font:
            glyphs = font["glyf"].glyphs
            mapping = {}
            for code, name in font.getBestCmap().items():
                data = getattr(glyphs[name], "data", b"")
                digest = (sha1(data).digest(), md5(data).digest())
                target = codepoints.get(hashes.get(digest))
                if target is not None:
                    mapping[code] = target
    except (binascii.Error, TTLibError, KeyError, TypeError, ValueError) as error:
        raise CourseError("无法读取当前测验的内嵌字体") from error
    if not mapping:
        raise CourseError("当前测验字体没有匹配到映射")
    return mapping


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def question_key(text):
    return compact(QUESTION_PREFIX.sub("", text))


def option_key(text):
    match = OPTION.fullmatch(unicodedata.normalize("NFKC", text).strip())
    return compact(match[2] if match else text)


class QuestionBank:
    def __init__(self, path: Path | None):
        self.questions = {}
        self.verified = {}
        self.count = 0
        if path is None:
            return
        try:
            entries = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(entries, list) or not entries:
                raise ValueError
            for row in entries:
                stem = question_key(row["question"])
                if not stem or row["type"] not in {"单选题", "多选题", "判断题"}:
                    raise ValueError
                if not isinstance(row["answer"], str) or not isinstance(row["options"], list):
                    raise ValueError
                aliases = row.get("aliases", [])
                if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
                    raise ValueError
                option_aliases = row.get("option_aliases", {})
                if not isinstance(option_aliases, dict) or any(
                    not isinstance(target, str) or not option_key(alias) or not option_key(target)
                    for alias, target in option_aliases.items()
                ):
                    raise ValueError
                for key in dict.fromkeys([stem, *(question_key(alias) for alias in aliases)]):
                    if not key:
                        raise ValueError
                    self.questions.setdefault(key, []).append(row)
            self.count = len(entries)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CourseError("题库缺失或格式错误") from error

    @staticmethod
    def signature(question, decoder):
        stem = question_key(question["stem"].translate(decoder))
        options = [option_key(option["text"].translate(decoder)) for option in question["options"]]
        return stem, question["type"], options

    def load_verified(self, path, course):
        if not course or not path.is_file():
            return
        with closing(sqlite3.connect(path)) as database:
            table = database.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='verified_answers'"
            ).fetchone()
            if table:
                self.verified.update(database.execute(
                    "SELECT fingerprint, record FROM verified_answers WHERE course = ?", (course,)
                ))

    def record_verified(self, path, course, questions, choices):
        if not course:
            raise CourseError("无法确认已判对答案所属的课程")
        if not choices:
            return
        entries = []
        for index, selected in choices.items():
            question = questions[index - 1]
            stem, kind, options = self.signature(question, {})
            fingerprint = json.dumps([stem, kind, sorted(options)], ensure_ascii=False)
            record = json.dumps({
                "question": question["stem"], "type": kind, "options": options,
                "selected": [options[i] for i in selected],
            }, ensure_ascii=False)
            entries.append((course, fingerprint, record))
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as database, database:
            database.execute("""CREATE TABLE IF NOT EXISTS verified_answers (
                course TEXT NOT NULL, fingerprint TEXT NOT NULL, record TEXT NOT NULL,
                PRIMARY KEY (course, fingerprint)
            )""")
            database.executemany(
                "INSERT INTO verified_answers VALUES (?, ?, ?) "
                "ON CONFLICT(course, fingerprint) DO UPDATE SET record=excluded.record", entries
            )
        self.verified.update((fingerprint, record) for _, fingerprint, record in entries)

    def match(self, question, decoder):
        stem, kind, options = self.signature(question, decoder)
        fingerprint = json.dumps([stem, kind, sorted(options)], ensure_ascii=False)
        record = self.verified.get(fingerprint)
        if record:
            selected = json.loads(record)["selected"]
            if all(options.count(option) == 1 for option in selected):
                return tuple(options.index(option) for option in selected)
            return None
        rows = self.questions.get(stem, [])
        if len(rows) != 1:
            return None
        row = rows[0]
        if question["type"] and question["type"] != row["type"]:
            return None
        if row["type"] == "判断题":
            options = [JUDGMENTS.get(option.lower(), option) for option in options]
            if sorted(options) != ["对", "错"]:
                return None
            expected = [JUDGMENTS.get(compact(row["answer"]).lower())]
        else:
            aliases = {option_key(alias): option_key(target)
                       for alias, target in row.get("option_aliases", {}).items()}
            options = [aliases.get(option, option) for option in options]
            letters = re.findall(r"[A-Z]", unicodedata.normalize("NFKC", row["answer"]).upper())
            by_letter = {}
            for text in row["options"]:
                match = OPTION.fullmatch(unicodedata.normalize("NFKC", text).strip())
                if match:
                    by_letter[match[1]] = compact(match[2])
            if sorted(options) != sorted(by_letter.values()):
                return None
            expected = [by_letter.get(letter) for letter in letters]
            if row["type"] == "单选题" and len(expected) != 1:
                return None
        if not expected or any(not item or options.count(item) != 1 for item in expected):
            return None
        if len(set(expected)) != len(expected):
            return None
        return tuple(options.index(item) for item in expected)
