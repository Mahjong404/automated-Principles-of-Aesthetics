import argparse
import sqlite3
import sys
import time
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass
from pathlib import Path

from course_notice import notify

try:
    from playwright.sync_api import Error, sync_playwright

    from course_background import background_playback
    from course_browser import (
        CourseBrowser, SubmissionCancelled, course_choices, exam_page, open_course_choice, space_page,
    )
    from course_data import CourseError, QuestionBank, font_decoder, question_key
    from course_journal import (
        begin_submission, find_attempt, pending_attempt, release_attempt, reserve_attempt, resolve_attempt,
    )
    from course_llm import CourseLLM
    from course_login import authenticated_page, course_id, prompt_credentials, read_account, reusable_context
    from course_report import RunReport, safe_text
except ModuleNotFoundError as error:
    if error.name in {"playwright", "fontTools"} and __name__ == "__main__":
        raise SystemExit("请先运行 python -m pip install -r requirements.txt") from None
    raise


ROOT = Path(__file__).resolve().parent
DEFAULT_QUESTIONS = ROOT / "data" / "questions.json"
FONT_DIR = ROOT / "chaoxing_solution_of_font_confusion"
LOGIN_CONFIG = ROOT / "real_data" / "account.json"
JOURNAL = ROOT / "real_data" / "submissions.sqlite3"
VIDEO_TIMEOUT = 3600
GRADE_TIMEOUT = 6

STATUS = {
    "catalog": "课程目录",
    "video": "视频待完成",
    "video_done": "视频任务已完成",
    "quiz": "章节测验待完成",
    "quiz_done": "章节测验已完成",
    "exam": "考试答题页",
    "locked": "任务未完成，平台阻止跳转",
}


@dataclass(frozen=True)
class ReviewRequired:
    message: str
    failed: bool = False
    pending: bool = False


def decoded_questions(browser, state, font_dir):
    data = browser.quiz_data(state)
    decoder = font_decoder(data["font"], font_dir)
    for question in data["questions"]:
        question["stem"] = question["stem"].translate(decoder)
        for option in question["options"]:
            option["text"] = option["text"].translate(decoder)
    return data["questions"]


def record_feedback(browser, review, bank, font_dir, journal, scope, questions, suggestions, recovered=False):
    deadline = time.monotonic() + GRADE_TIMEOUT
    while True:
        rows = decoded_questions(browser, review, font_dir)
        grades = {}
        for index, choices in suggestions.items():
            question = questions[index - 1]
            candidates = [row for row in rows if question_key(row["stem"]) == question_key(question["stem"])
                          and (not row["type"] or row["type"] == question["type"])
                          and (not row["id"] or not question["id"] or row["id"] == question["id"])]
            grades[index] = ""
            if len(candidates) != 1:
                continue
            row = candidates[0]
            if recovered:
                expected = sorted(question_key(question["options"][i]["text"]) for i in choices)
                checked = sorted(question_key(option["text"]) for option in row["options"] if option["checked"])
                if any(option["checked"] is None for option in row["options"]) or checked != expected:
                    continue
            grades[index] = row["grade"]
        if all(grades.values()) or time.monotonic() >= deadline:
            break
        browser.page.wait_for_timeout(250)
    correct = {index: choices for index, choices in suggestions.items() if grades[index] == "correct"}
    bank.record_verified(journal, scope, questions, correct)
    if correct:
        print(f"已写入题库 {len(correct)} 道经平台判对的答案")
    wrong = [str(index) for index, grade in grades.items() if grade == "wrong"]
    unknown = [str(index) for index, grade in grades.items() if not grade]
    if wrong:
        return ReviewRequired(f"平台判错的 LLM 题号：{'、'.join(wrong)}。已停止，请人工核对。", True)
    if unknown:
        return ReviewRequired(f"无法确认 LLM 题号 {'、'.join(unknown)} 的逐题判分，请人工核对。", True, True)
    return review


