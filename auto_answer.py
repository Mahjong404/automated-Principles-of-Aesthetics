import json
import re
import base64
import sys

from selenium.webdriver.common.by import By
from selenium.webdriver.edge.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

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

    # 读取题库
    with open(r"data/questions.json", "r", encoding="utf-8") as f:
        question_bank = json.load(f)

    # 等待所有题目块并逐题处理
    wait = WebDriverWait(wd, 10)
    question_blocks = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".TiMu")))

    for qb in question_blocks:
        # 题干容器
        try:
            title_el = qb.find_element(By.CSS_SELECTOR, ".Zy_TItle .font-cxsecret")
        except Exception:
            els = qb.find_elements(By.CSS_SELECTOR, ".font-cxsecret")
            title_el = els[0] if els else None

        if not title_el:
            continue

        # 解码题干并匹配题库
        question_raw = title_el.text
        question_decoded = decoder(question_raw)
        q_key = _normalize_question(question_decoded)

        matched = None
        for q in question_bank:
            if _normalize_question(q.get("question", "")) == q_key:
                matched = q
                break

        if not matched:
            # 未匹配则跳过该题
            continue

        # 从题库解析正确选项文本
        letter_to_text = {}
        if matched.get("type") == "判断题":
            # 单独处理判断题
            ans = matched.get("answer", "")
            if ans == "√":
                target_texts = {"对"}
            elif ans == "X":
                target_texts = {"错"}
            else:
                target_texts = set()
        else:
            # 单选题和多选题
            for opt in matched.get("options", []):
                m = re.match(r"\s*([A-D])、\s*(.+)\s*", opt)
                if m:
                    letter_to_text[m.group(1)] = m.group(2)

            target_letters = list(matched.get("answer", ""))
            target_texts = set(letter_to_text.get(l, "") for l in target_letters)

        target_texts = {t for t in target_texts if t}
        if not target_texts:
            continue

        # 遍历当前题块选项并点击匹配文本
        option_els = qb.find_elements(By.CSS_SELECTOR, "ul.Zy_ulTop li")
        for li in option_els:
            try:
                a_text_raw = li.find_element(By.CSS_SELECTOR, "a.fl.after").text
            except Exception:
                a_text_raw = li.text
            a_text_decoded = decoder(a_text_raw).strip()

            if a_text_decoded in target_texts:
                # 避免重复点击导致反选，检测 aria-checked
                checked = (li.get_attribute("aria-checked") == "true")
                if not checked:
                    wd.execute_script("arguments[0].click();", li)

    # 完成答题后点击提交按钮
    try:
        submit_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//*[self::a or self::button][contains(normalize-space(.), '提交')]")))
        wd.execute_script("arguments[0].click();", submit_btn)
    except TimeoutException:
        # 若不可点击，尝试滚动后重试
        try:
            wd.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            submit_btn = WebDriverWait(wd, 5).until(EC.element_to_be_clickable((By.XPATH, "//*[self::a or self::button][contains(normalize-space(.), '提交')]")))
            wd.execute_script("arguments[0].click();", submit_btn)
        except Exception:
            pass  # 提交失败，也将切换到 default_content 并返回

    # 切换回主文档处理弹窗
    wd.switch_to.default_content()

    # 处理提交后的确认弹窗
    try:
        pop_wait = WebDriverWait(wd, 5)
        pop_content_element = pop_wait.until(EC.visibility_of_element_located((By.ID, "popcontent")))
        pop_content = pop_content_element.text

        if "确认提交？" in pop_content:
            confirm_button = pop_wait.until(EC.element_to_be_clickable((By.ID, "popok")))
            wd.execute_script("arguments[0].click();", confirm_button)
        elif "您还有未做完的" in pop_content:
            cancel_button = pop_wait.until(EC.element_to_be_clickable((By.ID, "popno")))
            wd.execute_script("arguments[0].click();", cancel_button)
    except TimeoutException:
        # 没有找到确认弹窗，这可能是正常的，比如所有题目都答对并直接提交成功
        pass
    except Exception:
        # 处理弹窗时发生其他异常
        pass

    return wd