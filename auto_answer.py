import json
import re
import base64
import sys

from selenium.webdriver.common.by import By
from selenium.webdriver.edge.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 引入字体解码模块（方式B：纯Python本地调用）
sys.path.append(r"chaoxing_solution_of_font_confusion")
from chaoxing_solution_of_font_confusion.glyfSearch import translate


def _extract_ttf_base64(wd: WebDriver) -> str | None:
    html = wd.execute_script("return document.documentElement.outerHTML")
    m = re.search(r"font-ttf;charset=utf-8;base64,([^')]+)", html)
    return m.group(1) if m else None


def _build_decoder(table):
    src_chars, dst_chars = table
    mapping = dict(zip(src_chars, dst_chars))

    def decode_text(text: str) -> str:
        if not text or not mapping:
            return text
        for s, d in mapping.items():
            text = text.replace(s, d)
        return text

    return decode_text


def _normalize_question(s: str) -> str:
    # 去掉可能的题号前缀，如 "3、"
    s = re.sub(r"^\s*\d+、\s*", "", s)
    # 去掉题型标签前缀
    s = re.sub(r"^【(单选题|多选题|判断题)】", "", s)
    # 统一括号
    s = s.replace("（", "(").replace("）", ")")
    # 移除所有空白（含换行），避免页面排版影响匹配
    s = re.sub(r"\s+", "", s)
    return s


def answer(wd: WebDriver) -> WebDriver:
    # 进入包含题目的最内层 iframe
    wd.switch_to.frame(wd.find_element(By.TAG_NAME, "iframe"))
    wd.switch_to.frame(wd.find_element(By.TAG_NAME, "iframe"))
    wd.switch_to.frame(wd.find_element(By.TAG_NAME, "iframe"))

    # 构建字体解码器
    ttf_b64 = _extract_ttf_base64(wd)
    decoder = (lambda x: x)
    if ttf_b64:
        font_bytes = base64.b64decode(ttf_b64)
        decoder = _build_decoder(translate(font_bytes))

    # 等待题目与选项渲染
    wait = WebDriverWait(wd, 10)
    ti_mu = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".TiMu .font-cxsecret")))
    option_els = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, "ul.Zy_ulTop li.before-after-checkbox")))

    # 解码题干
    question_raw = ti_mu.text
    question_decoded = decoder(question_raw)

    # 读取题库
    with open(r"data/questions.json", "r", encoding="utf-8") as f:
        question_bank = json.load(f)

    # 匹配题干
    q_key = _normalize_question(question_decoded)
    matched = None
    for q in question_bank:
        if _normalize_question(q.get("question", "")) == q_key:
            matched = q
            break

    if not matched:
        raise RuntimeError(f"题干未在题库匹配：{q_key}")

    # 解析题库中的正确选项文本（按选项文本点击）
    # options 形如：["A、物色","B、景色",...]; answer 形如 "AB"
    letter_to_text = {}
    for opt in matched.get("options", []):
        m = re.match(r"\s*([A-D])、\s*(.+)\s*", opt)
        if m:
            letter_to_text[m.group(1)] = m.group(2)

    target_letters = list(matched.get("answer", ""))
    target_texts = set(letter_to_text.get(l, "") for l in target_letters)
    target_texts = {t for t in target_texts if t}

    if not target_texts:
        raise RuntimeError(f"未解析到正确选项文本：{matched}")

    # 遍历页面选项，按解码后的选项文本匹配并点击
    for li in option_els:
        try:
            a_text_raw = li.find_element(By.CSS_SELECTOR, "a.fl.after").text
        except Exception:
            a_text_raw = li.text  # 兜底：取整个 li 的文本
        a_text_decoded = decoder(a_text_raw).strip()

        if a_text_decoded in target_texts:
            # 使用 JS 点击，规避有时 Selenium 原生点击不触发的问题
            wd.execute_script("arguments[0].click();", li)

    return wd