def finish_submission(browser, review, bank, font_dir, journal, key, payload, report, recovered=False):
    if browser.submission_key(review) != key:
        raise CourseError("平台结果不属于本次提交的章节，请核对后再继续")
    report.note("平台已确认上次提交" if recovered else "平台已确认测验提交")
    if payload:
        report.enter("核对平台判分", "重新运行会先核对该章节的判分")
        result = record_feedback(
            browser, review, bank, font_dir, journal, payload["scope"], payload["questions"],
            {int(index): choices for index, choices in payload["suggestions"].items()}, recovered,
        )
        if isinstance(result, ReviewRequired):
            if not result.pending:
                resolve_attempt(journal, key, "review", result.message)
                report.enter("核对平台判分", "人工核对平台判错的题目，再打开后续章节继续")
            return result
        report.note("平台判分已核对，判对的模型答案已存入题库")
    resolve_attempt(journal, key, "complete")
    return review


def answer_quiz(browser, state, bank, font_dir, journal, course, settings, report):
    exam = state.kind == "exam"
    initial_url = browser.page.url
    initial_frame_url = state.frame.url
    report.enter("题库匹配", "核对当前题目后，从本章继续")
    questions = decoded_questions(browser, state, font_dir)
    plan = [bank.match(question, {}) for question in questions]
    missing = {index: question for index, (question, answer) in enumerate(zip(questions, plan), 1)
               if answer is None}
    print(f"题库核对 {len(plan) - len(missing)}/{len(plan)} 道")
    if not plan:
        raise CourseError("当前页没有可识别的完整题目")
    if any(not question["options"] or any(option["checked"] is None for option in question["options"])
           for question in questions):
        raise CourseError("无法核验所有题目的选项及选中状态")
    model = None
    if missing:
        if not settings or (isinstance(settings, dict) and not any(
            settings.get(key) for key in ("base_url", "api_key", "model")
        )):
            raise CourseError("题目匹配不完整，停在当前测验")
        model = CourseLLM(settings)
    save_only = exam or (model is not None and not model.auto_commit)
    report.enter("检查测验控件", "检查当前页的提交或暂存按钮后重新运行")
    button = browser.save_button(state.frame) if save_only else browser.submit_button(state.frame)
    count = button.count()
    if count != 1:
        label = "暂时保存" if save_only else "提交"
        raise CourseError(f"检测到 {count} 个{label}控件，需要唯一可用控件")
    suggestions = {}
    if model:
        report.enter("调用模型", "核对模型配置或在当前页人工处理待答题目")
        chapter = state.chapter["name"] if state.chapter else state.frame.title()
        suggestions = model.answer(course, chapter, missing)
        for index, selected in suggestions.items():
            plan[index - 1] = selected
        print(f"LLM 已为 {len(suggestions)} 道题提供答案")

    def current_questions():
        current = browser.inspect()
        if (current.kind != state.kind or current.frame != state.frame
                or browser.page.url != initial_url or current.frame.url != initial_frame_url
                or current.chapter != state.chapter):
            raise CourseError("当前测验页面已变化，请重新核对")
        return decoded_questions(browser, current, font_dir)

    def snapshot(rows):
        return [(q["id"], bank.signature(q, {})) for q in rows]

    report.enter("填写答案", "确认仍在同一测验后，从本章继续")
    if snapshot(current_questions()) != snapshot(questions):
        raise CourseError("题目或选项顺序已改变，请重新核对")
    if not save_only:
        key = browser.submission_key(state)
        scope, _ = browser.submission_identity(state)
        reserve_attempt(journal, key, scope, state.chapter["id"])
    try:
        browser.apply_choices(state, plan, questions)
        current = current_questions()
        if snapshot(current) != snapshot(questions):
            raise CourseError("题目或选项顺序已改变，请重新核对")
        for question, expected in zip(current, plan):
            checked = {index for index, option in enumerate(question["options"]) if option["checked"]}
            if checked != set(expected) or any(option["checked"] is None for option in question["options"]):
                raise CourseError("页面选中状态和预定答案不一致")
    except (CourseError, Error):
        if not save_only:
            release_attempt(journal, key)
        raise
    if save_only:
        report.enter("暂存测验", "在当前页人工核对暂存状态及答案")
        try:
            browser.confirm_draft(state)
        except CourseError as error:
            return ReviewRequired(str(error), True)
        result = "本页已暂存，请亲自核对答案。"
        if suggestions:
            result = f"本页已暂存，含 {len(suggestions)} 道 LLM 答案，请亲自核对。"
        report.note("平台已确认暂存")
        return ReviewRequired(result)
    payload = {"scope": course_id(initial_url) or course, "questions": questions, "suggestions": suggestions} if suggestions else {}
    report.enter("等待平台确认提交", "重新运行会先核对该章节的提交结果")
    begin_submission(journal, key, payload)
    try:
        review = browser.confirm_submission(state)
    except SubmissionCancelled:
        release_attempt(journal, key)
        report.enter("提交已取消", "核对未完成的题目后，从本章继续")
        raise
    except Error as error:
        raise CourseError("提交结果未确认，请先在浏览器中核对") from error
    return finish_submission(browser, review, bank, font_dir, journal, key, payload, report)


def show_state(state):
    print(STATUS[state.kind])
    if state.chapter:
        print(state.chapter["name"])
    if state.progress:
        print(f"任务点 {state.progress[0]}/{state.progress[1]}")
    if state.kind == "catalog":
        actionable = sum(chapter["pending"] > 0 and chapter["clickable"] and not chapter["locked"]
                         for chapter in state.chapters)
        print(f"已解锁的待办章节 {actionable} 个")


def show_preflight(browser, state, initial, bank, font_dir, attempt=None):
    print("\n课程体检")
    progress = state.progress or initial.progress
    print(f"  已完成 {progress[0]}/{progress[1]} 个任务点" if progress else "  当前页面未提供任务点总进度")
    chapter = state.chapter
    task = {
        "video": "视频", "video_done": "视频已完成，检查章节测验入口",
        "quiz": "章节测验", "quiz_done": "测验已完成，检查后续章节",
        "exam": "当前考试答题页", "locked": "当前任务锁定",
    }.get(state.kind, "暂无确认可进入的待办")
    if attempt:
        task = "核对上次提交"
    print(f"  下一项 {chapter['name'] + ' · ' if chapter else ''}{task}")
    chapters = initial.chapters or state.chapters
    if chapters:
        index = next((i for i, item in enumerate(chapters) if chapter and item["id"] == chapter["id"]), -1)
        locked = sum(item["locked"] for item in chapters[index + 1:])
        print(f"  目录显示后续有 {locked} 个锁定项" if locked else "  目录未显示后续锁定项")
    if state.kind in {"quiz", "exam"}:
        try:
            questions = decoded_questions(browser, state, font_dir)
        except CourseError as error:
            print(f"  题库预览不可用，{safe_text(error)}")
        else:
            matched = sum(bank.match(question, {}) is not None for question in questions)
            print(f"  题库匹配 {matched}/{len(questions)}，{len(questions) - matched} 题待核对")


def recover_submission(browser, state, attempt, bank, font_dir, journal, report):
    report.enter("恢复提交", "先在浏览器核对该章节的提交或判分状态，再重新运行")
    if attempt.phase == "review":
        report.enter("核对平台判分", "人工核对平台判错的题目，再打开后续章节继续")
        return ReviewRequired(attempt.payload["reason"], True)
    if state.kind in {"video", "video_done"}:
        state = browser.open_quiz()
    if state.kind != "quiz_done":
        return ReviewRequired("平台尚未显示已提交结果，已停在该章节等待人工确认。", True)
    if attempt.phase == "complete":
        return state
    return finish_submission(browser, state, bank, font_dir, journal, attempt.key, attempt.payload, report, recovered=True)


def run_course(browser, bank, font_dir, journal, course, settings=None, report=None):
    report = report or RunReport()
    report.enter("课程体检", "打开该课程的目录或当前章节后重新运行")
    initial = state = browser.ready()
    identity = browser.submission_identity(state, state.chapter or next(iter(state.chapters), None))
    pending = pending_attempt(journal, identity[0]) if identity else None
    if not pending and state.kind == "catalog":
        candidates = [chapter for chapter in state.chapters
                      if chapter["pending"] > 0 and chapter["clickable"] and not chapter["locked"]]
        if state.progress and state.progress[0] == state.progress[1] > 0:
            show_preflight(browser, state, initial, bank, font_dir)
            report.enter("课程目录", "可从个人空间选择其他课程")
            return "课程目录确认全部任务点已完成。"
        if not candidates:
            show_preflight(browser, state, initial, bank, font_dir)
            raise CourseError("目录中没有确认可进入的待办章节，请手动打开当前任务")
        report.select(candidates[0]["name"])
        state = browser.open_chapter(state, candidates[0])

    identity = browser.submission_identity(state)
    if not pending and identity:
        pending = pending_attempt(journal, identity[0])
    if pending:
        target = next((chapter for chapter in state.chapters if chapter["id"] == pending.chapter), None)
        report.enter("定位上次提交", "手动打开上次提交的章节后重新运行")
        if target:
            report.select(target["name"])
        if not target or (target != state.chapter and (target["locked"] or not target["clickable"])):
            raise CourseError("目录无法打开上次提交的章节，请先手动核对")
        if state.chapter != target:
            state = browser.open_chapter(state, target)
        if browser.submission_key(state) != pending.key:
            raise CourseError("打开的章节与上次提交记录不一致")
    attempt = pending or (find_attempt(journal, identity[1]) if identity else None)
    show_preflight(browser, state, initial, bank, font_dir, attempt if attempt and attempt.phase != "complete" else None)

    completed = 0
    while True:
        show_state(state)
        report.select(state.chapter["name"] if state.chapter else "当前考试页" if state.kind == "exam" else "尚未定位章节")
        report.enter("检查章节任务", "检查当前章节后重新运行")
        if state.kind == "locked":
            raise CourseError("请先完成当前任务或关闭提示")
        identity = browser.submission_identity(state)
        attempt = find_attempt(journal, identity[1]) if identity else None
        if attempt and (attempt.phase != "complete" or state.kind != "quiz_done"):
            state = recover_submission(browser, state, attempt, bank, font_dir, journal, report)
            if isinstance(state, ReviewRequired):
                return state
        while state.kind == "video":
            report.enter("视频播放", "重新运行，从当前未完成的视频继续")
            state = browser.play(state, VIDEO_TIMEOUT, on_complete=lambda: report.note("一项视频任务已完成"))
        if state.kind == "video_done" and browser.quiz_tab().count():
            report.enter("加载章节测验", "手动打开本章测验后重新运行")
            state = browser.open_quiz()
        if state.kind in {"quiz", "exam"}:
            state = answer_quiz(browser, state, bank, font_dir, journal, course, settings, report)
            if isinstance(state, ReviewRequired):
                return state
        if state.kind not in {"video_done", "quiz_done"}:
            raise CourseError("当前章节完成状态无法确认")
        completed += 1
        print(f"当前章节任务已完成，本次已处理 {completed} 个章节")
        chapter_index = state.chapters.index(state.chapter) if state.chapter else len(state.chapters)
        following = next(iter(state.chapters[chapter_index + 1:]), None)
        if following:
            report.enter("前往下一章节", f"打开「{safe_text(following['name'])}」后继续")
        else:
            report.enter("前往下一章节", "重新运行会检查当前课程的任务状态")
        state = browser.advance(state)
        if state is None:
            print("已到章节目录末尾")
            report.enter("目录末尾", "可从个人空间选择其他课程")
            return f"本次处理 {completed} 个章节，已到目录末尾。"


def parse_args(argv):
    parser = argparse.ArgumentParser(description="美学原理课程自动化")
    parser.add_argument("--run", action="store_true", required=True, help="运行学习通课程流程")
    login_mode = parser.add_mutually_exclusive_group()
    login_mode.add_argument("--standalone", action="store_true", help="在独立浏览器窗口检查并登录")
    login_mode.add_argument("--login", action="store_true", help="直接询问账号密码，在独立窗口登录")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    with ExitStack() as resources:
        return run_reported(args, resources)


def run_reported(args, resources):
    report = RunReport()
    report.enter("连接浏览器及登录", "确认浏览器和登录状态后重新运行")
    try:
        credentials = prompt_credentials() if args.login else None
        account = read_account(LOGIN_CONFIG)
        playwright = resources.enter_context(sync_playwright())
        context, reused = (None, None) if args.standalone or args.login else reusable_context(playwright)
        owned = context is None
        if owned:
            try:
                browser = playwright.chromium.launch(channel="msedge")
            except Error:
                browser = playwright.chromium.launch(channel="chrome")
            resources.callback(browser.close)
            context = browser.new_context()
        page, name = authenticated_page(context, reused, account, credentials)
        page.set_default_timeout(5000)
        result = operate(page, name, account.get("llm"), report)
        if isinstance(result, ReviewRequired):
            report.print_receipt(result.message, failed=result.failed)
            notify("课程助手 · 等待核对", safe_text(result.message), failed=result.failed)
            if owned and sys.stdin.isatty():
                try:
                    input("请在独立窗口核对，完成后按回车关闭窗口：")
                except EOFError:
                    pass
            return int(result.failed)
        report.print_receipt(result)
        notify("课程助手 · 运行完成", result)
        return 0
    except (CourseError, OSError, sqlite3.Error, Error) as error:
        message = safe_text(error) if isinstance(error, CourseError) else type(error).__name__
        report.print_receipt(message, failed=True)
        notify("课程助手 · 运行停止", message, failed=True)
        return 1
    except KeyboardInterrupt:
        report.print_receipt("已手动停止")
        return 130
    except Exception as error:
        report.print_receipt(type(error).__name__, failed=True)
        notify("课程助手 · 运行故障", type(error).__name__, failed=True)
        raise


def operate(page, course_name, settings, report=None):
    report = report or RunReport()
    report.enter("选择课程", "打开课程或从个人空间重新选课")
    if space_page(page.url):
        choices = course_choices(page)
        if not choices:
            raise CourseError("没有找到可进入的课程")
        print("我学的课")
        for index, (_, name, _, _) in enumerate(choices, 1):
            print(f"{index}. {name}")
        try:
            number = int(input("输入课程编号，按回车确认：").strip())
        except (EOFError, ValueError) as error:
            raise CourseError("请输入列表中的课程编号") from error
        if not 1 <= number <= len(choices):
            raise CourseError("课程编号超出列表范围")
        choice = choices[number - 1]
        course_name = choice[1]
        print(f"已选择《{course_name}》")
        page = open_course_choice(page, choice)
        page.set_default_timeout(5000)

    bank = QuestionBank(DEFAULT_QUESTIONS if course_name == "美学原理" else None)
    bank.load_verified(JOURNAL, course_id(page.url) or course_name)
    protection = nullcontext() if any(exam_page(frame.url) for frame in page.frames) else background_playback(page)
    with protection as remove_pause_listener:
        return run_course(CourseBrowser(page, remove_pause_listener), bank, FONT_DIR, JOURNAL, course_name, settings, report)


if __name__ == "__main__":
    raise SystemExit(main())
